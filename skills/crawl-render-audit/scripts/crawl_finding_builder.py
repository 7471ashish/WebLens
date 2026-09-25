"""
Finding Builder Module (finding_builder.py)
-------------------------------------------
Deterministic evaluation and transformation layer that converts factual observations
from the crawl/render audit pipeline into normalized, evidence-based audit findings.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            +---- crawler.py (HTTP baseline)
            |
            +---- robots_checker.py (Robots directives)
            |
            +---- sitemap_checker.py (Sitemap health)
            |
            +---- raw_html_analyzer.py (Pre-JS facts)
            |
            +---- render_analyzer.py (Post-JS facts)
            |
            +---- dom_comparator.py (Differential facts)
            |
            +---- finding_builder.py  <-- (THIS MODULE)
                    |
                    v
            normalized findings (ID, severity, evidence, suggested actions)

This module is strictly a decision and normalization layer. It performs no network
requests, browser automation, HTML parsing, or LLM invocations.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Mapping

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.finding_builder")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default configurable thresholds
DEFAULT_OPTIONS: dict[str, Any] = {
    "redirect_medium_threshold": 3,
    "redirect_high_threshold": 5,
    "raw_text_minimum_chars": 500,
    "render_text_increase_ratio_medium": 2.0,
    "render_text_increase_ratio_high": 10.0,
    "duplicate_sitemap_url_threshold": 10,
}

# Severity priority ranking for deterministic ordering
SEVERITY_ORDER: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "info": 5,
}

VALID_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
VALID_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")



# Helper Functions: Finding Creation & Validation


def _make_evidence(
    source: str,
    field: str,
    value: Any,
    details: str,
) -> dict[str, Any]:
    """Create a standardized evidence entry."""
    return {
        "source": source,
        "field": field,
        "value": value,
        "details": details,
    }


def _validate_and_normalize_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
    """Validate that finding satisfies the schema contract."""
    required_fields = (
        "id",
        "category",
        "title",
        "description",
        "severity",
        "affected_url",
        "evidence",
        "suggested_action",
        "confidence",
    )
    for rf in required_fields:
        if rf not in finding:
            logger.error("Finding missing required field '%s': %s", rf, finding)
            return None

    sev = str(finding["severity"]).lower()
    if sev not in VALID_SEVERITIES:
        sev = "medium"
    finding["severity"] = sev

    conf = str(finding["confidence"]).lower()
    if conf not in VALID_CONFIDENCES:
        conf = "medium"
    finding["confidence"] = conf

    if not isinstance(finding["evidence"], list):
        finding["evidence"] = [finding["evidence"]] if finding["evidence"] else []

    return finding



# Individual Rule Evaluators


def _evaluate_http(
    crawler_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate HTTP connectivity, status codes, and network errors."""
    findings: list[dict[str, Any]] = []
    if not crawler_res:
        return findings

    status_code = crawler_res.get("status_code")
    err_classification = crawler_res.get("error_classification") or (crawler_res.get("error") or {}).get("type")
    err_message = crawler_res.get("error_message") or (crawler_res.get("error") or {}).get("message")
    affected = crawler_res.get("final_url") or crawler_res.get("requested_url") or target_url

    # 1. HTTP 404 Not Found
    if status_code == 404:
        findings.append({
            "id": "HTTP-404",
            "category": "http",
            "title": "Target URL returned HTTP 404 Not Found",
            "description": f"The requested resource at {affected} does not exist on the server.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="crawler",
                field="status_code",
                value=404,
                details=f"Server returned HTTP status code 404 for {affected}",
            )],
            "suggested_action": "Verify the URL path and ensure the intended page or route exists.",
            "confidence": "high",
        })

    # 2. HTTP 403 Forbidden
    elif status_code == 403:
        findings.append({
            "id": "HTTP-403",
            "category": "http",
            "title": "Target URL returned HTTP 403 Forbidden",
            "description": f"The server refused access to {affected}, preventing automated crawlers from retrieving content.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="crawler",
                field="status_code",
                value=403,
                details=f"Server returned HTTP status code 403 Forbidden for {affected}",
            )],
            "suggested_action": "Check web server permissions, WAF settings, or bot blocking rules to permit crawler access.",
            "confidence": "high",
        })

    # 3. HTTP 5xx Server Error
    elif status_code is not None and 500 <= status_code <= 599:
        severity = "critical" if status_code == 503 else "high"
        findings.append({
            "id": f"HTTP-{status_code}",
            "category": "http",
            "title": f"Target URL returned server error HTTP {status_code}",
            "description": f"The web server encountered an internal error ({status_code}) while serving {affected}.",
            "severity": severity,
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="crawler",
                field="status_code",
                value=status_code,
                details=f"Server responded with HTTP {status_code} server error",
            )],
            "suggested_action": "Investigate web server and application backend logs to resolve server-side exceptions.",
            "confidence": "high",
        })

    # 4. Network, Timeout, DNS, and SSL Failures
    if err_classification:
        if err_classification == "timeout":
            findings.append({
                "id": "HTTP-TIMEOUT",
                "category": "http",
                "title": "HTTP request timed out connecting to target website",
                "description": f"The crawler timed out while attempting to reach {affected}.",
                "severity": "high",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="crawler",
                    field="error_classification",
                    value="timeout",
                    details=err_message or "Request timed out during connection or response transfer",
                )],
                "suggested_action": "Ensure the server responds within standard timeout limits and check network latency.",
                "confidence": "high",
            })
        elif err_classification in ("dns_failure", "dns_error"):
            findings.append({
                "id": "HTTP-DNS-FAIL",
                "category": "http",
                "title": "DNS resolution failed for target host",
                "description": f"The domain name for {affected} could not be resolved by DNS.",
                "severity": "critical",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="crawler",
                    field="error_classification",
                    value="dns_failure",
                    details=err_message or "DNS lookup failure for host",
                )],
                "suggested_action": "Verify domain DNS records (A/AAAA) and name server configurations.",
                "confidence": "high",
            })
        elif err_classification in ("connect_error", "connection_error", "connection_refused"):
            findings.append({
                "id": "HTTP-CONN-FAIL",
                "category": "http",
                "title": "Network connection failed or was refused",
                "description": f"Failed to establish network connection to {affected}.",
                "severity": "critical",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="crawler",
                    field="error_classification",
                    value="connection_error",
                    details=err_message or "Connection refused or host unreachable",
                )],
                "suggested_action": "Verify host availability, firewall routing, and web server listener.",
                "confidence": "high",
            })
        elif err_classification == "ssl_error":
            findings.append({
                "id": "HTTP-SSL-FAIL",
                "category": "http",
                "title": "SSL/TLS handshake failure",
                "description": f"The SSL certificate or handshake failed when connecting to {affected}.",
                "severity": "high",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="crawler",
                    field="error_classification",
                    value="ssl_error",
                    details=err_message or "SSL certificate verification or negotiation failed",
                )],
                "suggested_action": "Verify SSL certificate validity, hostname match, and intermediate certificate chain.",
                "confidence": "high",
            })
        elif err_classification == "redirect_loop":
            findings.append({
                "id": "HTTP-REDIRECT-LOOP",
                "category": "http",
                "title": "Redirect loop detected",
                "description": f"Target URL encountered a cyclical redirect chain preventing retrieval.",
                "severity": "high",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="crawler",
                    field="error_classification",
                    value="redirect_loop",
                    details=err_message or "Circular redirect loop encountered",
                )],
                "suggested_action": "Review server redirect rules to remove cyclical URL redirections.",
                "confidence": "high",
            })

    return findings


