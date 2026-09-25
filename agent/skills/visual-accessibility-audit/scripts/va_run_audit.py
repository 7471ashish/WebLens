"""
Top-level orchestrator and CLI runner for visual-accessibility-audit.

This module coordinates browser lifecycle, multi-viewport page rendering, and
independent visual, semantic accessibility, contrast, keyboard, and ARIA analyzers,
compiling normalized findings via finding_analyzer.py within a shared 300-second budget.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# Sibling module imports with fallback
try:
    from .browser import BrowserSession, close_browser, launch_browser, navigate
    from .page_renderer import render_page
    from .visual_analyzer import analyze_visual
    from .accessibility_analyzer import analyze_accessibility
    from .contrast_analyzer import analyze_contrast
    from .keyboard_analyzer import analyze_keyboard
    from .aria_analyzer import analyze_aria
    from .finding_analyzer import analyze_findings, analyze_findings_async
except ImportError:
    from browser import BrowserSession, close_browser, launch_browser, navigate
    from page_renderer import render_page
    from visual_analyzer import analyze_visual
    from accessibility_analyzer import analyze_accessibility
    from contrast_analyzer import analyze_contrast
    from keyboard_analyzer import analyze_keyboard
    from aria_analyzer import analyze_aria
    from finding_analyzer import analyze_findings, analyze_findings_async

logger = logging.getLogger("visual_accessibility_audit.run_audit")

AGENT_NAME = "visual-accessibility-audit"
AGENT_VERSION = "1.0.0"

DEFAULT_TOTAL_TIMEOUT_SECONDS = 300
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 15

DEFAULT_VIEWPORTS = [
    {"name": "desktop", "width": 1440, "height": 900},
    {"name": "tablet", "width": 1024, "height": 768},
    {"name": "mobile", "width": 390, "height": 844},
]


class AuditValidationError(Exception):
    """Raised when URL or options validation fails."""


def validate_target_url(url: str, allow_private: bool = False) -> None:
    """
    Validate target URL format, scheme, and protect against SSRF / private networks.
    """
    if not url or not isinstance(url, str):
        raise AuditValidationError("Target URL must be a non-empty string.")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise AuditValidationError(f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise AuditValidationError("Target URL is missing a valid hostname.")

    hostname_lower = hostname.lower()
    allow_local = allow_private or (os.environ.get("AUDIT_ALLOW_LOCAL") == "1")
    if not allow_local:
        if hostname_lower in ("localhost", "127.0.0.1", "::1"):
            raise AuditValidationError(f"SSRF protection: '{hostname}' is a forbidden loopback target.")

        # Check IP addresses directly
        try:
            ip = ipaddress.ip_address(hostname_lower)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or str(ip) == "169.254.169.254"
            ):
                raise AuditValidationError(f"SSRF protection: '{ip}' is a private or reserved IP address.")
        except ValueError:
            # Not a raw IP; valid domain name
            pass


def normalize_options(raw_options: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize configuration options with defaults and validation."""
    opts = raw_options or {}

    timeout = int(opts.get("timeout_seconds", DEFAULT_TOTAL_TIMEOUT_SECONDS))
    nav_timeout = int(opts.get("navigation_timeout_seconds", DEFAULT_NAVIGATION_TIMEOUT_SECONDS))

    viewports = opts.get("viewports") or DEFAULT_VIEWPORTS
    norm_vps = []
    for vp in viewports:
        name = str(vp.get("name", "desktop"))
        w = int(vp.get("width", 1440))
        h = int(vp.get("height", 900))
        norm_vps.append({"name": name, "width": w, "height": h})

    return {
        "timeout_seconds": timeout,
        "navigation_timeout_seconds": nav_timeout,
        "viewports": norm_vps if norm_vps else DEFAULT_VIEWPORTS,
        "run_visual": bool(opts.get("run_visual", True)),
        "run_accessibility": bool(opts.get("run_accessibility", True)),
        "run_contrast": bool(opts.get("run_contrast", True)),
        "run_keyboard": bool(opts.get("run_keyboard", True)),
        "run_aria": bool(opts.get("run_aria", True)),
        "max_elements": int(opts.get("max_elements", 5000)),
        "max_findings": int(opts.get("max_findings", 500)),
        "headless": bool(opts.get("headless", True)),
        "allow_private_ips": bool(opts.get("allow_private_ips", False)),
    }


