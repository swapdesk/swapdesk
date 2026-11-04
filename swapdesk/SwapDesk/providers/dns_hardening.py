"""providers.dns_hardening: DNS-over-HTTPS resolution + rebinding protection.

Split out of the original monolithic providers.py.
Some ISPs / home routers fail to resolve specific hostnames (crypto-infra
domains like api.sideshift.ai are a common target) even though the
rest of the internet works fine and there's no VPN/proxy involved. When DoH
is enabled, SwapDesk resolves provider hostnames ITSELF via DoH against a
resolver reached BY IP (so no local DNS lookup is needed to reach the
resolver), checks the answer for rebinding, and pins the request to that IP.

This is secure-DNS-FIRST, not a reactive fallback: SwapProvider._request
does the DoH lookup BEFORE ever touching the OS/network resolver, so a
lying local resolver never gets the first word. What happens when DoH
fails depends on Secure DNS mode:

* Secure DNS mode OFF (default): the request falls back to the OS/network
  resolver, and _notify_dns_fallback() tells the user which hostname
  dropped out of DoH-verified resolution, once per host.
* Secure DNS mode ON: nothing falls back. The request stops with an error
  naming every resolver that failed and why.

Answers are only accepted when the response is genuinely a reply to the
question asked (QR bit, RCODE, matching question name) and the address
isn't private/loopback/reserved. See _decode_dns_answer and
_is_bogus_answer.

Speaks plain RFC 8484 wire-format DoH (Accept: application/dns-message)
rather than vendor JSON convenience APIs, since JSON support is
inconsistent across resolvers (Quad9 retired its JSON endpoint in 2025)
while every provider below supports RFC 8484.

Configurable from Settings > DNS: the user can turn this off entirely, or
choose which resolver(s) to try (and in what order). _DOH_ENABLED,
_DOH_SECURE_MODE and _DOH_RESOLVERS are all rebuilt by
configure_doh_resolvers() on every settings save, so a change takes effect
without a restart.

Note that a SOCKS proxy takes precedence over all of this: when one is
configured, _request skips DoH entirely because the proxy does the
resolving (remotely, with socks5h://).
"""
from __future__ import annotations

import base64
import ipaddress
import socket
import struct
import threading
import time
from contextlib import contextmanager, suppress

import requests

DOH_PROVIDERS = {
    # key -> (display label, resolver IP, RFC 8484 endpoint reached by that
    # IP directly, so resolving the resolver's own hostname isn't needed).
    #
    # AdGuard DNS (94.140.14.14) was removed: its shared multi-service
    # anycast address needs the real hostname (dns.adguard-dns.com) in
    # SNI/Host to route to the DoH vhost, unlike Cloudflare/Google's
    # dedicated DoH-only IPs. A bare-IP URL against it isn't reliable, and
    # fixing that properly means resolving-then-pinning like _resolve_via
    # already does for provider requests, not a one-line entry here.
    "cloudflare": {"label": "Cloudflare",        "ip": "1.1.1.1", "url": "https://1.1.1.1/dns-query"},
    "google":     {"label": "Google Public DNS", "ip": "8.8.8.8", "url": "https://8.8.8.8/dns-query"},
    "quad9":      {"label": "Quad9",             "ip": "9.9.9.9", "url": "https://9.9.9.9/dns-query"},
}
DOH_PROVIDER_ORDER = ["cloudflare", "google", "quad9"]
DEFAULT_DOH_PROVIDERS = ["cloudflare", "google"]

_DOH_ENABLED = True
_DOH_SECURE_MODE = False  # zero-trust: never fall back to local/network DNS
_DOH_RESOLVERS = [DOH_PROVIDERS[k]["url"] for k in DEFAULT_DOH_PROVIDERS]
_doh_cache: dict[str, tuple[str, float]] = {}      # host -> (ip, expires_at)
_doh_neg_cache: dict[str, tuple[list, float]] = {}  # host -> (errors, expires_at)
_doh_lock = threading.Lock()

# A successful answer is reused for this long. Bounded rather than kept for
# the process lifetime so a provider that migrates hosts is picked up
# without a restart. A failure is remembered for much less: it's usually a
# transient network problem, and re-querying every resolver on every single
# request (which is what no negative caching means in practice) costs the
# full resolver-count x A/AAAA x timeout budget each time.
_DOH_TTL = 300.0
_DOH_NEG_TTL = 30.0

