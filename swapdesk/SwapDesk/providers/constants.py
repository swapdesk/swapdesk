"""providers.constants: coin registry, normalized status vocabulary, and
small shared helpers used across every provider implementation.
"""
from __future__ import annotations

import math
import re
import time
from decimal import Decimal, InvalidOperation

# Coin registry
#
# Maps a plain ticker to the per-provider network identifier. Swap providers
# disagree on network names (bitcoin vs btc, monero vs xmr), so we normalize
# here. Extend this dict to add more coins.
#   ticker: (display_name, sideshift_network, changenow_currency, changenow_network)
COINS: dict[str, tuple[str, str, str, str]] = {
    "BTC":  ("Bitcoin",       "bitcoin",   "btc",  "btc"),
    "XMR":  ("Monero",        "monero",    "xmr",  "xmr"),
    "LTC":  ("Litecoin",      "litecoin",  "ltc",  "ltc"),
    "ETH":  ("Ethereum",      "ethereum",  "eth",  "eth"),
    "DOGE": ("Dogecoin",      "dogecoin",  "doge", "doge"),
    "BCH":  ("Bitcoin Cash",  "bitcoincash", "bch", "bch"),
    "DASH": ("Dash",          "dash",      "dash", "dash"),
    "SOL":  ("Solana",        "solana",    "sol",  "sol"),
    # Transparent (t-addr) routing. Every provider here settles ZEC to a
    # t-address; shielded payout is provider-specific and not something this
    # app can promise, so address_looks_valid() accepts shielded formats but
    # the destination warning in the swap tab flags them.
    "ZEC":  ("Zcash",         "zcash",     "zec",  "zec"),
    # Privacy coins routed mainly through Trocador, which aggregates the
    # smaller exchanges that list them. SideShift/ChangeNOW network names
    # follow each provider's usual lowercase-ticker convention; a provider
    # that doesn't list one of these returns "unsupported" for the pair,
    # which is the intended outcome rather than an error.
    "FIRO": ("Firo",           "firo",      "firo", "firo"),
    "DCR":  ("Decred",         "decred",    "dcr",  "dcr"),
    "ARRR": ("Pirate Chain",   "piratechain", "arrr", "arrr"),
    # BEAM is curated to the SBBS address format only; see the note in
    # config.py's _PATTERNS for why the other Beam address formats are
    # rejected rather than accepted.
    "BEAM": ("Beam",           "beam",      "beam", "beam"),
    # ERC-20 on Ethereum mainnet. Added so the 0x DEX integration has a
    # second EVM token to pair against ETH (0x can't quote a pair with only
    # one token registered). sideshift_network/changenow_network follow the
    # same "ethereum"/"eth" convention used for native ETH above, matching
    # each provider's published coin listing for USDC-ERC20, worth a final
    # cross-check against /v2/coins (SideShift) and the ChangeNOW currency
    # list before relying on this pair for a real swap.
    "USDC": ("USD Coin",      "ethereum",  "usdc", "eth"),
    # Kaspa: single native network on every provider here (no L2/bridged
    # variant to disambiguate), so the ticker/network convention used for
    # BTC/DOGE/etc above applies directly.
    "KAS":  ("Kaspa",          "kaspa",     "kas",  "kas"),
}

def registry_only(names: dict[str, str]) -> dict[str, str]:
    """Drop every ticker that isn't in COINS.

    COINS is the app's supported set, not a starting point that a live
    coin-list refresh is free to grow. A provider's own catalogue runs to
    hundreds or thousands of assets, and a ticker that arrives that way has
    no entry in config._PATTERNS, so address_looks_valid() falls back to the
    near-no-op len(address) >= 16 check for it. Offering a coin the app
    cannot check a destination address for is worse than not offering it.
    Add the ticker to COINS (and a pattern in config.py) to support a coin.
    """
    return {t: n for t, n in names.items() if t in COINS}


# Trocador uses its own network labels. Native coins are "Mainnet"; a couple
# differ. If a pair errors with a network complaint, adjust here (or check
# GET /api/coins for the exact ticker/network strings).
TROCADOR_NETWORK: dict[str, str] = {
    "BTC": "Mainnet", "XMR": "Mainnet", "LTC": "Mainnet", "ETH": "ETH",
    "DOGE": "Mainnet", "BCH": "Mainnet", "DASH": "Mainnet", "SOL": "Mainnet",
    "ZEC": "Mainnet", "FIRO": "Mainnet", "DCR": "Mainnet", "ARRR": "Mainnet",
    "BEAM": "Mainnet", "USDC": "ETH", "KAS": "Mainnet",
}

