"""secretbox.py: authenticated encryption for SwapDesk's local secrets file.

Provider credentials in ``data/config.json`` are otherwise plaintext,
protected only by OS file permissions. This module lets ``config.py`` store
them as an encrypted envelope (``data/config.enc``) instead, unlocked with a
master password the user sets.

Design
------
* KDF: **scrypt** (memory-hard), so a stolen ``config.enc`` can't be brute
  forced cheaply on GPUs the way a fast hash (PBKDF2/SHA) could. Parameters
  are stored in the envelope so they can be raised in a future build without
  breaking existing files.
* Cipher: **AES-256-GCM** (AEAD). GCM authenticates as well as encrypts, so a
  tampered ciphertext fails to decrypt rather than returning garbage that a
  caller might act on. The KDF parameters and salt are fed in as GCM
  *associated data*, so an attacker can't weaken the file by editing the
  stored scrypt cost down and re-pointing us at it: any edit to the header
  invalidates the tag.
* No home-grown crypto. Everything routes through ``cryptography``'s
  high-level ``Scrypt`` KDF and ``AESGCM`` construction.

The envelope is JSON so it stays a portable, inspectable file that lives in
the same ``data/`` folder as everything else (the app's portable-install
model is deliberate). Only the ciphertext is opaque:

    {
      "swapdesk_encrypted": 1,          # format marker + version
      "kdf": "scrypt",
      "kdf_params": {"n": 65536, "r": 8, "p": 1, "length": 32},
      "salt": "<base64>",
      "nonce": "<base64>",              # 96-bit GCM nonce, fresh per write
      "ciphertext": "<base64>"          # AES-GCM output (ciphertext || tag)
    }

``config.py`` owns the session (it caches the derived key in memory after an
unlock so every save doesn't re-run scrypt); this module is stateless.
"""
from __future__ import annotations

import base64
import json
import os

# cryptography is an optional dependency in the sense that a source checkout
# without it still runs (plaintext config, exactly the pre-encryption
# behaviour). Everything here raises CryptoUnavailable if it's missing so the
# caller can fall back / explain, rather than dying on an import at startup.
try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a stripped install
    _AVAILABLE = False


class CryptoUnavailable(Exception):
    """The `cryptography` package isn't installed, so encryption is off."""


class BadPassword(Exception):
    """The supplied password didn't decrypt the envelope (wrong password, or
    the file was tampered with / corrupted)."""


class MalformedEnvelope(Exception):
    """The envelope isn't a well-formed SwapDesk encrypted blob."""


FORMAT_VERSION = 1
_MARKER = "swapdesk_encrypted"

# scrypt cost. n=2**16 with r=8 is ~64 MiB of memory per derivation: heavy
# enough to make offline guessing expensive, light enough for a sub-second
# interactive unlock on a normal desktop. Stored per-file so a later build
# can raise these without stranding older envelopes.
_DEFAULT_N = 1 << 16
_DEFAULT_R = 8
_DEFAULT_P = 1
_KEY_LEN = 32          # AES-256
_SALT_LEN = 16
_NONCE_LEN = 12        # 96-bit GCM nonce (the size AES-GCM is defined for)

# Ceiling on the cost parameters we'll accept OUT of a file. The AAD binding
# above stops an attacker weakening a stored envelope, but it can't stop this
# one: the KDF has to run before there's a tag to check, so n/r/p are fed to
# scrypt while still fully attacker-controlled. n=2**22 with r=8 is ~4 GiB;
# anything past that is a corrupt file or a deliberate memory bomb, not a
# future build's raised defaults, and it costs the user an OOM kill or a
# multi-minute hang on what should be a password prompt. The limit is well
# clear of _DEFAULT_N (2**16) so raising the defaults later stays possible
# without stranding anything.
_MAX_N = 1 << 22
_MAX_R = 32
_MAX_P = 16
_MAX_KEY_LEN = 64
# The only key sizes AES-GCM accepts (128/192/256-bit). _KEY_LEN is one of
# them; a stored `length` outside this set is an envelope that cannot be
# opened at all, so it's rejected as malformed rather than allowed through
# to fail as a raw ValueError inside the cipher.
_AES_KEY_LENS = frozenset({16, 24, 32})


def available() -> bool:
    """True if the crypto backend is importable (i.e. encryption is usable)."""
    return _AVAILABLE