# One lock per hostname so concurrent lookups of the SAME host collapse into
# a single query (the losers find the cache warm), while different hosts
# still resolve in parallel. Without this, quoting N providers at once fired
# N duplicate DoH queries for any host they shared.
_doh_host_locks: dict[str, threading.Lock] = {}
_doh_host_locks_guard = threading.Lock()


def _host_lock(hostname: str) -> threading.Lock:
    with _doh_host_locks_guard:
        lk = _doh_host_locks.get(hostname)
        if lk is None:
            lk = _doh_host_locks[hostname] = threading.Lock()
        return lk


def _doh_session() -> requests.Session:
    """Connection-pooled session for DoH queries only.

    Every resolver URL is an IP literal, so this never needs DNS itself and
    can't recurse back into this module. It deliberately ignores the app's
    SOCKS proxy setting for the same reason `_request` skips DoH entirely
    when a proxy is set: with a proxy in play the proxy does the resolving.
    Environment proxies are off for the same reason, and so a stray
    HTTPS_PROXY can't silently route DNS queries through a third party.
    A bare `requests.get` per query would pay a fresh TCP + TLS handshake to
    the resolver every lookup.
    """
    global _doh_sess
    with _doh_sess_lock:
        if _doh_sess is None:
            sess = requests.Session()
            sess.trust_env = False
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=len(DOH_PROVIDERS), pool_maxsize=8)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            _doh_sess = sess
        return _doh_sess


_doh_sess = None
_doh_sess_lock = threading.Lock()

# Fired (at most once per hostname per process) when secure DNS resolution
# fails for every configured DoH resolver and a request falls back to the
# OS/network's own DNS instead of a DoH-verified, rebinding-checked IP.
# Set by the GUI at startup via set_dns_fallback_notifier() so the user is
# told, rather than the fallback happening silently. Kept as a plain module
# callable (not a Tk reference) since this fires from background request
# threads, not the GUI thread.
_dns_fallback_notifier = None
_dns_fallback_notified: set[str] = set()
_dns_fallback_lock = threading.Lock()


def set_dns_fallback_notifier(fn) -> None:
    """Register `fn(hostname, doh_errors)` to be called the first time a
    given hostname falls back from DoH to local DNS in this process. `fn`
    may be invoked from any request thread (quote fetching runs on a
    ThreadPoolExecutor); it's responsible for hopping back to the GUI
    thread itself if it touches Tk widgets. Pass None to unregister."""
    global _dns_fallback_notifier
    _dns_fallback_notifier = fn


def _notify_dns_fallback(hostname: str, doh_errors: list[str]) -> None:
    with _dns_fallback_lock:
        if hostname in _dns_fallback_notified:
            return
        _dns_fallback_notified.add(hostname)
    if _dns_fallback_notifier is not None:
        with suppress(Exception):
            # A bug in the GUI's notifier callback must never take down a
            # swap-related network request.
            _dns_fallback_notifier(hostname, doh_errors)


def configure_doh_resolvers(cfg: dict) -> None:
    """Rebuild the DoH fallback toggle/resolver-list from Settings > DNS.
    Called by build_providers() on every save. Unknown/removed provider
    keys are ignored; an empty or all-unknown selection falls back to the
    Cloudflare+Google default rather than silently disabling the feature."""
    global _DOH_ENABLED, _DOH_SECURE_MODE, _DOH_RESOLVERS
    dns_cfg = cfg.get("dns", {})
    _DOH_SECURE_MODE = bool(dns_cfg.get("secure_mode", False))
    # Secure/zero-trust mode is meaningless without DoH itself switched on;
    # a user who enables secure_mode is enabling DoH by definition, even if
    # the plain "enabled" flag was independently left off from a prior save.
    _DOH_ENABLED = bool(dns_cfg.get("enabled", True)) or _DOH_SECURE_MODE
    chosen = [p for p in dns_cfg.get("providers", DEFAULT_DOH_PROVIDERS)
              if p in DOH_PROVIDERS]
    _DOH_RESOLVERS = [DOH_PROVIDERS[p]["url"] for p in chosen] or \
        [DOH_PROVIDERS[k]["url"] for k in DEFAULT_DOH_PROVIDERS]
    with _doh_lock:
        _doh_cache.clear()
        _doh_neg_cache.clear()
    with _dns_fallback_lock:
        _dns_fallback_notified.clear()


