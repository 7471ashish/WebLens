"""
Crawler Module (crawler.py)
---------------------------
Low-level asynchronous HTTP crawling and fetching layer for the `crawl-render-audit` skill.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            v
        crawler.py   <-- (THIS MODULE)
            |
            v
       HTTP response
            |
      +-----+-----+
      |           |
      v           v
robots_checker  raw_html_analyzer
                  |
                  v
           render_analyzer
                  |
                  v
           dom_comparator
                  |
                  v
           finding_builder

This module is strictly read-only and responsible only for safe, deterministic HTTP GET
transport, redirect tracing, timing, and raw payload capture.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import ssl
import sys
import time
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.crawler")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default configuration options
DEFAULT_OPTIONS: dict[str, Any] = {
    "user_agent": "Mozilla/5.0 (compatible; AgentAuditBot/1.0)",
    "timeout_ms": 15000,
    "max_redirects": 5,
    "verify_ssl": True,
    "max_response_bytes": 5_000_000,  # 5 MB
    "allow_private_ips": False,  # SSRF protection toggle
}



# Relevant headers to normalize and preserve for downstream audit modules
RELEVANT_HEADERS: tuple[str, ...] = (
    "content-type",
    "content-length",
    "location",
    "cache-control",
    "content-encoding",
    "server",
    "last-modified",
    "etag",
    "x-robots-tag",
    "vary",
    "strict-transport-security",
    "content-security-policy",
    "date",
)

# Standard redirect status codes
REDIRECT_STATUS_CODES: set[int] = {301, 302, 303, 307, 308}

# Common private / loopback IP networks for SSRF prevention
PRIVATE_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.ip_network("100.64.0.0/10"),   # Carrier-grade NAT
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 Unique Local Address
    ipaddress.ip_network("fe80::/10"),       # IPv6 Link-local
]



# Helper Functions: Validation, SSRF Safety, and Header Building


def validate_and_normalize_url(url: str) -> tuple[bool, str | None, str | None]:
    """
    Validate syntax and normalize the target URL.

    Returns:
        (is_valid, normalized_url_or_none, error_message_or_none)
    """
    if not url or not isinstance(url, str):
        return False, None, "Target URL must be a non-empty string"

    url_clean = url.strip()
    if not url_clean:
        return False, None, "Target URL cannot be whitespace"

    try:
        parsed = urlparse(url_clean)
    except Exception as exc:
        return False, None, f"Failed to parse URL: {exc}"

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return (
            False,
            None,
            f"Unsupported scheme '{scheme}'. Target URL must use http:// or https://",
        )

    if not parsed.netloc:
        return False, None, "Target URL must contain a valid host/domain name"

    # Normalize: lowercased scheme & netloc, ensure path is at least '/'
    path = parsed.path if parsed.path else "/"
    normalized = urlunparse((
        scheme,
        parsed.netloc.lower(),
        path,
        parsed.params,
        parsed.query,
        "",  # Strip fragment for crawling
    ))

    return True, normalized, None


def is_safe_target(hostname: str, allow_private: bool = False) -> tuple[bool, str | None]:
    """
    Check if a target hostname or IP address is safe against SSRF vulnerabilities.

    Rejects localhost, loopback, link-local, and private RFC-1918/ULA networks.
    """
    if allow_private:
        return True, None

    host_lower = hostname.lower().strip()
    # Strip port if present in host
    if ":" in host_lower and not host_lower.startswith("["):
        host_lower = host_lower.split(":", 1)[0]
    elif host_lower.startswith("[") and "]" in host_lower:
        # IPv6 literal with port, e.g. [::1]:8080
        host_lower = host_lower[1:].split("]", 1)[0]

    # Explicit localhost names
    blocked_hosts = {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "0.0.0.0",
        "127.0.0.1",
        "::1",
    }
    if host_lower in blocked_hosts:
        return False, f"Access to localhost/loopback target '{hostname}' is prohibited"

    # Try resolving hostname to IP addresses and verify they aren't private
    try:
        # Check if direct IP literal
        ip_obj = ipaddress.ip_address(host_lower)
        ips = [ip_obj]
    except ValueError:
        # Hostname - resolve via DNS
        try:
            addr_info = socket.getaddrinfo(host_lower, None, proto=socket.IPPROTO_TCP)
            ips = []
            for item in addr_info:
                ip_str = item[4][0]
                try:
                    ips.append(ipaddress.ip_address(ip_str))
                except ValueError:
                    pass
        except socket.gaierror:
            # DNS resolution will be handled gracefully during HTTP request
            return True, None
        except Exception:
            return True, None

    for ip in ips:
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return False, f"Target '{hostname}' resolves to private/restricted IP ({ip})"
        for net in PRIVATE_IP_NETWORKS:
            if ip in net:
                return False, f"Target '{hostname}' resolves to protected network {net} ({ip})"

    return True, None


def build_request_headers(options: dict[str, Any]) -> dict[str, str]:
    """Build safe, standard HTTP headers for crawler requests."""
    user_agent = options.get("user_agent", DEFAULT_OPTIONS["user_agent"])
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }


def classify_http_error(exc: Exception, timeout_ms: int) -> dict[str, str]:
    """Classify exceptions into clear, machine-readable error descriptors."""
    err_str = str(exc)

    if isinstance(exc, httpx.TimeoutException):
        return {
            "type": "timeout",
            "message": f"HTTP request timed out after {timeout_ms} ms",
        }
    if isinstance(exc, (httpx.ConnectError, socket.gaierror)):
        lowered = err_str.lower()
        if "getaddrinfo" in lowered or "name resolution" in lowered or "nodename nor servname" in lowered:
            return {
                "type": "dns_error",
                "message": f"DNS resolution failed: {err_str}",
            }
        return {
            "type": "connection_error",
            "message": f"Failed to establish connection: {err_str}",
        }
    # Safe SSL error checking (handles httpx wrapped SSL exceptions across all httpx versions)
    httpx_ssl = getattr(httpx, "SSLError", None)
    is_ssl = (
        (httpx_ssl is not None and isinstance(exc, httpx_ssl))
        or isinstance(exc, ssl.SSLError)
        or isinstance(getattr(exc, "__cause__", None), ssl.SSLError)
        or "ssl" in err_str.lower()
        or "certificate" in err_str.lower()
    )
    if is_ssl:
        return {
            "type": "ssl_error",
            "message": f"SSL certificate verification or handshake failed: {err_str}",
        }
    if isinstance(exc, httpx.TooManyRedirects):
        return {
            "type": "redirect_error",
            "message": "Exceeded maximum allowed redirects (possible redirect loop)",
        }
    if isinstance(exc, httpx.DecodingError):
        return {
            "type": "decoding_error",
            "message": f"Failed to decode response content: {err_str}",
        }
    if isinstance(exc, httpx.HTTPError):
        return {
            "type": "http_error",
            "message": f"HTTP transport error: {err_str}",
        }
    return {
        "type": "unknown_error",
        "message": f"Unexpected error during crawl: {err_str}",
    }


def extract_normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Extract and lowercase relevant HTTP headers."""
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        k_lower = key.lower()
        if k_lower in RELEVANT_HEADERS:
            normalized[k_lower] = value
        elif k_lower.startswith("x-") or k_lower.startswith("sec-"):
            normalized[k_lower] = value
    return normalized