def sanitize_url_for_report(url: str) -> str:
    """Strip passwords and credentials from URL before including in reports."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            user = parsed.username or ""
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            auth = f"{user}:[REDACTED]@" if user else "[REDACTED]@"
            netloc = f"{auth}{host}{port}"
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return url


def run_audit(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute end-to-end Visual and Accessibility Audit for a target URL.

    Args:
        target_url: Public HTTP/HTTPS website URL to audit.
        options: Optional configuration dictionary.

    Returns:
        Structured JSON-serializable audit report.
    """
    start_time_monotonic = time.monotonic()
    started_at_iso = datetime.now(timezone.utc).isoformat()
    clean_target_url = sanitize_url_for_report(target_url)
    opts_dict = options or {}
    allow_private = bool(opts_dict.get("allow_private_ips") or "127.0.0.1" in target_url or "localhost" in target_url)

    try:
        validate_target_url(target_url, allow_private=allow_private)
    except AuditValidationError as exc:
        return {
            "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
            "target": {"url": clean_target_url, "final_url": None, "title": None},
            "audit": {
                "status": "failed",
                "started_at": started_at_iso,
                "duration_ms": 0,
                "timeout_seconds": DEFAULT_TOTAL_TIMEOUT_SECONDS,
            },
            "viewports": [],
            "render": {"status": "failed"},
            "analyzers": {},
            "findings": {"status": "failed", "total": 0, "items": []},
            "coverage": {},
            "limitations": [str(exc)],
            "errors": [{"stage": "validation", "error": str(exc)}],
        }

    config = normalize_options(options)
    deadline = start_time_monotonic + config["timeout_seconds"]

    browser_session: BrowserSession | None = None
    render_result: dict[str, Any] | None = None

    analyzer_results: dict[str, Any] = {}
    analyzer_statuses: dict[str, dict[str, Any]] = {}
    findings_data: dict[str, Any] = {"status": "not_run", "total": 0, "items": []}
    errors: list[dict[str, Any]] = []
    limitations: list[str] = [
        "Automated accessibility analysis does not guarantee complete WCAG conformance.",
        "Cross-origin iframe content was not inspected.",
    ]

    try:
        # Check budget before browser launch
        if time.monotonic() >= deadline:
            for an in ("visual", "accessibility", "contrast", "keyboard", "aria"):
                if config.get(f"run_{an}", True):
                    analyzer_statuses[an] = {"status": "skipped_due_to_budget", "result": {}}
            limitations.append("Audit deadline exceeded before browser launch.")
            duration_ms = int((time.monotonic() - start_time_monotonic) * 1000)
            return {
                "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
                "target": {"url": clean_target_url, "final_url": None, "title": None},
                "audit": {
                    "status": "partial",
                    "started_at": started_at_iso,
                    "duration_ms": duration_ms,
                    "timeout_seconds": config["timeout_seconds"],
                },
                "viewports": config["viewports"],
                "render": {"status": "skipped_due_to_budget"},
                "analyzers": analyzer_statuses,
                "findings": findings_data,
                "coverage": {an: "skipped_due_to_budget" for an in analyzer_statuses},
                "limitations": limitations,
                "errors": [],
            }

        # 1. Launch isolated browser session
        allow_local_target = bool(config.get("allow_private_ips", False) or os.environ.get("AUDIT_ALLOW_LOCAL") == "1")
        browser_session = launch_browser({
            "viewport": config["viewports"][0],
            "headless": config["headless"],
            "navigation_timeout_ms": config["navigation_timeout_seconds"] * 1000,
            "allow_local": allow_local_target,
        })

        # 2. Render Page across configured viewports
        render_opts = {
            "viewports": config["viewports"],
            "navigation_timeout_seconds": config["navigation_timeout_seconds"],
            "max_elements": config["max_elements"],
            "deadline": deadline,
            "allow_local": allow_local_target,
        }
        render_result = render_page(
            target_url=clean_target_url,
            options=render_opts,
            browser_session=browser_session,
        )


        if not render_result or render_result.get("status") == "failed":
            limitations.append("The target page could not be rendered sufficiently for audit analysis.")
            duration_ms = int((time.monotonic() - start_time_monotonic) * 1000)
            return {
                "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
                "target": {"url": clean_target_url, "final_url": None, "title": None},
                "audit": {
                    "status": "not_testable",
                    "started_at": started_at_iso,
                    "duration_ms": duration_ms,
                    "timeout_seconds": config["timeout_seconds"],
                },
                "viewports": config["viewports"],
                "render": render_result or {"status": "failed"},
                "analyzers": {},
                "findings": {"status": "not_run", "total": 0, "items": []},
                "coverage": {"render": "failed"},
                "limitations": limitations,
                "errors": render_result.get("errors", []) if render_result else [{"stage": "render", "error": "Rendering returned no result"}],
            }

        # 3. Visual Analyzer
        if config["run_visual"]:
            if time.monotonic() >= deadline:
                analyzer_statuses["visual"] = {"status": "skipped_due_to_budget", "result": {}}
            else:
                try:
                    res_vis = analyze_visual(render_result, options={"deadline": deadline})
                    analyzer_results["visual"] = res_vis
                    analyzer_statuses["visual"] = {"status": res_vis.get("summary", {}).get("status", "completed"), "result": res_vis}
                except Exception as exc:
                    logger.exception("Visual analyzer failed: %s", exc)
                    errors.append({"stage": "visual", "error": str(exc)})
                    analyzer_statuses["visual"] = {"status": "failed", "error": str(exc)}

        # 4. Semantic Accessibility Analyzer
        if config["run_accessibility"]:
            if time.monotonic() >= deadline:
                analyzer_statuses["accessibility"] = {"status": "skipped_due_to_budget", "result": {}}
            else:
                try:
                    res_acc = analyze_accessibility(render_result, options={"deadline": deadline})
                    analyzer_results["accessibility"] = res_acc
                    analyzer_statuses["accessibility"] = {"status": res_acc.get("summary", {}).get("status", "completed"), "result": res_acc}
                except Exception as exc:
                    logger.exception("Accessibility analyzer failed: %s", exc)
                    errors.append({"stage": "accessibility", "error": str(exc)})
                    analyzer_statuses["accessibility"] = {"status": "failed", "error": str(exc)}

        # 5. Color Contrast Analyzer
        if config["run_contrast"]:
            if time.monotonic() >= deadline:
                analyzer_statuses["contrast"] = {"status": "skipped_due_to_budget", "result": {}}
            else:
                try:
                    res_con = analyze_contrast(render_result, options={"deadline": deadline})
                    analyzer_results["contrast"] = res_con
                    analyzer_statuses["contrast"] = {"status": res_con.get("summary", {}).get("status", "completed"), "result": res_con}
                except Exception as exc:
                    logger.exception("Contrast analyzer failed: %s", exc)
                    errors.append({"stage": "contrast", "error": str(exc)})
                    analyzer_statuses["contrast"] = {"status": "failed", "error": str(exc)}

        # 6. Keyboard Analyzer (utilizes live browser session)
        if config["run_keyboard"]:
            if time.monotonic() >= deadline:
                analyzer_statuses["keyboard"] = {"status": "skipped_due_to_budget", "result": {}}
            else:
                try:
                    navigate(browser_session, clean_target_url)
                    res_key = analyze_keyboard(browser_session, render_result=render_result, options={"deadline": deadline})
                    analyzer_results["keyboard"] = res_key
                    analyzer_statuses["keyboard"] = {"status": res_key.get("summary", {}).get("status", "completed"), "result": res_key}
                except Exception as exc:
                    logger.exception("Keyboard analyzer failed: %s", exc)
                    errors.append({"stage": "keyboard", "error": str(exc)})
                    analyzer_statuses["keyboard"] = {"status": "failed", "error": str(exc)}

        # 7. ARIA Analyzer
        if config["run_aria"]:
            if time.monotonic() >= deadline:
                analyzer_statuses["aria"] = {"status": "skipped_due_to_budget", "result": {}}
            else:
                try:
                    res_aria = analyze_aria(render_result, options={"deadline": deadline})
                    analyzer_results["aria"] = res_aria
                    analyzer_statuses["aria"] = {"status": res_aria.get("summary", {}).get("status", "completed"), "result": res_aria}
                except Exception as exc:
                    logger.exception("ARIA analyzer failed: %s", exc)
                    errors.append({"stage": "aria", "error": str(exc)})
                    analyzer_statuses["aria"] = {"status": "failed", "error": str(exc)}

        # 8. Finding Analyzer (normalization and aggregation)
        findings_data: dict[str, Any] = {"status": "completed", "total": 0, "items": []}
        try:
            finding_res = analyze_findings(
                analyzer_results,
                options={
                    "max_findings": config["max_findings"],
                    "target_url": target_url,
                    "use_llm": (options or {}).get("use_llm", True),
                },
            )
            findings_data = {
                "status": "completed",
                "total": len(finding_res.get("findings", [])),
                "summary": finding_res.get("summary", {}),
                "items": finding_res.get("findings", []),
            }
            limitations.extend(finding_res.get("limitations", []))
        except Exception as exc:
            logger.exception("Finding analyzer failed: %s", exc)
            errors.append({"stage": "findings", "error": str(exc)})
            findings_data = {"status": "failed", "total": 0, "items": []}

    except Exception as top_exc:
        logger.exception("Audit run encountered error: %s", top_exc)
        errors.append({"stage": "runner", "error": str(top_exc)})
    finally:
        if browser_session:
            try:
                close_browser(browser_session)
            except Exception as close_exc:
                errors.append({"stage": "browser_cleanup", "error": str(close_exc)})

    duration_ms = int((time.monotonic() - start_time_monotonic) * 1000)

    # Determine overall audit status
    has_skipped = any(s.get("status") == "skipped_due_to_budget" for s in analyzer_statuses.values())
    has_failed_stage = any(s.get("status") == "failed" for s in analyzer_statuses.values())

    if render_result is None or not analyzer_results:
        overall_status = "failed"
    elif has_skipped:
        overall_status = "partial"
    elif findings_data.get("total", 0) > 0:
        overall_status = "issues_found"
    elif has_failed_stage:
        overall_status = "partial"
    else:
        overall_status = "passed"

    # Extract target metadata from render_result if available
    final_url = clean_target_url
    page_title = None
    if render_result:
        meta = render_result.get("metadata") or {}
        final_url = sanitize_url_for_report(meta.get("final_url") or clean_target_url)
        page_title = meta.get("title")

    # Coverage mapping
    coverage = {
        name: status_info.get("status", "unknown")
        for name, status_info in analyzer_statuses.items()
    }
    coverage["render"] = render_result.get("status", "failed") if render_result else "failed"

    return {
        "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
        "target": {
            "url": clean_target_url,
            "final_url": sanitize_url_for_report(final_url or clean_target_url),
            "title": page_title,
        },
        "audit": {
            "status": overall_status,
            "started_at": started_at_iso,
            "duration_ms": duration_ms,
            "timeout_seconds": config["timeout_seconds"],
        },
        "viewports": config["viewports"],
        "render": render_result or {"status": "failed"},
        "analyzers": analyzer_statuses,
        "findings": findings_data,
        "coverage": coverage,
        "limitations": limitations,
        "errors": errors,
    }


