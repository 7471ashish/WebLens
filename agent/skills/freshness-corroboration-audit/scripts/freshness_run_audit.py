"""
Top-level runner/orchestrator for the independent Freshness & Corroboration Audit skill.

This module coordinates the complete audit pipeline:
crawler -> jsonld_analyzer -> entity_analyzer -> freshness_analyzer ->
external_corroborator -> conflict_detector -> finding_builder

It enforces overall time budgeting (default 5 minutes), per-request timeouts (default 15s),
stage error isolation, and deterministic JSON-serializable output without depending on
external rendering tools or foreign audit agents.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Any
import urllib.parse

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
else:
    sys.path.remove(SCRIPTS_DIR)
    sys.path.insert(0, SCRIPTS_DIR)

# Package/local sibling imports
try:
    from .conflict_detector import detect_conflicts
    from .freshness_crawler import crawl_page
    from .entity_analyzer import analyze_entities
    from .external_corroborator import corroborate
    from .freshness_finding_builder import build_findings, build_findings_async
    from .freshness_analyzer import analyze_freshness
    from .jsonld_analyzer import analyze_jsonld
except ImportError:
    from conflict_detector import detect_conflicts
    from freshness_crawler import crawl_page
    from entity_analyzer import analyze_entities
    from external_corroborator import corroborate
    from freshness_finding_builder import build_findings, build_findings_async
    from freshness_analyzer import analyze_freshness
    from jsonld_analyzer import analyze_jsonld

logger = logging.getLogger("freshness_corroboration_audit.run_audit")

# Default orchestrator options
DEFAULT_TOTAL_BUDGET_MS = 300_000  # 5 minutes overall budget
DEFAULT_TIMEOUT_MS = 15_000  # 15s per-request timeout
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000  # 5 MB
DEFAULT_MAX_EXTERNAL_SOURCES = 5
DEFAULT_MAX_CLAIMS = 10
DEFAULT_MAX_FINDINGS = 100
DEFAULT_STALE_AFTER_DAYS = 365
DEFAULT_VERY_STALE_AFTER_DAYS = 730
DEFAULT_MINIMUM_CONFIDENCE = "medium"


def _format_utc_timestamp(dt: datetime | None = None) -> str:
    """Format datetime as standard UTC ISO 8601 string."""
    t = dt or datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_target_url(url: Any) -> tuple[bool, str | None]:
    """Validate target URL format prior to executing audit."""
    if not isinstance(url, str) or not url.strip():
        return False, "Target URL must be a non-empty string."

    trimmed = url.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
    except Exception as exc:
        return False, f"Malformed URL syntax: {exc}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only http and https allowed."

    if not parsed.netloc:
        return False, "Target URL missing hostname or network location."

    return True, None


def run_audit(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute the complete Freshness & Corroboration Audit against a target URL.

    Args:
        target_url: The URL of the webpage to audit.
        options: Optional configuration options dictionary.

    Returns:
        Structured JSON-serializable dictionary with audit_metadata, stage results,
        findings, and structured errors.
    """
    start_monotonic = time.monotonic()
    started_at_str = _format_utc_timestamp()

    # Step 1: Normalize Options
    caller_opts = options or {}
    opts: dict[str, Any] = dict(caller_opts)

    total_budget_ms = int(opts.get("total_budget_ms", DEFAULT_TOTAL_BUDGET_MS))
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    max_redirects = int(opts.get("max_redirects", DEFAULT_MAX_REDIRECTS))
    max_response_bytes = int(opts.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES))

    check_jsonld = bool(opts.get("check_jsonld", True))
    check_entities = bool(opts.get("check_entities", True))
    check_freshness = bool(opts.get("check_freshness", True))
    check_external_corrob = bool(opts.get("check_external_corroboration", True))
    check_conflicts = bool(opts.get("check_conflicts", True))

    deadline = start_monotonic + (total_budget_ms / 1000.0)

    # Initialize Audit Metadata and Stage Tracking
    stages_status = {
        "crawl": "not_started",
        "jsonld": "not_started",
        "entities": "not_started",
        "freshness": "not_started",
        "corroboration": "not_started",
        "conflicts": "not_started",
        "findings": "not_started",
    }
    audit_errors: list[dict[str, Any]] = []

    # Validate target URL
    is_valid_url, url_err = _validate_target_url(target_url)
    if not is_valid_url:
        logger.error("Target URL validation failed: %s", url_err)
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        return {
            "audit_metadata": {
                "skill": "freshness-corroboration-audit",
                "version": "1.0",
                "target_url": str(target_url),
                "final_url": str(target_url),
                "started_at": started_at_str,
                "completed_at": _format_utc_timestamp(),
                "duration_ms": duration_ms,
                "total_budget_ms": total_budget_ms,
                "budget_exhausted": False,
                "status": "failed",
                "stages": {k: "skipped" if k != "crawl" else "failed" for k in stages_status},
            },
            "crawl": {
                "status": "failed",
                "target_url": str(target_url),
                "error": url_err,
            },
            "structured_data": {"status": "skipped", "summary": {}, "entities": [], "relationships": []},
            "entities": {"status": "skipped", "summary": {}, "entities": []},
            "temporal": {"status": "skipped", "summary": {}, "entities": []},
            "corroboration": {"status": "skipped", "summary": {}, "sources": []},
            "conflicts": {"status": "skipped", "summary": {}, "conflicts": []},
            "findings": {"target_url": str(target_url), "summary": {"total_findings": 0}, "findings": []},
            "errors": [
                {
                    "stage": "validation",
                    "status": "failed",
                    "error_type": "InvalidUrlError",
                    "message": url_err,
                    "recoverable": False,
                }
            ],
        }

    # ---------------------------------------------------------
    # STAGE 1: CRAWL (Reuse in-memory observation if provided)
    # ---------------------------------------------------------
    stages_status["crawl"] = "running"
    logger.info("Starting crawl for %s", target_url)

    crawler_options = dict(opts)
    crawler_options["timeout_ms"] = timeout_ms
    crawler_options["max_redirects"] = max_redirects
    crawler_options["max_response_bytes"] = max_response_bytes

    crawl_result: dict[str, Any] = {}
    try:
        if opts.get("crawl_result"):
            crawl_result = opts["crawl_result"]
            stages_status["crawl"] = "completed" if crawl_result.get("status") in ("success", "ok") or crawl_result.get("success") else "failed"
        elif opts.get("html"):
            crawl_result = {
                "status": "success",
                "target_url": target_url,
                "final_url": opts.get("final_url", target_url),
                "html": opts["html"],
                "status_code": opts.get("status_code", 200),
                "headers": opts.get("headers", {}),
                "response_time_ms": opts.get("response_time_ms", 100),
            }
            stages_status["crawl"] = "completed"
        else:
            crawl_result = crawl_page(target_url, crawler_options)
            if crawl_result.get("status") == "success":
                stages_status["crawl"] = "completed"
            else:
                stages_status["crawl"] = "failed"
                audit_errors.append(
                    {
                        "stage": "crawl",
                        "status": "failed",
                        "error_type": crawl_result.get("error_type", "CrawlError"),
                        "message": crawl_result.get("error_message", "Failed to crawl target URL."),
                        "recoverable": False,
                    }
                )
    except Exception as exc:
        logger.exception("Unexpected exception during crawl_page: %s", exc)
        stages_status["crawl"] = "failed"
        crawl_result = {
            "status": "error",
            "target_url": target_url,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        audit_errors.append(
            {
                "stage": "crawl",
                "status": "failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "recoverable": False,
            }
        )

    resp_info = crawl_result.get("response") if isinstance(crawl_result.get("response"), dict) else {}
    final_url = crawl_result.get("final_url") or resp_info.get("final_url") or target_url
    
    raw_html = crawl_result.get("html")
    if isinstance(raw_html, dict):
        html_content = raw_html.get("body") or ""
    elif isinstance(raw_html, str):
        html_content = raw_html
    else:
        html_content = ""

    status_code = resp_info.get("status_code") or crawl_result.get("status_code")
    content_type = resp_info.get("content_type") or crawl_result.get("content_type", "")
    page_metadata = {
        "headers": crawl_result.get("headers", {}),
        "status_code": status_code,
        "content_type": content_type,
        "last_modified": crawl_result.get("headers", {}).get("last-modified")
        or crawl_result.get("headers", {}).get("Last-Modified"),
    }

    # If crawl failed completely or returned no HTML, skip downstream parsing safely
    is_crawl_successful = stages_status["crawl"] == "completed"
    is_html = "html" in str(page_metadata.get("content_type", "")).lower() or bool(html_content)

    # ---------------------------------------------------------
    # STAGE 2: JSON-LD ANALYSIS
    # ---------------------------------------------------------
    jsonld_result: dict[str, Any] = {}
    extracted_entities: list[dict[str, Any]] = []
    extracted_relationships: list[dict[str, Any]] = []

    if not check_jsonld:
        stages_status["jsonld"] = "disabled"
        jsonld_result = {"status": "disabled", "summary": {}, "entities": [], "relationships": []}
    elif not is_crawl_successful:
        stages_status["jsonld"] = "skipped"
        jsonld_result = {"status": "skipped", "summary": {}, "entities": [], "relationships": []}
    elif not is_html:
        stages_status["jsonld"] = "not_applicable"
        jsonld_result = {"status": "not_applicable", "summary": {}, "entities": [], "relationships": []}
    else:
        stages_status["jsonld"] = "running"
        try:
            jsonld_result = analyze_jsonld(html_content, opts)
            stages_status["jsonld"] = "completed"
            extracted_entities = jsonld_result.get("entities", [])
            extracted_relationships = jsonld_result.get("relationships", [])
        except Exception as exc:
            logger.exception("Exception in analyze_jsonld: %s", exc)
            stages_status["jsonld"] = "failed"
            jsonld_result = {"status": "failed", "summary": {}, "entities": [], "relationships": []}
            audit_errors.append(
                {
                    "stage": "jsonld",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": True,
                }
            )

    # ---------------------------------------------------------
    # STAGE 3: ENTITY ANALYSIS
    # ---------------------------------------------------------
    entity_result: dict[str, Any] = {}
    canonical_entities = extracted_entities

    if not check_entities:
        stages_status["entities"] = "disabled"
        entity_result = {"status": "disabled", "summary": {}, "entities": []}
    elif not is_crawl_successful or stages_status["jsonld"] in ("skipped", "not_applicable"):
        stages_status["entities"] = "skipped"
        entity_result = {"status": "skipped", "summary": {}, "entities": []}
    else:
        stages_status["entities"] = "running"
        try:
            entity_result = analyze_entities(extracted_entities, extracted_relationships, opts)
            stages_status["entities"] = "completed"
            if entity_result.get("entities"):
                canonical_entities = entity_result["entities"]
        except Exception as exc:
            logger.exception("Exception in analyze_entities: %s", exc)
            stages_status["entities"] = "failed"
            entity_result = {"status": "failed", "summary": {}, "entities": []}
            audit_errors.append(
                {
                    "stage": "entities",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": True,
                }
            )

    # ---------------------------------------------------------
    # STAGE 4: FRESHNESS ANALYSIS
    # ---------------------------------------------------------
    freshness_result: dict[str, Any] = {}
    if not check_freshness:
        stages_status["freshness"] = "disabled"
        freshness_result = {"status": "disabled", "summary": {}, "entities": [], "observations": []}
    elif not is_crawl_successful or stages_status["jsonld"] in ("skipped", "not_applicable"):
        stages_status["freshness"] = "skipped"
        freshness_result = {"status": "skipped", "summary": {}, "entities": [], "observations": []}

    else:
        stages_status["freshness"] = "running"
        try:
            freshness_result = analyze_freshness(canonical_entities, extracted_relationships, page_metadata, opts)
            stages_status["freshness"] = "completed"
        except Exception as exc:
            logger.exception("Exception in analyze_freshness: %s", exc)
            stages_status["freshness"] = "failed"
            freshness_result = {"status": "failed", "summary": {}, "entities": [], "observations": []}
            audit_errors.append(
                {
                    "stage": "freshness",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": True,
                }
            )

    # ---------------------------------------------------------
    # STAGE 5: EXTERNAL CORROBORATION
    # ---------------------------------------------------------
    corroboration_result: dict[str, Any] = {}
    now_monotonic = time.monotonic()
    remaining_budget_ms = max(0, int((deadline - now_monotonic) * 1000))

    if not check_external_corrob:
        stages_status["corroboration"] = "disabled"
        corroboration_result = {
            "status": "disabled",
            "summary": {
                "sources_considered": 0,
                "sources_fetched": 0,
                "sources_successful": 0,
                "sources_failed": 0,
                "claims_checked": 0,
                "claims_supported": 0,
                "claims_disagreed": 0,
                "identity_checks": 0,
                "identity_matches": 0,
            },
            "sources": [],
            "identity_checks": [],
            "claim_checks": [],
            "observations": [],
        }
    elif remaining_budget_ms <= 1000:
        stages_status["corroboration"] = "skipped_due_to_budget"
        corroboration_result = {
            "status": "skipped_due_to_budget",
            "summary": {},
            "sources": [],
            "identity_checks": [],
            "claim_checks": [],
            "observations": [],
        }
    elif not is_crawl_successful:
        stages_status["corroboration"] = "skipped"
        corroboration_result = {"status": "skipped", "summary": {}, "sources": [], "identity_checks": [], "claim_checks": []}
    else:
        stages_status["corroboration"] = "running"
        corrob_options = dict(opts)
        corrob_options["total_budget_ms"] = remaining_budget_ms
        corrob_options["per_request_timeout_ms"] = min(timeout_ms, remaining_budget_ms)

        try:
            corroboration_result = corroborate(target_url, canonical_entities, freshness_result, corrob_options)
            stages_status["corroboration"] = "completed"
        except Exception as exc:
            logger.exception("Exception in corroborate: %s", exc)
            stages_status["corroboration"] = "failed"
            corroboration_result = {
                "status": "failed",
                "summary": {},
                "sources": [],
                "identity_checks": [],
                "claim_checks": [],
                "observations": [],
            }
            audit_errors.append(
                {
                    "stage": "corroboration",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": True,
                }
            )

    # ---------------------------------------------------------
    # STAGE 6: CONFLICT DETECTION
    # ---------------------------------------------------------
    conflict_result: dict[str, Any] = {}
    now_monotonic = time.monotonic()
    remaining_budget_ms = max(0, int((deadline - now_monotonic) * 1000))

    if not check_conflicts:
        stages_status["conflicts"] = "disabled"
        conflict_result = {"status": "disabled", "summary": {}, "conflicts": [], "observations": []}
    elif remaining_budget_ms <= 200:
        stages_status["conflicts"] = "skipped_due_to_budget"
        conflict_result = {"status": "skipped_due_to_budget", "summary": {}, "conflicts": [], "observations": []}
    elif not is_crawl_successful:
        stages_status["conflicts"] = "skipped"
        conflict_result = {"status": "skipped", "summary": {}, "conflicts": [], "observations": []}
    else:
        stages_status["conflicts"] = "running"
        try:
            conflict_result = detect_conflicts(canonical_entities, freshness_result, corroboration_result, opts)
            stages_status["conflicts"] = "completed"
        except Exception as exc:
            logger.exception("Exception in detect_conflicts: %s", exc)
            stages_status["conflicts"] = "failed"
            conflict_result = {"status": "failed", "summary": {}, "conflicts": [], "observations": []}
            audit_errors.append(
                {
                    "stage": "conflicts",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": True,
                }
            )

    # ---------------------------------------------------------
    # STAGE 7: FINDING BUILDER
    # ---------------------------------------------------------
    if not is_crawl_successful:
        stages_status["findings"] = "skipped"
        findings_result = {
            "target_url": target_url,
            "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            "findings": [],
            "observations": [],
        }
    else:
        try:
            findings_result = build_findings(
                target_url=target_url,
                jsonld=jsonld_result,
                entities=entity_result,
                freshness=freshness_result,
                corroboration=corroboration_result,
                conflicts=conflict_result,
                options=opts,
                raw_html=html_content,
            )
            stages_status["findings"] = "completed"
        except Exception as exc:
            logger.exception("Exception in build_findings: %s", exc)
            stages_status["findings"] = "failed"
            findings_result = {
                "target_url": target_url,
                "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                "findings": [],
                "observations": [],
            }
            audit_errors.append(
                {
                    "stage": "findings",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recoverable": False,
                }
            )

    # Final metadata assembly
    duration_ms = int((time.monotonic() - start_monotonic) * 1000)
    budget_exhausted = (time.monotonic() >= deadline) or any(
        st == "skipped_due_to_budget" for st in stages_status.values()
    )
    overall_status = "completed"
    if stages_status["crawl"] == "failed":
        overall_status = "failed"
    elif any(st == "failed" for st in stages_status.values()):
        overall_status = "partial_failure"

    # Sanitize crawl result to ensure no raw HTML is exposed in final output
    sanitized_crawl = dict(crawl_result)
    if "html" in sanitized_crawl:
        sanitized_crawl["html_length_bytes"] = len(html_content)
        del sanitized_crawl["html"]

    return {
        "audit_metadata": {
            "skill": "freshness-corroboration-audit",
            "version": "1.0",
            "target_url": target_url,
            "final_url": final_url,
            "started_at": started_at_str,
            "completed_at": _format_utc_timestamp(),
            "duration_ms": duration_ms,
            "total_budget_ms": total_budget_ms,
            "budget_exhausted": budget_exhausted,
            "status": overall_status,
            "stages": stages_status,
        },
        "crawl": sanitized_crawl,
        "structured_data": jsonld_result,
        "entities": entity_result,
        "temporal": freshness_result,
        "corroboration": corroboration_result,
        "conflicts": conflict_result,
        "findings": findings_result,
        "errors": audit_errors,
    }


async def run_audit_async(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Asynchronously execute freshness audit using shared LLMClient."""
    opts = options or {}
    report = run_audit(target_url, {**opts, "use_llm": False})
    if opts.get("use_llm", False):
        structured_data = report.get("structured_data")
        entities = report.get("entities")
        temporal = report.get("temporal")
        corroboration = report.get("corroboration")
        conflicts = report.get("conflicts")
        raw_html = opts.get("html") or opts.get("raw_html")
        findings_res = await build_findings_async(
            target_url=target_url,
            jsonld=structured_data,
            entities=entities,
            freshness=temporal,
            corroboration=corroboration,
            conflicts=conflicts,
            options=opts,
            raw_html=raw_html,
        )
        report["findings"] = findings_res
    return report



def main() -> int:
    """Command-line interface to execute run_audit against a target URL."""
    parser = argparse.ArgumentParser(
        description="End-to-end Freshness & Corroboration Audit runner."
    )
    parser.add_argument("target_url", help="Target webpage URL to audit")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Per-request timeout in milliseconds")
    parser.add_argument("--total-budget-ms", type=int, default=DEFAULT_TOTAL_BUDGET_MS, help="Total audit budget in milliseconds")
    parser.add_argument("--max-external-sources", type=int, default=DEFAULT_MAX_EXTERNAL_SOURCES, help="Max external sources to query")
    parser.add_argument("--max-claims", type=int, default=DEFAULT_MAX_CLAIMS, help="Max claims to check externally")
    parser.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS, help="Max findings to return")
    parser.add_argument("--no-jsonld", action="store_true", help="Disable JSON-LD analysis stage")
    parser.add_argument("--no-entities", action="store_true", help="Disable entity identity analysis stage")
    parser.add_argument("--no-freshness", action="store_true", help="Disable freshness metadata analysis stage")
    parser.add_argument("--no-external-corroboration", action="store_true", help="Disable external corroboration stage")
    parser.add_argument("--no-conflicts", action="store_true", help="Disable conflict detection stage")
    parser.add_argument("--require-jsonld", action="store_true", help="Require Schema.org structured data")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose diagnostic logging to stderr")

    args = parser.parse_args()

    # Configure logging to stderr so stdout remains pure JSON
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stderr)

    options: dict[str, Any] = {
        "timeout_ms": args.timeout_ms,
        "total_budget_ms": args.total_budget_ms,
        "max_external_sources": args.max_external_sources,
        "max_claims": args.max_claims,
        "max_findings": args.max_findings,
        "check_jsonld": not args.no_jsonld,
        "check_entities": not args.no_entities,
        "check_freshness": not args.no_freshness,
        "check_external_corroboration": not args.no_external_corroboration,
        "check_conflicts": not args.no_conflicts,
        "require_jsonld": args.require_jsonld,
    }

    try:
        result = run_audit(args.target_url, options)
        print(json.dumps(result, indent=2))
        return 0 if result.get("audit_metadata", {}).get("status") in ("completed", "partial_failure") else 1
    except Exception as exc:
        sys.stderr.write(f"Fatal runner error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