def _doh_label_for_url(url: str) -> str:
    for meta in DOH_PROVIDERS.values():
        if meta["url"] == url:
            return meta["label"]
    return url


_DOH_PAD_BLOCK = 128  # RFC 8467 padding: pad to a multiple of this many bytes


def _encode_dns_query(hostname: str, qtype: int) -> bytes:
    """Minimal single-question RFC 1035 DNS query message, with an EDNS(0)
    OPT record carrying an RFC 8467 PADDING option so the wire size lands
    on a fixed 128-byte boundary regardless of hostname length. Without
    this, a passive network observer can fingerprint which host is being
    queried purely from encrypted-request size (api.sideshift.ai
    vs ff.io produce very different byte counts otherwise)."""
    header = struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 1)  # ID=0, RD=1, ARCOUNT=1
    qname = b"".join(
        bytes([len(label)]) + label.encode("ascii")
        for label in hostname.strip(".").split(".") if label
    ) + b"\x00"
    question = qname + struct.pack(">HH", qtype, 1)  # QTYPE, QCLASS=IN

    # Bare OPT RR (name=root, TYPE=41/OPT, CLASS=UDP payload size, TTL=0)
    # followed by an OPTION(PADDING, code=12) whose length is computed so
    # the *whole message* pads to the next _DOH_PAD_BLOCK boundary.
    base_len = len(header) + len(question) + 1 + 10 + 4  # +OPT name/fixed +OPTION-CODE/LENGTH
    pad_len = (-base_len) % _DOH_PAD_BLOCK
    padding_option = struct.pack(">HH", 12, pad_len) + b"\x00" * pad_len  # OPTION-CODE=12 (PADDING)
    opt = b"\x00" + struct.pack(">HHIH", 41, 4096, 0, len(padding_option)) + padding_option

    return header + question + opt


def _skip_dns_name(msg: bytes, pos: int) -> int:
    """Advance past a (possibly compressed) DNS name, returning the new offset."""
    length = msg[pos]
    if length & 0xC0 == 0xC0:  # compression pointer: 2 bytes, always terminal
        return pos + 2
    while length != 0:
        pos += 1 + length
        length = msg[pos]
    return pos + 1


def _read_dns_name(msg: bytes, pos: int) -> tuple[str, int]:
    """Read a (possibly compressed) DNS name, returning (name, offset just
    past the name in the ORIGINAL stream). Compression pointers are followed
    to assemble the text but never advance the returned offset past the
    2-byte pointer itself, matching _skip_dns_name. A hop budget bounds the
    follow so a self-referential message can't spin here."""
    labels: list[str] = []
    end = None
    hops = 0
    while True:
        length = msg[pos]
        if length & 0xC0 == 0xC0:
            if end is None:
                end = pos + 2
            hops += 1
            if hops > 16:
                raise ValueError("DNS name compression loop")
            pos = ((length & 0x3F) << 8) | msg[pos + 1]
            continue
        if length == 0:
            if end is None:
                end = pos + 1
            break
        labels.append(msg[pos + 1:pos + 1 + length].decode("ascii", "replace"))
        pos += 1 + length
    return ".".join(labels), end


def _decode_dns_answer(msg: bytes, expect_host: str | None = None) -> str | None:
    """Pull the first A or AAAA record's address out of an RFC 8484
    wire-format response. Returns None on anything malformed/unexpected
    rather than raising, since this is a best-effort fallback path.

    Before any answer is read, the message is checked to actually BE a
    response to the question that was asked: the QR bit must be set, the
    RCODE must be 0 (NOERROR), there must be exactly one question, and that
    question's name must match `expect_host`. TLS to a resolver reached by
    pinned IP already makes substitution hard, but this module's whole
    premise is not trusting the resolution path, and without these checks a
    mismatched or error response, whatever its origin, would be read as the
    answer for a hostname it never mentions. The ANSWER's own owner name is
    deliberately NOT required to match: a legitimate CNAME chain ends in an
    A record owned by the alias target, not by the queried name.
    """
    try:
        _id, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", msg[:12])
        if not flags & 0x8000:          # QR bit clear: this is a query, not a reply
            return None
        if flags & 0x000F:              # RCODE != NOERROR (NXDOMAIN, SERVFAIL, ...)
            return None
        if qdcount != 1 or ancount < 1:
            return None
        qname, pos = _read_dns_name(msg, 12)
        pos += 4                        # QTYPE/QCLASS
        if expect_host is not None and \
                qname.strip(".").lower() != expect_host.strip(".").lower():
            return None
        for _ in range(ancount):
            pos = _skip_dns_name(msg, pos)
            rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", msg[pos:pos + 10])
            pos += 10
            rdata = msg[pos:pos + rdlength]
            pos += rdlength
            if rtype == 1 and rdlength == 4:      # A
                return ".".join(str(b) for b in rdata)
            if rtype == 28 and rdlength == 16:    # AAAA
                return ":".join(f"{rdata[i]:02x}{rdata[i + 1]:02x}"
                                 for i in range(0, 16, 2))
        return None
    except (struct.error, IndexError, ValueError):
        return None