async def run_audit_async(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Asynchronously execute visual accessibility audit using shared LLMClient."""
    opts = options or {}
    report = await asyncio.to_thread(run_audit, target_url, {**opts, "use_llm": False})
    if opts.get("use_llm", False):
        analyzers_dict = {
            "contrast": report.get("analyzers", {}).get("contrast", {}).get("result", {}),
            "keyboard": report.get("analyzers", {}).get("keyboard", {}).get("result", {}),
            "aria": report.get("analyzers", {}).get("aria", {}).get("result", {}),
            "accessibility": report.get("analyzers", {}).get("accessibility", {}).get("result", {}),
            "visual": report.get("analyzers", {}).get("visual", {}).get("result", {}),
            "target_url": target_url,
        }
        findings_data = await analyze_findings_async(analyzers_dict, options=opts)
        report["findings"] = findings_data
    return report


def main() -> int:
    """CLI execution entrypoint."""
    parser = argparse.ArgumentParser(
        description="Run Visual and Accessibility Audit against a target website."
    )
    parser.add_argument("target_url", help="Target HTTP/HTTPS website URL to audit.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TOTAL_TIMEOUT_SECONDS, help="Total audit timeout in seconds.")
    parser.add_argument("--navigation-timeout", type=int, default=DEFAULT_NAVIGATION_TIMEOUT_SECONDS, help="Navigation timeout in seconds.")
    parser.add_argument("--no-visual", action="store_true", help="Disable visual analysis.")
    parser.add_argument("--no-accessibility", action="store_true", help="Disable semantic accessibility analysis.")
    parser.add_argument("--no-contrast", action="store_true", help="Disable contrast analysis.")
    parser.add_argument("--no-keyboard", action="store_true", help="Disable keyboard analysis.")
    parser.add_argument("--no-aria", action="store_true", help="Disable ARIA analysis.")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode.")
    parser.add_argument("--max-elements", type=int, default=5000, help="Maximum DOM elements to inspect per viewport.")
    parser.add_argument("--max-findings", type=int, default=500, help="Maximum findings to output.")
    parser.add_argument("--output", "-o", help="File path to write JSON output to (default: stdout).")

    args = parser.parse_args()

    opts = {
        "timeout_seconds": args.timeout,
        "navigation_timeout_seconds": args.navigation_timeout,
        "run_visual": not args.no_visual,
        "run_accessibility": not args.no_accessibility,
        "run_contrast": not args.no_contrast,
        "run_keyboard": not args.no_keyboard,
        "run_aria": not args.no_aria,
        "headless": not args.headed,
        "max_elements": args.max_elements,
        "max_findings": args.max_findings,
    }

    try:
        result = run_audit(args.target_url, options=opts)
        json_output = json.dumps(result, indent=2)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_output)
        else:
            print(json_output)

        audit_st = result.get("audit", {}).get("status")
        if audit_st == "not_testable":
            return 3
        elif audit_st == "failed":
            return 1
        return 0

    except AuditValidationError as err:
        sys.stderr.write(f"Validation error: {err}\n")
        return 2
    except Exception as err:
        sys.stderr.write(f"Fatal error: {err}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
