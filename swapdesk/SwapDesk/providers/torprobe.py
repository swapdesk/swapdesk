"""providers.torprobe: find a usable local Tor SOCKS port.

Used by proxy_mode="auto" to route through Tor when it happens to be
running, without making a user who has never heard of Tor deal with a
failure dialog.

The probe is a real SOCKS5 greeting, not a TCP connect. Anything can be
listening on 9050, and treating "something accepted a connection" as "Tor is
here" would route swap traffic into whatever that is. A greeting that comes
back 05 00 means the peer speaks SOCKS5 and accepts no-auth, which is the
protocol the proxy URL is about to promise.

Speaking SOCKS5 still is not proof of Tor: a local Shadowsocks or Dante
would answer identically. That distinction only matters if the user believes
auto mode guarantees Tor, so the UI says "SOCKS proxy detected" rather than
"you are on Tor", and verify_is_tor() exists for anyone who wants the
stronger, network-touching check.
"""
from __future__ import annotations

import socket

# Probed in order. 9050 is the tor daemon's default (also what Brave and most
# packaged tor services use); 9150 is the Tor Browser bundle, which runs its
# own tor on a separate port so it doesn't collide with a system one.
DEFAULT_PORTS = (9050, 9150)

# Deliberately short. This runs during startup, and a host that is not
# listening refuses immediately; anything that makes us wait is not something
# to route swap traffic through anyway.
PROBE_TIMEOUT = 0.4


def _speaks_socks5(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """True if a SOCKS5 no-auth greeting is accepted on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            # VER=5, NMETHODS=1, METHODS=[0x00 no-auth]
            s.sendall(b"\x05\x01\x00")
            reply = s.recv(2)
            # VER=5, METHOD=0x00 (no-auth accepted). 0xFF means it speaks
            # SOCKS5 but wants credentials we do not have, which is not
            # usable here either.
            return reply == b"\x05\x00"
    except (OSError, socket.timeout):
        return False


def find_socks_port(host: str = "127.0.0.1",
                    ports: tuple = DEFAULT_PORTS) -> int | None:
    """First port on `host` that answers a SOCKS5 no-auth greeting."""
    for port in ports:
        if _speaks_socks5(host, port):
            return port
    return None


def proxy_url_for(port: int, host: str = "127.0.0.1") -> str:
    """Build the proxy URL for a detected port.

    socks5h, never socks5: the h routes DNS through the proxy. With plain
    socks5 the hostname is resolved locally first, so every provider this app
    talks to appears in the local resolver's log while the traffic itself
    goes through Tor, which defeats most of the point.
    """
    return f"socks5h://{host}:{port}"


def socks_available() -> bool:
    """True if PySocks is importable, i.e. requests can use a SOCKS proxy.

    Without it a detected port is useless: requests raises on the first
    call. Auto mode checks this before claiming a proxy is in use.
    """
    try:
        import socks  # noqa: F401
    except ImportError:
        return False
    return True


def verify_is_tor(proxy_url: str, timeout: int = 10) -> bool | None:
    """Ask the Tor Project whether this proxy is really Tor.

    Opt-in and never called during startup: it is a request to a third party
    that the user did not ask for, and it happens before they have chosen to
    contact anyone. Returns True/False, or None if the check itself could not
    be completed (offline, endpoint down), which callers must not treat as a
    negative.
    """
    try:
        import requests
        r = requests.get(
            "https://check.torproject.org/api/ip",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            allow_redirects=False,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 - any failure means "could not tell"
        return None
    value = data.get("IsTor") if isinstance(data, dict) else None
    return bool(value) if isinstance(value, bool) else None

# Fixed by j2sec