def _require() -> None:
    if not _AVAILABLE:
        raise CryptoUnavailable(
            "The 'cryptography' package is required to encrypt the config "
            "file. Install it with: pip install cryptography")


def new_salt() -> bytes:
    return os.urandom(_SALT_LEN)


def default_params() -> dict:
    return {"n": _DEFAULT_N, "r": _DEFAULT_R, "p": _DEFAULT_P, "length": _KEY_LEN}


def _checked_params(params: dict) -> tuple[int, int, int, int]:
    """Coerce the stored cost parameters to ints and reject anything outside
    the sane range. Raises MalformedEnvelope rather than letting a hand-edited
    or hostile file dictate how much memory scrypt is about to allocate."""
    try:
        n = int(params.get("n", _DEFAULT_N))
        r = int(params.get("r", _DEFAULT_R))
        p = int(params.get("p", _DEFAULT_P))
        length = int(params.get("length", _KEY_LEN))
    except (TypeError, ValueError) as e:
        raise MalformedEnvelope(f"non-numeric KDF parameter: {e}") from e
    if not (1 < n <= _MAX_N) or n & (n - 1):
        raise MalformedEnvelope(
            f"scrypt n={n} is not a power of two within 2..{_MAX_N}")
    if not (1 <= r <= _MAX_R):
        raise MalformedEnvelope(f"scrypt r={r} out of range")
    if not (1 <= p <= _MAX_P):
        raise MalformedEnvelope(f"scrypt p={p} out of range")
    if length not in _AES_KEY_LENS:
        # Not merely "out of range": AES-GCM is only defined for 128/192/256-bit
        # keys, so any other length produces a key AESGCM refuses with a bare
        # ValueError from inside decrypt(), escaping the documented
        # BadPassword/MalformedEnvelope contract. An envelope recording one was
        # never written by this module and can never be opened, so reject it
        # here where the caller is already handling MalformedEnvelope.
        raise MalformedEnvelope(
            f"key length {length} is not a valid AES key size "
            f"({', '.join(str(v) for v in sorted(_AES_KEY_LENS))})")
    return n, r, p, length


def derive_key(password: str, salt: bytes, params: dict) -> bytes:
    """Stretch `password` into an AES key with scrypt, using the cost
    parameters recorded alongside the file so an old envelope still opens
    after the defaults change. Those parameters come off disk and are used
    before the GCM tag can be checked, so they're range-checked first."""
    _require()
    n, r, p, length = _checked_params(params)
    kdf = Scrypt(salt=salt, length=length, n=n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def _aad(salt: bytes, params: dict) -> bytes:
    """Associated data bound into the GCM tag: the format version + KDF
    parameters + salt. Authenticating these means an attacker can't quietly
    downgrade the scrypt cost or swap the salt on a stored file and have it
    still open, they'd only get an authentication failure."""
    header = {
        "v": FORMAT_VERSION,
        "kdf": "scrypt",
        "n": int(params.get("n", _DEFAULT_N)),
        "r": int(params.get("r", _DEFAULT_R)),
        "p": int(params.get("p", _DEFAULT_P)),
        "length": int(params.get("length", _KEY_LEN)),
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt(plaintext: str, key: bytes, salt: bytes, params: dict) -> str:
    """Encrypt `plaintext` under an already-derived `key`, returning the JSON
    envelope as a string. A fresh random nonce is generated for every call:
    the key/salt stay stable across saves but the (key, nonce) pair never
    repeats, which is what AES-GCM requires."""
    _require()
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(salt, params))
    envelope = {
        _MARKER: FORMAT_VERSION,
        "kdf": "scrypt",
        "kdf_params": {
            "n": int(params.get("n", _DEFAULT_N)),
            "r": int(params.get("r", _DEFAULT_R)),
            "p": int(params.get("p", _DEFAULT_P)),
            "length": int(params.get("length", _KEY_LEN)),
        },
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ct).decode("ascii"),
    }
    return json.dumps(envelope, indent=2)


