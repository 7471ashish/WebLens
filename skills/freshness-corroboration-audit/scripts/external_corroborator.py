"""
Independent external corroboration module for freshness-corroboration-audit.

This module performs bounded, conservative external verification of selected identity
references (e.g. sameAs) and temporal claims against external sources using safe,
read-only HTTP retrieval with strict SSRF protection and resource budgeting.
"""

from __future__ import annotations

import gzip
import html.parser
import http.client
import ipaddress
import json
import logging
import re
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
logger = logging.getLogger("freshness_corroboration_audit.external_corroborator")

# Safe default options
DEFAULT_MAX_EXTERNAL_SOURCES = 5
DEFAULT_MAX_CLAIMS = 10
DEFAULT_PER_REQUEST_TIMEOUT_MS = 10000
DEFAULT_TOTAL_BUDGET_MS = 60000
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000  # 2 MB
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_SAMEAS_URLS_PER_ENTITY = 3
DEFAULT_MAX_SOURCES_PER_DOMAIN = 2
DEFAULT_USER_AGENT = "FreshnessCorroborationAudit/1.0"


def _is_private_or_local_ip(hostname: str) -> bool:
    """Check if hostname resolves to loopback, private, link-local, or reserved IP address."""
    lower_host = hostname.lower().strip()
    if lower_host in ("localhost", "localhost.localdomain", "broadcasthost"):
        return True

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
    except Exception:
        return False

    return False


def _validate_external_url(url: str) -> tuple[bool, str | None, urllib.parse.ParseResult | None]:
    """Validate that the URL has http/https scheme and a valid hostname."""
    if not url or not isinstance(url, str) or not url.strip():
        return False, "URL must be a non-empty string.", None

    trimmed = url.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
    except Exception as exc:
        return False, f"Malformed URL: {exc}", None

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}'. Only http and https allowed.", None

    if not parsed.netloc or not parsed.hostname:
        return False, "Missing hostname in URL.", None

    return True, None, parsed


def _normalize_string(text: str | None) -> str:
    """Normalize string for conservative comparison (lowercase, remove punctuation, collapse whitespace)."""
    if not text or not isinstance(text, str):
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


class _ExternalPageExtractor(html.parser.HTMLParser):
    """Lightweight parser to extract title, canonical URL, OpenGraph title, and JSON-LD snippets."""

    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.in_script = False
        self.title_parts: list[str] = []
        self.title: str | None = None
        self.canonical_url: str | None = None
        self.og_title: str | None = None
        self.jsonld_scripts: list[str] = []
        self.current_script_parts: list[str] = []
        self.dates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low_tag = tag.lower()
        attr_dict = {str(k).lower(): str(v).strip() if v else "" for k, v in attrs}

        if low_tag == "title":
            self.in_title = True
            self.title_parts = []
        elif low_tag == "link" and attr_dict.get("rel", "").lower() == "canonical":
            href = attr_dict.get("href")
            if href:
                self.canonical_url = href
        elif low_tag == "meta":
            prop = attr_dict.get("property", "").lower() or attr_dict.get("name", "").lower()
            content = attr_dict.get("content", "")
            if prop in ("og:title", "twitter:title") and content:
                self.og_title = content
            elif prop in ("article:published_time", "article:modified_time", "date") and content:
                self.dates.append(content)
        elif low_tag == "script":
            script_type = attr_dict.get("type", "").lower()
            if script_type.startswith("application/ld+json"):
                self.in_script = True
                self.current_script_parts = []

    def handle_endtag(self, tag: str) -> None:
        low_tag = tag.lower()
        if low_tag == "title" and self.in_title:
            self.title = "".join(self.title_parts).strip()
            self.in_title = False
        elif low_tag == "script" and self.in_script:
            script_content = "".join(self.current_script_parts).strip()
            if script_content and len(self.jsonld_scripts) < 5:
                self.jsonld_scripts.append(script_content)
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        elif self.in_script:
            self.current_script_parts.append(data)