def _evaluate_redirects(
    crawler_res: dict[str, Any] | None,
    target_url: str,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate redirect chain length and complexity."""
    findings: list[dict[str, Any]] = []
    if not crawler_res:
        return findings

    redirect_count = crawler_res.get("redirect_count", 0)
    redirect_chain = crawler_res.get("redirect_chain", [])
    med_thresh = options.get("redirect_medium_threshold", DEFAULT_OPTIONS["redirect_medium_threshold"])
    high_thresh = options.get("redirect_high_threshold", DEFAULT_OPTIONS["redirect_high_threshold"])
    affected = crawler_res.get("requested_url") or target_url

    if redirect_count >= high_thresh:
        findings.append({
            "id": "HTTP-REDIRECT-HIGH",
            "category": "http",
            "title": f"Excessive redirect chain detected ({redirect_count} hops)",
            "description": f"Navigating to {affected} triggered {redirect_count} sequential redirects, exceeding recommended limits.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="crawler",
                field="redirect_count",
                value=redirect_count,
                details=f"Chain: {' -> '.join(redirect_chain[:6])}",
            )],
            "suggested_action": "Reduce redirect hops by linking directly to the canonical destination URL.",
            "confidence": "high",
        })
    elif redirect_count >= med_thresh:
        findings.append({
            "id": "HTTP-REDIRECT-MED",
            "category": "http",
            "title": f"Multiple redirect hops detected ({redirect_count} hops)",
            "description": f"Navigating to {affected} triggered {redirect_count} redirects.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="crawler",
                field="redirect_count",
                value=redirect_count,
                details=f"Chain: {' -> '.join(redirect_chain[:5])}",
            )],
            "suggested_action": "Minimize intermediate redirect hops to optimize crawler crawl budget.",
            "confidence": "high",
        })

    return findings


def _evaluate_robots(
    robots_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate robots.txt crawl permissions and syntax."""
    findings: list[dict[str, Any]] = []
    if not robots_res:
        return findings

    # A missing robots.txt (404) is NOT an error (RFC 9309 specifies all URLs are allowed)
    if robots_res.get("robots_txt_status") == "not_found" or robots_res.get("resource_state") in ("missing", "unavailable", "error"):
        return findings

    target_eval = robots_res.get("target_url_evaluation", {})
    allowed = target_eval.get("allowed")
    matched_rule = target_eval.get("matched_rule")
    user_agent = target_eval.get("user_agent")
    affected = robots_res.get("target_url") or target_url

    if allowed is False:
        findings.append({
            "id": "ROBOTS-DISALLOWED",
            "category": "robots",
            "title": f"Target page is blocked by robots.txt for User-agent '{user_agent}'",
            "description": f"Robots.txt explicitly disallows crawlers with user agent '{user_agent}' from crawling {affected}.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="robots_checker",
                field="crawl_permission",
                value="disallowed",
                details=f"Matched rule: {matched_rule or 'Disallow: /'} in {robots_res.get('robots_txt_url')}",
            )],
            "suggested_action": "Review robots.txt directives and remove disallow rules blocking intended public pages.",
            "confidence": "high",
        })

    return findings


