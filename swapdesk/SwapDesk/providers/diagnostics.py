"""providers.diagnostics: connectivity diagnostics, coin-list refresh, and
the build_providers() factory that assembles configured provider instances.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from . import dns_hardening as _dns
from . import torprobe as _torprobe
from .base import ProviderError, SwapProvider
from .chainflip import Chainflip
from .changenow import ChangeNow
from .constants import COINS, registry_only
from .fixedfloat import FixedFloat
from .sideshift import SideShift
from .stealthex import StealthEX
from .thorfork import MayaProtocol, THORChain
from .trocador import Trocador
from .zerox import ZeroExDEX


@dataclass
class DiagResult:
    label: str
    host: str
    ok: bool
    detail: str


def run_diagnostics(providers: list, timeout: float = 6.0) -> list:
    """Test connectivity to every provider host plus the DoH resolvers and a
    known-good control site. Returns a list of DiagResult, control site
    first. Safe to call from a background thread (does no UI work)."""
    targets: list[tuple[str, str]] = [("Internet (control)", "https://www.google.com")]
    for p in providers:
        base = getattr(p, "BASE", None) or getattr(p, "site", None)
        if base:
            targets.append((p.name, base))
    if _dns._DOH_ENABLED:
        for url in _dns._DOH_RESOLVERS:
            targets.append((f"{_dns._doh_label_for_url(url)} DoH", url))

    seen_hosts = set()
    deduped: list[tuple[str, str, str]] = []  # (label, url, host)
    for label, url in targets:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or url
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        deduped.append((label, url, host))

    # Reuse a real provider's session (proxy settings and all) so results
    # reflect actual runtime behavior. A bare unproxied session would give
    # misleading "unreachable" results for everything when the user has Tor/
    # SOCKS proxy routing enabled in Settings.
    session = providers[0].session if providers else requests.Session()
    # The provider session's own User-Agent is reused deliberately. A
    # distinct "SwapDesk-diagnostics/1.0" string undid the anti-fingerprinting
    # choice made in SwapProvider.__init__ (a generic browser UA, so provider
    # traffic is not identifiable as this app) at the exact moment the app
    # contacts every provider host in quick succession -- the most
    # recognisable traffic pattern it ever produces.
    diag_headers = {}

    def ping_one(item: tuple[str, str, str]) -> DiagResult:
        label, url, host = item
        try:
            # Resolved the same way a real request is. Every DNS guarantee the
            # app makes -- DoH-first, rebinding rejection, IP pinning, the
            # Secure-DNS-mode hard stop -- lives in SwapProvider._request, and
            # this function calls session.get directly. So a user who had
            # switched on "never use your network's DNS" and then clicked Run
            # Diagnostics emitted a plaintext lookup to their ISP resolver for
            # every provider host at once: the complete profile of what this
            # app talks to, to the exact resolver they asked never to be used.
            if _dns._DOH_ENABLED and not session.proxies:
                ip, doh_errors = _dns._doh_resolve(host) if host else (None, [])
                if ip:
                    with _dns._resolve_via(host, ip):
                        session.get(url, timeout=timeout, headers=diag_headers)
                    return DiagResult(label, host, True, "reachable")
                if _dns._DOH_SECURE_MODE:
                    detail = "; ".join(doh_errors) or "no resolver reachable"
                    return DiagResult(
                        label, host, False,
                        f"secure DNS mode: no DoH resolver could resolve this "
                        f"host ({detail})")
                _dns._notify_dns_fallback(host, doh_errors)
            session.get(url, timeout=timeout, headers=diag_headers)
            return DiagResult(label, host, True, "reachable")
        except requests.exceptions.SSLError as e:
            return DiagResult(label, host, False, f"TLS error: {e}")
        # Before ConnectionError, not after: requests.exceptions.ConnectTimeout
        # subclasses BOTH Timeout and ConnectionError, so with the connection
        # handler first a host that never answered the TCP handshake was
        # reported as "connection blocked/reset" and the timeout branch below
        # only ever saw read timeouts. A diagnostics pane that calls a timeout a
        # block sends the user after a firewall that isn't there.
        except requests.exceptions.Timeout:
            return DiagResult(label, host, False, f"timed out after {timeout:.0f}s")
        except requests.exceptions.ConnectionError as e:
            low = str(e).lower()
            # SwapProvider._is_dns_error inspects the exception chain rather
            # than the message, so this stays correct on a non-English system
            # where glibc translates "Name or service not known".
            if SwapProvider._is_dns_error(e):
                reason = "DNS resolution failed (this host specifically)"
            elif "connection refused" in low:
                reason = "connection refused"
            else:
                reason = "connection blocked/reset"
            return DiagResult(label, host, False, reason)
        except requests.RequestException as e:
            return DiagResult(label, host, False, str(e))

    if not deduped:
        return []
    # Each ping was a separate up-to-`timeout` HTTP call done one at a time,
    # so a run with several unreachable hosts (each eating the full timeout)
    # made "Run Diagnostics" take minutes instead of seconds. The shared
    # `session` is only read here (headers/proxies), never mutated, and
    # urllib3's underlying connection pool is safe for concurrent use, so
    # pinging every host at once is safe. pool.map preserves input order,
    # which summarize_diagnostics() depends on (results[0] must stay the
    # control site).
    with ThreadPoolExecutor(max_workers=len(deduped)) as pool:
        return list(pool.map(ping_one, deduped))


def summarize_diagnostics(results: list) -> str:
    """One-line takeaway to put above the detailed list."""
    if not results:
        return "No results."
    control = results[0]
    failed = [r for r in results[1:] if not r.ok]
    if not failed:
        return "Everything reachable. If a provider still fails in-app it may be transient; try again."
    if not control.ok:
        return ("No general internet connectivity detected, or something is blocking "
                 "this app's outbound connections entirely (firewall/antivirus), "
                 "even the control site failed.")
    if all("DNS" in r.detail for r in failed):
        return ("Internet works, but DNS fails specifically for: "
                 + ", ".join(r.host for r in failed) +
                 ". Likely your ISP/router is blocking those specific domains, "
                 "try a different DNS provider or network.")
    return ("Internet works, but these hosts are blocked: "
            + ", ".join(f"{r.host} ({r.detail})" for r in failed) +
            ". Check antivirus/firewall rules for this app.")


# Coin discovery: ask every provider for its own coin list and merge
#
# The GUI starts with the small curated COINS set (safe, hand-verified
# network mappings) and calls this to expand the dropdown to everything the
# configured providers actually support. Each provider keeps its OWN
# ticker->network/code table (populated by that provider's fetch_coins()),
# so a coin can be offered even if only one provider out of several lists
# it: the per-provider quote/swap code already reports "not supported here"
# for a coin its own table doesn't have, exactly as it does today for the
# curated set.
@dataclass
class CoinRefreshResult:
    provider: str
    ok: bool
    error: str | None = None
    skipped_ambiguous: int = 0   # multi-network coins deliberately left out (see fetch_coins docs)


def refresh_all_coins(providers: list) -> tuple[dict[str, str], list[CoinRefreshResult]]:
    """Best-effort per provider: call fetch_coins() on every provider that
    has one, ignoring failures (offline, missing API key, rate-limited,
    that provider just keeps whatever coin list it already had, curated or
    from a previous successful refresh). Returns (merged {ticker: name} for
    the GUI's coin dropdowns, per-provider results for a status message)."""
    merged: dict[str, str] = {t: v[0] for t, v in COINS.items()}

    def fetch_one(p) -> tuple[CoinRefreshResult, dict[str, str]]:
        try:
            names = p.fetch_coins()
            skipped = getattr(p, "skipped_ambiguous_coins", 0)
            return (CoinRefreshResult(p.name, True, skipped_ambiguous=skipped),
                    names)
        except (ProviderError, requests.RequestException) as e:
            existing = getattr(p, "names", None) or {}
            return CoinRefreshResult(p.name, False, str(e)), existing
        except Exception as e:  # noqa: BLE001 - defensive. A bad response
            # shape shouldn't crash the whole coin refresh; the narrower
            # ProviderError/RequestException case above already covers the
            # expected network/API failures.
            return CoinRefreshResult(p.name, False, f"unexpected error: {e}"), {}

    # Each provider is queried over its own connection, so doing this one
    # at a time made a full coin refresh as slow as the SUM of every
    # provider's response time. Running them concurrently instead bounds it
    # to the slowest single provider; each call only touches its own local
    # variables, and the merge into `merged` happens back on this thread
    # after every future returns, so there's nothing shared to race on.
    queryable = [p for p in providers if getattr(p, "fetch_coins", None) is not None]
    results: list[CoinRefreshResult] = []
    if queryable:
        with ThreadPoolExecutor(max_workers=len(queryable)) as pool:
            for result, names in pool.map(fetch_one, queryable):
                results.append(result)
                # registry_only(): a provider reports its whole catalogue,
                # and merging that verbatim put every asset it lists into
                # the From/To pickers. The refresh exists to find out which
                # of OUR coins each provider can route, not to widen the
                # supported set, so the registry is the ceiling.
                for t, n in registry_only(names).items():
                    merged.setdefault(t, n)
    return merged, results


def _sanitize_header_value(v: str) -> str:
    """Defense in depth: reject credentials/config values containing
    control characters that could be abused for header/URL injection.
    requests/urllib3 already reject these, but fail loudly here too."""
    if not v:
        return v
    if any(c in v for c in '\r\n\0'):
        raise ProviderError("Refusing to use credential containing control characters")
    return v.strip()


def build_providers(cfg: dict) -> list[SwapProvider]:
    """Instantiate providers from a config dict."""
    _dns.configure_doh_resolvers(cfg)
    ss = cfg.get("sideshift", {})
    cn = cfg.get("changenow", {})
    cf = cfg.get("chainflip", {})
    sx = cfg.get("stealthex", {})
    tr = cfg.get("trocador", {})
    dx = cfg.get("dex", {})
    ff = cfg.get("fixedfloat", {})
    priv = cfg.get("privacy", {})
    providers = [
        Trocador(api_key=_sanitize_header_value(tr.get("api_key", ""))),
        SideShift(secret=_sanitize_header_value(ss.get("secret", "")),
                  affiliate_id=_sanitize_header_value(ss.get("affiliate_id", ""))),
        FixedFloat(api_key=_sanitize_header_value(ff.get("api_key", "")),
                   api_secret=_sanitize_header_value(ff.get("api_secret", ""))),
        ZeroExDEX(api_key=_sanitize_header_value(dx.get("api_key", ""))),
        ChangeNow(api_key=_sanitize_header_value(cn.get("api_key", ""))),
        # Protocol-level, no credentials: these take no config at all, so
        # they're always constructed. Whether they're actually queried is
        # decided by the enabled_providers toggle like every other provider.
        THORChain(),
        MayaProtocol(),
        Chainflip(api_key=_sanitize_header_value(cf.get("api_key", ""))),
        StealthEX(api_key=_sanitize_header_value(sx.get("api_key", ""))),
    ]
    proxy_enabled, proxy_url = resolve_proxy(priv)
    for p in providers:
        err = p.configure_proxy(proxy_enabled, proxy_url)
        if err:
            # Don't silently fall back to a leaky direct connection when the
            # user explicitly asked for proxy routing, surface it instead.
            raise ProviderError(err)
    return providers


def proxy_mode(priv: dict) -> str:
    """Read the proxy mode, tolerating a pre-proxy_mode config.

    load_config() migrates the old boolean, but build_providers is also
    called with hand-built dicts (the CLI, the direct-connection fallback),
    so the old key is honoured here too rather than silently reading as
    "auto".
    """
    mode = str(priv.get("proxy_mode") or "").strip().lower()
    if mode in ("off", "auto", "on"):
        return mode
    if "proxy_enabled" in priv:
        return "on" if priv.get("proxy_enabled") else "off"
    return "auto"


def resolve_proxy(priv: dict) -> tuple[bool, str]:
    """Decide whether this run is proxied, and through what.

    Returns (enabled, url). In "auto" a missing Tor is not an error: the
    caller gets (False, "") and runs direct, which is the whole point of the
    mode. In "on" the configured URL is returned unconditionally so that
    configure_proxy raises when it can't be used, keeping the fail-closed
    behaviour that mode promises.
    """
    mode = proxy_mode(priv)
    if mode == "off":
        return False, ""
    if mode == "on":
        return True, priv.get("proxy_url", "")
    # auto: only claim a proxy when one is genuinely usable. A detected port
    # with no PySocks is not usable, and reporting it as proxied would be the
    # silent-clearnet failure this mode exists to avoid.
    if not _torprobe.socks_available():
        return False, ""
    port = _torprobe.find_socks_port()
    if port is None:
        return False, ""
    return True, _torprobe.proxy_url_for(port)