def _decompress_payload(raw_bytes: bytes, encoding: str | None) -> bytes:
    """Decompress gzip/deflate if necessary."""
    if not encoding:
        return raw_bytes
    enc = encoding.lower().strip()
    try:
        if enc == "gzip":
            return gzip.decompress(raw_bytes)
        elif enc == "deflate":
            try:
                return zlib.decompress(raw_bytes)
            except zlib.error:
                return zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
    except Exception:
        pass
    return raw_bytes


def _fetch_external_url(
    url: str,
    timeout_ms: int,
    max_redirects: int,
    max_response_bytes: int,
    user_agent: str,
    allow_local: bool = False,
) -> dict[str, Any]:
    """Fetch external URL with bounded redirects, size protection, and SSRF prevention."""
    start_time = time.monotonic()
    current_url = url
    redirect_count = 0
    visited_urls: set[str] = {current_url}

    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            return None

    ssl_context = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirectHandler(), urllib.request.HTTPSHandler(context=ssl_context))

    while True:
        is_valid, err_msg, parsed = _validate_external_url(current_url)
        if not is_valid or parsed is None:
            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "fetch_status": "invalid_url",
                "final_url": current_url,
                "status_code": None,
                "content_type": None,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": err_msg or "Invalid URL.",
            }

        # SSRF Check
        if not allow_local and parsed.hostname and _is_private_or_local_ip(parsed.hostname):
            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "fetch_status": "blocked_private_address",
                "final_url": current_url,
                "status_code": None,
                "content_type": None,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": f"Address '{current_url}' targets a private/local network.",
            }

        req = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            },
            method="GET",
        )

        try:
            with opener.open(req, timeout=timeout_ms / 1000.0) as resp:
                status_code = resp.status if hasattr(resp, "status") else resp.code
                headers_dict = {str(k).lower(): str(v) for k, v in resp.headers.items()} if hasattr(resp, "headers") else {}
                content_type = headers_dict.get("content-type", "")
                content_encoding = headers_dict.get("content-encoding")

                # Read body bounded
                chunks: list[bytes] = []
                total_read = 0
                while True:
                    chunk = resp.read(min(32768, max_response_bytes - total_read + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)
                    if total_read > max_response_bytes:
                        duration = int((time.monotonic() - start_time) * 1000)
                        return {
                            "fetch_status": "response_too_large",
                            "final_url": current_url,
                            "status_code": status_code,
                            "content_type": content_type,
                            "response_size_bytes": total_read,
                            "response_time_ms": duration,
                            "redirect_count": redirect_count,
                            "body_bytes": b"",
                            "error": f"Response exceeded {max_response_bytes} bytes.",
                        }

                raw_bytes = b"".join(chunks)
                decompressed = _decompress_payload(raw_bytes, content_encoding)
                duration = int((time.monotonic() - start_time) * 1000)

                return {
                    "fetch_status": "success",
                    "final_url": current_url,
                    "status_code": status_code,
                    "content_type": content_type,
                    "response_size_bytes": len(decompressed),
                    "response_time_ms": duration,
                    "redirect_count": redirect_count,
                    "body_bytes": decompressed,
                    "error": None,
                }

        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            headers_dict = {str(k).lower(): str(v) for k, v in http_err.headers.items()} if http_err.headers else {}
            content_type = headers_dict.get("content-type", "")

            # Check redirect status
            if status_code in (301, 302, 303, 307, 308):
                location = headers_dict.get("location")
                if not location:
                    duration = int((time.monotonic() - start_time) * 1000)
                    return {
                        "fetch_status": "http_error",
                        "final_url": current_url,
                        "status_code": status_code,
                        "content_type": content_type,
                        "response_size_bytes": 0,
                        "response_time_ms": duration,
                        "redirect_count": redirect_count,
                        "body_bytes": b"",
                        "error": "Redirect without location header.",
                    }

                next_url = urllib.parse.urljoin(current_url, location)
                redirect_count += 1

                if next_url in visited_urls:
                    duration = int((time.monotonic() - start_time) * 1000)
                    return {
                        "fetch_status": "redirect_loop",
                        "final_url": next_url,
                        "status_code": status_code,
                        "content_type": content_type,
                        "response_size_bytes": 0,
                        "response_time_ms": duration,
                        "redirect_count": redirect_count,
                        "body_bytes": b"",
                        "error": f"Redirect loop detected at '{next_url}'.",
                    }

                if redirect_count > max_redirects:
                    duration = int((time.monotonic() - start_time) * 1000)
                    return {
                        "fetch_status": "too_many_redirects",
                        "final_url": next_url,
                        "status_code": status_code,
                        "content_type": content_type,
                        "response_size_bytes": 0,
                        "response_time_ms": duration,
                        "redirect_count": redirect_count,
                        "body_bytes": b"",
                        "error": f"Redirects exceeded limit of {max_redirects}.",
                    }

                visited_urls.add(next_url)
                current_url = next_url
                continue

            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "fetch_status": "http_error",
                "final_url": current_url,
                "status_code": status_code,
                "content_type": content_type,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": f"HTTP status {status_code}.",
            }

        except urllib.error.URLError as url_err:
            duration = int((time.monotonic() - start_time) * 1000)
            reason = url_err.reason
            err_str = str(reason).lower()
            status_name = "connection_error"
            if isinstance(reason, socket.timeout) or "timed out" in err_str:
                status_name = "timeout"
            elif isinstance(reason, socket.gaierror) or "getaddrinfo failed" in err_str:
                status_name = "dns_error"
            elif isinstance(reason, ssl.SSLError) or "ssl" in err_str or "certificate" in err_str:
                status_name = "ssl_error"

            return {
                "fetch_status": status_name,
                "final_url": current_url,
                "status_code": None,
                "content_type": None,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": str(reason),
            }

        except (socket.timeout, TimeoutError):
            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "fetch_status": "timeout",
                "final_url": current_url,
                "status_code": None,
                "content_type": None,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": "Request timed out.",
            }

        except Exception as exc:
            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "fetch_status": "connection_error",
                "final_url": current_url,
                "status_code": None,
                "content_type": None,
                "response_size_bytes": 0,
                "response_time_ms": duration,
                "redirect_count": redirect_count,
                "body_bytes": b"",
                "error": str(exc),
            }