def _evaluate_sitemap(
    sitemap_res: dict[str, Any] | None,
    target_url: str,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate sitemap validity, accessibility, and parse errors."""
    findings: list[dict[str, Any]] = []
    if not sitemap_res:
        return findings

    # Missing sitemap is not a finding
    sitemaps = sitemap_res.get("sitemaps", [])
    if not sitemaps:
        return findings

    dup_threshold = options.get("duplicate_sitemap_url_threshold", DEFAULT_OPTIONS["duplicate_sitemap_url_threshold"])
    affected = sitemap_res.get("target_url") or target_url

    for sm in sitemaps:
        sm_url = sm.get("url") or sm.get("requested_url") or sm.get("final_url")
        status = sm.get("status")
        errors = sm.get("errors", []) or ([sm.get("error")] if sm.get("error") else [])
        dup_count = sm.get("duplicate_urls_count", 0) or sm.get("duplicate_url_count", 0)

        if status in ("malformed", "invalid_xml", "invalid_gzip", "too_large") or sm.get("resource_state") == "invalid":
            findings.append({
                "id": "SITEMAP-MALFORMED",
                "category": "sitemap",
                "title": f"Malformed or invalid sitemap XML: {sm_url}",
                "description": f"The sitemap file at {sm_url} failed XML parsing or has invalid structure.",
                "severity": "medium",
                "affected_url": sm_url or affected,
                "evidence": [_make_evidence(
                    source="sitemap_checker",
                    field="status",
                    value=status or "invalid",
                    details=f"Parsing errors: {'; '.join(str(e) for e in errors[:2]) if errors else 'Invalid XML syntax / HTML content'}",
                )],
                "suggested_action": "Validate sitemap XML against the standard sitemaps.org XML schema.",
                "confidence": "high",
            })
        elif status == "http_error":
            findings.append({
                "id": "SITEMAP-INACCESSIBLE",
                "category": "sitemap",
                "title": f"Sitemap URL returned HTTP error: {sm_url}",
                "description": f"The declared sitemap at {sm_url} returned status code {sm.get('status_code')}.",
                "severity": "medium",
                "affected_url": sm_url or affected,
                "evidence": [_make_evidence(
                    source="sitemap_checker",
                    field="status_code",
                    value=sm.get("status_code"),
                    details=f"Sitemap {sm_url} is unreachable (HTTP {sm.get('status_code')})",
                )],
                "suggested_action": "Ensure declared sitemap files exist and return HTTP 200.",
                "confidence": "high",
            })

        if dup_count > dup_threshold:
            findings.append({
                "id": "SITEMAP-DUPLICATES",
                "category": "sitemap",
                "title": f"Excessive duplicate URLs in sitemap ({dup_count} duplicates)",
                "description": f"Sitemap {sm_url} contains {dup_count} duplicate <loc> entries.",
                "severity": "low",
                "affected_url": sm_url or affected,
                "evidence": [_make_evidence(
                    source="sitemap_checker",
                    field="duplicate_urls_count",
                    value=dup_count,
                    details=f"Found {dup_count} duplicate URL entries in {sm_url}",
                )],
                "suggested_action": "Deduplicate sitemap entries to ensure each unique page is listed once.",
                "confidence": "high",
            })

    return findings


def _evaluate_raw_vs_rendered(
    dom_comp_res: dict[str, Any] | None,
    raw_res: dict[str, Any] | None,
    rend_res: dict[str, Any] | None,
    target_url: str,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate DOM comparison metrics and JavaScript rendering dependency."""
    findings: list[dict[str, Any]] = []
    if not dom_comp_res:
        return findings

    dep = dom_comp_res.get("rendering_dependency", {})
    text_comp = dom_comp_res.get("text_content", {})
    affected = dom_comp_res.get("navigation", {}).get("rendered_final_url") or target_url

    raw_chars = text_comp.get("raw_characters", 0) or 0
    rend_chars = text_comp.get("rendered_characters", 0) or 0
    diff_chars = text_comp.get("difference", 0) or 0
    ratio = text_comp.get("rendered_to_raw_ratio")

    high_ratio = options.get("render_text_increase_ratio_high", DEFAULT_OPTIONS["render_text_increase_ratio_high"])
    med_ratio = options.get("render_text_increase_ratio_medium", DEFAULT_OPTIONS["render_text_increase_ratio_medium"])
    min_raw_chars = options.get("raw_text_minimum_chars", DEFAULT_OPTIONS["raw_text_minimum_chars"])

    # 1. Extreme Rendering Dependency (High Severity)
    # Raw HTML has almost no content while rendered DOM contains substantial content
    if dep.get("content_available_only_after_render") or (
        raw_chars < min_raw_chars and rend_chars >= min_raw_chars and (ratio is None or ratio >= high_ratio)
    ):
        findings.append({
            "id": "RENDER-GAP-HIGH",
            "category": "rendering",
            "title": "Severe content dependency on client-side JavaScript rendering",
            "description": f"Raw HTML returned from server contains minimal text ({raw_chars} chars), but rendered DOM reveals {rend_chars} chars. Automated crawlers lacking JS execution cannot read main page content.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [
                _make_evidence(
                    source="dom_comparator",
                    field="text_content",
                    value={"raw_characters": raw_chars, "rendered_characters": rend_chars, "difference": diff_chars},
                    details=f"Raw HTML text: {raw_chars} characters, Rendered DOM text: {rend_chars} characters",
                ),
            ],
            "suggested_action": "Implement Server-Side Rendering (SSR) or Static Site Generation (SSG) to include main content in raw HTML.",
            "confidence": "high",
        })

    # 2. Moderate Rendering Dependency (Medium Severity)
    elif ratio is not None and ratio >= med_ratio and diff_chars >= 500:
        findings.append({
            "id": "RENDER-GAP-MED",
            "category": "rendering",
            "title": "Important content generated dynamically by client-side JavaScript",
            "description": f"Rendered text ({rend_chars} chars) is {ratio:.1f}x larger than raw HTML text ({raw_chars} chars). Some content is unavailable before JavaScript execution.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [
                _make_evidence(
                    source="dom_comparator",
                    field="text_content",
                    value={"raw_characters": raw_chars, "rendered_characters": rend_chars, "ratio": ratio},
                    details=f"Rendered text is {ratio:.1f}x larger than raw text ({diff_chars} chars added post-render)",
                ),
            ],
            "suggested_action": "Render key textual content in the initial HTML payload to ensure immediate accessibility.",
            "confidence": "medium",
        })

    # 3. Empty Content Page (Medium Severity)
    # Page completes rendering but content remains empty/sparse
    if dep.get("rendering_did_not_materially_increase_content") and rend_chars < 150:
        findings.append({
            "id": "RENDER-EMPTY-PAGE",
            "category": "rendering",
            "title": "Page contains little to no visible content after rendering",
            "description": f"The page completed rendering but contains only {rend_chars} characters of visible body text.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [
                _make_evidence(
                    source="render_analyzer",
                    field="visible_character_count",
                    value=rend_chars,
                    details=f"Only {rend_chars} visible text characters present post-hydration",
                ),
            ],
            "suggested_action": "Ensure the page renders populated content and check for client-side routing or data-fetching errors.",
            "confidence": "high",
        })

    return findings


