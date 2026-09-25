"""
Sitemap Checker Module (sitemap_checker.py)
-------------------------------------------
Deterministic, read-only sitemap discovery, XML parsing, index recursion,
and factual validation layer for the `crawl-render-audit` skill.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            +---- crawler.py
            |
            +---- robots_checker.py
            |
            +---- sitemap_checker.py  <-- (THIS MODULE)
            |
            +---- raw_html_analyzer.py
            |
            +---- render_analyzer.py
            |
            +---- dom_comparator.py
            |
            +---- finding_builder.py
            |
            v
    structured findings

This module discovers, fetches, decompresses (gzip), validates XML structure,
extracts <urlset> and <sitemapindex> entries, and collects factual metrics
without calculating audit severities or recommendations.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

# Import safe URL and SSRF validators from crawler
try:
    from crawl_crawler import (
        DEFAULT_OPTIONS as CRAWLER_DEFAULTS,
        classify_http_error,
        extract_normalized_headers,
        is_safe_target,
        validate_and_normalize_url,
    )
except ImportError:
    from .crawl_crawler import (
        DEFAULT_OPTIONS as CRAWLER_DEFAULTS,
        classify_http_error,
        extract_normalized_headers,
        is_safe_target,
        validate_and_normalize_url,
    )

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.sitemap_checker")
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
    "max_sitemaps": 10,
    "max_urls": 10000,
    "max_xml_size_bytes": 5_242_880,  # 5 MB
    "verify_ssl": True,
    "allow_private_ips": False,
    "max_redirects": 5,
}



VALID_XML_CONTENT_TYPES: tuple[str, ...] = (
    "application/xml",
    "text/xml",
    "application/x-gzip",
    "application/gzip",
    "text/plain",
)

REDIRECT_STATUS_CODES: set[int] = {301, 302, 303, 307, 308}



# Helper Functions: Discovery & Origin Resolution


def get_origin(url: str) -> str:
    """Extract `<scheme>://<netloc>` from any target URL."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "", ""))


