import socket
from ipaddress import ip_address

import pytest

from app.config import ReplayConfig
from app.replay.ssrf import DestinationError, is_blocked, validate

STRICT = ReplayConfig(allow_private_networks=False)
PERMISSIVE = ReplayConfig(allow_private_networks=True)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://internal:70/_secret",
        "ftp://internal/secrets",
        "redis://localhost:6379",
        "data:text/plain,payload",
        "//example.com/no-scheme",
    ],
)
def test_only_http_schemes_are_allowed(url):
    with pytest.raises(DestinationError):
        validate(url, STRICT)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://0.0.0.0/hook",
        "http://10.0.0.1/hook",
        "http://192.168.1.10/hook",
        "http://172.16.0.5/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/hook",
        "http://[::ffff:127.0.0.1]/hook",
        "http://[fe80::1]/hook",
        "http://[fc00::1]/hook",
        "http://[::]/hook",
    ],
)
def test_internal_destinations_are_rejected(url):
    with pytest.raises(DestinationError):
        validate(url, STRICT)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.1",
        "172.20.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:10.0.0.1",
        "ff02::1",
    ],
)
def test_blocked_address_table(address):
    assert is_blocked(ip_address(address))


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert not is_blocked(ip_address(address))


def test_credentials_in_the_url_are_rejected():
    with pytest.raises(DestinationError, match="Credentials"):
        validate("http://user:pass@example.com/hook", STRICT)


def test_unresolvable_host_is_rejected(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(DestinationError, match="Cannot resolve"):
        validate("http://nope.invalid/hook", STRICT)


def _resolves_to(monkeypatch, *addresses):
    def fake(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_a_single_private_answer_rejects_the_whole_name(monkeypatch):
    # DNS returning one public and one private address must not pass
    _resolves_to(monkeypatch, "93.184.216.34", "127.0.0.1")
    with pytest.raises(DestinationError, match="blocked address"):
        validate("http://rebind.test/hook", STRICT)


def test_validated_destination_pins_the_address_and_keeps_the_name(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    destination = validate("http://example.test:8443/hook", STRICT)

    assert destination.ip == "93.184.216.34"
    assert destination.pinned.host == "93.184.216.34"
    assert destination.authority == "example.test:8443"
    assert destination.url.host == "example.test"


def test_private_networks_pass_when_explicitly_allowed():
    assert validate("http://127.0.0.1:9000/hook", PERMISSIVE).ip == "127.0.0.1"


def test_ipv6_authority_is_bracketed(monkeypatch):
    _resolves_to(monkeypatch, "2606:4700::1111")
    assert validate("http://[2606:4700::1111]/hook", STRICT).authority == "[2606:4700::1111]"
