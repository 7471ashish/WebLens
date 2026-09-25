"""
Independent crawler module for freshness-corroboration-audit.

This module provides a safe, read-only HTTP fetcher specifically scoped to retrieve
the target webpage for subsequent JSON-LD, Schema.org, entity, temporal, and
corroboration auditing. It has zero dependencies on other audit skills or third-party
packages.
"""

from __future__ import annotations

import gzip
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.crawler")

# Safe defaults
DEFAULT_USER_AGENT = "FreshnessCorroborationAudit/1.0"
DEFAULT_TIMEOUT_MS = 15000
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000  # 5 MB
DEFAULT_MAX_TOTAL_TIME_MS = 300_000  # 5 minutes total budget

# Sensitive headers to exclude from output
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "token",
}

# Accepted HTML MIME types
ACCEPTED_HTML_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
}


def _validate_url(url: str) -> tuple[bool, str | None, urllib.parse.ParseResult | None]:
    """Validate target URL syntax and scheme."""
    if not url or not isinstance(url, str) or not url.strip():
        return False, "Target URL must be a non-empty string.", None

    trimmed = url.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
    except Exception as exc:
        return False, f"Malformed URL: {exc}", None

    if parsed.scheme.lower() not in ("http", "https"):
        return (
            False,
            f"Unsupported scheme '{parsed.scheme}'. Only 'http' and 'https' are supported.",
            None,
        )

    if not parsed.netloc or not parsed.hostname:
        return False, "Missing or invalid hostname in URL.", None

    return True, None, parsed


