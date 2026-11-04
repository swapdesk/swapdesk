"""providers.base: shared data types (Quote, Swap), error classes, and the
SwapProvider base class every provider integration subclasses.
"""
from __future__ import annotations

import random
import threading
import socket
import time
from contextlib import suppress
from dataclasses import dataclass, field
from decimal import Decimal

import requests

from . import dns_hardening as _dns
from .constants import STATUS_WAITING


@dataclass
class Quote:
    """An indicative estimate for a pair + amount from one provider."""
    provider: str
    from_coin: str
    to_coin: str
    send_amount: Decimal
    estimated_receive: Decimal | None
    rate: Decimal | None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    eta_minutes: int | None = None
    via: str | None = None          # aggregator's chosen underlying exchange
    error: str | None = None
    # True when `error` means "this provider simply doesn't route this coin
    # pair" (unknown coin, no pool, wrong chain, etc.) rather than a
    # transient/network/config problem. Lets the UI tell "nobody offers
    # this pair, try a different one" apart from "something's actually
    # broken right now": those need very different messaging.
    unsupported: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.estimated_receive is not None


@dataclass
class Swap:
    """A created swap order. Contains the deposit address to fund."""
    provider: str
    order_id: str
    from_coin: str
    to_coin: str
    deposit_address: str
    deposit_memo: str | None
    send_amount: Decimal | None
    deposit_min: Decimal | None
    deposit_max: Decimal | None
    estimated_receive: Decimal | None
    settle_address: str
    expires_at: str | None
    status: str = STATUS_WAITING
    raw: dict = field(default_factory=dict)
    # The destination address as INDEPENDENTLY read back out of the
    # provider's own create-order response (never the local settle_address
    # variable we sent in), e.g. SideShift's "settleAddress", ChangeNOW's
    # "payoutAddress", FixedFloat's to.address. This is the only field that
    # can actually catch "provider recorded a different destination than
    # what we asked for" (bug, MITM, mutated request). None = this
    # provider's response has no independently-readable echo, so the
    # check can't run. Callers treat None as unverified, not verified ok.
    settle_address_confirmed_by_provider: str | None = None
    # True when a refund address the caller supplied was actually transmitted.
    # None means "not applicable / not reported". THORChain and Maya set this
    # to False when the memo budget forced the refund target to be dropped,
    # so the deposit window can say so instead of leaving the user believing
    # a field they filled in is in effect.
    refund_address_sent: bool | None = None

    def __post_init__(self):
        # An empty or whitespace-only echo is an absent one. Providers read
        # this field with .get() (Trocador through a chain of four fallback
        # names), so a response carrying "settleAddress": "" arrives here as
        # "" rather than None. That is not falsy-but-harmless: the compare in
        # verify_provider_confirmed_destination only special-cases None, so
        # an "" would reach the address compare, fail it, and raise a SAFETY
        # ABORT claiming the destination had been altered. Collapsing it to
        # None here routes it to the honest "unverified" path instead, once
        # for every provider rather than in each one.
        echoed = self.settle_address_confirmed_by_provider
        if echoed is not None and not str(echoed).strip():
            self.settle_address_confirmed_by_provider = None


class ProviderError(Exception):
    pass