# Core Crawl Functionality


async def crawl_url(
    target_url: str,
    options: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Perform a safe, read-only HTTP GET crawl of `target_url`.

    Args:
        target_url: The URL to request.
        options: Optional configuration dictionary (user_agent, timeout_ms, max_redirects, verify_ssl, max_response_bytes, allow_private_ips).
        client: Optional custom httpx.AsyncClient (e.g. for testing with MockTransport).

    Returns:
        Structured dictionary matching the crawler output contract.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_OPTIONS["timeout_ms"]))
    timeout_sec = timeout_ms / 1000.0
    max_redirects = int(opts.get("max_redirects", DEFAULT_OPTIONS["max_redirects"]))
    verify_ssl = bool(opts.get("verify_ssl", DEFAULT_OPTIONS["verify_ssl"]))
    max_response_bytes = int(opts.get("max_response_bytes", DEFAULT_OPTIONS["max_response_bytes"]))
    allow_private_ips = bool(opts.get("allow_private_ips", DEFAULT_OPTIONS["allow_private_ips"]))

    # Step 1: URL Validation
    is_valid, normalized_url, err_msg = validate_and_normalize_url(target_url)
    if not is_valid or normalized_url is None:
        logger.warning("Invalid URL supplied: %s (%s)", target_url, err_msg)
        return {
            "success": False,
            "requested_url": target_url,
            "final_url": None,
            "error": {
                "type": "invalid_url",
                "message": err_msg or "Invalid URL supplied",
            },
        }

    # Step 2: SSRF Safety Check on initial URL
    parsed_init = urlparse(normalized_url)
    is_safe, ssrf_err = is_safe_target(parsed_init.netloc, allow_private_ips)
    if not is_safe:
        logger.warning("SSRF check failed for %s: %s", normalized_url, ssrf_err)
        return {
            "success": False,
            "requested_url": normalized_url,
            "final_url": None,
            "error": {
                "type": "security_error",
                "message": ssrf_err or "Access to requested target host is restricted",
            },
        }

    logger.info("Starting crawl for: %s (timeout: %d ms)", normalized_url, timeout_ms)
    req_headers = build_request_headers(opts)

    start_mono = time.perf_counter()
    redirects_list: list[dict[str, Any]] = []
    visited_urls: set[str] = {normalized_url}
    current_url = normalized_url

    # Instantiate client if not provided
    own_client = False
    if client is None:
        own_client = True
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(timeout_sec, connect=timeout_sec),
            limits=limits,
            follow_redirects=False,  # We manage redirects manually for precise logging & SSRF safety
        )

    try:
        response: httpx.Response | None = None
        redirect_count = 0

        while True:
            # Check elapsed time against total operation timeout
            elapsed_ms = (time.perf_counter() - start_mono) * 1000.0
            remaining_sec = max(0.1, (timeout_ms - elapsed_ms) / 1000.0)
            if elapsed_ms >= timeout_ms:
                raise httpx.TimeoutException(f"Crawl operation timed out after {timeout_ms} ms")

            # Execute single HTTP GET request
            req = client.build_request("GET", current_url, headers=req_headers)
            response = await client.send(req)

            # Check if response is a redirect
            if response.status_code in REDIRECT_STATUS_CODES and "location" in response.headers:
                redirect_count += 1
                location_header = response.headers["location"]
                next_url = urljoin(current_url, location_header)

                # Validate next URL
                is_valid_redirect, norm_redirect, val_err = validate_and_normalize_url(next_url)
                if not is_valid_redirect or norm_redirect is None:
                    return {
                        "success": False,
                        "requested_url": normalized_url,
                        "final_url": current_url,
                        "error": {
                            "type": "redirect_error",
                            "message": f"Redirected to invalid URL '{next_url}': {val_err}",
                        },
                    }

                # Record redirect step
                redirect_entry = {
                    "from": current_url,
                    "status": response.status_code,
                    "to": norm_redirect,
                }
                redirects_list.append(redirect_entry)
                logger.info("Redirect (%d) -> %s", response.status_code, norm_redirect)

                # Check max redirect limit
                if redirect_count > max_redirects:
                    return {
                        "success": False,
                        "requested_url": normalized_url,
                        "final_url": current_url,
                        "error": {
                            "type": "redirect_error",
                            "message": f"Exceeded maximum allowed redirects limit ({max_redirects})",
                        },
                    }

                # Check redirect loop
                if norm_redirect in visited_urls:
                    return {
                        "success": False,
                        "requested_url": normalized_url,
                        "final_url": current_url,
                        "error": {
                            "type": "redirect_error",
                            "message": f"Redirect loop detected: '{norm_redirect}' visited multiple times",
                        },
                    }

                # SSRF check on redirected host
                next_parsed = urlparse(norm_redirect)
                is_safe_next, ssrf_next_err = is_safe_target(next_parsed.netloc, allow_private_ips)
                if not is_safe_next:
                    return {
                        "success": False,
                        "requested_url": normalized_url,
                        "final_url": current_url,
                        "error": {
                            "type": "security_error",
                            "message": f"Redirect blocked: {ssrf_next_err}",
                        },
                    }

                visited_urls.add(norm_redirect)
                current_url = norm_redirect
                continue

            # Non-redirect response reached
            break

        total_duration_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)
        assert response is not None

        # Content Type & Encoding extraction
        content_type_header = response.headers.get("content-type", "")
        content_type_lower = content_type_header.lower()
        is_html = "text/html" in content_type_lower or "application/xhtml+xml" in content_type_lower
        is_text = (
            is_html
            or content_type_lower.startswith("text/")
            or "application/json" in content_type_lower
            or "application/xml" in content_type_lower
            or "text/plain" in content_type_lower
            or "text/xml" in content_type_lower
        )

        # Raw Body extraction with truncation safety
        raw_bytes = response.content
        content_length_bytes = len(raw_bytes)
        body_truncated = False

        if content_length_bytes > max_response_bytes:
            raw_bytes = raw_bytes[:max_response_bytes]
            body_truncated = True
            logger.warning(
                "Response size (%d bytes) exceeded max limit (%d bytes), truncated.",
                content_length_bytes,
                max_response_bytes,
            )

        body_available = False
        body_content: str | None = None

        if is_text:
            body_available = True
            encoding = response.encoding or "utf-8"
            try:
                body_content = raw_bytes.decode(encoding, errors="replace")
            except Exception:
                body_content = raw_bytes.decode("utf-8", errors="replace")

        normalized_headers = extract_normalized_headers(response.headers)

        logger.info(
            "Crawl completed for %s: status=%d, time=%.2fms, redirects=%d, bytes=%d",
            current_url,
            response.status_code,
            total_duration_ms,
            redirect_count,
            content_length_bytes,
        )

        if 200 <= response.status_code < 300:
            res_state = "present"
        elif response.status_code in (404, 410):
            res_state = "missing"
        elif response.status_code in (401, 403):
            res_state = "forbidden"
        elif response.status_code in (408, 502, 503, 504):
            res_state = "unavailable"
        else:
            res_state = "error" if response.status_code >= 500 else "present"

        return {
            "success": True,
            "requested_url": normalized_url,
            "final_url": current_url,
            "status_code": response.status_code,
            "resource_state": res_state,
            "content_type": content_type_header or None,
            "content_length_bytes": content_length_bytes,
            "response_time_ms": total_duration_ms,
            "redirect_count": redirect_count,
            "redirects": redirects_list,
            "headers": normalized_headers,
            "body_available": body_available,
            "body_truncated": body_truncated,
            "body": body_content,
        }

    except Exception as exc:
        total_duration_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)
        err_info = classify_http_error(exc, timeout_ms)
        err_type = err_info.get("type", "unknown_error")
        res_state = "unavailable" if err_type in ("dns_error", "timeout", "connection_error", "ssl_error", "redirect_error") else "error"
        logger.warning("Crawl failed for %s (%.2fms): %s", normalized_url, total_duration_ms, err_info)
        return {
            "success": False,
            "requested_url": normalized_url,
            "final_url": current_url if current_url != normalized_url else None,
            "status_code": None,
            "resource_state": res_state,
            "error_classification": err_type,
            "error_message": err_info.get("message", str(exc)),
            "error": err_info,
        }

    finally:
        if own_client and client is not None:
            await client.aclose()


def crawl_url_sync(target_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronous convenience wrapper around `crawl_url`."""
    return asyncio.run(crawl_url(target_url, options))



# CLI Testing Interface


def _cli_entrypoint() -> None:
    """CLI runner for direct command-line testing."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python crawler.py <URL> [--allow-private] [--json-full]")
        print("Example: python crawler.py https://example.com")
        sys.exit(0)

    url = sys.argv[1]
    allow_private = "--allow-private" in sys.argv
    json_full = "--json-full" in sys.argv

    opts = {
        "allow_private_ips": allow_private,
    }

    result = crawl_url_sync(url, opts)

    # For clean CLI output, create a summary view unless --json-full is explicitly passed
    if not json_full and result.get("success") and result.get("body"):
        summary_result = dict(result)
        body_text = summary_result.pop("body", "")
        summary_result["body_preview"] = (
            (body_text[:200] + "... [truncated for CLI output]")
            if len(body_text) > 200
            else body_text
        )
        print(json.dumps(summary_result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