def _is_private_or_local_ip(hostname: str) -> bool:
    """Check if hostname resolves to local, loopback, link-local, or private IP address."""
    # Check string representations directly
    lower_host = hostname.lower()
    if lower_host in ("localhost", "localhost.localdomain", "broadcasthost"):
        return True

    # Check if host is direct IP literal
    try:
        ip = ipaddress.ip_address(lower_host)
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        pass

    # Resolve hostname to verify destination IP addresses
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if (
                    ip.is_loopback
                    or ip.is_private
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return True
            except ValueError:
                continue
    except (socket.gaierror, socket.herror, TimeoutError):
        # DNS resolution failure will be handled during HTTP connection
        return False
    except Exception:
        return False

    return False


def _extract_charset(content_type_header: str | None) -> str:
    """Extract character encoding from Content-Type header, defaulting to utf-8."""
    if not content_type_header:
        return "utf-8"
    parts = content_type_header.split(";")
    for part in parts[1:]:
        item = part.strip()
        if item.lower().startswith("charset="):
            charset = item[8:].strip(" '\"")
            if charset:
                return charset.lower()
    return "utf-8"


def _extract_mime_type(content_type_header: str | None) -> str:
    """Extract primary MIME type from Content-Type header."""
    if not content_type_header:
        return "application/octet-stream"
    return content_type_header.split(";")[0].strip().lower()


def _sanitize_headers(headers: dict[str, str] | http.client.HTTPMessage) -> dict[str, Any]:
    """Extract and sanitize safe, useful headers for audit analysis."""
    header_map: dict[str, str] = {}
    if hasattr(headers, "items"):
        for k, v in headers.items():
            lower_k = k.lower().strip()
            if lower_k not in SENSITIVE_HEADERS:
                header_map[lower_k] = str(v).strip()

    return {
        "content_type": header_map.get("content-type"),
        "last_modified": header_map.get("last-modified"),
        "etag": header_map.get("etag"),
        "cache_control": header_map.get("cache-control"),
        "date": header_map.get("date"),
    }


def _decompress_payload(raw_bytes: bytes, content_encoding: str | None) -> bytes:
    """Decompress gzip or deflate payloads if required."""
    if not content_encoding:
        return raw_bytes
    encoding = content_encoding.strip().lower()
    try:
        if encoding == "gzip":
            return gzip.decompress(raw_bytes)
        elif encoding == "deflate":
            try:
                return zlib.decompress(raw_bytes)
            except zlib.error:
                # Some servers return raw deflate stream without zlib header
                return zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
    except Exception as exc:
        logger.warning("Failed to decompress %s payload: %s", encoding, exc)
    return raw_bytes


def crawl_page(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Retrieve target webpage using safe, bounded, read-only HTTP GET.

    Args:
        target_url: Fully qualified HTTP/HTTPS URL to retrieve.
        options: Optional configuration dictionary.
            - timeout_ms: Per-request timeout in milliseconds (default: 15000).
            - max_redirects: Maximum redirect hops (default: 5).
            - max_response_bytes: Maximum response body size (default: 5000000).
            - user_agent: Custom User-Agent string (default: FreshnessCorroborationAudit/1.0).
            - max_total_time_ms: Maximum total budget across redirects (default: 300000).
            - allow_local: Allow localhost/private IPs (default: False, used for internal testing).

    Returns:
        Structured JSON-serializable dictionary matching skill output contract.
    """
    opts = options or {}
    timeout_ms = max(500, min(int(opts.get("timeout_ms", DEFAULT_TIMEOUT_MS)), 60000))
    max_redirects = max(0, min(int(opts.get("max_redirects", DEFAULT_MAX_REDIRECTS)), 20))
    max_response_bytes = max(1024, int(opts.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES)))
    user_agent = str(opts.get("user_agent", DEFAULT_USER_AGENT))
    max_total_time_ms = max(1000, int(opts.get("max_total_time_ms", DEFAULT_MAX_TOTAL_TIME_MS)))
    allow_local = bool(opts.get("allow_local", False))

    request_info = {
        "requested_url": target_url,
        "method": "GET",
        "user_agent": user_agent,
    }

    # Step 1: URL Validation
    is_valid, err_msg, parsed_url = _validate_url(target_url)
    if not is_valid or parsed_url is None:
        logger.error("URL validation failed for '%s': %s", target_url, err_msg)
        return {
            "component": "crawler",
            "status": "invalid_input",
            "request": request_info,
            "response": None,
            "redirects": {
                "count": 0,
                "chain": [],
            },
            "headers": {},
            "html": {
                "available": False,
                "body": None,
            },
            "errors": [
                {
                    "type": "invalid_url",
                    "message": err_msg or "Invalid URL supplied.",
                }
            ],
        }

    # Step 2: SSRF Protection
    if not allow_local and parsed_url.hostname and _is_private_or_local_ip(parsed_url.hostname):
        logger.warning("Blocked SSRF attempt targeting local/private host: %s", parsed_url.hostname)
        return {
            "component": "crawler",
            "status": "blocked",
            "request": request_info,
            "response": None,
            "redirects": {
                "count": 0,
                "chain": [],
            },
            "headers": {},
            "html": {
                "available": False,
                "body": None,
            },
            "errors": [
                {
                    "type": "unsafe_target",
                    "message": "Target resolves to a restricted local/private address.",
                }
            ],
        }

    # Step 3: Bounded Crawl with Manual Redirect Handling
    start_time = time.monotonic()
    current_url = target_url
    redirect_chain: list[dict[str, Any]] = []
    visited_urls: set[str] = {current_url}

    logger.info("Starting crawl for '%s' (timeout: %dms, max_redirects: %d)", target_url, timeout_ms, max_redirects)

    # Prepare custom opener that does NOT automatically follow redirects
    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            return None

    ssl_context = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirectHandler(), urllib.request.HTTPSHandler(context=ssl_context))

    last_response_headers: dict[str, Any] = {}
    last_status_code: int | None = None
    response_body_str: str | None = None
    content_type_raw: str | None = None
    content_length: int = 0

    while True:
        elapsed_total_ms = (time.monotonic() - start_time) * 1000
        if elapsed_total_ms >= max_total_time_ms:
            logger.warning("Total execution budget exceeded (%d ms)", elapsed_total_ms)
            return {
                "component": "crawler",
                "status": "error",
                "request": request_info,
                "response": None,
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": False,
                    "body": None,
                },
                "errors": [
                    {
                        "type": "timeout",
                        "message": f"Total crawl budget of {max_total_time_ms}ms exceeded.",
                    }
                ],
            }

        # Calculate remaining timeout for this specific HTTP request
        req_timeout_sec = min(timeout_ms, max(100, max_total_time_ms - elapsed_total_ms)) / 1000.0

        # Validate redirect target for SSRF
        curr_parsed = urllib.parse.urlparse(current_url)
        if not allow_local and curr_parsed.hostname and _is_private_or_local_ip(curr_parsed.hostname):
            logger.warning("Redirect target blocked by SSRF protection: %s", current_url)
            return {
                "component": "crawler",
                "status": "blocked",
                "request": request_info,
                "response": None,
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": False,
                    "body": None,
                },
                "errors": [
                    {
                        "type": "unsafe_target",
                        "message": f"Redirect destination '{current_url}' resolves to a restricted local/private address.",
                    }
                ],
            }

        req = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            },
            method="GET",
        )

        try:
            with opener.open(req, timeout=req_timeout_sec) as resp:
                status_code = resp.status if hasattr(resp, "status") else resp.code
                last_status_code = status_code
                headers_dict = {str(k).lower().strip(): str(v).strip() for k, v in resp.headers.items()} if hasattr(resp, "headers") and resp.headers else {}
                last_response_headers = _sanitize_headers(headers_dict)
                content_type_raw = headers_dict.get("content-type")
                content_encoding = headers_dict.get("content-encoding")

                # Read body in chunks with size limit protection
                chunks: list[bytes] = []
                total_bytes_read = 0
                while True:
                    remaining_budget = max_response_bytes - total_bytes_read + 1
                    chunk = resp.read(min(65536, remaining_budget))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_bytes_read += len(chunk)
                    if total_bytes_read > max_response_bytes:
                        logger.warning("Response exceeded max size limit of %d bytes", max_response_bytes)
                        duration_ms = int((time.monotonic() - start_time) * 1000)
                        return {
                            "component": "crawler",
                            "status": "response_too_large",
                            "request": request_info,
                            "response": {
                                "status_code": status_code,
                                "final_url": current_url,
                                "content_type": _extract_mime_type(content_type_raw),
                                "content_length": total_bytes_read,
                                "duration_ms": duration_ms,
                            },
                            "redirects": {
                                "count": len(redirect_chain),
                                "chain": redirect_chain,
                            },
                            "headers": last_response_headers,
                            "html": {
                                "available": False,
                                "body": None,
                            },
                            "errors": [
                                {
                                    "type": "response_too_large",
                                    "message": f"Response exceeded maximum configured size of {max_response_bytes} bytes.",
                                }
                            ],
                        }

                raw_bytes = b"".join(chunks)
                decompressed_bytes = _decompress_payload(raw_bytes, content_encoding)
                content_length = len(decompressed_bytes)

                # Decode encoding safely
                charset = _extract_charset(content_type_raw)
                try:
                    response_body_str = decompressed_bytes.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    response_body_str = decompressed_bytes.decode("utf-8", errors="replace")

                break

        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            last_status_code = status_code
            headers_dict = {str(k).lower().strip(): str(v).strip() for k, v in http_err.headers.items()} if http_err.headers else {}
            last_response_headers = _sanitize_headers(headers_dict)
            content_type_raw = headers_dict.get("content-type")

            # Check if this is a redirect status
            if status_code in (301, 302, 303, 307, 308):
                location = headers_dict.get("location")
                if not location:
                    # Missing location header on redirect
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    return {
                        "component": "crawler",
                        "status": "http_error",
                        "request": request_info,
                        "response": {
                            "status_code": status_code,
                            "final_url": current_url,
                            "content_type": _extract_mime_type(content_type_raw),
                            "content_length": 0,
                            "duration_ms": duration_ms,
                        },
                        "redirects": {
                            "count": len(redirect_chain),
                            "chain": redirect_chain,
                        },
                        "headers": last_response_headers,
                        "html": {
                            "available": False,
                            "body": None,
                        },
                        "errors": [
                            {
                                "type": "http_error",
                                "message": f"Redirect status {status_code} received without Location header.",
                            }
                        ],
                    }

                next_url = urllib.parse.urljoin(current_url, location)
                redirect_chain.append(
                    {
                        "status_code": status_code,
                        "from": current_url,
                        "to": next_url,
                    }
                )

                # Check redirect loop
                if next_url in visited_urls:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    logger.warning("Redirect loop detected at %s", next_url)
                    return {
                        "component": "crawler",
                        "status": "redirect_loop",
                        "request": request_info,
                        "response": {
                            "status_code": status_code,
                            "final_url": next_url,
                            "content_type": _extract_mime_type(content_type_raw),
                            "content_length": 0,
                            "duration_ms": duration_ms,
                        },
                        "redirects": {
                            "count": len(redirect_chain),
                            "chain": redirect_chain,
                        },
                        "headers": last_response_headers,
                        "html": {
                            "available": False,
                            "body": None,
                        },
                        "errors": [
                            {
                                "type": "redirect_loop",
                                "message": f"Redirect loop encountered at '{next_url}'.",
                            }
                        ],
                    }

                # Check redirect depth limit
                if len(redirect_chain) > max_redirects:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    logger.warning("Exceeded maximum redirects (%d)", max_redirects)
                    return {
                        "component": "crawler",
                        "status": "error",
                        "request": request_info,
                        "response": {
                            "status_code": status_code,
                            "final_url": next_url,
                            "content_type": _extract_mime_type(content_type_raw),
                            "content_length": 0,
                            "duration_ms": duration_ms,
                        },
                        "redirects": {
                            "count": len(redirect_chain),
                            "chain": redirect_chain,
                        },
                        "headers": last_response_headers,
                        "html": {
                            "available": False,
                            "body": None,
                        },
                        "errors": [
                            {
                                "type": "too_many_redirects",
                                "message": f"Redirect chain exceeded configured limit of {max_redirects} hops.",
                            }
                        ],
                    }

                visited_urls.add(next_url)
                current_url = next_url
                continue

            # Non-redirect HTTP error (e.g. 404, 403, 500)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            body_bytes = http_err.read(min(65536, max_response_bytes)) if hasattr(http_err, "read") else b""
            charset = _extract_charset(content_type_raw)
            try:
                body_str = body_bytes.decode(charset, errors="replace")
            except Exception:
                body_str = body_bytes.decode("utf-8", errors="replace")

            logger.info("Received HTTP %d for '%s'", status_code, current_url)
            return {
                "component": "crawler",
                "status": "http_error",
                "request": request_info,
                "response": {
                    "status_code": status_code,
                    "final_url": current_url,
                    "content_type": _extract_mime_type(content_type_raw),
                    "content_length": len(body_bytes),
                    "duration_ms": duration_ms,
                },
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": bool(body_str),
                    "body": body_str if body_str else None,
                },
                "errors": [
                    {
                        "type": "http_error",
                        "message": f"HTTP request returned status {status_code}.",
                    }
                ],
            }

        except urllib.error.URLError as url_err:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            reason = url_err.reason
            error_type = "connection_error"
            error_msg = str(reason)

            if isinstance(reason, socket.timeout) or "timed out" in error_msg.lower():
                error_type = "timeout"
                error_msg = "HTTP request timed out."
            elif isinstance(reason, socket.gaierror) or "getaddrinfo failed" in error_msg.lower():
                error_type = "dns_error"
                error_msg = "DNS resolution failed for hostname."
            elif isinstance(reason, ssl.SSLError) or "certificate" in error_msg.lower() or "ssl" in error_msg.lower():
                error_type = "ssl_error"
                error_msg = f"TLS/SSL validation failure: {reason}"

            logger.warning("Crawl network error (%s) for '%s': %s", error_type, current_url, error_msg)
            return {
                "component": "crawler",
                "status": "error",
                "request": request_info,
                "response": None,
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": False,
                    "body": None,
                },
                "errors": [
                    {
                        "type": error_type,
                        "message": error_msg,
                    }
                ],
            }

        except (TimeoutError, socket.timeout):
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning("Crawl socket timeout for '%s'", current_url)
            return {
                "component": "crawler",
                "status": "error",
                "request": request_info,
                "response": None,
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": False,
                    "body": None,
                },
                "errors": [
                    {
                        "type": "timeout",
                        "message": "HTTP request timed out.",
                    }
                ],
            }

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("Unexpected error during crawl of '%s': %s", current_url, exc)
            return {
                "component": "crawler",
                "status": "error",
                "request": request_info,
                "response": None,
                "redirects": {
                    "count": len(redirect_chain),
                    "chain": redirect_chain,
                },
                "headers": last_response_headers,
                "html": {
                    "available": False,
                    "body": None,
                },
                "errors": [
                    {
                        "type": "unknown_error",
                        "message": f"Unexpected crawler failure: {exc}",
                    }
                ],
            }

    # Step 4: Validate Content Type
    duration_ms = int((time.monotonic() - start_time) * 1000)
    mime_type = _extract_mime_type(content_type_raw)
    is_html = mime_type in ACCEPTED_HTML_MIME_TYPES

    errors_list: list[dict[str, str]] = []
    status_label = "success"

    if not is_html:
        status_label = "unsupported_content_type"
        errors_list.append(
            {
                "type": "unsupported_content_type",
                "message": f"Target returned content-type '{mime_type}', expected HTML (text/html, application/xhtml+xml).",
            }
        )

    return {
        "component": "crawler",
        "status": status_label,
        "request": request_info,
        "response": {
            "status_code": last_status_code or 200,
            "final_url": current_url,
            "content_type": mime_type,
            "content_length": content_length,
            "duration_ms": duration_ms,
        },
        "redirects": {
            "count": len(redirect_chain),
            "chain": redirect_chain,
        },
        "headers": last_response_headers,
        "html": {
            "available": is_html and (response_body_str is not None),
            "body": response_body_str if is_html else None,
        },
        "errors": errors_list,
    }


def main() -> int:
    """Command-line interface for crawler.py."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s")
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python crawler.py <URL> [options_json]\n")
        return 1

    target = sys.argv[1]
    options: dict[str, Any] = {}

    if len(sys.argv) >= 3:
        try:
            options = json.loads(sys.argv[2])
        except json.JSONDecodeError as err:
            sys.stderr.write(f"Invalid options JSON: {err}\n")
            return 1

    result = crawl_page(target, options)
    print(json.dumps(result, indent=2))

    # Exit code: 0 if success, 1 if any failure/error
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
