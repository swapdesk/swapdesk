"""
config.py: local, self-hosted config + history storage for SwapDesk.

Running from source, everything lives in a single folder next to the app
(portable install):
    ./data/config.json    provider API keys/secrets  (PLAINTEXT. See note)
    ./data/history.json    local record of created swaps

In a compiled build the app folder is a temp directory that PyInstaller
deletes on exit, so writable state moves to the platform's per-user location
instead: %APPDATA%\\SwapDesk, ~/Library/Application Support/SwapDesk, or
$XDG_DATA_HOME/SwapDesk. See _app_dir().

SECURITY NOTE
-------------
Provider credentials can be stored one of two ways:

* ENCRYPTED (recommended): set a master password and the keys live in
  data/config.enc, encrypted with AES-256-GCM under a scrypt-derived key
  (see secretbox.py). The plaintext config.json is shredded once the
  encrypted copy exists. Nothing on disk is readable without the password.
* PLAINTEXT (opt-out / no master password set): keys live in
  data/config.json in the clear, protected only by OS file permissions.
  This is the original behaviour, kept for headless/automated use where an
  interactive unlock prompt isn't possible.

Either way these are *affiliate/partner* credentials for creating swaps.
They are NOT wallet keys and cannot move your coins. Both files are created
with a restrictive ACL (Windows) / chmod 600 (POSIX) where possible, and the
data/ directory itself is locked to the current user.

history.json is the more privacy-sensitive of the two: it links your
provider, timestamps, deposit address, and destination address for every
swap you've ever made. auto_clear_history_days defaults to 90 (not 0/keep
forever) for that reason: change it in Settings if you want a different
window.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import secretbox

# Suppresses the console window PyInstaller's --windowed exe would otherwise
# briefly flash open for every subprocess.run() call below (icacls has no
# window of its own on a --windowed build's parent process, so Windows spawns
# one per call unless told not to). No-op / unused on non-Windows.
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _icacls() -> str:
    """Absolute path to icacls.exe.

    Not the bare name: CreateProcess searches the application directory and
    the current directory before System32, and this app is portable, so it
    routinely runs from a user-writable folder. A file named icacls.exe
    dropped next to the launcher would then be executed with the user's
    token every time the config is saved. Falls back to the bare name only
    if SystemRoot is unset, where there is nothing better to resolve
    against.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if not root:
        return "icacls"
    return str(Path(root) / "System32" / "icacls.exe")

# Guards read-modify-write access to history.json so concurrent status-poll
# threads (one per open deposit window) can't clobber each other's updates.
_HISTORY_LOCK = threading.Lock()

def _resource_dir() -> Path:
    """Where read-only bundled files (VERSION) live.

    PyInstaller unpacks a one-file build into a temp dir it exposes as
    sys._MEIPASS, and that path changes every launch.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def _app_dir() -> Path:
    """Where writable state (config, history) lives.

    Running from source this is the app folder, which keeps a portable
    install portable. Frozen, it MUST NOT be: sys._MEIPASS is a temp
    directory deleted on exit, so writing config there loses the user's API
    keys on every single launch, and next to the .exe often isn't writable
    (Program Files) anyway. Frozen builds therefore use the standard
    per-user location for the platform.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "SwapDesk"


RESOURCE_DIR = _resource_dir()
APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"        # plaintext store (legacy / opt-out)
CONFIG_ENC_PATH = DATA_DIR / "config.enc"     # master-password-encrypted store
HISTORY_PATH = DATA_DIR / "history.json"
COINS_PATH = DATA_DIR / "coins.json"


class ConfigLocked(Exception):
    """Raised by load_config()/save_config() when an encrypted config.enc
    exists but no master password has been supplied this session yet. The
    GUI (app.py) and CLI (preflight.py) each unlock BEFORE they touch the
    config, so this only surfaces if something bypasses that ordering."""


# In-memory master-password session. After a successful unlock() we cache the
# scrypt-derived key (not the password) plus the salt/params it was derived
# with, so every save_config() re-encrypts without re-running the ~64 MiB
# scrypt derivation. Cleared by lock(); never written to disk.
_session_key: bytes | None = None
_session_salt: bytes | None = None
_session_params: dict | None = None
_SESSION_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "trocador": {"api_key": ""},
    "sideshift": {"secret": "", "affiliate_id": ""},
    "changenow": {"api_key": ""},
    "chainflip": {"api_key": ""},
    "stealthex": {"api_key": ""},
    "fixedfloat": {"api_key": "", "api_secret": ""},
    "dex": {"api_key": ""},
    "auto_select_best": True,
    # name -> bool. Missing entries default to DISABLED: the authoritative
    # value is providers.constants.PROVIDER_ENABLED_DEFAULT (False), read
    # through providers.provider_enabled(). Providers are opt-in, so a fresh
    # install queries nobody until the user switches one on in Settings; that
    # is a privacy guarantee, asserted in CI, not a convenience default.
    # Written by ui.settings_tab.on_save_settings.
    "enabled_providers": {},
    "privacy": {
        # "off"  never use a proxy.
        # "auto" use a local Tor/SOCKS proxy if one is actually running,
        #        otherwise run direct. Never blocks startup, so a user who
        #        has never heard of Tor sees no failure, and a user who has
        #        it running gets it without configuring anything.
        # "on"   require the proxy. Fails closed: if it can't be used the
        #        app refuses to make requests rather than falling back to
        #        clearnet. For people who would rather not swap at all than
        #        swap unproxied.
        #
        # Default "auto" rather than "on" because "on" cannot be a safe
        # default: it would turn a fresh install into a failure dialog on
        # every machine without Tor. Its worst case is identical to "off".
        "proxy_mode": "auto",
        # Used when proxy_mode is "on". In "auto" the URL is built from
        # whichever port the probe found, so this is not consulted.
        "proxy_url": "socks5h://127.0.0.1:9050",   # Tor default SOCKS port
        "auto_clear_history_days": 90,              # 0 = keep forever
        "warn_on_address_reuse": True,
    },
    # DNS-over-HTTPS fallback (see providers/dns_hardening.py DOH_PROVIDERS / _doh_resolve).
    # Only used when local/ISP DNS fails to resolve a provider host; never
    # replaces the OS resolver otherwise. "providers" is an ordered list of
    # keys into providers.DOH_PROVIDERS, tried in order until one resolves.
    "dns": {
        "enabled": True,
        "providers": ["cloudflare", "google"],
    },
}


def _log_permission_warning(msg: str) -> None:
    """Best-effort: leave a visible trace when we couldn't lock down
    permissions on secrets, so the GUI can surface a one-time banner on
    next startup instead of silently running with weaker-than-intended
    file permissions."""
    with contextlib.suppress(Exception):
        warn_path = APP_DIR / "permission_warning.txt"
        warn_path.write_text(msg, encoding="utf-8")


def _windows_user() -> str:
    """Best-effort resolution of the current Windows username.

    Reading only the USERNAME env var was not enough: when it was empty
    the icacls call was silently skipped, leaving whatever ACL the file or
    directory inherited, with no warning. This tries
    two additional, independent sources and raises if none resolve a
    name, so the caller's except-block fails CLOSED (logs a warning)
    instead of silently leaving weaker-than-intended permissions.
    """
    for getter in (
        lambda: os.environ.get("USERNAME", ""),
        lambda: os.getlogin(),
        lambda: getpass.getuser(),
    ):
        with contextlib.suppress(Exception):
            name = getter()
            if name:
                return name
    raise OSError("could not determine current Windows username via USERNAME, "
                   "os.getlogin(), or getpass.getuser()")