def _extract_evidence_from_html(html_text: str) -> dict[str, Any]:
    """Extract minimal structured evidence from external HTML response."""
    extractor = _ExternalPageExtractor()
    try:
        extractor.feed(html_text)
    except Exception:
        pass

    entity_names: list[str] = []
    entity_types: list[str] = []
    temporal_values: list[str] = list(extractor.dates)

    # Inspect extracted JSON-LD blocks safely
    for script_str in extractor.jsonld_scripts:
        try:
            data = json.loads(script_str)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    if "@graph" in item and isinstance(item["@graph"], list):
                        for g_item in item["@graph"]:
                            if isinstance(g_item, dict):
                                if "name" in g_item and isinstance(g_item["name"], str):
                                    entity_names.append(g_item["name"])
                                if "@type" in g_item:
                                    t = g_item["@type"]
                                    if isinstance(t, str):
                                        entity_types.append(t)
                                    elif isinstance(t, list):
                                        entity_types.extend([str(x) for x in t])
                                for tf in ("datePublished", "dateModified", "dateCreated"):
                                    if tf in g_item and isinstance(g_item[tf], str):
                                        temporal_values.append(g_item[tf])
                    else:
                        if "name" in item and isinstance(item["name"], str):
                            entity_names.append(item["name"])
                        if "@type" in item:
                            t = item["@type"]
                            if isinstance(t, str):
                                entity_types.append(t)
                            elif isinstance(t, list):
                                entity_types.extend([str(x) for x in t])
                        for tf in ("datePublished", "dateModified", "dateCreated"):
                            if tf in item and isinstance(item[tf], str):
                                temporal_values.append(item[tf])
        except Exception:
            continue

    if extractor.og_title and extractor.og_title not in entity_names:
        entity_names.append(extractor.og_title)

    return {
        "title": extractor.title,
        "canonical_url": extractor.canonical_url,
        "entity_names": list(dict.fromkeys(entity_names)),
        "entity_types": list(dict.fromkeys(entity_types)),
        "temporal_values": list(dict.fromkeys(temporal_values)),
    }