class ProviderNetworkError(ProviderError):
    """Raised specifically for connection-layer failures (DNS/timeout/refused/
    TLS) as opposed to the provider's API returning an application-level
    error. Lets callers retry against a fallback host only for the former.

    status_code / retry_after are only set when this was raised from a real
    HTTP response (gateway-class 408/425/429/500/502/503/504), never for a
    pure connection failure (DNS/timeout/refused have no status code). The
    retry loop in SwapProvider._request uses status_code to decide whether
    to retry at all, and retry_after (seconds, from a 429's Retry-After
    header when present) to decide how long to wait instead of guessing."""
    def __init__(self, message, status_code: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


# Shared "which network is native for this ticker" rule.
#
# ChangeNOW, Trocador and SideShift each publish a live coin catalogue where
# a ticker can be listed on several networks (USDT-erc20/USDT-trc20/...).
# This app has no per-coin network selector in the UI, so guessing wrong
# here means a deposit/settle address on a chain the user never chose and
# can't see, a wrong-network transfer is typically unrecoverable.
#
# The rule used to be: if a ticker only has one network, trust it (nothing
# to guess between); if it has several, only accept the one whose network
# id/name matches the ticker itself. That "only one row -> trust it"
# shortcut turned out to be unsafe: live-checked against ChangeNOW's real
# /currencies today, FIRO is currently listed with exactly one row and it is
# the BSC-bridged token (network "bsc"), not native Firo -- the shortcut
# would silently route a "FIRO" swap onto Binance Smart Chain. And on
# SideShift, BTC and ETH now each list several networks whose ids are full
# chain names ("bitcoin", "ethereum") that never string-match the ticker, so
# the multi-network branch dropped them both as "ambiguous" even though each
# has one unambiguous native entry.
#
# `expected` closes both gaps: callers that already know a ticker's native
# network id (constants.COINS' per-provider network field, or a live signal
# like SideShift's own "mainnet" field on /coins) pass it in, and it is
# checked FIRST, by exact (case-insensitive) match against the live rows.
# If `expected` is given and isn't among the live rows, the ticker is
# treated as unavailable rather than falling back to a guess -- this is
# what stops the FIRO case: "firo" is expected, only "bsc" is offered, so
# nothing is picked instead of trusting the lone bsc row. Callers with no
# curated expectation (a ticker outside COINS) fall back to the old
# ticker-string-match heuristic, unchanged.
def pick_native_network(networks: list, ticker: str,
                        expected: str | None = None) -> str | None:
    """Pick the native network out of a ticker's list of network ids/names.

    `expected` (optional): the network id already known to be this ticker's
    native chain. Checked first; if given and present among `networks`
    (case-insensitive), it wins outright, no further guessing. If given and
    NOT present, returns None (unavailable) rather than falling through to
    the heuristic below, since falling through would mean giving up on a
    known-correct answer in favour of a guess.

    Falls back to the ticker-match heuristic when `expected` is None:
    returns None ("ambiguous, skip this ticker") if there's more than one
    network and none matches the ticker exactly (case-insensitive); trusts
    the sole entry if there's exactly one.
    """
    if not networks:
        return None
    # .strip() defensively even though every current caller's network_of/
    # nets already strips: this is the one place that HAS to be right for
    # every caller, present and future, and a stray-whitespace network id
    # can only ever cause a false "unavailable" here (never a match against
    # the WRONG entry, since the compare is exact post-normalization), so
    # stripping centrally costs nothing and closes that gap for good.
    if expected:
        lowered = {str(n).strip().lower(): str(n).strip() for n in networks}
        return lowered.get(str(expected).strip().lower())
    if len(networks) == 1:
        return str(networks[0]).strip()
    return next((str(n).strip() for n in networks
                if str(n).strip().lower() == ticker.strip().lower()), None)


def group_native_rows(rows: list[dict], *, ticker_of, network_of,
                       expected: dict[str, str] | None = None
                       ) -> tuple[dict[str, dict], int]:
    """For catalogues that list one row per (ticker, network) pair
    (ChangeNOW's /currencies, Trocador's /coins): group rows by ticker,
    then keep only the native row per pick_native_network's rule.

    ticker_of / network_of are callables extracting the ticker/network
    strings from a row (each provider names these fields differently).
    Rows missing a ticker or network are dropped.

    `expected` (optional): {ticker: known-native-network-id}, e.g. built
    from constants.COINS or a provider's own curated table. See
    pick_native_network's docstring for why this matters -- without it, a
    ticker whose only currently-listed row is a bridged/wrapped variant (or
    whose native id is a full chain name rather than the ticker itself) is
    handled unsafely or dropped unnecessarily.

    Returns (row_by_ticker, skipped_ambiguous_count).
    """
    by_ticker: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = ticker_of(row)
        network = network_of(row)
        if not ticker or not network:
            continue
        by_ticker.setdefault(ticker, []).append(row)

    result: dict[str, dict] = {}
    skipped = 0
    for ticker, trows in by_ticker.items():
        chosen_net = pick_native_network(
            [network_of(r) for r in trows], ticker,
            expected=(expected or {}).get(ticker))
        if chosen_net is None:
            skipped += 1
            continue
        result[ticker] = next(r for r in trows if network_of(r) == chosen_net)
    return result, skipped


_PROXY_SCHEMES = frozenset({"socks5", "socks5h", "http", "https"})


def proxy_url_problem(proxy_url) -> str | None:
    """Why proxy_url can't be used as the required proxy, or None if it can."""
    from urllib.parse import urlsplit
    url = (proxy_url or "").strip() if isinstance(proxy_url, str) else ""
    if not url:
        return ("Proxy routing is set to ON but no proxy URL is configured. "
                "Enter one in Settings > Privacy (e.g. socks5h://127.0.0.1:9050) "
                "or turn the proxy off.")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018  (raises ValueError on a malformed port)
    except ValueError:
        parts = None
    if (parts is None or parts.scheme.lower() not in _PROXY_SCHEMES
            or not parts.hostname
            or any(ch.isspace() or not ch.isprintable() for ch in url)):
        return ("Proxy routing is set to ON but the proxy URL is not usable. "
                "It must look like socks5h://HOST:PORT (socks5://, http:// "
                "and https:// are also accepted).")
    return None


# Base
class StatusNotes:
    """Per-order text to show under a status, when the status alone is too
    general to say what the provider means. Written by the polling thread and
    read by the GUI thread, so access is locked."""

    def __init__(self):
        self._notes: dict[str, str] = {}
        self._lock = threading.Lock()

    def set(self, order_id, text: str | None) -> None:
        key = str(order_id)
        with self._lock:
            if text:
                self._notes[key] = text
            else:
                self._notes.pop(key, None)

    def get(self, order_id) -> str | None:
        with self._lock:
            return self._notes.get(str(order_id))


class SwapProvider:
    name = "base"
    site = ""
    # True when get_quote() can return a real, useful rate with zero
    # credentials configured (SideShift's /pair and Trocador's /new_rate are
    # public endpoints). False (the
    # safe default) means get_quote() will always just hand back a "needs an
    # API key" Quote error without configured() credentials, those
    # providers are skipped from the comparison entirely rather than
    # queried-just-to-fail, so an unconfigured provider doesn't show up as a
    # noisy error card next to real quotes.
    can_quote_without_config = False

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        # Per-order text for statuses whose shared wording is too general to
        # say what this provider means by them. Empty unless the provider
        # fills it in during get_status().
        self.notes = StatusNotes()
        self.session = requests.Session()
        # Environment proxies are NOT honoured. requests merges HTTPS_PROXY /
        # ALL_PROXY in at send time without ever touching session.proxies, and
        # session.proxies is what the whole app reads to answer "am I
        # proxied?" (the header route pill, _proxy_in_use(), the DoH gate in
        # _request, and the proxy attribution in _describe_network_error).
        # Leaving trust_env at its default meant an env proxy silently carried
        # every provider call while the UI said "Direct", and the DoH lookup --
        # which runs on a trust_env=False session -- went out over the real IP
        # carrying the hostname of every provider about to be contacted. The
        # proxy the app uses is the one configured in Settings, and nothing
        # else; providers.dns_hardening._doh_session() sets this for the same
        # reason.
        self.session.trust_env = False
        # A generic, widely-shared UA string rather than anything that
        # identifies this app/version, so providers can't tell our traffic
        # apart from any other browser's (no "SwapDesk/1.0" tag broadcast
        # on every call).
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
        })

    def _map_status(self, raw_status, default: str, upper: bool = False) -> str:
        """Look up a provider's own status spelling in this provider's
        class-level `_MAP` and fall back to `default` for anything not in
        it (retired/new/unrecognized spelling), rather than raising.
        SideShift/ChangeNow/Trocador use lowercase status strings;
        FixedFloat uses uppercase, hence `upper`. Pulled out because all
        four providers had this exact lookup duplicated twice each (once
        in create_swap's initial status, once in get_status's poll)."""
        key = str(raw_status or "")
        key = key.upper() if upper else key.lower()
        return self._MAP.get(key, default)

    def configure_proxy(self, enabled: bool, proxy_url: str) -> str | None:
        """Route this provider's requests through a SOCKS5/Tor proxy.
        Returns an error string if proxy support isn't available, else None."""
        if not enabled:
            self.session.proxies = {}
            return None
        # Required but unusable is an error, never a silent direct connection.
        problem = proxy_url_problem(proxy_url)
        if problem:
            self.session.proxies = {}
            return problem
        try:
            import socks  # noqa: F401  (provided by PySocks / requests[socks])
        except ImportError:
            return ("SOCKS proxy requested but PySocks isn't installed. "
                    "Run: pip install \"requests[socks]\"")
        self.session.proxies = {"http": proxy_url, "https": proxy_url}
        return None

    def _checked_deposit_address(self, value) -> str:
        """Validate the deposit address out of a create-swap response and
        return it stripped, or raise ProviderError.

        Every provider needs this identical guard and each used to spell it
        `if not addr`, which only rejects the falsy cases: None, "" and 0. A
        response carrying " ", "\\n" or "\\x00" is truthy, so four of the eight
        providers passed one straight into a Swap and the UI would have
        presented it as the address to send real funds to. Anything not a
        non-empty string once stripped is a broken response, not an address.

        Living on the base class rather than in each integration is the point:
        eight copies of one guard is how four of them ended up different.
        """
        if not isinstance(value, str):
            raise ProviderError(
                f"{self.name}: response gave a deposit address of type "
                f"{type(value).__name__}, not a string. Do not send funds. "
                f"Try again.")
        addr = value.strip()
        if not addr:
            raise ProviderError(
                f"{self.name}: response gave a blank deposit address. Do not "
                f"send funds. Try again.")
        # Control characters can't occur in any address format this app
        # supports, and would corrupt the value on display or on the clipboard.
        if any(ch in addr for ch in "\r\n\t\0"):
            raise ProviderError(
                f"{self.name}: response gave a deposit address containing "
                f"control characters. Do not send funds. Try again.")
        return addr

    def _checked_memo(self, value):
        """Validate a deposit memo/tag out of a create-swap response.

        Returns None for "no memo", or the stripped string. Raises
        ProviderError for a value that is present but unusable.

        The memo carries exactly the same stake as the deposit address on the
        routes that use one -- a deposit whose memo is wrong or mangled is as
        unrecoverable as one sent to the wrong address -- and it had none of
        the guards _checked_deposit_address applies. A memo containing a
        newline breaks the display and can be silently truncated by whatever
        the user pastes into; a non-string reaches a Tk label as a repr; an
        empty-after-strip value would render a "MEMO REQUIRED" row containing
        nothing, which is worse than showing no row at all.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProviderError(
                f"{self.name}: response gave a deposit memo of type "
                f"{type(value).__name__}, not a string. Do not send funds. "
                f"Try again.")
        memo = value.strip()
        if not memo:
            return None
        if any(ch in memo for ch in "\r\n\t\0"):
            raise ProviderError(
                f"{self.name}: response gave a deposit memo containing "
                f"control characters. Do not send funds. Try again.")
        return memo

    def _checked_order_id(self, value) -> str:
        """The order id out of a create-swap response, as a string, or raise.

        The id is polled, shown and written to history, so an object, a list,
        a boolean or a blank value is a broken response, not an order handle.
        """
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ProviderError(
                f"{self.name}: response gave an order id of type "
                f"{type(value).__name__}. Do not send funds. Try again.")
        order_id = str(value).strip()
        if not order_id or len(order_id) > 256 or not order_id.isprintable():
            raise ProviderError(
                f"{self.name}: response gave an unusable order id. Do not "
                f"send funds. Try again.")
        return order_id

    def configured(self) -> bool:
        """True if credentials required to CREATE a swap are present."""
        raise NotImplementedError

    def get_quote(self, from_coin: str, to_coin: str, amount: Decimal,
                 destination: str = "") -> Quote:
        # destination is optional and ignored by most providers (their
        # quotes don't depend on it). It's accepted here so a provider
        # whose quote does depend on the destination chain can use it
        # when the UI already has one at quote time.
        raise NotImplementedError

    def create_swap(self, from_coin: str, to_coin: str, amount: Decimal,
                    settle_address: str, refund_address: str = "") -> Swap:
        raise NotImplementedError

    def get_status(self, order_id: str) -> str:
        raise NotImplementedError

    def status_detail(self, order_id: str) -> str | None:
        """Extra facts from the last get_status() worth showing under the
        status, or None when the status says it all."""
        return self.notes.get(order_id)

    # helpers
    def _get(self, url, **kw):
        return self._request("get", url, **kw)

    def _post(self, url, **kw):
        return self._request("post", url, **kw)

    # Rate-limit/backoff retry policy, applies to every provider uniformly
    # since it lives in the shared base class rather than being reimplemented
    # (or forgotten) per provider. Only retries responses classified as
    # _GATEWAY_STATUS (408/425/429/500/502/503/504): a transient host/gateway
    # problem, not a request the API rejected on its merits (400/401/403/404
    # fail immediately, retrying those would just burn time to the same
    # error). Connection-level failures (DNS/timeout/refused) are handled by
    # the DNS-fallback branch below and are NOT retried here beyond that,
    # since a dead host won't come back in a few seconds the way a rate
    # limit does.
    MAX_RETRIES = 3
    _BACKOFF_BASE = 0.75      # seconds; doubles each attempt
    _BACKOFF_MAX = 8.0        # cap so a chain of retries can't stall the UI

    def _request(self, method: str, url, retry_on=None, **kw):
        # retry_on narrows which gateway statuses this one request retries
        # on. A request that creates an order passes only the statuses that
        # mean the server did not act on it: after a 500, 502 or 504 the
        # order may already exist, and retrying would open a second one.
        retry_statuses = self._GATEWAY_STATUS if retry_on is None else retry_on
        # Never follow redirects on an API call. requests strips Authorization
        # across a host change but nothing else, so X-API-KEY, API-Key,
        # x-changenow-api-key, x-sideshift-secret and X-API-SIGN would all
        # survive a 302 to an attacker-chosen host. A redirect target also
        # escapes the DoH pin below, which only ever pins the hostname in the
        # URL we were given. None of these APIs redirect in normal operation,
        # so a redirect is a signal, not a step to follow.
        kw.setdefault("allow_redirects", False)
        call = getattr(self.session, method)
        attempt = 0
        while True:
            host = None
            doh_errors: list[str] = []
            try:
                if _dns._DOH_ENABLED and not self.session.proxies:
                    # Secure-DNS-first: resolve via DoH (rebinding-checked,
                    # RFC 8467 padded) BEFORE ever touching local/network
                    # DNS, not just as a reactive fallback after local DNS
                    # fails. _dns._doh_resolve() caches per hostname, so this is
                    # one extra round trip the first time a given host is
                    # hit in this process, not on every request.
                    from urllib.parse import urlparse
                    host = urlparse(url).hostname
                    ip, doh_errors = _dns._doh_resolve(host) if host else (None, [])
                    if ip:
                        with _dns._resolve_via(host, ip):
                            r = call(url, timeout=self.timeout, **kw)
                    elif _dns._DOH_SECURE_MODE:
                        # Zero-trust: never silently drop to local/network
                        # DNS. Every configured DoH resolver failed for this
                        # host, so stop here and say exactly why, rather
                        # than completing the request over an unverified
                        # resolver the user explicitly asked not to trust.
                        raise ProviderNetworkError(
                            self._describe_secure_dns_failure(host, doh_errors))
                    else:
                        # Not in secure mode: fall back to local/network DNS,
                        # but the user is told this specific host dropped
                        # out of DoH-verified resolution (once per host, not
                        # once per request) rather than it happening quietly.
                        _dns._notify_dns_fallback(host, doh_errors)
                        r = call(url, timeout=self.timeout, **kw)
                else:
                    r = call(url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                if self._is_dns_error(e) and not self.session.proxies:
                    if _dns._DOH_SECURE_MODE:
                        raise ProviderNetworkError(
                            self._describe_secure_dns_failure(host, doh_errors))
                    raise ProviderNetworkError(self._describe_dns_dead_end(host, doh_errors))
                raise ProviderNetworkError(self._describe_network_error(e, url))
            try:
                return self._json(r)
            except ProviderNetworkError as e:
                if (e.status_code in retry_statuses
                        and attempt < self.MAX_RETRIES):
                    attempt += 1
                    delay = min(e.retry_after, self._BACKOFF_MAX) if e.retry_after is not None else \
                        min(self._BACKOFF_BASE * (2 ** (attempt - 1)), self._BACKOFF_MAX)
                    delay += random.uniform(0, 0.25)
                    time.sleep(delay)
                    continue
                raise

    def _describe_secure_dns_failure(self, host: str, doh_errors: list) -> str:
        resolver_names = ", ".join(_dns._doh_label_for_url(u) for u in _dns._DOH_RESOLVERS)
        detail = "; ".join(doh_errors) if doh_errors else \
            "no configured resolver could be reached"
        return (f"{self.name}: Secure DNS mode is ON, so this request will "
                f"NOT fall back to your network's own DNS resolver. Every "
                f"configured DNS-over-HTTPS resolver ({resolver_names}) "
                f"failed to resolve {host}: {detail}. Turn off Secure DNS "
                f"mode in Settings > DNS to allow a fallback to your "
                f"network's resolver, or check whether outbound HTTPS "
                f"(port 443) to these resolvers is being blocked by a "
                f"firewall, VPN, or antivirus.")

    def _describe_dns_dead_end(self, host: str, doh_errors: list) -> str:
        if not _dns._DOH_ENABLED:
            return (f"{self.name}: couldn't resolve {host}, and the DNS-over-"
                    f"HTTPS fallback is turned off in Settings > DNS.")
        if not doh_errors:
            # DoH wasn't even attempted (e.g. hostname couldn't be parsed).
            return (f"{self.name}: couldn't resolve {host}, and the DNS-over-"
                    f"HTTPS fallback couldn't run either.")
        detail = "; ".join(doh_errors)
        resolver_names = ", ".join(_dns._doh_label_for_url(u) for u in _dns._DOH_RESOLVERS)
        # Distinguish "we reached the DoH resolver and it told us the name has
        # no record" from "we couldn't reach the DoH resolver at all". The
        # first means outbound HTTPS is FINE and the hostname genuinely does
        # not exist (dead/retired host), telling the user to go check their
        # antivirus in that case sends them chasing a problem they don't have.
        if any("answered but had no" in e for e in doh_errors):
            single = len(_dns._DOH_RESOLVERS) == 1
            return (f"{self.name}: {host} does not resolve. {resolver_names} "
                    f"{'was' if single else 'were'} reached successfully over "
                    f"HTTPS and {'reports' if single else 'report'} this "
                    f"hostname has no A/AAAA record. "
                    f"Your internet and DNS are working; this node's hostname "
                    f"is simply dead or retired, so there is nothing to fix "
                    f"locally. ({detail})")
        return (f"{self.name}: couldn't resolve {host}: local DNS failed, and "
                f"the DNS-over-HTTPS fallback ({resolver_names}, by IP) also "
                f"failed: {detail}. That points to something blocking outbound "
                f"HTTPS itself on this machine/network. Check antivirus, "
                f"Windows Firewall, or a router-level block, rather than DNS "
                f"settings.")

    @staticmethod
    def _is_dns_error(e: requests.RequestException) -> bool:
        """True if `e` is a name-resolution failure rather than some other
        connection error.

        Decided from the exception chain first, and only then from the message
        text. Two of the three strings this used to match on are not stable:
        "Name or service not known" is glibc's gai_strerror output and is
        TRANSLATED under a non-English locale, and NameResolutionError is a
        urllib3 implementation detail with no compatibility guarantee. A
        German or French user with failing DNS was therefore told to check
        their internet connection, and in Secure DNS mode lost the message
        naming which resolvers failed and why.

        socket.gaierror carries an errno (EAI_NONAME, EAI_AGAIN) that no
        locale touches, so finding it in the chain is the reliable test. The
        string match is kept as a fallback for the case where the underlying
        exception has been discarded before it reaches us.
        """
        if not isinstance(e, requests.exceptions.ConnectionError):
            return False
        seen: set[int] = set()
        cur: BaseException | None = e
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, socket.gaierror):
                return True
            # urllib3 raises its own NameResolutionError, which is not a
            # gaierror subclass but is unambiguous by type name.
            if type(cur).__name__ == "NameResolutionError":
                return True
            cur = cur.__cause__ or cur.__context__
        low = str(e).lower()
        return ("getaddrinfo failed" in low or "nameresolutionerror" in low
                or "name or service not known" in low)

    @staticmethod
    def _safe_url(url) -> str:
        """A URL with its query string dropped, for use in error text.

        StealthEX authenticates with an `api_key` query parameter and
        Chainflip with `apiKey`, so the raw URL of a failing request carries a
        live credential. Those strings reach the status line, the diagnostics
        pane and -- through the crash handler -- crash.log, which users are
        asked to attach to bug reports.
        """
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(str(url))
        except ValueError:
            return "(unparseable URL)"
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        return base + ("?<redacted>" if parts.query else "")

    def _describe_network_error(self, e, url):
        """Turn raw urllib3/requests connection failures (which read like a
        stack trace: 'Max retries exceeded', 'NameResolutionError',
        'getaddrinfo failed') into a short, actionable message. This is a
        local networking problem (no internet, DNS being blocked, a
        firewall/AV/VPN, or (if a proxy is configured) Tor not running),
        not something wrong with this provider's account or the swap itself.
        """
        from urllib.parse import urlparse
        host = urlparse(url).hostname or url
        low = str(e).lower()
        via_proxy = bool(self.session.proxies)

        # Order matters, because requests' exception hierarchy overlaps:
        # SSLError and ConnectTimeout are BOTH subclasses of ConnectionError,
        # and ConnectTimeout is also a Timeout. Testing ConnectionError first
        # therefore swallowed both of the specific cases below and answered
        # with the generic "couldn't connect - check your internet connection",
        # so a corporate TLS interception looked like no internet and a
        # connect timeout never mentioned the timeout. Most specific first.
        if isinstance(e, requests.exceptions.SSLError):
            return (f"{self.name}: TLS/certificate error connecting to {host}. "
                    f"If you're behind a proxy or corporate network that "
                    f"intercepts HTTPS, that's the likely cause.")
        if isinstance(e, requests.exceptions.Timeout):
            return (f"{self.name}: {host} took too long to respond (timed out "
                    f"after {self.timeout}s). It may be slow or unreachable "
                    f"right now - try again in a moment.")
        if isinstance(e, requests.exceptions.ConnectionError):
            # Same question, same answer: call the classifier rather than
            # restating its rules, which is how the two drifted apart.
            if self._is_dns_error(e):
                if via_proxy:
                    return (f"{self.name}: couldn't resolve {host} through your "
                            f"configured proxy. Check that Tor/your SOCKS proxy is "
                            f"actually running and reachable.")
                return (f"{self.name}: couldn't resolve {host}, even after "
                        f"falling back to DNS-over-HTTPS. Either you have no "
                        f"internet connectivity right now, or something is "
                        f"actively blocking outbound HTTPS entirely (a "
                        f"firewall/antivirus, or your router) - a plain DNS "
                        f"fix on this machine won't help since we already "
                        f"tried bypassing local DNS.")
            if "connection refused" in low:
                if via_proxy:
                    return (f"{self.name}: connection refused by your configured "
                            f"proxy - is Tor/your SOCKS proxy actually running on "
                            f"that address/port?")
                return (f"{self.name}: connection to {host} was refused. It may "
                        f"be temporarily down, or something local is blocking it.")
            return (f"{self.name}: couldn't connect to {host} - check your "
                    f"internet connection{' and proxy settings' if via_proxy else ''}.")
        # Deliberately never interpolates str(e) here. The three branches
        # above hand-write their own safe text for exactly this reason; this
        # fallback used to do `str(e).replace(str(url), self._safe_url(url))`,
        # meant to redact a live query-string credential (StealthEX
        # `api_key`, Chainflip `apiKey`) out of the exception text before it
        # reaches the status line, diagnostics pane, or crash.log. That
        # never actually redacted anything: `url` here never carries the
        # query string (request params are merged in separately, by
        # requests/urllib3, only at send time), so `str(url)` can't match
        # the query-bearing string the exception embeds, and the raw,
        # unredacted exception text -- credential included, when present --
        # was returned as-is. Showing the exception's type plus the already
        # query-stripped `host`/`_safe_url(url)` keeps this branch
        # informative without ever passing raw exception internals through.
        return (f"{self.name}: network error reaching {host} "
                f"({type(e).__name__}) at {self._safe_url(url)}. Check your "
                f"connection and try again.")

    # JSON-body status codes that mean the *host/gateway* is transiently
    # unavailable (upstream down, overloaded, rate-limited) rather than the API
    # rejecting this specific request on its merits. For a multi-node provider
    # these are worth retrying against a different host; for a single-host one
    # they surface as a network-level failure. Deliberately excludes 400/401/
    # 403/404 with a JSON body, those mean "this request/credential is wrong"
    # and would fail identically no matter which host answered.
    _GATEWAY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

    # The subset of _GATEWAY_STATUS that means the request was NOT acted on
    # (rate-limited or briefly unavailable before doing anything), safe to
    # retry even for a call that creates a real order. 500/502/504 are
    # excluded: after one of those the order may already exist server-side,
    # and _request()'s default retry would then open a second one. Every
    # create_swap() that places a real order should pass this as retry_on=.
    # (chainflip.py's own SWAP_RETRY_STATUSES predates this shared constant
    # and encodes the identical set -- left as-is rather than touched here.)
    ORDER_CREATE_RETRY_STATUSES = frozenset({408, 429, 503})

    def _json(self, r: requests.Response):
        # _request never follows redirects, and a 3xx is not an answer from
        # the API: its body (if any) must not be read as a successful result,
        # least of all from an order-creating call.
        if 300 <= r.status_code < 400:
            raise ProviderError(
                f"{self.name}: HTTP {r.status_code}: the API answered with a "
                f"redirect, which SwapDesk does not follow. Nothing was "
                f"accepted from this response; try again later.")
        try:
            data = r.json()
        except ValueError:
            # Body wasn't JSON. A JSON API that suddenly answers with HTML/text
            # almost always means we never reached the real API: a CDN/WAF page
            # (e.g. Cloudflare's "Just a moment…" bot challenge), a captive
            # portal, or a bare gateway error. That's a host-level failure,
            # raise it as a *network* error so multi-node providers fall through
            # to another host, and describe it accurately instead of blaming an
            # API key.
            body = r.text or ""
            low = body.lower()
            server = (r.headers.get("server") or "").lower()
            is_challenge = (
                "just a moment" in low
                or "cf-browser-verification" in low
                or "attention required" in low
                or "cloudflare" in server
            )
            if is_challenge:
                detail = ("host is behind a Cloudflare bot-check that blocked "
                          "this request (not an API-key problem)")
            else:
                snippet = " ".join(body.split())[:140]
                detail = snippet or "non-JSON response"
            raise ProviderNetworkError(
                f"{self.name}: HTTP {r.status_code}: {detail}",
                status_code=r.status_code,
                retry_after=self._parse_retry_after(r))
        if r.status_code >= 400:
            msg = None
            if isinstance(data, dict):
                err = data.get("error")
                err_str = None
                if isinstance(err, dict):
                    err_str = err.get("message") or err.get("code")
                elif isinstance(err, str):
                    err_str = err
                # A top-level `message` (live-confirmed on ChangeNOW: e.g.
                # "Currency btcc is not supported") is the human-readable
                # explanation; `error` alone is often a short machine code
                # ("not_valid_params") that names the category, not the
                # problem. Prefer the readable one when both are present,
                # rather than letting a truthy `error` string win first and
                # never even look at `message`.
                msg = data.get("message") or err_str
            # Truncated for the same reason the non-JSON branch above is:
            # this string is provider-authored and lands in a Tk label. An
            # unbounded one wedges the GUI thread laying out a megabyte of
            # text in a card sized for a sentence.
            detail = str(msg or data)
            if len(detail) > 300:
                detail = detail[:300] + "..."
            text = f"{self.name}: HTTP {r.status_code}: {detail}"
            # A well-formed JSON error means we DID reach the API. Only a true
            # auth rejection warrants an API-key hint; 5xx/rate-limit are host-
            # side and retriable, everything else is a request the API refused.
            if r.status_code in (401, 403):
                text += "  (check your API key)"
            if r.status_code in self._GATEWAY_STATUS:
                raise ProviderNetworkError(text, status_code=r.status_code,
                                           retry_after=self._parse_retry_after(r))
            raise ProviderError(text)
        return data

    @staticmethod
    def _parse_retry_after(r: requests.Response) -> float | None:
        """Honor a 429/503's Retry-After header (seconds, or occasionally an
        HTTP-date) over our own backoff guess when the provider tells us
        exactly how long to wait. Returns None (caller falls back to
        exponential backoff) if the header is absent or unparseable."""
        raw = r.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        with suppress(Exception):
            import datetime
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw)
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            delta = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            return max(0.0, delta)
        return None