def parse_header(envelope: str) -> tuple[bytes, dict]:
    """Pull (salt, kdf_params) out of an envelope so a caller can derive the
    key. Raises MalformedEnvelope if it isn't a SwapDesk encrypted blob."""
    try:
        obj = json.loads(envelope)
    except (ValueError, TypeError) as e:
        raise MalformedEnvelope(f"not valid JSON: {e}") from e
    if not isinstance(obj, dict) or _MARKER not in obj:
        raise MalformedEnvelope("missing SwapDesk encryption marker")
    # The marker doubles as the format version, and it was written but never
    # read. A future build that changes the envelope layout would have had
    # every older install report "wrong password" -- the one message that
    # makes a user reach for the destructive reset. Say what is actually
    # wrong instead.
    version = obj.get(_MARKER)
    if version != FORMAT_VERSION:
        raise MalformedEnvelope(
            f"envelope format version {version!r} is not supported by this "
            f"build (expected {FORMAT_VERSION}). This file was written by a "
            f"different version of SwapDesk; upgrade rather than resetting.")
    try:
        salt = base64.b64decode(obj["salt"], validate=True)
        params = dict(obj.get("kdf_params") or {})
    except (KeyError, ValueError, TypeError) as e:
        raise MalformedEnvelope(f"bad header: {e}") from e
    if len(salt) != _SALT_LEN:
        raise MalformedEnvelope(
            f"bad header: salt is {len(salt)} bytes, expected {_SALT_LEN}")
    _checked_params(params)
    return salt, params


def decrypt(envelope: str, key: bytes) -> str:
    """Decrypt an envelope with an already-derived `key`. Raises BadPassword
    on an authentication failure (wrong key or tampered file) and
    MalformedEnvelope on a structurally broken blob."""
    _require()
    try:
        obj = json.loads(envelope)
        salt = base64.b64decode(obj["salt"])
        nonce = base64.b64decode(obj["nonce"], validate=True)
        ct = base64.b64decode(obj["ciphertext"], validate=True)
        params = dict(obj.get("kdf_params") or {})
    except (ValueError, TypeError, KeyError) as e:
        raise MalformedEnvelope(f"bad envelope: {e}") from e
    if len(salt) != _SALT_LEN:
        raise MalformedEnvelope(
            f"bad envelope: salt is {len(salt)} bytes, expected {_SALT_LEN}")
    if len(ct) < 16:
        raise MalformedEnvelope("bad envelope: ciphertext is too short")
    if len(nonce) != _NONCE_LEN:
        # AESGCM raises a bare ValueError for this, which escapes past the
        # json/base64 handler above and breaks the documented contract that
        # a structurally broken blob comes back as MalformedEnvelope.
        raise MalformedEnvelope(
            f"bad envelope: nonce is {len(nonce)} bytes, expected {_NONCE_LEN}")
    # Same reasoning as the nonce check above, for the stored KDF parameters.
    # _aad() coerces n/r/p/length with int(), so a hand-edited or corrupt
    # envelope carrying a non-numeric cost parameter raised a bare ValueError
    # from inside the try below, which only catches InvalidTag: it escaped past
    # the json/base64 handler and broke the documented contract. unlock() never
    # saw this because derive_key() range-checks first, but config.py decrypts
    # directly with a cached session key and does not.
    _checked_params(params)
    if len(key) not in _AES_KEY_LENS:
        # A key of the wrong size makes AESGCM() itself raise ValueError before
        # any tag is checked. Report it as a malformed envelope (the length
        # that produced it is recorded there) rather than leaking a raw
        # cipher-construction error to the caller. The key itself is never
        # included in the message.
        raise MalformedEnvelope(
            f"bad envelope: derived key is {len(key)} bytes, which is not a "
            f"valid AES key size")
    try:
        pt = AESGCM(key).decrypt(nonce, ct, _aad(salt, params))
    except InvalidTag as e:
        raise BadPassword("could not decrypt (wrong password or corrupt/"
                          "tampered file)") from e
    try:
        return pt.decode("utf-8")
    except UnicodeDecodeError as e:
        # Authenticated, so this isn't tampering: it's an envelope written by
        # something that wasn't us. Still structurally wrong, not a bad password.
        raise MalformedEnvelope(f"decrypted payload is not UTF-8: {e}") from e


def unlock(envelope: str, password: str) -> tuple[str, bytes, bytes, dict]:
    """Convenience: derive the key from `password` + the envelope's stored
    salt/params, decrypt, and hand back (plaintext, key, salt, params) so the
    caller can cache the key for subsequent saves. Raises BadPassword if the
    password is wrong."""
    salt, params = parse_header(envelope)
    key = derive_key(password, salt, params)
    plaintext = decrypt(envelope, key)
    return plaintext, key, salt, params

# Fixed by j2sec