def corroborate(
    target_url: str,
    entities: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Perform bounded external corroboration of identity references and temporal claims.

    Args:
        target_url: Primary webpage URL being audited.
        entities: List of entities from entity_analyzer/jsonld_analyzer.
        freshness: Optional freshness results from freshness_analyzer.
        options: Optional configuration dictionary.
            - max_external_sources: Maximum external hosts to query (default: 5).
            - max_claims: Maximum claims to check (default: 10).
            - per_request_timeout_ms: Timeout per external request (default: 10000).
            - total_budget_ms: Maximum total budget for external calls (default: 60000).
            - max_response_bytes: Maximum body size per request (default: 2000000).
            - max_redirects: Maximum redirects to follow (default: 5).
            - max_sameas_urls_per_entity: Max sameAs URLs per entity (default: 3).
            - max_sources_per_domain: Max external requests to same domain (default: 2).
            - allow_local: Allow loopback/local targets (default: False, used for unit tests).

    Returns:
        JSON-serializable dictionary with summary, sources, identity_checks, claim_checks, and observations.
    """
    opts = options or {}
    max_sources = int(opts.get("max_external_sources", DEFAULT_MAX_EXTERNAL_SOURCES))
    max_claims = int(opts.get("max_claims", DEFAULT_MAX_CLAIMS))
    timeout_ms = int(opts.get("per_request_timeout_ms", DEFAULT_PER_REQUEST_TIMEOUT_MS))
    total_budget_ms = int(opts.get("total_budget_ms", DEFAULT_TOTAL_BUDGET_MS))
    max_bytes = int(opts.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES))
    max_redirects = int(opts.get("max_redirects", DEFAULT_MAX_REDIRECTS))
    max_sameas_per_entity = int(opts.get("max_sameas_urls_per_entity", DEFAULT_MAX_SAMEAS_URLS_PER_ENTITY))
    max_per_domain = int(opts.get("max_sources_per_domain", DEFAULT_MAX_SOURCES_PER_DOMAIN))
    user_agent = str(opts.get("user_agent", DEFAULT_USER_AGENT))
    allow_local = bool(opts.get("allow_local", False))

    start_time = time.monotonic()

    # Step 1: Collect Candidate URLs to corroborate from Entities
    candidate_urls: list[tuple[str, str, dict[str, Any]]] = []  # (url, entity_id, entity_dict)
    seen_urls: set[str] = set()

    for ent in entities if isinstance(entities, list) else []:
        if not isinstance(ent, dict):
            continue
        ent_id = ent.get("internal_id") or ent.get("id") or "unknown_entity"

        sameas_list = ent.get("sameAs", [])
        extracted_sameas: list[str] = []

        if isinstance(sameas_list, list):
            for s in sameas_list:
                val = s.get("value") if isinstance(s, dict) else s
                if isinstance(val, str) and val.strip():
                    extracted_sameas.append(val.strip())

        for url_str in extracted_sameas[:max_sameas_per_entity]:
            if url_str not in seen_urls:
                seen_urls.add(url_str)
                candidate_urls.append((url_str, ent_id, ent))

    sources_considered = len(candidate_urls)
    logger.info("Found %d candidate corroboration URL(s)", sources_considered)

    sources_fetched = 0
    sources_successful = 0
    sources_failed = 0

    claims_checked = 0
    claims_supported = 0
    claims_disagreed = 0

    identity_checks_count = 0
    identity_matches_count = 0

    fetched_sources_map: dict[str, dict[str, Any]] = {}
    domain_counts: dict[str, int] = {}

    sources_list: list[dict[str, Any]] = []
    identity_checks_list: list[dict[str, Any]] = []
    claim_checks_list: list[dict[str, Any]] = []
    observations_list: list[dict[str, Any]] = []

    # Step 2: Bounded Fetch of Selected Sources
    for req_url, ent_id, ent in candidate_urls:
        if len(sources_list) >= max_sources:
            break

        elapsed_ms = (time.monotonic() - start_time) * 1000
        if elapsed_ms >= total_budget_ms:
            logger.warning("Total budget of %d ms exhausted during corroboration.", total_budget_ms)
            observations_list.append(
                {
                    "code": "corroboration_budget_exhausted",
                    "elapsed_ms": int(elapsed_ms),
                    "total_budget_ms": total_budget_ms,
                }
            )
            break

        # Check domain limit
        is_valid, _, parsed = _validate_external_url(req_url)
        if not is_valid or parsed is None or not parsed.hostname:
            identity_checks_count += 1
            identity_checks_list.append(
                {
                    "entity_id": ent_id,
                    "reference_url": req_url,
                    "final_url": None,
                    "status": "invalid_reference",
                    "signals": [],
                }
            )
            continue

        domain = parsed.hostname.lower()
        if domain_counts.get(domain, 0) >= max_per_domain:
            continue

        # Execute fetch
        sources_fetched += 1
        remaining_timeout = min(timeout_ms, max(500, int(total_budget_ms - elapsed_ms)))

        fetch_res = _fetch_external_url(
            url=req_url,
            timeout_ms=remaining_timeout,
            max_redirects=max_redirects,
            max_response_bytes=max_bytes,
            user_agent=user_agent,
            allow_local=allow_local,
        )

        domain_counts[domain] = domain_counts.get(domain, 0) + 1

        final_url = fetch_res["final_url"]
        status_code = fetch_res["status_code"]
        fetch_status = fetch_res["fetch_status"]

        evidence_dict: dict[str, Any] = {
            "title": None,
            "canonical_url": None,
            "entity_names": [],
            "entity_types": [],
            "temporal_values": [],
        }

        if fetch_status == "success" and status_code and 200 <= status_code < 400:
            sources_successful += 1
            # Check content type is HTML or JSON
            c_type = fetch_res["content_type"] or ""
            if "html" in c_type or "xml" in c_type or "json" in c_type:
                try:
                    html_str = fetch_res["body_bytes"].decode("utf-8", errors="replace")
                    evidence_dict = _extract_evidence_from_html(html_str)
                except Exception:
                    pass
            else:
                fetch_status = "unsupported_content_type"
        else:
            sources_failed += 1

        source_record = {
            "requested_url": req_url,
            "final_url": final_url,
            "domain": domain,
            "source_role": "external",
            "fetch_status": fetch_status,
            "status_code": status_code,
            "content_type": fetch_res["content_type"],
            "response_size_bytes": fetch_res["response_size_bytes"],
            "response_time_ms": fetch_res["response_time_ms"],
            "redirect_count": fetch_res["redirect_count"],
            "evidence": evidence_dict,
        }
        if fetch_res.get("error"):
            source_record["error"] = fetch_res["error"]

        sources_list.append(source_record)
        fetched_sources_map[req_url] = source_record

        # Step 3: Identity Matching Check
        identity_checks_count += 1
        signals: list[str] = []
        id_match_status = "unreachable"

        if fetch_status == "success" and status_code == 200:
            target_name = ent.get("name")
            target_types = ent.get("types") or ([ent.get("type")] if ent.get("type") else [])
            norm_target_name = _normalize_string(target_name)

            has_name_match = False
            has_type_match = False
            has_canonical_match = False

            # Check canonical URL
            if evidence_dict.get("canonical_url"):
                can_norm = evidence_dict["canonical_url"].rstrip("/")
                req_norm = (final_url or req_url).rstrip("/")
                if can_norm == req_norm:
                    has_canonical_match = True
                    signals.append("canonical_url_match")

            # Check name matching against title, entity_names
            names_to_check = list(evidence_dict.get("entity_names", []))
            if evidence_dict.get("title"):
                names_to_check.append(evidence_dict["title"])

            if norm_target_name:
                for cand in names_to_check:
                    norm_cand = _normalize_string(cand)
                    if norm_target_name == norm_cand or (
                        len(norm_target_name) > 3 and norm_target_name in norm_cand
                    ):
                        has_name_match = True
                        signals.append("name_match")
                        break

            # Check type matching
            ext_types = [t.lower() for t in evidence_dict.get("entity_types", [])]
            for tt in target_types:
                if tt and tt.lower() in ext_types:
                    has_type_match = True
                    signals.append("type_compatible")
                    break

            if has_name_match and (has_type_match or has_canonical_match):
                id_match_status = "strong_identity_match"
                identity_matches_count += 1
            elif has_name_match:
                id_match_status = "probable_identity_match"
                identity_matches_count += 1
            elif has_canonical_match or names_to_check:
                id_match_status = "weak_identity_match"
            else:
                id_match_status = "reachable_no_identity_evidence"

        identity_checks_list.append(
            {
                "entity_id": ent_id,
                "reference_url": req_url,
                "final_url": final_url,
                "status": id_match_status,
                "signals": signals,
            }
        )

        # Step 4: Temporal Claim Corroboration
        if len(claim_checks_list) < max_claims:
            target_temporal = ent.get("temporal", {})
            target_pub = ent.get("datePublished") or target_temporal.get("datePublished", {}).get("normalized")
            target_mod = ent.get("dateModified") or target_temporal.get("dateModified", {}).get("normalized")

            ext_dates = evidence_dict.get("temporal_values", [])
            for claim_field, target_val in (("datePublished", target_pub), ("dateModified", target_mod)):
                if target_val and len(claim_checks_list) < max_claims:
                    claims_checked += 1
                    claim_res = "insufficient_evidence"

                    if ext_dates:
                        # Compare normalized date prefixes (YYYY-MM-DD)
                        t_prefix = str(target_val)[:10]
                        matched = any(str(d)[:10] == t_prefix for d in ext_dates)
                        if matched:
                            claim_res = "supported"
                            claims_supported += 1
                        else:
                            claim_res = "disagreement"
                            claims_disagreed += 1
                            observations_list.append(
                                {
                                    "code": "external_temporal_disagreement",
                                    "entity_id": ent_id,
                                    "claim_type": claim_field,
                                    "target_value": target_val,
                                    "external_values": ext_dates,
                                    "source_url": req_url,
                                }
                            )

                    claim_checks_list.append(
                        {
                            "entity_id": ent_id,
                            "claim_type": claim_field,
                            "target_value": target_val,
                            "external_values": ext_dates,
                            "result": claim_res,
                            "source_url": req_url,
                            "evidence": {
                                "source_field": claim_field,
                            },
                        }
                    )

    return {
        "component": "external_corroborator",
        "status": "success",
        "summary": {
            "sources_considered": sources_considered,
            "sources_fetched": sources_fetched,
            "sources_successful": sources_successful,
            "sources_failed": sources_failed,
            "claims_checked": claims_checked,
            "claims_supported": claims_supported,
            "claims_disagreed": claims_disagreed,
            "identity_checks": identity_checks_count,
            "identity_matches": identity_matches_count,
        },
        "sources": sources_list,
        "identity_checks": identity_checks_list,
        "claim_checks": claim_checks_list,
        "observations": observations_list,
        "errors": [],
    }


def main() -> int:
    """Command-line interface to execute bounded external corroboration from input JSON."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write("Usage: python external_corroborator.py <input.json>\n")
        return 1

    file_path = sys.argv[1]
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Error reading JSON file '{file_path}': {exc}\n")
        return 1

    target_url = data.get("target_url", "")
    entities = data.get("entities", [])
    freshness = data.get("freshness", {})
    options = data.get("options", {})

    result = corroborate(target_url, entities, freshness, options)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