_dir_secured_this_run = False  # set True after the first successful icacls
                                # lockdown; DATA_DIR's ACL doesn't change
                                # between saves within one process, so
                                # re-spawning icacls.exe on every single
                                # load_config()/save_config()/save_history()
                                # call (4+ call sites, some firing on every
                                # save) is pure overhead once it's confirmed.


def _ensure_dir() -> None:
    global _dir_secured_this_run
    # mode=0o700 is masked by umask, so the chmod below still matters on
    # POSIX; passing it here narrows (doesn't widen) the create-time perms.
    #
    # Failure is raised, not swallowed: without this directory there is
    # nowhere to store keys or history, so continuing would silently lose
    # every save. It is raised as a clear OSError rather than whatever the
    # filesystem produced, because on Windows this resolves through APPDATA
    # and a redirected or offline roaming profile is a real cause that the
    # raw error does not name.
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise OSError(
            f"SwapDesk could not create its data folder at {DATA_DIR}: {exc}. "
            f"This is usually a redirected or unavailable profile folder, or "
            f"a permissions problem on that path.") from exc
    if os.name == "nt" and _dir_secured_this_run:
        return
    try:
        if os.name == "nt":
            user = _windows_user()
            r = subprocess.run(
                [_icacls(), str(DATA_DIR), "/inheritance:r", "/grant:r", f"{user}:F"],
                shell=False, capture_output=True, check=False,
                # stdin must be given explicitly: a PyInstaller --windowed
                # build has no console, so the inherited standard handles are
                # invalid and subprocess raises OSError [WinError 6] trying to
                # pass them on. capture_output only covers stdout/stderr, so
                # stdin is the one left to fail on.
                stdin=subprocess.DEVNULL,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if r.returncode != 0:
                raise OSError(f"icacls failed: {r.stderr.decode('utf-8', errors='ignore')}")
            _dir_secured_this_run = True
        else:
            DATA_DIR.chmod(stat.S_IRWXU)  # 0o700: owner-only
    except Exception as exc:  # noqa: BLE001 - deliberately broad: this
        # spans a Windows subprocess call and a POSIX chmod, each of which
        # fails in different exception types, and every failure funnels to
        # the same one-time startup banner regardless of cause.
        _log_permission_warning(f"Could not restrict {DATA_DIR}: {exc}")


def _lock_down(path: Path) -> None:
    """Restrict the secrets file to the current user. Failures are
    surfaced via _log_permission_warning rather than swallowed, since a
    silently-unrestricted secrets file is a meaningful loss of guarantee."""
    try:
        if os.name == "nt":
            # Owner-only: use icacls to strip inheritance and grant only the user.
            user = _windows_user()
            r = subprocess.run(
                [_icacls(), str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                shell=False, capture_output=True, check=False,
                # stdin must be given explicitly: a PyInstaller --windowed
                # build has no console, so the inherited standard handles are
                # invalid and subprocess raises OSError [WinError 6] trying to
                # pass them on. capture_output only covers stdout/stderr, so
                # stdin is the one left to fail on.
                stdin=subprocess.DEVNULL,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if r.returncode != 0:
                raise OSError(f"icacls failed: {r.stderr.decode('utf-8', errors='ignore')}")
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except Exception as exc:  # noqa: BLE001 - same cross-platform spread
        # as _ensure_dir above: Windows subprocess vs. POSIX chmod raise
        # different exception types, both funnel to the same warning.
        _log_permission_warning(f"Could not restrict permissions on {path}: {exc}")


def _replace_with_retry(tmp: str, path: Path) -> None:
    """os.replace() over `path`, tolerant of a Windows-only failure mode.

    On POSIX, rename/replace onto an open file always succeeds, the old
    inode just stays alive until the last reader closes it. Windows has no
    equivalent: MoveFileEx (what os.replace uses under the hood) can raise
    PermissionError/WinError 5 if another handle has `path` open without
    FILE_SHARE_DELETE at that instant, e.g. a concurrent read of
    history.json from the GUI thread. This is transient, not a real
    permissions problem, and clears as soon as the reader's `open()` call
    finishes (a handful of milliseconds), so retry briefly before giving
    up for real."""
    if os.name != "nt":
        os.replace(tmp, path)
        return
    delay = 0.005
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def _secure_write(path: Path, text: str, shred_existing: bool = False) -> None:
    """Atomically write `text` to `path` with owner-only permissions.

    Two properties, both of which the previous write_text-then-chmod approach
    lacked:

    1. NO truncation window. The content is written to a temp file in the
       same directory, flushed+fsynced, then os.replace()'d over the target.
       os.replace is atomic on a single filesystem, so a concurrent reader
       (load_config / load_history run WITHOUT the history lock) always sees
       either the complete old file or the complete new one. Never a
       half-written one. A crash mid-write leaves the intact old file in
       place, not a truncated/corrupt one. Under a concurrent read/write
       probe this eliminated the partial-JSON reads the old path produced.

    2. NO world-readable window on POSIX. mkstemp creates the temp file
       0600 by default; the replaced file therefore inherits 0600 from the
       first byte. _lock_down() still runs afterward to enforce the ACL on
       the final path (and to cover Windows, where NTFS ACLs, not umask,
       govern access and mkstemp's mode is a no-op).

    On Windows specifically, the final swap goes through
    _replace_with_retry() rather than a bare os.replace(), since a
    concurrent reader holding `path` open there can cause a transient
    PermissionError that POSIX simply doesn't have (see its docstring)."""
    dir_path = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(dir_path), prefix=".tmp-", suffix=path.suffix)
    outgoing = None
    fh = None
    try:
        if os.name != "nt":
            os.chmod(tmp, 0o600)  # mkstemp is already 0600; explicit for clarity
        fh = os.fdopen(fd, "w", encoding="utf-8")
        with fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if shred_existing and path.exists():
            # A second name for the revision about to be orphaned, so it is
            # scrubbed only AFTER the replace succeeds. Scrubbing it first
            # would destroy the only copy of the keys if the replace failed.
            outgoing = _link_outgoing(path)
        _replace_with_retry(tmp, path)   # atomic swap into place
    except BaseException:
        # Never leave a stray temp file behind on any failure. The descriptor
        # is closed first when no file object took ownership of it, or the
        # unlink fails on Windows and the temp file stays.
        if fh is None:
            with contextlib.suppress(OSError):
                os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if outgoing is not None:
            # Unlink only: this name still shares the live file's data.
            with contextlib.suppress(OSError):
                outgoing.unlink()
        raise
    if outgoing is not None:
        _shred_file(outgoing)
    _lock_down(path)


def _link_outgoing(path: Path) -> Path | None:
    """Hard-link `path` under a temporary name in the same directory.

    Returns the link, or None where hard links are unavailable (FAT/exFAT,
    some network shares). None means the outgoing revision is simply not
    scrubbed: keeping the user's data safe outranks a best-effort shred.
    """
    link = path.with_name(f".old-{os.getpid()}-{os.urandom(4).hex()}{path.suffix}")
    try:
        os.link(path, link)
    except (OSError, NotImplementedError):
        return None
    return link


# Master-password encryption (see secretbox.py)
# config.enc, when present, is the authoritative store and REPLACES the
# plaintext config.json (the plaintext file is shredded once an encrypted
# copy is written). Precedence in load_config(): config.enc wins if present.

ENCRYPTION_MARKER_PATH = APP_DIR / "encryption_enabled"


def _set_encryption_marker(enabled: bool) -> None:
    """Record, outside the config store, that this install uses encryption.

    The whole state machine for "is this install encrypted" used to be
    CONFIG_ENC_PATH.exists(), which makes deleting one file indistinguishable
    from never having set a password. Combined with
    security.master_password_prompt_dismissed -- read from the same plaintext
    file an attacker would be editing -- an encrypted install could be
    downgraded to plaintext with no prompt, no banner and no record. The
    marker does not protect the keys; it makes their disappearance visible.
    """
    with contextlib.suppress(OSError):
        _ensure_dir()
        if enabled:
            ENCRYPTION_MARKER_PATH.write_text("1", encoding="utf-8")
            _lock_down(ENCRYPTION_MARKER_PATH)
        elif ENCRYPTION_MARKER_PATH.exists():
            ENCRYPTION_MARKER_PATH.unlink()


def encryption_was_enabled() -> bool:
    """True if this install previously had a master password set."""
    try:
        return ENCRYPTION_MARKER_PATH.exists()
    except OSError:
        return False


def encryption_unexpectedly_missing() -> bool:
    """True when the marker says encrypted but config.enc is gone.

    Read at startup. Either the encrypted store was deleted or moved, or the
    data directory is not the one this install was using. Both deserve a stop,
    not a silent fresh start into plaintext.
    """
    return encryption_was_enabled() and not CONFIG_ENC_PATH.exists()


def crypto_available() -> bool:
    """True if the `cryptography` backend is installed, so encryption can be
    offered/used at all. False means the app runs exactly as before
    (plaintext config, OS permissions only)."""
    return secretbox.available()


def is_config_encrypted() -> bool:
    return CONFIG_ENC_PATH.exists()


def config_exists() -> bool:
    """True if EITHER store exists on disk. False only on a genuine first
    run, which is when the GUI offers to set up a master password."""
    return CONFIG_ENC_PATH.exists() or CONFIG_PATH.exists()


def master_password_prompt_dismissed() -> bool:
    """True if the user chose Not now at the encrypt prompt for the current
    plaintext config. Only relevant while unencrypted (an encrypted config is
    gated on unlock, not on this). Reads config.json directly so a genuine
    first run doesn't get a file written just to answer the question."""
    if not CONFIG_PATH.exists():
        return False
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return bool(isinstance(data, dict)
                and isinstance(data.get("security"), dict)
                and data["security"].get("master_password_prompt_dismissed"))


def set_master_password_prompt_dismissed(dismissed: bool = True) -> None:
    """Persist that the user declined encryption, so a plaintext config isn't
    offered it on every launch. Called only from the unencrypted path, so
    save_config here writes plaintext, never over an encrypted store."""
    cfg = load_config()
    cfg.setdefault("security", {})["master_password_prompt_dismissed"] = bool(dismissed)
    save_config(cfg)


def is_unlocked() -> bool:
    return _session_key is not None


def _set_session(key: bytes, salt: bytes, params: dict) -> None:
    global _session_key, _session_salt, _session_params
    with _SESSION_LOCK:
        _session_key, _session_salt, _session_params = key, salt, params


def _clear_session() -> None:
    global _session_key, _session_salt, _session_params
    with _SESSION_LOCK:
        _session_key = _session_salt = _session_params = None


def lock() -> None:
    """Forget the in-memory master key. load/save then refuse until the next
    unlock() (encrypted store) or fall through to plaintext (no enc store)."""
    _clear_session()


def unlock(password: str) -> bool:
    """Try to unlock the encrypted config with `password`. On success the
    derived key is cached for the session and True is returned; on a wrong
    password, False. Raises secretbox.MalformedEnvelope if config.enc exists
    but isn't a valid envelope (corrupt/foreign file), which is a different
    problem from a wrong password and shouldn't be silently retried."""
    if not CONFIG_ENC_PATH.exists():
        return False
    try:
        envelope = CONFIG_ENC_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        _plaintext, key, salt, params = secretbox.unlock(envelope, password)
    except secretbox.BadPassword:
        return False
    _set_session(key, salt, params)
    return True


def set_master_password(password: str) -> None:
    """Encrypt the current config under a new master password. Reads the
    current config (plaintext, defaults, or an already-unlocked encrypted
    one), writes config.enc, then shreds the plaintext config.json. After
    this the session is unlocked with the new key."""
    if not secretbox.available():
        raise secretbox.CryptoUnavailable(
            "Encryption needs the 'cryptography' package: pip install cryptography")
    cfg = load_config()  # requires unlock if already encrypted; else plaintext/defaults
    salt = secretbox.new_salt()
    params = secretbox.default_params()
    key = secretbox.derive_key(password, salt, params)
    _set_session(key, salt, params)
    try:
        save_config(cfg)
    except Exception:
        # See change_master_password: a failed write must not leave the
        # process believing it is unlocked under a key that was never
        # persisted, because the next save_config() would then encrypt to a
        # password the user was told had not been set.
        _clear_session()
        raise
    _set_encryption_marker(True)  # session set -> writes config.enc and shreds config.json


def change_master_password(old_password: str, new_password: str) -> bool:
    """Re-key the encrypted config. Verifies `old_password` first; returns
    False if it's wrong (and leaves everything untouched)."""
    if not is_config_encrypted():
        return False
    if not unlock(old_password):
        return False
    cfg = load_config()
    salt = secretbox.new_salt()
    params = secretbox.default_params()
    key = secretbox.derive_key(new_password, salt, params)
    previous = (_session_key, _session_salt, _session_params)
    _set_session(key, salt, params)
    try:
        # shred_existing=True inside save_config scrubs the OLD envelope
        # before the new one replaces it. Without that, the previous
        # config.enc was unlinked but not overwritten, and it still decrypts
        # under the OLD password: rotating after a shoulder-surf or keylogger
        # scare did not actually revoke anything against someone able to carve
        # free space.
        save_config(cfg)
    except Exception:
        # Do not leave the session holding a key whose envelope was never
        # written. The caller is told the change failed; the process must
        # agree with it, or the next save silently re-encrypts everything
        # under a password the UI just said did not take.
        _set_session(*previous) if previous[0] else _clear_session()
        raise
    return True


def remove_master_password(password: str) -> bool:
    """Decrypt back to a plaintext config.json and delete config.enc.
    Verifies `password` first; returns False if it's wrong."""
    if not is_config_encrypted():
        return False
    if not unlock(password):
        return False
    cfg = load_config()
    # Write the plaintext copy FIRST (atomically), then drop the session and
    # shred the encrypted store. Ordering it this way means there's never a
    # moment where neither file exists (no data-loss window), and we never
    # route through save_config() while an encrypted store is still on disk
    # (which would be refused).
    _secure_write(CONFIG_PATH, json.dumps(cfg, indent=2),
                  shred_existing=True)
    _clear_session()
    _set_encryption_marker(False)   # deliberate removal, not a downgrade
    if CONFIG_ENC_PATH.exists():
        _shred_file(CONFIG_ENC_PATH)
    return True


def prompt_and_unlock_cli() -> bool:
    """Terminal unlock for the CLI (preflight.py). No-op (returns True) when
    the config isn't encrypted or is already unlocked. Gives three attempts
    then gives up."""
    if not is_config_encrypted() or is_unlocked():
        return True
    for _ in range(3):
        try:
            pw = getpass.getpass(
                "SwapDesk config is encrypted. Enter master password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if unlock(pw):
            return True
        print("Incorrect master password.")
    return False


def _shred_file(path: Path) -> None:
    """Best-effort removal of a secrets file: overwrite the bytes before
    unlinking so a casual undelete doesn't recover the old plaintext keys.
    This is NOT a guarantee on journaling/copy-on-write filesystems or SSDs
    with wear-levelling, hence best-effort, but it's strictly better than a
    bare unlink and costs nothing."""
    with contextlib.suppress(OSError):
        size = path.stat().st_size
        with open(path, "r+b", buffering=0) as fh:
            fh.write(os.urandom(size))
            fh.flush()
            os.fsync(fh.fileno())
    with contextlib.suppress(OSError):
        path.unlink()


def reset_encrypted_config() -> bool:
    """Discard config.enc without the password: the forgot-password path.

    There is deliberately no recovery here. The master password is the key,
    it is never stored, and scrypt+AES-GCM has no backdoor by design, so a
    forgotten password means the encrypted provider keys are gone. The only
    thing this app can honestly offer is a clean start.

    Only config.enc is removed. History is a separate, unencrypted file and
    is left alone: it holds no credentials, and silently wiping a user's
    swap record as a side effect of a password reset would be a surprise
    they never asked for.

    Returns True if an encrypted config was present and removed.
    """
    if not CONFIG_ENC_PATH.exists():
        return False
    _shred_file(CONFIG_ENC_PATH)
    lock()  # drop any cached session key so nothing stale survives the reset
    _set_encryption_marker(False)   # deliberate reset, not a silent downgrade
    return not CONFIG_ENC_PATH.exists()


def _decrypt_config_dict() -> dict:
    """Decrypt config.enc with the cached session key and return the parsed
    dict. Assumes is_unlocked(); callers check first."""
    envelope = CONFIG_ENC_PATH.read_text(encoding="utf-8")
    plaintext = secretbox.decrypt(envelope, _session_key)
    return json.loads(plaintext)


def _merge_defaults(data: dict) -> dict:
    """Merge loaded config over DEFAULT_CONFIG (forward-compatible: a config
    written by an older build gains any keys added since)."""
    if not isinstance(data, dict):
        raise ConfigLocked(
            "The config file is valid JSON but not a settings object. It has "
            "been left untouched; fix it or move it aside to start fresh.")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        elif isinstance(merged.get(k), dict):
            continue          # wrong type for a settings section: keep the default
        else:
            merged[k] = v
    _migrate_proxy_mode(data, merged)
    return merged


def _migrate_proxy_mode(loaded: dict, merged: dict) -> None:
    """Carry a pre-proxy_mode config's boolean over to the new setting.

    proxy_enabled=True meant "require the proxy, fail closed", so it maps to
    "on" and NOT to "auto": someone who deliberately turned Tor on is the
    exact person who must not be silently downgraded to a direct connection.
    False maps to "off" for the mirror reason, since defaulting them to
    "auto" would start routing traffic somewhere they never asked for.

    Only applies when the old key is present and the new one is not, so a
    config already carrying proxy_mode is left alone.
    """
    old_privacy = loaded.get("privacy")
    if not isinstance(old_privacy, dict):
        return
    if "proxy_mode" in old_privacy or "proxy_enabled" not in old_privacy:
        return
    merged.setdefault("privacy", {})["proxy_mode"] = (
        "on" if old_privacy.get("proxy_enabled") else "off")


def load_config() -> dict:
    _ensure_dir()
    # Encrypted store wins when present. It must be unlocked first (the GUI
    # and CLI both do this before any config access); if it isn't, fail
    # loudly rather than silently falling back to a stale/absent plaintext.
    if CONFIG_ENC_PATH.exists():
        if not is_unlocked():
            raise ConfigLocked(
                "config.enc is encrypted; call unlock(password) first")
        try:
            data = _decrypt_config_dict()
        except (secretbox.BadPassword, secretbox.MalformedEnvelope,
                ValueError, OSError) as exc:
            # No DEFAULT_CONFIG fallback here, unlike the plaintext branch
            # below. There the file is already unreadable and nothing is lost.
            # Here it is readable-but-unusable, and defaults would hand back an
            # empty config that the next save_config() re-encrypts over
            # config.enc, destroying keys that were still recoverable. Refuse
            # and leave the file alone.
            raise ConfigLocked(
                f"config.enc could not be read ({exc}). It may be corrupt or "
                f"from another install. The file has been left untouched; "
                f"restore a backup or delete it to start fresh.") from exc
        return _merge_defaults(data)

    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError:
        # Unreadable (permissions, a device error). Nothing to preserve and
        # nothing we could write back anyway, so start from defaults.
        return json.loads(json.dumps(DEFAULT_CONFIG))
    except ValueError as exc:
        # Readable but not valid JSON, which usually means a hand edit with a
        # trailing comma, not a lost file. Returning defaults here handed back
        # an empty config that the next save_config() wrote straight over the
        # top, destroying keys that a one-character fix would have recovered.
        # Same reasoning as the config.enc branch above: refuse and leave it.
        raise ConfigLocked(
            f"config.json could not be parsed ({exc}). The file has been left "
            f"untouched; fix the JSON or move it aside to start fresh.") from exc
    return _merge_defaults(data)


def save_config(cfg: dict) -> None:
    _ensure_dir()
    payload = json.dumps(cfg, indent=2)
    if is_unlocked():
        # Encrypted mode: write the envelope, then shred any leftover
        # plaintext config.json so keys don't linger in two places.
        envelope = secretbox.encrypt(payload, _session_key, _session_salt,
                                     _session_params)
        _secure_write(CONFIG_ENC_PATH, envelope, shred_existing=True)
        if CONFIG_PATH.exists():
            _shred_file(CONFIG_PATH)
    elif CONFIG_ENC_PATH.exists():
        # An encrypted store exists but we're locked. Writing plaintext here
        # would silently drop the secrets next to the encrypted file and
        # defeat encryption, so refuse. (remove_master_password() removes
        # config.enc explicitly before it wants a plaintext write.)
        raise ConfigLocked(
            "config is encrypted and locked; unlock before saving")
    else:
        _secure_write(CONFIG_PATH, payload, shred_existing=True)


def _set_history_aside() -> None:
    """Move an unparseable history.json out of the way instead of letting the
    next append_history() overwrite it.

    A swap record carries the order ID that lets a user find an order at the
    provider after a failure. Returning [] for a damaged file is right for the
    caller (nothing usable to show), but the very next swap would then write a
    fresh one-entry file over the old bytes and destroy anything a hand-repair
    could have recovered. load_config() refuses for the same reason; history
    has no "refuse" option because a swap must still be recordable, so the
    damaged file is renamed (permissions preserved, owner-only) and kept.

    Never raises: if the rename fails (read-only folder, a concurrent loader
    got there first) the caller still gets [] and nothing is made worse.
    """
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = HISTORY_PATH.with_name(f"history.corrupt-{stamp}.json")
    n = 1
    while dest.exists():
        dest = HISTORY_PATH.with_name(f"history.corrupt-{stamp}-{n}.json")
        n += 1
    try:
        os.replace(HISTORY_PATH, dest)
    except OSError:
        return
    # Only the file name is reported, never its contents. stderr is None in a
    # windowed (no-console) build.
    try:
        if sys.stderr is not None:
            sys.stderr.write(
                f"SwapDesk: history.json could not be read and was kept as "
                f"{dest.name} in the data folder; a new history was started.\n")
    except Exception:  # noqa: BLE001 - a notice must never break loading
        pass


def load_history() -> list:
    _ensure_dir()
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except OSError:
        # An I/O error says nothing about the content: leave the file alone.
        return []
    except ValueError:
        # Not valid JSON (includes bad UTF-8). Keep the bytes, start fresh.
        _set_history_aside()
        return []
    # Callers iterate rows and call .get() on them; anything else in a
    # hand-edited file would break preflight and the status poll.
    if not isinstance(data, list):
        _set_history_aside()
        return []
    return [e for e in data if isinstance(e, dict)]


def load_coins_cache() -> dict:
    """Return the last-saved merged {ticker: name} coin map, or {} if there
    is no cache yet or it's unreadable.

    This is public reference data (the universe of coins the configured
    providers offer), not user data. It exists purely so the From/To coin
    dropdowns come up fully populated on the first frame instead of sitting
    on the curated fallback until the startup background refresh lands. A
    stale or missing cache is never fatal: the curated set and the refresh
    both cover it."""
    _ensure_dir()
    if not COINS_PATH.exists():
        return {}
    try:
        data = json.loads(COINS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Coerce to the expected {str: str} shape, dropping anything malformed
    # rather than feeding a hand-edited/corrupt file straight into the UI.
    return {str(t): str(n) for t, n in data.items()
            if isinstance(t, str) and isinstance(n, str)}


def save_coins_cache(merged: dict) -> None:
    """Persist the merged coin map for the next cold start. Best-effort: a
    write failure just means the next launch falls back to curated + a fresh
    refresh, so errors are swallowed rather than surfaced.

    Written atomically but WITHOUT the owner-only lockdown that config.json
    and history.json get: this is public data, and skipping _lock_down()
    avoids spawning an icacls subprocess on Windows on every background
    refresh."""
    if not merged:
        return
    _ensure_dir()
    try:
        fd, tmp = tempfile.mkstemp(dir=str(COINS_PATH.parent), prefix=".tmp-",
                                   suffix=COINS_PATH.suffix)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(merged, fh)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, COINS_PATH)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError:
        pass


def coins_cache_age_seconds() -> float | None:
    """Seconds since the coin cache was last written, or None if there is no
    cache. Lets startup skip the network refresh when the cache is recent: the
    set of coins providers offer barely moves day to day."""
    try:
        return time.time() - COINS_PATH.stat().st_mtime
    except OSError:
        return None


def append_history(entry: dict) -> None:
    with _HISTORY_LOCK:
        hist = load_history()
        hist.insert(0, entry)
        _secure_write(HISTORY_PATH, json.dumps(hist[:200], indent=2))


def update_history_status(order_id: str, status: str) -> bool:
    """Locked read-modify-write for a single history entry's status. Safe to
    call from multiple polling threads concurrently (one per open deposit
    window) without one update clobbering another. Returns True only if the
    stored value actually changed."""
    return update_history_field(order_id, "status", status)


def update_history_field(order_id: str, field: str, value) -> bool:
    """Locked read-modify-write for a single field on a single history
    entry. Same concurrency guarantee as update_history_status (which is
    now a thin wrapper over this).

    Returns True if the entry was found AND the value differed, False
    otherwise. Callers use this to skip work that only matters when
    something really changed: status polling runs every 15s per open swap
    and most ticks report the same status, so redrawing the history list on
    every tick was rebuilding a few hundred widgets to produce an identical
    view."""
    if not order_id:
        return False
    with _HISTORY_LOCK:
        hist = load_history()
        changed = False
        for e in hist:
            if e.get("order_id") == order_id:
                if e.get(field) != value:
                    e[field] = value
                    changed = True
                break
        if changed:
            _secure_write(HISTORY_PATH, json.dumps(hist, indent=2))
        return changed


def prune_history(max_age_days: int) -> int:
    """Delete history entries older than max_age_days. 0/None = no-op.
    Returns the number of entries removed."""
    if not max_age_days:
        return 0
    import datetime
    with _HISTORY_LOCK:
        hist = load_history()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - \
            datetime.timedelta(days=max_age_days)
        kept = []
        removed = 0
        for e in hist:
            ts = e.get("time")
            t = None
            try:
                t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError, TypeError):
                # Entries written before the now_iso() fix used a
                # non-ISO "%Y-%m-%d %H:%M UTC" format. Parse that too so
                # upgrading doesn't leave old entries permanently unprunable.
                try:
                    # "UTC" here is a literal suffix, not a %z-parseable
                    # offset, so strptime necessarily returns naive; made
                    # aware on the next line before it's ever compared.
                    t = datetime.datetime.strptime(  # noqa: DTZ007
                        ts, "%Y-%m-%d %H:%M UTC")
                    t = t.replace(tzinfo=datetime.timezone.utc)
                except (ValueError, AttributeError, TypeError):
                    kept.append(e)  # truly unparseable. Keep, don't risk data loss
                    continue
            if t >= cutoff:
                kept.append(e)
            else:
                removed += 1
        if removed:
            _secure_write(HISTORY_PATH, json.dumps(kept, indent=2))
        return removed


def clear_history() -> None:
    with _HISTORY_LOCK:
        _secure_write(HISTORY_PATH, json.dumps([], indent=2))


def previously_used_destination(address: str) -> dict | None:
    """Return the most recent past history entry that sent to this same
    destination address, or None. Used to warn against address reuse, which
    undermines the privacy of non-transparent chains less but still links
    activity together on transparent chains (BTC/LTC/DOGE/etc)."""
    if not address:
        return None
    address = address.strip().lower()
    for e in load_history():
        dest = e.get("destination")
        if isinstance(dest, str) and dest.strip().lower() == address:
            return e
    return None


# Address book: addresses you've explicitly confirmed are yours/correct.
#
# Mitigates the "typo'd but still valid-format address" failure mode: format
# checks (below) can't tell a correctly-typed WRONG address from a correctly-
# typed RIGHT one. An address book lets preflight distinguish "an address
# you've vouched for before" from "an address typed for the first time right
# now", and warn extra hard on the latter for anything but a small amount.
# Stored in its own file (not history.json) since it's forward-looking
# ("addresses I trust") rather than a backward-looking record of past swaps.
ADDRESS_BOOK_PATH = DATA_DIR / "address_book.json"
_ADDRESS_BOOK_LOCK = threading.Lock()


def _valid_book(data) -> dict:
    """Coerce a loaded address book to the shape the rest of the code assumes.

    This file is the only thing standing between a coin with no curated
    address pattern and preflight's hard failure, so a malformed or
    hand-edited one must not reach the lookup as a surprise type. Anything
    that is not {coin: {address_lower: {"address": str, "label": str}}} is
    dropped rather than trusted.
    """
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for coin, entries in data.items():
        if not isinstance(coin, str) or not isinstance(entries, dict):
            continue
        clean: dict = {}
        for addr_key, entry in entries.items():
            if not isinstance(addr_key, str) or not isinstance(entry, dict):
                continue
            addr = entry.get("address")
            if not isinstance(addr, str) or not addr.strip():
                continue
            label = entry.get("label")
            clean[addr_key] = {
                "address": addr,
                "label": label if isinstance(label, str) else "",
            }
        if clean:
            out[coin] = clean
    return out


def load_address_book() -> dict:
    """Returns {coin: {address_lower: {"address": str, "label": str}}}.

    The value is the entry dict written by add_to_address_book, not a bare
    label string: the original casing is kept alongside for display, since
    the key is lowercased for lookup.
    """
    _ensure_dir()
    if not ADDRESS_BOOK_PATH.exists():
        return {}
    try:
        return _valid_book(json.loads(
            ADDRESS_BOOK_PATH.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return {}


def add_to_address_book(coin: str, address: str, label: str = "") -> None:
    coin = coin.upper()
    addr_key = (address or "").strip().lower()
    if not addr_key:
        return
    with _ADDRESS_BOOK_LOCK:
        book = load_address_book()
        book.setdefault(coin, {})[addr_key] = {
            "address": address.strip(),   # original casing preserved for display
            "label": label,
        }
        _secure_write(ADDRESS_BOOK_PATH, json.dumps(book, indent=2))


def _address_book_entry(coin: str, address: str) -> dict | None:
    """The raw stored entry for this (coin, address) pair, or None."""
    book = load_address_book()
    entry = book.get(coin.upper(), {}).get((address or "").strip().lower())
    return entry if isinstance(entry, dict) else None


def is_confirmed_address(coin: str, address: str) -> bool:
    """True if this (coin, address) pair was previously saved to the address
    book, regardless of whether a label was given.

    PRESENCE is the signal, not the label. is_in_address_book() below returns
    the label, which is the empty string for an address saved with
    add_to_address_book(coin, address) and no third argument, the documented
    way to save one. Callers that used bool(is_in_address_book(...)) as a
    "has the user vouched for this address" test therefore read every
    unlabelled saved address as unsaved. For the preflight gate that HARD
    FAILS coins with no curated format pattern, that made the failure
    unclearable: the remediation text told the user to call
    add_to_address_book(), which did not lift the block. Anything asking
    "has this been confirmed before" must use this function; only display
    code that actually wants the label text should use the one below."""
    return _address_book_entry(coin, address) is not None


def is_in_address_book(coin: str, address: str) -> str | None:
    """Returns the saved label if this (coin, address) pair was previously
    confirmed, else None. An entry saved without a label returns "", which
    is FALSY: use is_confirmed_address() for presence checks, and reserve
    this for when the label text itself is wanted."""
    entry = _address_book_entry(coin, address)
    return entry.get("label") if entry else None


# Light client-side address sanity checks. These are NOT full validation,
# they only catch obvious paste errors before you commit funds. The provider
# performs authoritative validation server-side.
_PATTERNS = {
    # bech32/bech32m data part is conventionally all-lowercase or
    # all-uppercase (BIP-173), accept either case here (mixed case is
    # still rejected by real wallets/nodes; this is only a paste-error
    # sanity check, not full validation) instead of only ever accepting
    # lowercase and bouncing a correctly-cased uppercase paste.
    # bech32 hrp is now matched case-insensitively (scoped (?i:...) on the
    # prefix only), so an all-uppercase "BC1..." paste, a valid BIP-173
    # encoding of the same address, is accepted instead of bounced. The
    # base58 alternative stays case-sensitive (case is significant there).
    "BTC":  re.compile(r"^((?i:bc1)[0-9A-Za-z]{20,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,39})$"),
    "LTC":  re.compile(r"^((?i:ltc1)[0-9A-Za-z]{20,90}|[LM3][a-km-zA-HJ-NP-Z1-9]{26,39})$"),
    # Base58: Monero uses the same alphabet as Bitcoin, excluding 0, O, I
    # and l to avoid visual ambiguity. Accepting those would weaken this
    # paste-error check versus the BTC pattern below.
    "XMR":  re.compile(r"^[48][1-9A-HJ-NP-Za-km-z]{94,105}$"),
    "ETH":  re.compile(r"^0x[0-9a-fA-F]{40}$"),
    # Dogecoin P2PKH addresses are exactly 34 characters; the previous
    # {32,34} on the tail allowed 35-36 char strings through too.
    "DOGE": re.compile(r"^D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}$"),
    # CashAddr charset is "qpzry9x8gf2tvdw0s3jn54khce6mua7l": no '1' or 'b'/
    # 'i'/'o' (previous [a-z0-9] let '1' through, which cashaddr never uses).
    "BCH":  re.compile(r"^(bitcoincash:)?[qp][02-9ac-hj-np-vwxyz]{40,60}$|^[13][a-km-zA-HJ-NP-Z1-9]{25,39}$"),
    "DASH": re.compile(r"^X[1-9A-HJ-NP-Za-km-z]{33}$"),
    # NOTE: SOL is intentionally NOT validated by regex. A Solana address is
    # a raw 32-byte ed25519 public key with NO version byte and NO checksum,
    # so a base58 charset+length regex cannot tell it apart from a BTC/DASH/
    # DOGE base58 address of similar length, a mis-pasted wrong-coin address
    # would be waved straight through on a real-funds field. address_looks_valid()
    # below special-cases SOL to base58-decode and require EXACTLY 32 bytes,
    # which rejects the 25-byte checksummed base58 addresses of those other
    # chains. Keeping SOL out of _PATTERNS routes it to that stricter check.
    # USDC is ERC-20 in this app (see EVM_TOKENS in providers/zerox.py, COINS in providers/constants.py),
    # same address shape as ETH. Without this it fell through to the
    # near-no-op "len(address) >= 16" fallback meant for truly-unknown
    # coins, which barely catches a fat-fingered address on a real-funds
    # field.
    "USDC": re.compile(r"^0x[0-9a-fA-F]{40}$"),
    # Kaspa: "kaspa:" + bech32 data part. Schnorr (32-byte pubkey, 'q...')
    # and ECDSA (33-byte pubkey, 'p...') addresses both observed at 61
    # chars after the prefix; a small range is kept rather than a fixed
    # count for the same reason as the base58 coins above (bech32 output
    # length can shift by a character or two). "kaspatest:"/other network
    # prefixes are deliberately NOT accepted: this app only ever settles
    # to mainnet.
    "KAS":  re.compile(r"^(?i:kaspa):[02-9ac-hj-np-z]{60,65}$"),
    # Zcash has three address families, and all three are legitimate
    # destinations depending on the provider:
    #   t1 / t3  transparent, base58 + checksum, Bitcoin-shaped. Every
    #            provider wired up here settles to these.
    #   zs1...   Sapling shielded, bech32.
    #   u1...    unified, bech32m, variable length and can be long.
    # Accepting the shielded forms is deliberate: rejecting them would push
    # a privacy-coin user toward the transparent address, which is the
    # opposite of what this app is for. Whether a given provider can
    # actually PAY OUT to a shielded address is a separate question this
    # regex can't answer, so the swap tab warns instead of guessing.
    "ZEC":  re.compile(r"^(t[13][a-km-zA-HJ-NP-Z1-9]{33}"
                       r"|(?i:zs1)[0-9a-z]{70,80}"
                       r"|(?i:u1)[0-9a-z]{100,})$"),
    # Firo (formerly Zcoin): base58 P2PKH, 'a' prefix. Length is a range
    # rather than a fixed count because base58 output varies by a character
    # or two with leading zero bytes; pinning it exactly would reject a
    # minority of valid addresses. The prefix still rules out every other
    # coin here.
    "FIRO": re.compile(r"^a[1-9A-HJ-NP-Za-km-z]{25,34}$"),
    # Decred: base58 P2PKH, 'Ds' prefix. Decred uses a BLAKE-256 checksum
    # rather than double-SHA256, so the checksum can't be verified with the
    # base58 helper used for BTC/LTC below; prefix and charset only.
    "DCR":  re.compile(r"^Ds[1-9A-HJ-NP-Za-km-z]{25,34}$"),
    # Pirate Chain: shielded-only in practice. Uses Sapling 'zs1' addresses,
    # the SAME format as Zcash shielded addresses.
    #
    # This is a real hazard with no regex fix: an ARRR zs1 address and a ZEC
    # zs1 address are indistinguishable by shape, so this pattern cannot
    # catch a user pasting one where the other belongs, and that mistake is
    # unrecoverable. preflight warns on it explicitly instead. The legacy
    # transparent 'R' form is accepted too, though exchanges rarely use it.
    "ARRR": re.compile(r"^((?i:zs1)[0-9a-z]{70,80}"
                       r"|R[1-9A-HJ-NP-Za-km-z]{25,34})$"),
    # Beam: the SBBS address, which is hex and 64-67 characters
    # (BeamMW/beam wiki, "New address types support": old-style addresses are
    # "hex encoded and have length 64-67 characters").
    #
    # Restricted to SBBS deliberately, because that is the form a swap payout
    # needs: Beam's own docs state exchanges and mining pools require an SBBS
    # address. Beam also has newer base58 "regular", offline and max-privacy
    # formats, but their lengths are not documented, so a pattern covering
    # them would have to be loose enough to accept almost anything. Rejecting
    # them here is the correct outcome rather than a limitation: a provider
    # cannot settle to one anyway, so accepting one would let the user
    # approve a destination the swap can never reach.
    "BEAM": re.compile(r"^[0-9a-fA-F]{64,67}$"),
}


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def _base58_decode(s: str) -> bytes | None:
    """Minimal, dependency-free base58 decode. Returns the raw bytes, or
    None if `s` contains a non-base58 character. Used for length-based
    sanity checks (e.g. SOL = exactly 32 bytes), NOT full validation."""
    if len(s) > 128:  # No legitimate base58 address exceeds this
        return None
    num = 0
    for ch in s:
        val = _B58_INDEX.get(ch)
        if val is None:
            return None
        num = num * 58 + val
    # Recover leading-zero bytes, which base58 encodes as leading '1's.
    n_leading_zeros = len(s) - len(s.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * n_leading_zeros + body


# Base58Check version bytes each chain actually mints, for the coins whose
# addresses are Base58Check at all.
#
# These drive validation instead of a leading-character regex, because the
# leading character is not a reliable property of a version byte. Firo's 0x52
# is the case that proved it: about 1.2% of valid Firo addresses encode to a
# leading 'Z' rather than 'a', because a 25-byte value starting 0x52 straddles
# a base58 digit boundary, and an '^a' pattern rejected every one of them.
# Decoding and comparing the version byte has no such blind spot, and it
# verifies the checksum on the way past, which a charset-and-length pattern
# cannot do at all.
#
# DCR is deliberately absent: Decred checksums with BLAKE-256 rather than
# double-SHA256, so _b58check_decode() cannot verify it and it keeps the
# prefix-and-charset pattern. BEAM (hex), KAS/bech32/CashAddr (bech32-family)
# and XMR (Monero's own base58 variant) are not Base58Check either.
_B58CHECK_VERSIONS: dict[str, set[bytes]] = {
    "BTC":  {b"\x00", b"\x05"},                  # P2PKH '1', P2SH '3'
    "LTC":  {b"\x30", b"\x32", b"\x05"},         # 'L', 'M', legacy P2SH '3'
    "DOGE": {b"\x1e"},                           # 'D'
    "DASH": {b"\x4c"},                           # 'X'
    "BCH":  {b"\x00", b"\x05"},                  # legacy '1' / '3'
    "ZEC":  {b"\x1c\xb8", b"\x1c\xbd"},          # transparent t1 / t3
    "FIRO": {b"\x52"},                           # 'a' (and sometimes 'Z')
    "ARRR": {b"\x3c"},                           # transparent 'R'
}

# Length window for a legacy Base58Check address: a 25-byte payload encodes to
# 33-34 characters and a 26-byte one (two-byte versions, e.g. ZEC) to 35-36,
# with leading zero bytes shortening the low end. Used only to tell "this is a
# corrupted legacy address" apart from "this is some other address format
# entirely", which decides whether a failed checksum is fatal or irrelevant.
_B58_LEGACY_LENGTHS = range(26, 37)


def _b58check_decode(address: str) -> bytes | None:
    """Decode a Base58Check string and verify its trailing 4-byte
    double-SHA256 checksum, returning version||payload with the checksum
    stripped, or None if it isn't valid Base58Check.

    The checksum is the whole point: it is a 32-bit digest over the version
    and payload, so it rejects effectively every single-character
    transcription error, which is the paste mistake this app exists to catch
    before funds move."""
    raw = _base58_decode(address)
    if raw is None or len(raw) < 5:
        return None
    data, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4] != checksum:
        return None
    return data


def _looks_like_legacy_b58(address: str) -> bool:
    """True if `address` is the right shape to BE a legacy Base58Check address
    (base58 alphabet throughout, plausible length) whether or not it actually
    checksums. Distinguishes a corrupted legacy address, which must be
    rejected, from a bech32/CashAddr/hex address that was never Base58Check
    and has to go to its own pattern instead."""
    return (len(address) in _B58_LEGACY_LENGTHS
            and all(ch in _B58_INDEX for ch in address))


# Segwit (bech32 / bech32m) addresses, BIP-173 and BIP-350. The _PATTERNS entry
# for these accepted any alphanumeric tail, so a single mistyped character in a
# bc1/ltc1 address passed exactly the check that Base58Check addresses above
# fail -- the same "the checksum is the whole point" argument applies here.
_SEGWIT_HRP = {"BTC": "bc", "LTC": "ltc"}
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def _segwit_address_valid(hrp: str, address: str) -> bool:
    """Full BIP-173/350 decode: charset, single case, checksum (bech32 for
    witness v0, bech32m for v1+), and witness program length."""
    if address != address.lower() and address != address.upper():
        return False                      # mixed case is invalid by spec
    addr = address.lower()
    sep = addr.rfind("1")
    if sep != len(hrp) or addr[:sep] != hrp or len(addr) > 90 or len(addr) - sep < 8:
        return False
    data = [_BECH32_CHARSET.find(c) for c in addr[sep + 1:]]
    if -1 in data:
        return False
    const = _bech32_polymod([ord(c) >> 5 for c in hrp] + [0]
                            + [ord(c) & 31 for c in hrp] + data)
    if const not in (1, _BECH32M_CONST):
        return False
    words = data[:-6]
    witver = words[0]
    if witver > 16 or (witver == 0) != (const == 1):
        return False
    acc = bits = 0
    prog = []
    for w in words[1:]:
        acc = ((acc << 5) | w) & 0xFFF
        bits += 5
        while bits >= 8:
            bits -= 8
            prog.append((acc >> bits) & 0xFF)
    if bits >= 5 or (acc << (8 - bits)) & 0xFF:
        return False                      # non-zero padding
    if not 2 <= len(prog) <= 40:
        return False
    return witver != 0 or len(prog) in (20, 32)


def _sol_address_looks_valid(address: str) -> bool:
    """A Solana address is a base58-encoded 32-byte ed25519 pubkey. Decoding
    and requiring exactly 32 bytes is the only client-side check that
    distinguishes it from a same-length base58 address on another chain
    (BTC/DASH/DOGE legacy decode to 25 bytes). Not a checksum check (SOL
    addresses carry none), but it stops the wrong-coin paste this app
    otherwise waved through."""
    if not (32 <= len(address) <= 44):
        return False
    decoded = _base58_decode(address)
    return decoded is not None and len(decoded) == 32


# Legacy Base58Check version bytes that more than one supported chain uses.
#
# There is no encoding-level fix for this and no regex that can help: a legacy
# address carries a one-byte version and a checksum over it, nothing that names
# a chain. BTC, LTC and BCH all minted P2SH addresses under version 0x05, so
# one '3...' string is a genuinely valid address on all three, and BTC and BCH
# share 0x00 for P2PKH '1...' the same way. Rejecting these would bounce
# addresses that are correct, and accepting them silently is how a deposit ends
# up on the wrong chain, unrecoverably. So they stay VALID and the caller warns:
# see legacy_address_ambiguity() below and the block in preflight.run_preflight
# that consumes it, which mirrors how the ARRR/ZEC 'zs1' collision is handled.
#
# Only chains this app actually supports are listed. Adding a coin that reuses
# one of these version bytes means adding it here too.
_LEGACY_B58_SHARED_VERSIONS = {
    0x00: ("BTC", "BCH"),           # P2PKH, renders as '1...'
    0x05: ("BTC", "LTC", "BCH"),    # P2SH,  renders as '3...'
}

# The modern, chain-specific format to point the user at instead. Each of these
# encodes the chain in the string itself, so the collision above cannot happen.
_UNAMBIGUOUS_FORMAT_HINT = {
    "BTC": "a bech32 'bc1...' address",
    "LTC": "an 'M...' P2SH address or a bech32 'ltc1...' one",
    "BCH": "a CashAddr 'bitcoincash:...' address",
}


def _base58check_payload(address: str) -> tuple[int, bytes] | None:
    """Decode a legacy Base58Check address and verify its checksum, returning
    (version_byte, payload) or None if it isn't a well-formed 25-byte
    Base58Check string. Deliberately separate from address_looks_valid(): this
    only ever informs a warning, it never decides validity."""
    raw = _base58_decode(address)
    if raw is None or len(raw) != 25:
        return None
    body, checksum = raw[:21], raw[21:]
    if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] != checksum:
        return None
    return body[0], body[1:]


def legacy_address_ambiguity(coin: str, address: str) -> tuple[str, ...]:
    """Which OTHER supported chains this legacy Base58 address is also a
    syntactically valid address for.

    Returns an empty tuple for everything unambiguous: bech32, CashAddr, XMR,
    ETH, an address whose version byte only one supported chain uses, and
    anything that isn't valid Base58Check at all. A non-empty result means the
    string cannot be told apart from an address on the named chains, so the
    caller must say so rather than route funds on a guess.

    `coin` is the chain the user SAID this address is for; it's excluded from
    the result, which lists only what it could be confused with.
    """
    coin = (coin or "").upper()
    address = (address or "").strip()
    if not address:
        return ()
    decoded = _base58check_payload(address)
    if decoded is None:
        return ()
    version, _payload = decoded
    sharing = _LEGACY_B58_SHARED_VERSIONS.get(version)
    if not sharing or coin not in sharing:
        return ()
    return tuple(c for c in sharing if c != coin)


def unambiguous_format_hint(coin: str) -> str:
    """The chain-specific address format to recommend for `coin`, or "" if
    there's nothing better to suggest. Used only in warning text."""
    return _UNAMBIGUOUS_FORMAT_HINT.get((coin or "").upper(), "")


def has_curated_pattern(coin: str) -> bool:
    """True if `coin` has a real per-chain format check in _PATTERNS (or
    the special-cased SOL decode-check). False means address_looks_valid()
    is only doing the near-no-op len(address) >= 16 fallback below.

    Callers that create swaps should treat False here
    as a signal to require an explicit, separate user confirmation before
    proceeding, since the client-side check alone barely catches a
    fat-fingered or wrong-coin address for these tickers.
    """
    coin = coin.upper()
    return coin == "SOL" or coin in _PATTERNS


def address_looks_valid(coin: str, address: str) -> bool:
    address = (address or "").strip()
    if not address:
        return False
    coin = coin.upper()
    if coin == "SOL":
        return _sol_address_looks_valid(address)
    hrp = _SEGWIT_HRP.get(coin)
    if hrp and address.lower().startswith(hrp + "1"):
        return _segwit_address_valid(hrp, address)
    # Base58Check coins are validated by decoding and checking the checksum and
    # version byte, NOT by matching a charset-and-length pattern. A pattern
    # accepts every single-character typo in an address (the checksum exists
    # precisely to catch those), and it also has to guess the leading
    # character, which is not a stable property of a version byte. See
    # _B58CHECK_VERSIONS. Non-Base58Check forms fall through to _PATTERNS below.
    versions = _B58CHECK_VERSIONS.get(coin)
    if versions is not None:
        data = _b58check_decode(address)
        if data is not None:
            # Well-formed Base58Check. Accept only if this chain actually mints
            # that version byte, which is also what keeps a DOGE address from
            # passing as BTC. The 20 is the hash160 every one of these carries.
            return any(data.startswith(v) and len(data) == len(v) + 20
                       for v in versions)
        if _looks_like_legacy_b58(address):
            # Base58 alphabet and legacy length, but the checksum did not
            # verify: a corrupted or mistyped legacy address. Falling through
            # to the pattern here would wave it straight past, which is the
            # whole defect this check exists to close.
            return False
        # Not Base58Check at all (bech32, CashAddr, shielded, hex): the
        # coin's own pattern is the right test for those.
    pat = _PATTERNS.get(coin)
    if pat is None:
        # No curated format for this ticker (e.g. only seen via a live
        # coin-list refresh). Intentionally weak, just rejects empty/
        # near-empty paste errors. A wrong per-coin regex is worse than
        # none. NOT a substitute for a real per-coin check;
        # add one to _PATTERNS above for any coin you rely on regularly.
        # See has_curated_pattern(): callers should gate on that, not on
        # the boolean returned here, to require extra confirmation.
        return len(address) >= 16
    return bool(pat.match(address))