# Normalized status vocabulary shared across providers.
STATUS_WAITING    = "Waiting for deposit"
STATUS_CONFIRMING = "Confirming deposit"
STATUS_EXCHANGING = "Exchanging"
STATUS_SENDING    = "Sending to you"
STATUS_COMPLETE   = "Complete"
STATUS_REFUNDED   = "Refunded"
STATUS_EXPIRED    = "Expired"
STATUS_FAILED     = "Failed"
STATUS_UNKNOWN    = "Unknown"
# Deliberately NOT terminal: the deposit didn't match what was expected
# (short/partial payment) and the trade needs the user to look at it, but
# it may still resolve on its own once they act, collapsing this into
# STATUS_COMPLETE ("Done, coins sent") would tell the user a stuck/partial
# trade is finished when it isn't; collapsing it into STATUS_FAILED would
# stop polling before it has a chance to actually finish.
STATUS_PARTIAL    = "Partially paid. Check provider"
# Deliberately NOT terminal, for the same reason: FixedFloat's EMERGENCY
# state means the deposit arrived late/short/over, user must choose
# "exchange at new rate" or "refund" on ff.io. Funds are safe but parked,
# not lost, not actually failed. Keep polling, once resolved the UI picks
# up the real terminal status (DONE/REFUNDED/EXPIRED) instead of showing
# "failed" prematurely.
STATUS_NEEDS_ACTION = "Needs your input on the provider's site"
# Deliberately NOT terminal: the swap did not go through and the deposit is
# on its way back, but the return transfer has not been seen on-chain yet.
# Reporting it as STATUS_REFUNDED or STATUS_FAILED at this point would stop
# polling before the refund exists.
STATUS_REFUNDING  = "Refund in progress"

# Status strings are persisted verbatim into history.json, so an entry
# written by an older build keeps whatever wording that build used. The
# history view colours a row by looking the stored string up against the
# constants above, and an unrecognised value falls back to grey. Renaming a
# status therefore turns an old "your funds are stuck" row from red to grey
# unless its previous spelling is still resolvable. Map retired spellings
# here rather than freezing the wording forever.
_LEGACY_STATUS = {
    "Partially paid \u2014 check provider": STATUS_PARTIAL,   # pre-0.1.9
}


def normalize_status(stored: str) -> str:
    """Resolve a status string from history to its current spelling."""
    return _LEGACY_STATUS.get(stored, stored)

# Statuses that mean the swap is over (stop polling).
TERMINAL_STATUSES = {STATUS_COMPLETE, STATUS_REFUNDED, STATUS_EXPIRED, STATUS_FAILED}

# The complete normalized vocabulary.
KNOWN_STATUSES = frozenset({
    STATUS_WAITING, STATUS_CONFIRMING, STATUS_EXCHANGING, STATUS_SENDING,
    STATUS_COMPLETE, STATUS_REFUNDED, STATUS_EXPIRED, STATUS_FAILED,
    STATUS_UNKNOWN, STATUS_PARTIAL, STATUS_NEEDS_ACTION, STATUS_REFUNDING,
})


# Providers are opt-in: absent from enabled_providers means off, so a fresh
# install queries nobody until the user picks. Kept here rather than
# defaulted at each call site so a new provider can't ship enabled by
# inheriting a stray True.
PROVIDER_ENABLED_DEFAULT = False


def provider_enabled(enabled_cfg: dict, name: str) -> bool:
    """True if the user has switched `name` on in Settings."""
    return bool((enabled_cfg or {}).get(name, PROVIDER_ENABLED_DEFAULT))


_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _addr_in_memo_fields(addr: str, fields: list) -> bool:
    """True if `addr` matches one of the ':'-delimited memo fields exactly.
    EVM addresses match case-insensitively (a node may echo an ETH address
    lowercased even if the user supplied it checksummed); all others must
    match byte-for-byte, since case is significant for base58/bech32."""
    if addr in fields:
        return True
    if _EVM_ADDR_RE.match(addr):
        low = addr.lower()
        return any(_EVM_ADDR_RE.match(f) and f.lower() == low for f in fields)
    return False


def _dec(value) -> Decimal | None:
    """Best-effort Decimal parse; returns None on junk (e.g. None, '', 'nan')."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    # Reject non-finite values outright (inf / nan are never valid amounts).
    if not d.is_finite():
        return None
    # Bound the exponent as well as finiteness. Decimal("1E+999999999") is
    # finite, so it used to pass straight through to theme.fmt()/full_str(),
    # where the deliberate non-exponential "f" format expands it in full: a
    # measured 1e9-character string and ~10s of frozen GUI thread, from one
    # field of one quote response. No real amount or rate in this app is
    # outside 1e-30..1e30; anything past that is a broken or hostile response.
    if not (-30 <= d.adjusted() <= 30):
        return None
    return d


def _eta_minutes(secs) -> int | None:
    """Whole minutes from a provider's duration-in-seconds field, or None.

    Rejects bool (an int subclass) and the NaN/Infinity that Python's json
    accepts, which int() cannot convert.
    """
    if isinstance(secs, bool) or not isinstance(secs, (int, float)):
        return None
    if not math.isfinite(secs) or secs < 0:
        return None
    return max(1, int(secs // 60))


def _epoch_utc(value) -> str | None:
    """Render a provider's Unix-epoch deadline as "%Y-%m-%d %H:%M:%S UTC", or
    None when it is not a usable timestamp (bool, NaN, out of range)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(value))
    except (OverflowError, OSError, ValueError):
        return None
