"""providers: non-custodial swap provider integrations for SwapDesk.

Split (2026) from the original single-file providers.py into a package, one
module per provider, so each integration can be read/audited/changed in
isolation instead of scrolling a 2600-line file. This __init__ re-exports
the same public surface the old module had, so `import providers as prov`
call sites elsewhere in the app (app.py, preflight.py) are unaffected.

Design principles
-----------------
* NON-CUSTODIAL: every swap is created so the provider settles the output
  coin DIRECTLY to the user's destination address. SwapDesk never receives,
  holds, or forwards funds. It only asks a provider for a deposit address and
  shows it to you.
* Decimal everywhere for monetary values (no float rounding surprises).
* One normalized interface (Quote / Swap / Status) over nine different APIs.

Providers implemented
---------------------
* Trocador.app         -> privacy exchange AGGREGATOR (fans out to 20+ partners)
* SideShift.ai  (v2)   -> variable-rate shifts (7-day window, rate locks on deposit)
* FixedFloat    (v2)   -> instant non-custodial swap (signed requests)
* 0x (DEX)      (v2)   -> on-chain DEX aggregator (quote-only; execute in wallet)
* ChangeNOW     (v2)   -> standard/floating flow
* THORChain            -> decentralized, memo-based; no credentials
* Maya Protocol        -> THORChain fork, same memo flow; no credentials
* Chainflip            -> via Broker-as-a-Service (BaaS)
* StealthEX            -> instant exchange

Every provider except 0x uses a "floating" flow, so estimates are
indicative and the real rate fixes when your deposit is seen on-chain.
0x is quote-only (no deposit flow, you sign from your wallet).

Module layout
-------------
* dns_hardening.py -> DoH resolution, rebinding protection, fallback notice
* torprobe.py      -> finds a usable local Tor SOCKS port
* constants.py     -> COINS registry, STATUS_* vocabulary, shared helpers
* base.py          -> Quote, Swap, ProviderError(s), SwapProvider base class
* sideshift.py, changenow.py, trocador.py, fixedfloat.py, zerox.py,
  stealthex.py, chainflip.py
                   -> one provider each
* thorfork.py      -> THORChain and Maya Protocol, which share a memo format
                      and therefore a base class
* diagnostics.py   -> connectivity diagnostics, coin refresh, build_providers()

Low-level DNS-hardening internals (``_DOH_ENABLED``, ``_doh_resolve``,
``_resolve_via``, etc.) live in and are owned by ``providers.dns_hardening``
now rather than this top-level namespace. Code that needs to read or patch
that state should do so via ``providers.dns_hardening.*`` so it's touching
the actual live module state rather than a stale copy.
"""
from __future__ import annotations

# Imports below are grouped by role and annotated, not isort-ordered: the
# grouping is how you find which submodule owns a name. isort would flatten
# it back into one alphabetical block.
from . import dns_hardening  # (exposed as providers.dns_hardening)

# --- DNS hardening: public, stable entry points only -----------------------
from .dns_hardening import (
    DEFAULT_DOH_PROVIDERS,
    DOH_PROVIDER_ORDER,
    DOH_PROVIDERS,
    configure_doh_resolvers,
    set_dns_fallback_notifier,
)

# --- Constants / shared vocabulary ------------------------------------------
from .constants import (
    COINS,
    STATUS_COMPLETE,
    STATUS_CONFIRMING,
    STATUS_EXCHANGING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_NEEDS_ACTION,
    STATUS_PARTIAL,
    STATUS_REFUNDED,
    STATUS_REFUNDING,
    STATUS_SENDING,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    KNOWN_STATUSES,
    TERMINAL_STATUSES,
    TROCADOR_NETWORK,
    PROVIDER_ENABLED_DEFAULT,
    _addr_in_memo_fields,
    _dec,
    provider_enabled,
    normalize_status,
    registry_only,
)

# --- Base types --------------------------------------------------------------
from .base import (
    ProviderError,
    ProviderNetworkError,
    Quote,
    Swap,
    SwapProvider,
    proxy_url_problem,
)

# --- Providers ---------------------------------------------------------------
from .chainflip import Chainflip
from .torprobe import (
    find_socks_port,
    proxy_url_for,
    socks_available,
    verify_is_tor,
)
from .changenow import ChangeNow
from .fixedfloat import FixedFloat
from .sideshift import SideShift
from .trocador import Trocador
from .stealthex import StealthEX
from .thorfork import MayaProtocol, THORChain, _ThorForkProvider
from .zerox import EVM_TOKENS, ZeroExDEX

# --- Diagnostics / coin refresh / factory ------------------------------------
from .diagnostics import (
    CoinRefreshResult,
    DiagResult,
    build_providers,
    refresh_all_coins,
    proxy_mode,
    resolve_proxy,
    run_diagnostics,
    summarize_diagnostics,
)

__all__ = [  # noqa: RUF022  (grouped by role, not alphabetical)
    "dns_hardening",
    "DEFAULT_DOH_PROVIDERS", "DOH_PROVIDER_ORDER", "DOH_PROVIDERS",
    "configure_doh_resolvers", "set_dns_fallback_notifier",
    "COINS", "TROCADOR_NETWORK", "TERMINAL_STATUSES", "KNOWN_STATUSES",
    "normalize_status",
    "registry_only",
    "_addr_in_memo_fields", "_dec",
    "PROVIDER_ENABLED_DEFAULT", "provider_enabled",
    "STATUS_WAITING", "STATUS_CONFIRMING", "STATUS_EXCHANGING",
    "STATUS_SENDING", "STATUS_COMPLETE", "STATUS_REFUNDED", "STATUS_EXPIRED",
    "STATUS_FAILED", "STATUS_UNKNOWN", "STATUS_PARTIAL", "STATUS_NEEDS_ACTION",
    "STATUS_REFUNDING",
    "Quote", "Swap", "ProviderError", "ProviderNetworkError", "SwapProvider",
    "SideShift", "ChangeNow", "Trocador", "FixedFloat", "ZeroExDEX",
    "THORChain", "MayaProtocol", "_ThorForkProvider", "Chainflip",
    "find_socks_port", "proxy_url_for", "socks_available", "verify_is_tor",
    "proxy_mode", "resolve_proxy", "proxy_url_problem",
    "StealthEX",
    "EVM_TOKENS",
    "DiagResult", "run_diagnostics", "summarize_diagnostics",
    "CoinRefreshResult", "refresh_all_coins", "build_providers",
]