# Built once at import: constructing these per-answer would put an
# ip_network parse in the path of every DNS resolution.
_EXTRA_BOGUS_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),    # RFC 6598 carrier-grade NAT
    ipaddress.ip_network("192.88.99.0/24"),   # deprecated 6to4 relay anycast
    ipaddress.ip_network("64:ff9b::/96"),     # NAT64, embeds an IPv4 dest
    ipaddress.ip_network("64:ff9b:1::/48"),   # local-use NAT64
]


def _is_bogus_answer(ip: str) -> bool:
    """Reject DNS-rebinding-style answers for the public provider hostnames
    we resolve here: a malicious/compromised resolver (or a DoH endpoint
    that's itself been MITM'd) could point a swap provider's hostname at
    127.0.0.1 or an RFC 1918 address, causing us to send API calls to an
    attacker-controlled or unintended local service instead of the real
    provider. None of the providers we resolve via DoH are ever
    legitimately private/loopback/link-local."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    # IPv4-mapped IPv6 (::ffff:127.0.0.1, ::ffff:10.0.0.1, ...): a dual-stack
    # socket connecting to one of these treats it exactly like the embedded
    # IPv4 address, so that's what must be checked. Checked BEFORE the outer
    # address's own flags: Python's ipaddress marks the entire ::ffff:0:0/96
    # block is_reserved=True regardless of what's embedded (it's IANA
    # special-purpose space), which would otherwise reject even a mapped
    # PUBLIC address like ::ffff:1.1.1.1.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return _is_bogus_addr(mapped)
    return _is_bogus_addr(addr)


def _is_bogus_addr(addr) -> bool:
    if (addr.is_private or addr.is_loopback or addr.is_reserved
            or addr.is_link_local or addr.is_multicast or addr.is_unspecified):
        return True
    # Ranges Python's flags don't cover consistently. 100.64.0.0/10 (RFC 6598,
    # carrier-grade NAT) is the one that matters: it is not is_private on
    # Python 3.12 but IS on 3.13, so the same answer was accepted or rejected
    # depending on which interpreter the user happened to be running. Deciding
    # it here rather than inheriting whatever the stdlib does this year means
    # the rebinding check behaves the same everywhere. A provider hostname
    # resolving into ISP NAT space is never legitimate.
    for net in _EXTRA_BOGUS_NETS:
        if addr.version == net.version and addr in net:
            return True
    return False


def _doh_query(url: str, hostname: str, qtype: int, timeout: float) -> str | None:
    query = _encode_dns_query(hostname, qtype)
    b64 = base64.urlsafe_b64encode(query).rstrip(b"=").decode("ascii")
    r = _doh_session().get(url, params={"dns": b64},
                           headers={"Accept": "application/dns-message"},
                           timeout=timeout)
    r.raise_for_status()
    # hostname is passed so the decoder can confirm the response's question
    # section is the question we asked (see _decode_dns_answer).
    return _decode_dns_answer(r.content, hostname)


def _doh_resolve(hostname: str, timeout: float = 5.0):
    """Resolve hostname to an IP address via DNS-over-HTTPS, bypassing the
    OS/local resolver entirely. Returns (ip_or_None, list_of_per_resolver_errors)
    so callers can explain *why* it failed rather than just that it did.

    Both outcomes are cached (see _DOH_TTL / _DOH_NEG_TTL) and concurrent
    lookups of the same hostname collapse into one query.
    """
    if not _DOH_ENABLED:
        return None, []

    cached = _doh_cache_get(hostname)
    if cached is not None:
        return cached

    # Serialize same-host lookups. Different hosts hold different locks, so
    # concurrent provider quotes still resolve in parallel.
    with _host_lock(hostname):
        # Re-check: a thread that was waiting on this lock has almost
        # certainly had its answer filled in by the winner.
        cached = _doh_cache_get(hostname)
        if cached is not None:
            return cached

        errors = []
        for url in _DOH_RESOLVERS:
            try:
                ip = _doh_query(url, hostname, 1, timeout)      # A
                if ip is None:
                    ip = _doh_query(url, hostname, 28, timeout)  # AAAA
                if ip and _is_bogus_answer(ip):
                    # Anti DNS-rebinding: never trust a private/loopback/
                    # reserved answer for a public provider hostname. Reported
                    # distinctly (not folded into "no record") since this is a
                    # signal worth surfacing, not a dead host.
                    errors.append(f"{url} returned {ip}, a private/reserved "
                                   f"address rejected as a likely rebinding "
                                   f"attempt")
                    continue
                if ip:
                    with _doh_lock:
                        _doh_cache[hostname] = (ip, time.monotonic() + _DOH_TTL)
                        _doh_neg_cache.pop(hostname, None)
                    return ip, errors
                errors.append(f"{url} answered but had no A/AAAA record")
            except requests.RequestException as e:
                errors.append(f"{url}: {e.__class__.__name__}: {e}")
            except (ValueError, KeyError) as e:
                errors.append(f"{url}: bad response ({e})")

        # Remember the failure briefly. The caller still gets the full error
        # list, so the message the user sees is unchanged; what changes is
        # that the next request within the window doesn't re-pay the whole
        # resolver x A/AAAA x timeout budget to reach the same conclusion.
        with _doh_lock:
            _doh_neg_cache[hostname] = (errors, time.monotonic() + _DOH_NEG_TTL)
        return None, errors


def _doh_cache_get(hostname: str):
    """Return (ip, []) on a live positive hit, (None, errors) on a live
    negative hit, or None when nothing usable is cached."""
    now = time.monotonic()
    with _doh_lock:
        hit = _doh_cache.get(hostname)
        if hit is not None:
            ip, expires = hit
            if expires > now:
                return ip, []
            del _doh_cache[hostname]
        neg = _doh_neg_cache.get(hostname)
        if neg is not None:
            errors, expires = neg
            if expires > now:
                return None, list(errors)
            del _doh_neg_cache[hostname]
    return None


_resolve_install_lock = threading.Lock()
_resolve_installed = False
_real_getaddrinfo = None
_resolve_overrides = threading.local()


def _pinned_getaddrinfo(host, *args, **kwargs):
    """Replacement for socket.getaddrinfo installed once, process-wide.
    Only consults the CURRENT THREAD's override map (see _resolve_via
    below), so it never affects DNS resolution happening on other
    threads, and needs no lock on the hot path."""
    overrides = getattr(_resolve_overrides, "map", None)
    if overrides and host in overrides:
        host = overrides[host]
    return _real_getaddrinfo(host, *args, **kwargs)


def _ensure_resolver_installed() -> None:
    global _resolve_installed, _real_getaddrinfo
    if _resolve_installed:
        return
    with _resolve_install_lock:
        if _resolve_installed:
            return
        _real_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = _pinned_getaddrinfo
        _resolve_installed = True


@contextmanager
def _resolve_via(hostname: str, ip: str):
    """Temporarily make THIS THREAD's calls to socket.getaddrinfo answer
    `hostname` with `ip`, leaving resolution of every other hostname (and
    every other thread's resolution of `hostname` itself) untouched. Lets
    us retry a request "pinned" to a DoH-resolved IP while keeping TLS
    SNI/verification against the real hostname (we only patch the
    resolution step, not the connection itself, so certificate checks are
    unaffected).

    Patching socket.getaddrinfo process-wide and holding a lock for the
    full retried HTTP request would serialize every other thread's network
    calls (status polling, coin refresh, concurrent provider quoting) behind
    whichever thread hit the DNS fallback first. Overrides are stored in
    thread-local state instead, so concurrent threads never contend with
    each other here: each only ever sees its own override, and the
    shared getaddrinfo wrapper (installed once, lazily) is otherwise a
    passthrough to the real resolver.
    """
    _ensure_resolver_installed()
    overrides = getattr(_resolve_overrides, "map", None)
    if overrides is None:
        overrides = {}
        _resolve_overrides.map = overrides
    previous = overrides.get(hostname)
    overrides[hostname] = ip
    try:
        yield
    finally:
        if previous is None:
            overrides.pop(hostname, None)
        else:
            overrides[hostname] = previous