def discover_sitemap_urls(
    target_url: str,
    robots_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Determine sitemap candidates based on robots.txt and conventional locations.

    Priority Order:
    1. Sitemaps declared in `robots_result["sitemaps"]`.
    2. Conventional origin `<origin>/sitemap.xml`.
    3. Fallback convention `<origin>/sitemap_index.xml`.
    """
    origin = get_origin(target_url)
    conventional_url = urljoin(origin + "/", "sitemap.xml")
    fallback_index_url = urljoin(origin + "/", "sitemap_index.xml")

    robots_sitemaps: list[str] = []
    if robots_result and isinstance(robots_result, dict):
        raw_sitemaps = robots_result.get("sitemaps", [])
        if isinstance(raw_sitemaps, list):
            for s_url in raw_sitemaps:
                if isinstance(s_url, str) and s_url.strip():
                    is_valid, norm, _ = validate_and_normalize_url(s_url)
                    if is_valid and norm and norm not in robots_sitemaps:
                        robots_sitemaps.append(norm)

    discovered: list[str] = []
    if robots_sitemaps:
        discovered.extend(robots_sitemaps)
    else:
        discovered.append(conventional_url)

    return {
        "robots_sitemaps": robots_sitemaps,
        "conventional_sitemap": conventional_url,
        "fallback_sitemap": fallback_index_url,
        "discovered_sitemaps": discovered,
    }


def _strip_namespace(tag: str) -> str:
    """Strip XML namespace prefix e.g. '{http://...}urlset' -> 'urlset'."""
    if "}" in tag:
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _validate_loc(loc_value: str) -> bool:
    """Validate that a <loc> URL string is a valid HTTP/HTTPS URL."""
    if not loc_value or not isinstance(loc_value, str):
        return False
    is_valid, _, _ = validate_and_normalize_url(loc_value.strip())
    return is_valid



# HTTP Fetcher for Sitemap Files


async def _fetch_sitemap_file(
    url: str,
    options: dict[str, Any],
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """
    Safely fetch a single sitemap file following redirects and checking boundaries.
    """
    timeout_ms = int(options.get("timeout_ms", DEFAULT_OPTIONS["timeout_ms"]))
    max_redirects = int(options.get("max_redirects", DEFAULT_OPTIONS["max_redirects"]))
    max_xml_size_bytes = int(options.get("max_xml_size_bytes", DEFAULT_OPTIONS["max_xml_size_bytes"]))
    allow_private_ips = bool(options.get("allow_private_ips", DEFAULT_OPTIONS["allow_private_ips"]))

    # Step 1: URL & SSRF Validation
    is_valid, norm_url, val_err = validate_and_normalize_url(url)
    if not is_valid or norm_url is None:
        return {
            "status": "error",
            "requested_url": url,
            "final_url": None,
            "error_type": "invalid_url",
            "error_message": val_err or "Invalid sitemap URL",
        }

    parsed = urlparse(norm_url)
    is_safe, ssrf_err = is_safe_target(parsed.netloc, allow_private_ips)
    if not is_safe:
        return {
            "status": "error",
            "requested_url": norm_url,
            "final_url": None,
            "error_type": "security_error",
            "error_message": ssrf_err or "Access to restricted target prohibited",
        }

    user_agent = options.get("user_agent", DEFAULT_OPTIONS["user_agent"])
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/xml,text/xml,application/x-gzip,*/*;q=0.8",
    }

    start_mono = time.perf_counter()
    redirects_list: list[dict[str, Any]] = []
    visited_urls: set[str] = {norm_url}
    current_url = norm_url
    redirect_count = 0

    try:
        response: httpx.Response | None = None

        while True:
            elapsed_ms = (time.perf_counter() - start_mono) * 1000.0
            if elapsed_ms >= timeout_ms:
                raise httpx.TimeoutException(f"Sitemap fetch timed out after {timeout_ms} ms")

            req = client.build_request("GET", current_url, headers=headers)
            response = await client.send(req)

            if response.status_code in REDIRECT_STATUS_CODES and "location" in response.headers:
                redirect_count += 1
                location = response.headers["location"]
                next_url = urljoin(current_url, location)

                is_valid_next, norm_next, red_err = validate_and_normalize_url(next_url)
                if not is_valid_next or norm_next is None:
                    return {
                        "status": "error",
                        "requested_url": norm_url,
                        "final_url": current_url,
                        "error_type": "redirect_error",
                        "error_message": f"Redirected to invalid URL '{next_url}': {red_err}",
                    }

                redirects_list.append({
                    "from": current_url,
                    "status": response.status_code,
                    "to": norm_next,
                })

                if redirect_count > max_redirects:
                    return {
                        "status": "error",
                        "requested_url": norm_url,
                        "final_url": current_url,
                        "error_type": "redirect_error",
                        "error_message": f"Exceeded max redirects ({max_redirects})",
                    }

                if norm_next in visited_urls:
                    return {
                        "status": "error",
                        "requested_url": norm_url,
                        "final_url": current_url,
                        "error_type": "redirect_error",
                        "error_message": f"Redirect loop detected: '{norm_next}'",
                    }

                next_parsed = urlparse(norm_next)
                is_safe_next, ssrf_next_err = is_safe_target(next_parsed.netloc, allow_private_ips)
                if not is_safe_next:
                    return {
                        "status": "error",
                        "requested_url": norm_url,
                        "final_url": current_url,
                        "error_type": "security_error",
                        "error_message": f"Redirect blocked: {ssrf_next_err}",
                    }

                visited_urls.add(norm_next)
                current_url = norm_next
                continue

            break

        total_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)
        assert response is not None

        content_type = response.headers.get("content-type", "")
        raw_bytes = response.content
        raw_size = len(raw_bytes)

        # Check raw size limit
        if raw_size > max_xml_size_bytes:
            return {
                "status": "too_large",
                "requested_url": norm_url,
                "final_url": current_url,
                "status_code": response.status_code,
                "size_bytes": raw_size,
                "max_size_bytes": max_xml_size_bytes,
                "response_time_ms": total_ms,
            }

        # Handle non-200 HTTP response
        if response.status_code != 200:
            return {
                "status": "http_error",
                "requested_url": norm_url,
                "final_url": current_url,
                "status_code": response.status_code,
                "content_type": content_type or None,
                "response_time_ms": total_ms,
                "redirect_count": redirect_count,
                "redirects": redirects_list,
            }

        # Handle Gzip decompression: if raw_bytes already starts with XML, it was decompressed by transport
        if raw_bytes.strip().startswith(b"<"):
            xml_bytes = raw_bytes
        elif raw_bytes.startswith(b"\x1f\x8b") or current_url.endswith(".gz"):
            try:
                xml_bytes = gzip.decompress(raw_bytes)
            except Exception as gz_err:
                return {
                    "status": "invalid_gzip",
                    "requested_url": norm_url,
                    "final_url": current_url,
                    "status_code": 200,
                    "error_message": f"Failed to decompress gzip content: {gz_err}",
                    "response_time_ms": total_ms,
                }
        else:
            xml_bytes = raw_bytes

            if len(xml_bytes) > max_xml_size_bytes:
                return {
                    "status": "too_large",
                    "requested_url": norm_url,
                    "final_url": current_url,
                    "status_code": 200,
                    "size_bytes": len(xml_bytes),
                    "max_size_bytes": max_xml_size_bytes,
                    "response_time_ms": total_ms,
                }

        # Content-type validation heuristic
        content_type_valid = any(ct in content_type.lower() for ct in VALID_XML_CONTENT_TYPES)

        normalized_headers = extract_normalized_headers(response.headers)

        return {
            "status": "ok",
            "requested_url": norm_url,
            "final_url": current_url,
            "status_code": 200,
            "content_type": content_type or None,
            "content_type_valid": content_type_valid,
            "content_length": len(xml_bytes),
            "response_time_ms": total_ms,
            "redirect_count": redirect_count,
            "redirects": redirects_list,
            "headers": normalized_headers,
            "xml_bytes": xml_bytes,
        }

    except Exception as exc:
        total_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)
        err_info = classify_http_error(exc, timeout_ms)
        return {
            "status": "error",
            "requested_url": norm_url,
            "final_url": current_url if current_url != norm_url else None,
            "error_type": err_info.get("type", "unknown_error"),
            "error_message": err_info.get("message", str(exc)),
            "response_time_ms": total_ms,
        }



# XML Parsing: URLSet and SitemapIndex


def parse_sitemap_xml(
    xml_bytes: bytes,
    max_urls: int = 10000,
) -> dict[str, Any]:
    """
    Parse sitemap XML payload safely into either a `urlset` or `sitemapindex`.
    """
    if not xml_bytes or not xml_bytes.strip():
        return {
            "valid_xml": False,
            "recognized_sitemap_type": False,
            "type": "empty",
            "error": "Empty sitemap content",
        }

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as pe:
        return {
            "valid_xml": False,
            "recognized_sitemap_type": False,
            "type": "malformed_xml",
            "error": f"XML parse error: {pe}",
        }
    except Exception as ex:
        return {
            "valid_xml": False,
            "recognized_sitemap_type": False,
            "type": "malformed_xml",
            "error": f"Unexpected XML error: {ex}",
        }

    root_tag = _strip_namespace(root.tag)

    if root_tag == "urlset":
        urls: list[dict[str, Any]] = []
        seen_locs: set[str] = set()
        duplicate_count = 0
        invalid_url_count = 0
        has_loc = False
        truncated = False

        for url_elem in root:
            if _strip_namespace(url_elem.tag) != "url":
                continue

            loc_val: str | None = None
            lastmod_val: str | None = None
            changefreq_val: str | None = None
            priority_val: str | None = None

            for child in url_elem:
                c_tag = _strip_namespace(child.tag)
                text_val = (child.text or "").strip()
                if c_tag == "loc":
                    loc_val = text_val
                elif c_tag == "lastmod":
                    lastmod_val = text_val
                elif c_tag == "changefreq":
                    changefreq_val = text_val
                elif c_tag == "priority":
                    priority_val = text_val

            if loc_val:
                has_loc = True
                if not _validate_loc(loc_val):
                    invalid_url_count += 1

                if loc_val in seen_locs:
                    duplicate_count += 1
                else:
                    seen_locs.add(loc_val)

            if len(urls) < max_urls:
                entry: dict[str, Any] = {"loc": loc_val}
                if lastmod_val:
                    entry["lastmod"] = lastmod_val
                if changefreq_val:
                    entry["changefreq"] = changefreq_val
                if priority_val:
                    entry["priority"] = priority_val
                urls.append(entry)
            else:
                truncated = True

        return {
            "valid_xml": True,
            "recognized_sitemap_type": True,
            "type": "urlset",
            "has_loc": has_loc,
            "url_count": len(urls),
            "urls": urls,
            "duplicate_url_count": duplicate_count,
            "invalid_url_count": invalid_url_count,
            "truncated": truncated,
        }

    elif root_tag == "sitemapindex":
        child_sitemaps: list[dict[str, Any]] = []
        seen_child_locs: set[str] = set()
        duplicate_sitemap_count = 0
        has_loc = False

        for sm_elem in root:
            if _strip_namespace(sm_elem.tag) != "sitemap":
                continue

            loc_val: str | None = None
            lastmod_val: str | None = None

            for child in sm_elem:
                c_tag = _strip_namespace(child.tag)
                text_val = (child.text or "").strip()
                if c_tag == "loc":
                    loc_val = text_val
                elif c_tag == "lastmod":
                    lastmod_val = text_val

            if loc_val:
                has_loc = True
                if loc_val in seen_child_locs:
                    duplicate_sitemap_count += 1
                else:
                    seen_child_locs.add(loc_val)

            entry = {"loc": loc_val}
            if lastmod_val:
                entry["lastmod"] = lastmod_val
            child_sitemaps.append(entry)

        return {
            "valid_xml": True,
            "recognized_sitemap_type": True,
            "type": "sitemapindex",
            "has_loc": has_loc,
            "sitemap_count": len(child_sitemaps),
            "sitemaps": child_sitemaps,
            "duplicate_sitemap_count": duplicate_sitemap_count,
        }

    return {
        "valid_xml": True,
        "recognized_sitemap_type": False,
        "type": "unexpected_root",
        "root_element": root.tag,
        "error": f"Unrecognized root element '<{root.tag}>'",
    }



# Recursive Index Crawler & Main Interface


async def check_sitemap(
    target_url: str,
    robots_result: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Discover, fetch, and validate sitemaps for the given `target_url`.

    Args:
        target_url: Target web page or domain URL.
        robots_result: Output from `robots_checker.py` containing discovered sitemap URLs.
        options: Configuration options (timeout_ms, max_sitemaps, max_urls, max_xml_size_bytes).
        client: Optional custom httpx.AsyncClient (e.g. for testing with MockTransport).

    Returns:
        Structured observation dictionary conforming to the skill output contract.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    max_sitemaps = int(opts.get("max_sitemaps", DEFAULT_OPTIONS["max_sitemaps"]))
    max_urls = int(opts.get("max_urls", DEFAULT_OPTIONS["max_urls"]))
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_OPTIONS["timeout_ms"]))

    # Step 1: Validate input URL
    is_valid, norm_target, val_err = validate_and_normalize_url(target_url)
    if not is_valid or norm_target is None:
        logger.warning("Invalid target URL in check_sitemap: %s (%s)", target_url, val_err)
        return {
            "skill": "crawl-render-audit",
            "component": "sitemap_checker",
            "target_url": target_url,
            "discovery": {
                "robots_sitemaps": [],
                "conventional_sitemap": "",
                "discovered_sitemaps": [],
            },
            "summary": {
                "sitemap_found": False,
                "sitemaps_checked": 0,
                "sitemaps_successful": 0,
                "sitemaps_failed": 0,
                "total_urls_discovered": 0,
                "total_sitemaps_discovered": 0,
            },
            "sitemaps": [],
            "errors": [{
                "type": "invalid_url",
                "message": val_err or "Invalid URL supplied",
            }],
        }

    # Step 2: Discover Sitemap candidates
    discovery_info = discover_sitemap_urls(norm_target, robots_result)
    queue: deque[str] = deque(discovery_info["discovered_sitemaps"])
    visited_sitemaps: set[str] = set()

    checked_sitemaps: list[dict[str, Any]] = []
    errors_list: list[dict[str, Any]] = []
    total_urls_discovered = 0
    total_sitemaps_found = 0
    successful_count = 0
    failed_count = 0
    all_discovered_urls: list[str] = []

    own_client = False
    if client is None:
        own_client = True
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        client = httpx.AsyncClient(
            verify=bool(opts.get("verify_ssl", True)),
            timeout=httpx.Timeout(timeout_ms / 1000.0),
            limits=limits,
            follow_redirects=False,
        )

    try:
        while queue and len(checked_sitemaps) < max_sitemaps:
            current_sitemap_url = queue.popleft()
            if current_sitemap_url in visited_sitemaps:
                continue

            visited_sitemaps.add(current_sitemap_url)
            logger.info("Checking sitemap (%d/%d): %s", len(checked_sitemaps) + 1, max_sitemaps, current_sitemap_url)

            # Fetch sitemap file
            fetch_res = await _fetch_sitemap_file(current_sitemap_url, opts, client)

            status = fetch_res.get("status")

            if status == "error":
                failed_count += 1
                err_entry = {
                    "url": current_sitemap_url,
                    "type": fetch_res.get("error_type", "fetch_error"),
                    "message": fetch_res.get("error_message", "Failed to fetch sitemap"),
                }
                errors_list.append(err_entry)
                checked_sitemaps.append({
                    "requested_url": current_sitemap_url,
                    "final_url": fetch_res.get("final_url"),
                    "status": "error",
                    "resource_state": "unavailable",
                    "error": fetch_res.get("error_message"),
                })
                continue

            if status == "http_error":
                failed_count += 1
                status_code = fetch_res.get("status_code", 0)
                if status_code == 404:
                    sm_res_state = "missing"
                elif status_code in (401, 403):
                    sm_res_state = "forbidden"
                elif status_code in (408, 502, 503, 504):
                    sm_res_state = "unavailable"
                else:
                    sm_res_state = "error"
                checked_sitemaps.append({
                    "requested_url": current_sitemap_url,
                    "final_url": fetch_res.get("final_url"),
                    "status": "not_found" if status_code == 404 else "http_error",
                    "status_code": status_code,
                    "resource_state": sm_res_state,
                    "content_type": fetch_res.get("content_type"),
                    "response_time_ms": fetch_res.get("response_time_ms"),
                })
                continue

            if status == "too_large":
                failed_count += 1
                checked_sitemaps.append({
                    "requested_url": current_sitemap_url,
                    "final_url": fetch_res.get("final_url"),
                    "status": "too_large",
                    "status_code": fetch_res.get("status_code", 200),
                    "size_bytes": fetch_res.get("size_bytes"),
                    "max_size_bytes": fetch_res.get("max_size_bytes"),
                    "response_time_ms": fetch_res.get("response_time_ms"),
                })
                continue

            if status == "invalid_gzip":
                failed_count += 1
                checked_sitemaps.append({
                    "requested_url": current_sitemap_url,
                    "final_url": fetch_res.get("final_url"),
                    "status": "invalid_gzip",
                    "status_code": 200,
                    "error": fetch_res.get("error_message"),
                    "response_time_ms": fetch_res.get("response_time_ms"),
                })
                continue

            # Parse XML
            xml_bytes = fetch_res.get("xml_bytes", b"")
            remaining_urls_quota = max(0, max_urls - total_urls_discovered)
            parse_res = parse_sitemap_xml(xml_bytes, max_urls=remaining_urls_quota)

            sitemap_type = parse_res.get("type", "unknown")
            valid_xml = parse_res.get("valid_xml", False)
            recognized_type = parse_res.get("recognized_sitemap_type", False)

            if not valid_xml or not recognized_type:
                failed_count += 1
                checked_sitemaps.append({
                    "requested_url": current_sitemap_url,
                    "final_url": fetch_res.get("final_url"),
                    "status": "invalid_xml",
                    "status_code": 200,
                    "resource_state": "invalid",
                    "content_type": fetch_res.get("content_type"),
                    "response_time_ms": fetch_res.get("response_time_ms"),
                    "valid_xml": valid_xml,
                    "recognized_sitemap_type": recognized_type,
                    "error": parse_res.get("error"),
                })
                continue

            # Successful valid XML sitemap
            successful_count += 1
            total_sitemaps_found += 1

            sitemap_entry: dict[str, Any] = {
                "requested_url": current_sitemap_url,
                "final_url": fetch_res.get("final_url"),
                "status": "ok",
                "status_code": 200,
                "resource_state": "present",
                "content_type": fetch_res.get("content_type"),
                "content_type_valid": fetch_res.get("content_type_valid", True),
                "response_time_ms": fetch_res.get("response_time_ms"),
                "type": sitemap_type,
                "valid_xml": True,
                "recognized_sitemap_type": True,
                "has_loc": parse_res.get("has_loc", False),
            }

            if sitemap_type == "urlset":
                urls_found = parse_res.get("url_count", 0)
                total_urls_discovered += urls_found
                sitemap_entry["url_count"] = urls_found
                sitemap_entry["duplicate_url_count"] = parse_res.get("duplicate_url_count", 0)
                sitemap_entry["invalid_url_count"] = parse_res.get("invalid_url_count", 0)
                sitemap_entry["truncated"] = parse_res.get("truncated", False)
                if urls_found == 0:
                    sitemap_entry["resource_state"] = "empty"
                # Keep preview of URLs for inspectability
                raw_urls = parse_res.get("urls", [])
                sitemap_entry["sample_urls"] = raw_urls[:5] if raw_urls else []
                for u in raw_urls:
                    if u and u not in all_discovered_urls and len(all_discovered_urls) < 2000:
                        all_discovered_urls.append(u)

            elif sitemap_type == "sitemapindex":
                child_list = parse_res.get("sitemaps", [])
                sitemap_entry["sitemap_count"] = len(child_list)
                sitemap_entry["duplicate_sitemap_count"] = parse_res.get("duplicate_sitemap_count", 0)
                if len(child_list) == 0:
                    sitemap_entry["resource_state"] = "empty"

                # Queue child sitemaps for recursive fetching
                for child in child_list:
                    child_loc = child.get("loc")
                    if child_loc and child_loc not in visited_sitemaps:
                        is_valid_child, norm_child, _ = validate_and_normalize_url(child_loc)
                        if is_valid_child and norm_child and norm_child not in visited_sitemaps:
                            queue.append(norm_child)

            checked_sitemaps.append(sitemap_entry)

    finally:
        if own_client and client is not None:
            await client.aclose()

    sitemap_found = (successful_count > 0)

    if successful_count > 0:
        if total_urls_discovered == 0 and all(sm.get("resource_state") == "empty" for sm in checked_sitemaps if sm.get("status") == "ok"):
            overall_resource_state = "empty"
        else:
            overall_resource_state = "present"
    elif any(sm.get("resource_state") == "invalid" or sm.get("status") in ("invalid_xml", "invalid_gzip", "too_large") for sm in checked_sitemaps):
        overall_resource_state = "invalid"
    elif any(sm.get("resource_state") == "missing" or sm.get("status_code") == 404 for sm in checked_sitemaps):
        overall_resource_state = "missing"
    elif any(sm.get("resource_state") == "forbidden" or sm.get("status_code") in (401, 403) for sm in checked_sitemaps):
        overall_resource_state = "forbidden"
    elif any(sm.get("resource_state") == "unavailable" or sm.get("status") == "error" for sm in checked_sitemaps):
        overall_resource_state = "unavailable"
    elif errors_list:
        overall_resource_state = "unavailable"
    else:
        overall_resource_state = "missing" if len(checked_sitemaps) > 0 else "unavailable"

    logger.info(
        "Sitemap audit finished for %s: checked=%d, success=%d, failed=%d, total_urls=%d, state=%s",
        norm_target,
        len(checked_sitemaps),
        successful_count,
        failed_count,
        total_urls_discovered,
        overall_resource_state,
    )

    return {
        "skill": "crawl-render-audit",
        "component": "sitemap_checker",
        "target_url": norm_target,
        "discovery": discovery_info,
        "resource_state": overall_resource_state,
        "summary": {
            "sitemap_found": sitemap_found,
            "resource_state": overall_resource_state,
            "sitemaps_checked": len(checked_sitemaps),
            "sitemaps_successful": successful_count,
            "sitemaps_failed": failed_count,
            "total_urls_discovered": total_urls_discovered,
            "total_sitemaps_discovered": total_sitemaps_found,
        },
        "sitemaps": checked_sitemaps,
        "discovered_urls": all_discovered_urls,
        "errors": errors_list,
    }


def check_sitemap_sync(
    target_url: str,
    robots_result: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous convenience wrapper around `check_sitemap`."""
    return asyncio.run(check_sitemap(target_url, robots_result, options))



# CLI Testing Interface


def _cli_entrypoint() -> None:
    """CLI runner for direct command-line verification."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python sitemap_checker.py <TARGET_URL> [--max-sitemaps N] [--max-urls N] [--timeout-ms N]")
        print("Example: python sitemap_checker.py https://example.com")
        sys.exit(0)

    url = sys.argv[1]
    opts: dict[str, Any] = {}

    for i, arg in enumerate(sys.argv):
        if arg == "--max-sitemaps" and i + 1 < len(sys.argv):
            opts["max_sitemaps"] = int(sys.argv[i + 1])
        elif arg == "--max-urls" and i + 1 < len(sys.argv):
            opts["max_urls"] = int(sys.argv[i + 1])
        elif arg == "--timeout-ms" and i + 1 < len(sys.argv):
            opts["timeout_ms"] = int(sys.argv[i + 1])

    result = check_sitemap_sync(url, options=opts)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
