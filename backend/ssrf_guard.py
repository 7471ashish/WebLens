"""
SSRF Protection and URL Validation Helper.
Ensures target URLs use HTTP/HTTPS schemes and do not resolve to loopback,
private RFC-1918, link-local, cloud metadata, or internal intranet addresses.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("weblens.ssrf_guard")

# Blacklisted IPv4 and IPv6 networks
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),          # Current network (only valid as source)
    ipaddress.ip_network("10.0.0.0/8"),          # Private RFC 1918
    ipaddress.ip_network("100.64.0.0/10"),       # Shared Address Space (Carrier-grade NAT)
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),      # Link-local / AWS & Cloud Metadata
    ipaddress.ip_network("172.16.0.0/12"),       # Private RFC 1918
    ipaddress.ip_network("192.0.0.0/24"),        # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),        # Documentation (TEST-NET-1)
    ipaddress.ip_network("192.88.99.0/24"),      # 6to4 Relay Anycast
    ipaddress.ip_network("192.168.0.0/16"),      # Private RFC 1918
    ipaddress.ip_network("198.18.0.0/15"),       # Benchmark testing
    ipaddress.ip_network("198.51.100.0/24"),     # Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),      # Documentation (TEST-NET-3)
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved
    ipaddress.ip_network("255.255.255.255/32"), # Limited Broadcast
    # IPv6 blocked ranges
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("::/128"),             # Unspecified
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6
    ipaddress.ip_network("64:ff9b::/96"),       # IPv4/IPv6 translation
    ipaddress.ip_network("100::/64"),           # Discard prefix
    ipaddress.ip_network("2001:db8::/32"),      # Documentation
    ipaddress.ip_network("fc00::/7"),           # Unique local (ULA)
    ipaddress.ip_network("fe80::/10"),          # Link-local unicast
    ipaddress.ip_network("ff00::/8"),           # Multicast
]

# Prohibited hostnames explicitly
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "169.254.169.254",
    "instance-data",
}


class SSRFValidationError(ValueError):
    """Raised when a URL violates SSRF safety rules."""
    pass


def is_ip_blocked(ip_addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address belongs to any blocked network."""
    return any(ip_addr in network for network in _BLOCKED_NETWORKS)


def validate_target_url(url: str, allow_private: bool = False) -> str:
    """
    Validate target URL against SSRF attack vectors:
    1. Scheme must be http or https
    2. Hostname must be present and not in blocked hostnames
    3. Hostname is resolved via DNS and all resolved IPs are verified not to be private/loopback/metadata.

    Returns the normalized, validated URL string.
    Raises SSRFValidationError if unsafe.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("Target URL must be a non-empty string.")

    cleaned_url = url.strip()
    parsed = urlparse(cleaned_url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFValidationError(
            f"Invalid URL scheme '{parsed.scheme}'. Only 'http' and 'https' are supported."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("Target URL has no valid hostname.")

    hostname_lower = hostname.lower()

    if allow_private:
        # Development / test override mode if explicitly allowed
        return cleaned_url

    if hostname_lower in _BLOCKED_HOSTNAMES or hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
        raise SSRFValidationError(f"Target host '{hostname}' is a restricted local or internal domain.")

    # Check if hostname directly represents an IP literal
    try:
        direct_ip = ipaddress.ip_address(hostname_lower)
        if is_ip_blocked(direct_ip):
            raise SSRFValidationError(
                f"Target IP '{direct_ip}' belongs to a blocked internal or private network range."
            )
        return cleaned_url
    except ValueError:
        # Not a raw IP literal; proceed with DNS resolution
        pass

    # Resolve hostname via DNS to prevent DNS rebinding attacks
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addr_info = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise SSRFValidationError(f"Could not resolve host '{hostname}': {exc}")

    if not addr_info:
        raise SSRFValidationError(f"DNS resolution returned no addresses for host '{hostname}'.")

    # Verify every resolved IP address
    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            resolved_ip = ipaddress.ip_address(ip_str)
            if is_ip_blocked(resolved_ip):
                raise SSRFValidationError(
                    f"Resolved host '{hostname}' maps to blocked IP '{resolved_ip}'."
                )
        except ValueError:
            raise SSRFValidationError(f"Invalid resolved IP '{ip_str}' for host '{hostname}'.")

    return cleaned_url