def _evaluate_rendering_runtime(
    rendered_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate browser rendering runtime errors and failures."""
    findings: list[dict[str, Any]] = []
    if not rendered_res:
        return findings

    rend = rendered_res.get("rendering", {})
    status = rend.get("status")
    raw_affected = rendered_res.get("navigation", {}).get("final_url") or target_url
    affected = re.split(r"[\r\n]+", str(raw_affected))[0].strip()
    affected = re.split(r"\s*(?:Call log|Call:|Page\.goto)\b", affected, flags=re.IGNORECASE)[0].strip()

    # Infrastructure/tooling launch failures (e.g. missing Chromium binary) must not produce site-side findings
    if status == "browser_launch_failure":
        return findings

    if status in ("browser_error", "environment_error"):
        errors = rendered_res.get("errors", [])
        err_msg = str(errors[0].get("message", "Browser failed to complete rendering")) if errors else "Browser failed to complete rendering"
        clean_err = re.split(r"[\r\n]+", err_msg)[0].strip()
        clean_err = re.split(r"\s*(?:Call log|Call:)\b", clean_err, flags=re.IGNORECASE)[0].strip()

        is_env = (status == "environment_error") or any(
            hint in clean_err.lower()
            for hint in (
                "err_http2_protocol_error",
                "net::err_",
                "err_connection",
                "err_name_not_resolved",
                "target closed",
                "connection reset",
                "certificate_verify_failed",
                "protocol error",
                "playwrighttimeouterror",
            )
        )

        if is_env:
            findings.append({
                "id": "RENDER-EXEC-ERROR",
                "category": "rendering",
                "title": "Browser rendering environment/network diagnostic",
                "description": f"Browser navigation encountered an environment/network condition while loading {affected}.",
                "severity": "low",
                "classification": "environment",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="render_analyzer",
                    field="rendering_status",
                    value="environment_error",
                    details=f"Environment network condition: {clean_err[:150]}",
                )],
                "suggested_action": "Re-run browser rendering in a compatible network/browser environment before treating this as a site defect.",
                "confidence": "high",
            })
        else:
            findings.append({
                "id": "RENDER-EXEC-ERROR",
                "category": "rendering",
                "title": "Browser rendering execution failure",
                "description": f"Headless browser encountered an execution failure while rendering {affected}.",
                "severity": "medium",
                "classification": "rendering",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="render_analyzer",
                    field="rendering_status",
                    value="browser_error",
                    details=clean_err[:150],
                )],
                "suggested_action": "Inspect client-side script errors and verify page compatibility with modern browser engines.",
                "confidence": "medium",
            })

    js_info = rendered_res.get("javascript", {})
    js_err_count = js_info.get("error_count", 0)
    if js_err_count > 0:
        js_errors = js_info.get("errors", [])
        sample_msg = js_errors[0].get("message") if js_errors else "Uncaught JavaScript error"
        findings.append({
            "id": "RENDER-JS-EXCEPTION",
            "category": "rendering",
            "title": f"Unhandled JavaScript runtime error detected ({js_err_count} errors)",
            "description": f"Client-side scripts threw {js_err_count} unhandled exceptions during page rendering.",
            "severity": "low",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="render_analyzer",
                field="javascript_errors",
                value=js_err_count,
                details=f"Sample error: {sample_msg[:150]}",
            )],
            "suggested_action": "Fix JavaScript runtime errors in browser console to prevent broken client-side interactions.",
            "confidence": "high",
        })

    return findings


def _evaluate_canonical(
    raw_res: dict[str, Any] | None,
    rend_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate canonical link tags for invalid syntax or conflicts."""
    findings: list[dict[str, Any]] = []
    if not raw_res and not rend_res:
        return findings

    raw_can = (raw_res or {}).get("canonical", {})
    rend_can = (rend_res or {}).get("canonical", {})
    affected = target_url

    # Check for invalid canonical syntax in raw or rendered
    if raw_can.get("exists") and raw_can.get("is_valid") is False:
        findings.append({
            "id": "CANONICAL-INVALID",
            "category": "canonical",
            "title": "Invalid canonical URL tag",
            "description": f"The canonical link href '{raw_can.get('value')}' is not a valid absolute or relative URL.",
            "severity": "high",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="canonical",
                value=raw_can.get("value"),
                details=f"Invalid canonical URL: {raw_can.get('value')}",
            )],
            "suggested_action": "Provide a valid absolute URL for <link rel='canonical'>.",
            "confidence": "high",
        })

    # Check for multiple duplicate canonical tags
    if raw_can.get("duplicate_count", 0) > 0:
        findings.append({
            "id": "CANONICAL-MULTIPLE",
            "category": "canonical",
            "title": "Multiple canonical URL tags defined in HTML head",
            "description": "The page declares more than one canonical link tag, causing ambiguity for search engines.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="duplicate_canonical_count",
                value=raw_can.get("duplicate_count", 0) + 1,
                details=f"Found {raw_can.get('duplicate_count', 0) + 1} canonical link tags",
            )],
            "suggested_action": "Specify exactly one canonical URL per page.",
            "confidence": "high",
        })

    # Check for conflict between raw and rendered canonical
    raw_val = raw_can.get("value")
    rend_val = rend_can.get("value")
    if raw_val and rend_val and raw_val != rend_val:
        findings.append({
            "id": "CANONICAL-CONFLICT",
            "category": "canonical",
            "title": "Conflicting canonical URLs between raw HTML and rendered DOM",
            "description": f"Raw HTML specifies canonical '{raw_val}' while JavaScript modified it to '{rend_val}'.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="dom_comparator",
                field="canonical",
                value={"raw": raw_val, "rendered": rend_val},
                details=f"Raw canonical '{raw_val}' != Rendered canonical '{rend_val}'",
            )],
            "suggested_action": "Align raw and rendered canonical tags to avoid conflicting crawl signals.",
            "confidence": "high",
        })

    return findings


def _evaluate_metadata(
    raw_res: dict[str, Any] | None,
    rend_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate title and meta description tags."""
    findings: list[dict[str, Any]] = []
    if not raw_res and not rend_res:
        return findings

    raw = raw_res or {}
    rend = rend_res or {}
    affected = target_url

    raw_title = raw.get("title")
    rend_title = rend.get("title")
    if raw_title is None and rend_title is None:
        return findings

    # Missing title in both raw and rendered
    if (raw_title or {}).get("exists") is False and (rend_title or {}).get("exists") is False:
        findings.append({
            "id": "META-TITLE-MISSING",
            "category": "metadata",
            "title": "Missing document <title> tag",
            "description": "The page does not declare a <title> element in the document head.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="title_exists",
                value=False,
                details="No <title> tag found in raw HTML or rendered DOM",
            )],
            "suggested_action": "Add a descriptive, unique <title> tag inside the <head> element.",
            "confidence": "high",
        })

    # Missing meta description
    raw_desc = raw.get("meta_description") or {}
    rend_desc = rend.get("meta_description") or {}
    if raw_desc.get("exists") is False and rend_desc.get("exists") is False:
        findings.append({
            "id": "META-DESC-MISSING",
            "category": "metadata",
            "title": "Missing meta description tag",
            "description": "The page does not declare a <meta name='description'> tag.",
            "severity": "low",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="meta_description_exists",
                value=False,
                details="No meta description found in HTML",
            )],
            "suggested_action": "Add a compelling <meta name='description' content='...'> tag summarizing the page.",
            "confidence": "high",
        })

    return findings


def _evaluate_semantic_structure(
    raw_res: dict[str, Any] | None,
    rend_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate headings hierarchy and semantic structural containers."""
    findings: list[dict[str, Any]] = []
    if not raw_res and not rend_res:
        return findings

    raw = raw_res or {}
    rend = rend_res or {}
    affected = target_url

    raw_h = raw.get("headings")
    rend_h = rend.get("headings")
    if raw_h is None and rend_h is None:
        return findings

    raw_h = raw_h or {}
    rend_h = rend_h or {}

    # Zero H1 headings across both raw and rendered
    raw_h1_count = raw_h.get("h1_count", 0) or 0
    rend_h1_count = rend_h.get("h1_count", 0) or 0
    if raw_h1_count == 0 and rend_h1_count == 0:
        findings.append({
            "id": "SEMANTIC-NO-H1",
            "category": "semantic",
            "title": "Missing primary heading (<h1>)",
            "description": "The page lacks a top-level <h1> heading to identify the primary subject of the document.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="h1_count",
                value=0,
                details="0 <h1> headings found in document",
            )],
            "suggested_action": "Include exactly one clear, descriptive <h1> heading on each page.",
            "confidence": "high",
        })

    # Multiple H1 headings
    if rend_h1_count > 1 or raw_h1_count > 1:
        count = rend_h1_count or raw_h1_count
        findings.append({
            "id": "SEMANTIC-MULTIPLE-H1",
            "category": "semantic",
            "title": f"Multiple <h1> headings detected ({count} tags)",
            "description": f"The document contains {count} <h1> headings, which may dilute topical focus for automated crawlers.",
            "severity": "low",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="render_analyzer",
                field="h1_count",
                value=count,
                details=f"Found {count} <h1> tags: {', '.join(rend_h.get('h1_text', [])[:3])}",
            )],
            "suggested_action": "Reserve <h1> for the single main page topic and use <h2>-<h6> for sub-sections.",
            "confidence": "high",
        })

    return findings


