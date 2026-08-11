import socket
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address

import httpx

from app.config import ReplayConfig

ALLOWED_SCHEMES = frozenset({"http", "https"})
IpAddress = IPv4Address | IPv6Address


class DestinationError(Exception):
    """The destination is not allowed, never retried"""


@dataclass(frozen=True)
class Destination:
    url: httpx.URL
    ip: str

    @property
    def pinned(self) -> httpx.URL:
        # connect to the address we validated, not to whatever dns says next
        return self.url.copy_with(host=self.ip)

    @property
    def authority(self) -> str:
        host = self.url.host
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{self.url.port}" if self.url.port else host


def is_blocked(ip: IpAddress) -> bool:
    # ipv4-mapped ipv6 (::ffff:127.0.0.1) hides a v4 address inside a v6 one
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return is_blocked(mapped)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve(host: str, port: int) -> list[IpAddress]:
    try:
        results = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DestinationError(f"Cannot resolve '{host}'") from exc
    return [ip_address(info[4][0]) for info in results]


def validate(url: str, config: ReplayConfig) -> Destination:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise DestinationError("Malformed URL") from exc

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise DestinationError(f"Scheme '{parsed.scheme or 'none'}' is not allowed")
    if not parsed.host:
        raise DestinationError("Destination has no host")
    if parsed.userinfo:
        raise DestinationError("Credentials in the URL are not allowed")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = resolve(parsed.host, port)
    if not addresses:
        raise DestinationError(f"Cannot resolve '{parsed.host}'")

    if not config.allow_private_networks:
        # Every address must pass
        for candidate in addresses:
            if is_blocked(candidate):
                raise DestinationError(f"'{parsed.host}' resolves to blocked address {candidate}")

    return Destination(url=parsed, ip=str(addresses[0]))
