"""
Unit tests for SSRF Guard and URL Validation.
"""

import ipaddress
import os
import socket
import sys
import pytest

# Ensure backend root is on path
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from ssrf_guard import SSRFValidationError, validate_target_url


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch):
    """Ensure tests are offline resilient and deterministic without real DNS latency."""
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # Known safe external domains for unit testing
        if host in ("example.com", "example.org", "adobe.com"):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
            ]
        # Rebinding simulation
        if host == "rebind-to-local.test":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
            ]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "valid_url",
    [
        "https://example.com",
        "https://example.com/some/path?param=1",
        "http://example.org",
        "https://adobe.com",
    ],
)
def test_valid_urls_pass(valid_url):
    result = validate_target_url(valid_url)
    assert result == valid_url


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://localhost",
        "http://localhost:8000",
        "http://localhost.localdomain",
        "http://127.0.0.1:8000",
        "http://127.0.0.2",
        "http://10.0.0.1",
        "http://10.255.255.255",
        "http://172.16.0.1",
        "http://172.31.255.255",
        "http://192.168.1.1",
        "http://192.168.0.254",
        "http://169.254.169.254",  # AWS/Cloud Metadata IP
        "http://0.0.0.0",
        "http://[::1]",
        "file:///etc/passwd",
        "ftp://example.com",
        "gopher://example.com",
        "data:text/html,<h1>Hello</h1>",
        "javascript:alert(1)",
        "http://rebind-to-local.test",
        "",
    ],
)
def test_unsafe_urls_rejected(unsafe_url):
    with pytest.raises(SSRFValidationError):
        validate_target_url(unsafe_url)


def test_allow_private_override():
    # When explicitly allowed (e.g. testing local sites)
    url = "http://127.0.0.1:8080/test"
    assert validate_target_url(url, allow_private=True) == url