def _evaluate_machine_readability(
    raw_res: dict[str, Any] | None,
    rend_res: dict[str, Any] | None,
    dom_comp_res: dict[str, Any] | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Evaluate structured data JSON-LD and machine-readable semantics."""
    findings: list[dict[str, Any]] = []
    if not raw_res and not rend_res and not dom_comp_res:
        return findings

    raw = raw_res or {}
    rend = rend_res or {}
    affected = target_url

    raw_sd = raw.get("structured_data", {})
    rend_sd = rend.get("structured_data", {})

    # Invalid JSON-LD block syntax
    inv_raw = raw_sd.get("invalid_json_ld_blocks", 0) or 0
    inv_rend = rend_sd.get("invalid_json_ld_blocks", 0) or 0
    if inv_raw > 0 or inv_rend > 0:
        count = max(inv_raw, inv_rend)
        findings.append({
            "id": "MACHINE-JSONLD-INVALID",
            "category": "structured_data",
            "title": f"Malformed JSON-LD structured data syntax ({count} blocks)",
            "description": f"Encountered {count} <script type='application/ld+json'> block(s) containing invalid JSON syntax.",
            "severity": "medium",
            "affected_url": affected,
            "evidence": [_make_evidence(
                source="raw_html_analyzer",
                field="invalid_json_ld_blocks",
                value=count,
                details=f"{count} structured data blocks failed JSON decoding",
            )],
            "suggested_action": "Validate JSON-LD syntax with schema.org validator and fix JSON escaping/formatting errors.",
            "confidence": "high",
        })

    # Structured data only present after client rendering
    if dom_comp_res:
        sd_comp = dom_comp_res.get("structured_data", {})
        if sd_comp.get("json_ld_added_after_render"):
            types = sd_comp.get("types_added_after_render", [])
            findings.append({
                "id": "MACHINE-JSONLD-JS-ONLY",
                "category": "structured_data",
                "title": "Structured data (JSON-LD) injected only after JavaScript execution",
                "description": f"Structured data schema types ({', '.join(types[:3])}) were absent in raw HTML and added via JS.",
                "severity": "low",
                "affected_url": affected,
                "evidence": [_make_evidence(
                    source="dom_comparator",
                    field="types_added_after_render",
                    value=types,
                    details=f"Types added post-hydration: {', '.join(types)}",
                )],
                "suggested_action": "Embed JSON-LD schemas directly into the initial server HTML response.",
                "confidence": "high",
            })

    return findings



# Main Builder Function


def build_findings_qualitative_llm(
    audit_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Qualitatively evaluate crawl & render architecture using LLM reasoning (sync fallback)."""
    opts = options or {}
    client = opts.get("llm_client")
    if client is None:
        import os, sys
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate_root in [
            os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..")),
        ]:
            if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                sys.path.insert(0, candidate_root)
        try:
            from llm_client import LLMClient
            client = LLMClient()
        except Exception:
            return []

    crawler_res = audit_results.get("crawler") or {}
    robots_res = audit_results.get("robots") or {}
    sitemap_res = audit_results.get("sitemap") or {}
    raw_res = audit_results.get("raw_html") or {}
    rend_res = audit_results.get("rendered") or {}
    dom_comp = audit_results.get("dom_comparison") or {}

    prompt = f"""
Audit Target URL: {crawler_res.get('final_url') or crawler_res.get('requested_url')}
HTTP Status: {crawler_res.get('status_code')}
Redirects: {crawler_res.get('redirect_count', 0)} redirects (chain: {crawler_res.get('redirect_chain', [])})
Robots.txt status: {robots_res.get('status', 'unknown')}, allowed: {robots_res.get('target_url_evaluation', {}).get('allowed')}
Sitemaps found: {len(sitemap_res.get('sitemaps', []))}
Pre-JS Raw HTML Chars: {raw_res.get('text_content', {}).get('character_count', 0)}
Post-JS Rendered Chars: {rend_res.get('text_content', {}).get('visible_character_count', 0)}
Hydration Ratio: {dom_comp.get('text_content', {}).get('rendered_to_raw_ratio', 'N/A')}

Perform a qualitative, non-mathematical audit of this website's technical crawlability, indexing, and rendering architecture.
Identify architectural defects, crawler friction, and indexing risks.
Return JSON with 'findings' array containing objects with:
id (prefixed with 'CR-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""
    result = client._generate_qualitative_fallback(prompt)
    raw_list = (result or {}).get("findings", [])
    return _process_llm_crawl_findings(raw_list, crawler_res, robots_res, sitemap_res, raw_res=raw_res, rend_res=rend_res, dom_comp=dom_comp)


async def build_findings_qualitative_llm_async(
    audit_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Qualitatively evaluate crawl & render architecture using LLM reasoning (async)."""
    opts = options or {}
    client = opts.get("llm_client")
    if client is None:
        import os, sys
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate_root in [
            os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..")),
        ]:
            if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                sys.path.insert(0, candidate_root)
        try:
            from llm_client import LLMClient
            client = LLMClient()
        except Exception:
            return []

    crawler_res = audit_results.get("crawler") or {}
    robots_res = audit_results.get("robots") or {}
    sitemap_res = audit_results.get("sitemap") or {}
    raw_res = audit_results.get("raw_html") or {}
    rend_res = audit_results.get("rendered") or {}
    dom_comp = audit_results.get("dom_comparison") or {}

    prompt = f"""
Audit Target URL: {crawler_res.get('final_url') or crawler_res.get('requested_url')}
HTTP Status: {crawler_res.get('status_code')}
Redirects: {crawler_res.get('redirect_count', 0)} redirects (chain: {crawler_res.get('redirect_chain', [])})
Robots.txt status: {robots_res.get('status', 'unknown')}, allowed: {robots_res.get('target_url_evaluation', {}).get('allowed')}
Sitemaps found: {len(sitemap_res.get('sitemaps', []))}
Pre-JS Raw HTML Chars: {raw_res.get('text_content', {}).get('character_count', 0)}
Post-JS Rendered Chars: {rend_res.get('text_content', {}).get('visible_character_count', 0)}
Hydration Ratio: {dom_comp.get('text_content', {}).get('rendered_to_raw_ratio', 'N/A')}

Perform a qualitative, non-mathematical audit of this website's technical crawlability, indexing, and rendering architecture.
Identify architectural defects, crawler friction, and indexing risks.
Return JSON with 'findings' array containing objects with:
id (prefixed with 'CR-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""
    try:
        result = await client.query_json(prompt)
        raw_list = (result or {}).get("findings", [])
    except Exception as e:
        logger.warning("Qualitative LLM crawl audit fallback: %s", e)
        raw_list = []

    return _process_llm_crawl_findings(raw_list, crawler_res, robots_res, sitemap_res, raw_res=raw_res, rend_res=rend_res, dom_comp=dom_comp)


def _process_llm_crawl_findings(
    raw_list: list[Any],
    crawler_res: dict[str, Any],
    robots_res: dict[str, Any],
    sitemap_res: dict[str, Any],
    raw_res: dict[str, Any] | None = None,
    rend_res: dict[str, Any] | None = None,
    dom_comp: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_res = raw_res or {}
    rend_res = rend_res or {}
    dom_comp = dom_comp or {}


    validated = []
    target_final_url = crawler_res.get('final_url') or crawler_res.get('requested_url') or ""
    crawler_err = crawler_res.get("error_classification")
    crawler_status_code = crawler_res.get("status_code")
    crawler_state = crawler_res.get("resource_state")

    # If the page crawl itself failed at network/transport level or returned HTTP error,
    # return only the truthful HTTP/transport finding without evaluating page content.
    if crawler_err in ("dns_failure", "dns_error", "timeout", "connection_error", "ssl_error") or (crawler_status_code and crawler_status_code >= 400) or crawler_state in ("unavailable", "missing", "error"):
        return _evaluate_http(crawler_res, target_final_url)

    redirect_count = int(crawler_res.get('redirect_count') or 0)
    robots_info = robots_res.get('robots_txt') or {}
    robots_res_state = robots_res.get('resource_state') or robots_info.get('resource_state')
    robots_raw_status = robots_info.get('status') or robots_res.get('status')
    robots_status = str(robots_raw_status or '')
    robots_found = bool(robots_info.get('found', False) or robots_status == '200' or robots_res_state == 'present')
    robots_url = robots_info.get('url') or robots_res.get('robots_url') or f"{target_final_url.rstrip('/')}/robots.txt"
    robots_unavailable = robots_res_state in ("unavailable", "error") or robots_status in ("unavailable", "error")
    robots_missing = (robots_res_state == "missing") or (robots_status == "404" and not robots_found)

    sitemap_summary = sitemap_res.get('summary') or {}
    sitemap_res_state = sitemap_res.get('resource_state') or sitemap_summary.get('resource_state')
    sitemaps_successful = int(sitemap_summary.get('sitemaps_successful', 0) or 0)
    sitemap_found = bool(sitemap_summary.get('sitemap_found', False) or sitemaps_successful > 0 or sitemap_res_state == 'present')
    sitemaps_list = sitemap_res.get('sitemaps') or []
    sitemap_unavailable = sitemap_res_state in ("unavailable", "error") or sitemap_summary.get("resource_state") in ("unavailable", "error")

    http_reasons = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        410: "Gone",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }

    # Resolve actual sitemap HTTP status from fetch response
    sitemap_checked_url = f"{target_final_url.rstrip('/')}/sitemap.xml"
    sitemap_code = None
    for sm in sitemaps_list:
        if isinstance(sm, dict):
            req_u = sm.get("requested_url") or sm.get("final_url") or ""
            if "sitemap.xml" in req_u or not sitemap_code:
                if sm.get("status_code"):
                    sitemap_code = sm.get("status_code")
    if not sitemap_code:
        sitemap_code = sitemap_res.get("status_code")
    if not sitemap_code and sitemap_res_state == "missing":
        sitemap_code = 404

    sitemap_missing = (sitemap_res_state == "missing") or (sitemap_code == 404 and not sitemap_found)

    try:
        s_code_int = int(sitemap_code)
        s_reason = http_reasons.get(s_code_int, "")
        sitemap_status_str = f"HTTP {s_code_int} {s_reason}".strip() if s_reason else f"HTTP {s_code_int}"
    except (ValueError, TypeError):
        sitemap_status_str = f"HTTP {sitemap_code}" if sitemap_code else "HTTP 404"

    # Resolve actual robots HTTP status from fetch response
    try:
        r_code_int = int(robots_status)
        r_reason = http_reasons.get(r_code_int, "")
        robots_status_str = f"HTTP {r_code_int} {r_reason}".strip() if r_reason else f"HTTP {r_code_int}"
    except (ValueError, TypeError):
        robots_status_str = f"HTTP {robots_status}" if robots_status else "HTTP 404"

    for rf in raw_list:
        fid = rf.get("id", "CR-GEN-001")
        title = rf.get("title", "")
        ev = rf.get("evidence", "")

        # Grounding check - if redirect count <= 1, DROP any redirect chain finding!
        if ("redirect" in fid.lower() or "redirect" in title.lower() or "redirect" in ev.lower()) and redirect_count <= 1:
            logger.info("Dropping hallucinated redirect chain finding: redirect_count=%d", redirect_count)
            continue

        # For checklist categories, gate strictly on observation and resource state
        if "sitemap" in fid.lower() or "sitemap" in title.lower():
            if sitemap_found or sitemap_unavailable or not sitemap_missing:
                continue
            ev = f"checked at {sitemap_checked_url}, returned {sitemap_status_str} (sitemap not discovered)."
            rf["severity"] = "low"
            fid = "CR-SITE-001"

        if "robots" in fid.lower() or "robots" in title.lower():
            if robots_found or robots_unavailable or not robots_missing:
                continue
            ev = f"checked at {robots_url}, returned {robots_status_str}."
            fid = "CR-ROBOTS-001"

        norm = _validate_and_normalize_finding({
            "id": fid,
            "category": "crawl_render",
            "title": title or "Crawl and render optimization",
            "description": ev,
            "severity": rf.get("severity", "low" if "sitemap" in fid.lower() else "medium"),
            "affected_url": target_final_url,
            "evidence": [ev],
            "suggested_action": rf.get("suggested_action", {}),
            "confidence": "high"
        })
        if norm:
            validated.append(norm)

    robots_has_http_response = False
    try:
        if robots_status and int(robots_status) >= 400:
            robots_has_http_response = True
    except (ValueError, TypeError):
        pass

    sitemap_has_http_response = False
    try:
        if sitemap_code and int(sitemap_code) >= 400:
            sitemap_has_http_response = True
    except (ValueError, TypeError):
        pass

    # Ensure raw checklist check for missing sitemap is emitted ONLY when verified HTTP error/not found, NEVER on unavailable
    has_existing_sitemap = any("sitemap" in (f.get("id", "") + " " + f.get("title", "")).lower() for f in validated)
    if not sitemap_found and not has_existing_sitemap and not sitemap_unavailable and (sitemap_res_state == "missing" or sitemap_has_http_response):
        norm = _validate_and_normalize_finding({
            "id": "CR-SITE-001",
            "category": "sitemap",
            "title": "Missing XML sitemap (/sitemap.xml)",
            "description": f"checked at {sitemap_checked_url}, returned {sitemap_status_str} (sitemap not discovered).",
            "severity": "low",
            "affected_url": sitemap_checked_url,
            "evidence": [f"checked at {sitemap_checked_url}, returned {sitemap_status_str} (sitemap not discovered)."],
            "suggested_action": {
                "summary": "Deploy a comprehensive sitemap.xml to streamline search engine crawl discovery.",
                "priority": "low"
            },
            "confidence": "high"
        })
        if norm:
            validated.append(norm)

    # Ensure robots.txt failure is surfaced ONLY when verified HTTP error/missing, NEVER on unavailable
    has_existing_robots = any("robots" in (f.get("id", "") + " " + f.get("title", "")).lower() for f in validated)
    if not robots_found and not has_existing_robots and not robots_unavailable and (robots_res_state in ("missing", "forbidden") or robots_has_http_response):
        norm = _validate_and_normalize_finding({
            "id": "CR-ROBOTS-001",
            "category": "robots",
            "title": "Missing robots.txt configuration file (/robots.txt)",
            "description": f"checked at {robots_url}, returned {robots_status_str} (crawling unconstrained but directives absent).",
            "severity": "low",
            "affected_url": robots_url,
            "evidence": [f"checked at {robots_url}, returned {robots_status_str} (robots.txt absent)."],
            "suggested_action": {
                "summary": "Deploy a valid robots.txt file declaring crawl rate guidelines and sitemap location.",
                "priority": "low"
            },
            "confidence": "high"
        })
        if norm:
            validated.append(norm)

    # Requirement 2.A: Loading Placeholder / Preloader / Hydration Dependency Detection
    spa_ind = raw_res.get("spa_indicators") or {}
    has_preloader = bool(spa_ind.get("has_preloader"))
    preloader_details = spa_ind.get("preloader_details") or {}
    is_spa_root = bool(spa_ind.get("is_spa_root"))
    raw_chars = int((raw_res.get("text_content") or {}).get("character_count", 0) or 0)
    rend_chars = int((rend_res.get("text_content") or {}).get("visible_character_count", 0) or 0)
    if has_preloader or ((raw_chars < 500 or raw_chars < rend_chars * 0.30 or is_spa_root) and rend_chars >= 500):
        expansion = round(float(rend_chars) / max(1, raw_chars), 1)
        
        p_tag = preloader_details.get("tag") or "div"
        p_id = preloader_details.get("id")
        p_class = preloader_details.get("class")
        if preloader_details.get("selector"):
            el_repr = preloader_details["selector"]
        elif p_id:
            el_repr = f"{p_tag}#{p_id}"
        elif p_class:
            first_cls = p_class.split()[0] if isinstance(p_class, str) else ""
            el_repr = f"{p_tag}.{first_cls}" if first_cls else p_tag
        else:
            el_repr = p_tag

        blocks_viewport = bool(preloader_details.get("blocks_viewport", True))
        is_progress_bar = bool(preloader_details.get("is_progress_bar", False))

        if has_preloader:
            if blocks_viewport and not is_progress_bar:
                sev = "high"
                title = f"Client-side rendering dependency with viewport-blocking preloader ({el_repr})"
                desc = f"Initial HTML payload contains a viewport-blocking preloader overlay ({el_repr}), creating client-side rendering dependency. Crawlers or assistants lacking interactive JavaScript execution risk indexing loading states."
                ev_str = f"Initial raw HTML contains viewport-blocking preloader overlay ({el_repr}). Rendered DOM contains {rend_chars} visible text characters post-hydration."
            else:
                sev = "medium"
                title = f"Client-side rendering dependency with loading indicator ({el_repr})"
                desc = f"Initial HTML payload contains a loading placeholder element ({el_repr}), indicating content relies on client-side rendering. Search crawlers may index intermediate or partial content."
                ev_str = f"Initial raw HTML contains loading element ({el_repr}). Rendered DOM contains {rend_chars} visible text characters post-hydration."
        else:
            sev = "high" if ((raw_chars < 200 and rend_chars >= 500) or expansion >= 5.0) else "medium"
            desc = f"Website relies heavily on client-side JavaScript rendering ({expansion}x expansion from {raw_chars} raw characters to {rend_chars} rendered characters). Search crawlers or AI agents without JavaScript execution receive an incomplete document."
            ev_str = f"Initial raw HTML contains only {raw_chars} text characters, whereas rendered DOM contains {rend_chars} visible text characters post-hydration ({expansion}x content expansion)."
            title = "Client-side rendering dependency with pre-hydration content gap"

        norm = _validate_and_normalize_finding({
            "id": "CR-HYDRATE-001",
            "category": "rendering",
            "title": title,
            "description": desc,
            "severity": sev,
            "affected_url": target_final_url,
            "evidence": [ev_str],
            "suggested_action": {
                "summary": "Ensure critical text and product information are server-rendered or accessible without client-side JavaScript execution.",
                "priority": sev
            },
            "confidence": "high"
        })
        if norm:
            validated.append(norm)

    # Requirement 2.B: Single-Page / Anchor-Only Navigation Detection
    # Check internal links in raw_res or DOM
    a_tags_stats = raw_res.get("links") or {}
    tot_links = int(a_tags_stats.get("total", 0) or 0)
    frag_links = int(a_tags_stats.get("fragment_only", 0) or 0)
    int_links = int(a_tags_stats.get("internal", 0) or 0) + int(a_tags_stats.get("relative", 0) or 0)
    tot_internal = frag_links + int_links
    if (tot_internal > 0 and frag_links / tot_internal >= 0.70) or frag_links >= 3:
        pct_anchor = round((frag_links / max(1, tot_internal)) * 100, 1)
        norm = _validate_and_normalize_finding({
            "id": "CR-NAV-ANCHOR-001",
            "category": "crawlability",
            "title": "Site navigation relies entirely on same-page anchor hashes",
            "description": f"Evaluated internal navigation links; {frag_links} of {tot_internal} internal links ({pct_anchor}%) target same-page fragment anchors (#section). The website exposes effectively only one indexable URL to search crawlers.",
            "severity": "medium",
            "affected_url": target_final_url,
            "evidence": [f"Checked {tot_internal} internal links at {target_final_url}; {frag_links} ({pct_anchor}%) target same-page fragment anchors. Site exposes effectively only one indexable URL."],
            "suggested_action": {
                "summary": "Structure primary site sections into discrete, crawlable routes (e.g. /about, /products) with unique meta titles and canonical tags.",
                "priority": "medium"
            },
            "confidence": "high"
        })
        if norm:
            validated.append(norm)

    return validated


def build_findings(
    audit_results: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Evaluate audit results across all pipeline components and produce
    normalized, deduplicated findings sorted by severity and category.

    Args:
        audit_results: Combined dictionary containing outputs from crawler, robots,
                       sitemap, raw_html, rendered, and dom_comparison modules.
        options: Optional threshold configuration overrides.

    Returns:
        List of structured, JSON-serializable finding dictionaries.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    results = audit_results or {}
    crawler_res = results.get("crawler")
    robots_res = results.get("robots")
    sitemap_res = results.get("sitemap")
    raw_res = results.get("raw_html")
    rend_res = results.get("rendered")
    dom_comp_res = results.get("dom_comparison")

    target_url = (
        (crawler_res or {}).get("final_url")
        or (crawler_res or {}).get("requested_url")
        or (raw_res or {}).get("target_url")
        or (rend_res or {}).get("target_url")
        or "https://example.com"
    )

    all_raw_findings: list[dict[str, Any]] = []

    # 1. Deterministic Analysis ALWAYS executes first
    all_raw_findings.extend(_evaluate_http(crawler_res, target_url))

    crawler_err = (crawler_res or {}).get("error_classification")
    crawler_state = (crawler_res or {}).get("resource_state")
    page_transport_failed = (
        crawler_err in ("dns_failure", "dns_error", "timeout", "connection_error", "ssl_error")
        or crawler_state in ("unavailable", "missing", "error")
    )

    if not page_transport_failed:
        all_raw_findings.extend(_evaluate_redirects(crawler_res, target_url, opts))
        all_raw_findings.extend(_evaluate_raw_vs_rendered(dom_comp_res, raw_res, rend_res, target_url, opts))
        all_raw_findings.extend(_evaluate_rendering_runtime(rend_res, target_url))
        all_raw_findings.extend(_evaluate_canonical(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_metadata(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_semantic_structure(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_machine_readability(raw_res, rend_res, dom_comp_res, target_url))

    all_raw_findings.extend(_evaluate_robots(robots_res, target_url))
    all_raw_findings.extend(_evaluate_sitemap(sitemap_res, target_url, opts))

    # 2. Optional Qualitative LLM enrichment (merged & grounded, never bypassing deterministic analysis)
    if opts.get("use_llm", False) and not page_transport_failed:
        llm_findings = build_findings_qualitative_llm(results, opts)
        if llm_findings:
            all_raw_findings.extend(llm_findings)


    # Validate, normalize, and deduplicate findings by finding ID
    deduped_map: dict[str, dict[str, Any]] = {}
    for raw_f in all_raw_findings:
        normalized = _validate_and_normalize_finding(raw_f)
        if normalized is None:
            continue
        fid = normalized["id"]
        if fid in deduped_map:
            # Combine evidence if duplicate encountered
            existing_ev = deduped_map[fid].get("evidence", [])
            existing_ev.extend(normalized.get("evidence", []))
            deduped_map[fid]["evidence"] = existing_ev
        else:
            deduped_map[fid] = normalized

    final_findings = list(deduped_map.values())

    # Sort deterministically by severity priority, category, then finding ID
    final_findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f["severity"], 99),
            f["category"],
            f["id"],
        )
    )

    logger.info("Generated %d normalized findings", len(final_findings))
    return final_findings


async def build_findings_async(
    audit_results: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Asynchronously evaluate audit results and build findings with shared LLMClient."""
    opts = options or {}
    results = audit_results or {}

    crawler_res = results.get("crawler")
    robots_res = results.get("robots")
    sitemap_res = results.get("sitemap")
    raw_res = results.get("raw_html")
    rend_res = results.get("rendered")
    dom_comp_res = results.get("dom_comparison")

    target_url = (
        (crawler_res or {}).get("final_url")
        or (crawler_res or {}).get("requested_url")
        or (raw_res or {}).get("target_url")
        or (rend_res or {}).get("target_url")
        or "https://example.com"
    )

    all_raw_findings: list[dict[str, Any]] = []

    # 1. Deterministic Analysis ALWAYS executes first
    all_raw_findings.extend(_evaluate_http(crawler_res, target_url))

    crawler_err = (crawler_res or {}).get("error_classification")
    crawler_state = (crawler_res or {}).get("resource_state")
    page_transport_failed = (
        crawler_err in ("dns_failure", "dns_error", "timeout", "connection_error", "ssl_error")
        or crawler_state in ("unavailable", "missing", "error")
    )

    if not page_transport_failed:
        all_raw_findings.extend(_evaluate_redirects(crawler_res, target_url, opts))
        all_raw_findings.extend(_evaluate_raw_vs_rendered(dom_comp_res, raw_res, rend_res, target_url, opts))
        all_raw_findings.extend(_evaluate_rendering_runtime(rend_res, target_url))
        all_raw_findings.extend(_evaluate_canonical(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_metadata(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_semantic_structure(raw_res, rend_res, target_url))
        all_raw_findings.extend(_evaluate_machine_readability(raw_res, rend_res, dom_comp_res, target_url))

    all_raw_findings.extend(_evaluate_robots(robots_res, target_url))
    all_raw_findings.extend(_evaluate_sitemap(sitemap_res, target_url, opts))

    # 2. Optional Qualitative LLM enrichment
    if opts.get("use_llm", False) and not page_transport_failed:
        llm_findings = await build_findings_qualitative_llm_async(results, opts)
        if llm_findings:
            all_raw_findings.extend(llm_findings)

    deduped_map: dict[str, dict[str, Any]] = {}
    for raw_f in all_raw_findings:
        normalized = _validate_and_normalize_finding(raw_f)
        if normalized is None:
            continue
        fid = normalized["id"]
        if fid in deduped_map:
            existing_ev = deduped_map[fid].get("evidence", [])
            existing_ev.extend(normalized.get("evidence", []))
            deduped_map[fid]["evidence"] = existing_ev
        else:
            deduped_map[fid] = normalized

    final_findings = list(deduped_map.values())
    final_findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f["severity"], 99),
            f["category"],
            f["id"],
        )
    )
    return final_findings




# CLI Testing Interface


def _cli_entrypoint() -> None:
    """CLI runner to convert an audit_results.json file into structured findings."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python finding_builder.py <AUDIT_RESULTS_JSON_PATH>")
        print("Example: python finding_builder.py full_audit_results.json")
        sys.exit(0)

    filepath = sys.argv[1]
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to load input JSON: {exc}"}, indent=2))
        sys.exit(1)

    findings = build_findings(data)
    print(json.dumps(findings, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
