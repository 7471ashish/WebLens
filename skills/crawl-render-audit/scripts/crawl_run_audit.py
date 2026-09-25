"""
End-to-End Audit Runner (run_audit.py)
--------------------------------------
Entrypoint script that executes the complete 7-stage crawl-render audit pipeline
for any target URL and outputs structured findings.

Pipeline Workflow:
    Target URL
        |
        +--> 1. crawler.py (HTTP GET, redirects, status)
        |
        +--> 2. robots_checker.py (RFC 9309 robots.txt permissions)
        |
        +--> 3. sitemap_checker.py (sitemap discovery & XML parsing)
        |
        +--> 4. raw_html_analyzer.py (Initial pre-JS HTML facts)
        |
        +--> 5. render_analyzer.py (Headless Chromium Playwright render)
        |
        +--> 6. dom_comparator.py (Raw vs Rendered content gap analysis)
        |
        +--> 7. finding_builder.py (Severity, Evidence, Suggested Actions)
        |
        v
    Final Normalized Audit Report
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any

from crawl_crawler import crawl_url
from dom_comparator import compare_dom
from crawl_finding_builder import build_findings, build_findings_async
from raw_html_analyzer import analyze_raw_html
from render_analyzer import analyze_rendered_page
from robots_checker import check_robots
from sitemap_checker import check_sitemap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("crawl_render_audit.runner")


from page_discovery import (
    DEFAULT_MAX_PAGES,
    DiscoveredPage,
    discover_and_select_representative_pages,
    normalize_url,
    is_same_site,
)

async def audit_single_page(
    page_url: str,
    page_type: str = "general",
    options: dict[str, Any] | None = None,
    prefetched_crawl: dict[str, Any] | None = None,
    prefetched_raw_html: dict[str, Any] | None = None,
    robots_result: dict[str, Any] | None = None,
    sitemap_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit an individual webpage through crawl, raw HTML, render, DOM comparison, and finding builder."""
    opts = options or {}
    t0 = time.perf_counter()

    # Stage 1: HTTP Crawl
    if prefetched_crawl is not None:
        crawl_result = prefetched_crawl
    else:
        crawl_result = await crawl_url(page_url, options=opts)

    # Stage 2: Raw HTML analysis
    if prefetched_raw_html is not None:
        raw_html_result = prefetched_raw_html
    else:
        raw_html_body = crawl_result.get("body", "") if crawl_result.get("success") else ""
        raw_html_result = analyze_raw_html(
            html=raw_html_body,
            target_url=crawl_result.get("final_url") or page_url,
            response_metadata=crawl_result,
            options=opts,
        )

    # Stage 3: Playwright Browser Render analysis (skip if crawl failed hard)
    if crawl_result.get("success"):
        render_result = await analyze_rendered_page(
            target_url=crawl_result.get("final_url") or page_url,
            options=opts,
        )
    else:
        render_result = {
            "skill": "crawl-render-audit",
            "component": "render_analyzer",
            "target_url": page_url,
            "rendering": {"status": "skipped", "navigation_completed": False},
            "errors": crawl_result.get("errors", []),
        }

    # Stage 4: DOM / Content gap comparison
    dom_comp_result = compare_dom(
        raw_result=raw_html_result,
        rendered_result=render_result,
        options=opts,
    )

    page_obs = {
        "crawler": crawl_result,
        "robots": robots_result or {},
        "sitemap": sitemap_result or {},
        "raw_html": raw_html_result,
        "rendered": render_result,
        "dom_comparison": dom_comp_result,
    }

    # Stage 5: Finding Generation for this page
    findings = await build_findings_async(page_obs, options=opts)
    duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "url": page_url,
        "final_url": crawl_result.get("final_url") or page_url,
        "page_type": page_type,
        "success": bool(crawl_result.get("success")),
        "status_code": crawl_result.get("status_code"),
        "duration_ms": duration_ms,
        "crawl_result": crawl_result,
        "raw_html_result": raw_html_result,
        "render_result": render_result,
        "dom_comparison_result": dom_comp_result,
        "findings": findings,
        "observations": page_obs,
    }


async def run_complete_site_audit(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run an end-to-end multi-page website audit across representative site pages.

    Args:
        target_url: Target URL of the website.
        options: Configuration options (max_pages, timeout_seconds, etc.).

    Returns:
        Structured multi-page audit report.
    """
    opts = options or {}
    start_time = time.perf_counter()
    max_pages = int(opts.get("max_pages", DEFAULT_MAX_PAGES))
    timeout_sec = float(opts.get("timeout_seconds", 240))
    deadline = start_time + timeout_sec

    logger.info("==================================================")
    logger.info("Starting Bounded Multi-Page Audit for: %s (max_pages=%d)", target_url, max_pages)
    logger.info("==================================================")

    # 1. Robots.txt check & strict enforcement
    logger.info("[1/4] Checking robots.txt directives...")
    robots_result = await check_robots(target_url, options=opts)
    eval_dict = robots_result.get("target_url_evaluation", {}) if isinstance(robots_result, dict) else {}
    robots_allowed = eval_dict.get("allowed") if "allowed" in eval_dict else robots_result.get("is_allowed", True)

    site_ident = target_url.split("://")[-1].split("/")[0].replace("www.", "")

    if robots_allowed is False:
        logger.warning("Robots.txt disallows automated crawling for %s. Halting active deeper crawl.", target_url)
        total_duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        # Generate robots findings
        blocked_findings = build_findings({"robots": robots_result}, options=opts)
        if not blocked_findings:
            blocked_findings = [{
                "id": "CR-ROBOTS-DISALLOW",
                "category": "crawlability",
                "severity": "high",
                "title": "Robots.txt Disallows Automated Crawling",
                "description": f"Robots.txt on {target_url} disallows automated crawlers from fetching pages.",
                "evidence": [f"Robots.txt evaluation returned allowed=False for URL {target_url}."],
                "suggested_action": "Update robots.txt directives to permit legitimate AI search agents.",
            }]
        cov_info = {
            "pages_discovered": 1,
            "pages_selected": 0,
            "pages_crawled": 0,
            "pages_successfully_audited": 0,
            "page_types_covered": ["blocked"],
            "robots_checked": True,
            "sitemap_checked": False,
            "render_evaluated": False,
        }
        return {
            "skill": "crawl-render-audit",
            "site": site_ident,
            "target_url": target_url,
            "audit_duration_ms": total_duration_ms,
            "robots_allowed": False,
            "coverage": cov_info,
            "audit_metadata": cov_info,
            "summary": {
                "status_code": None,
                "robots_allowed": False,
                "sitemaps_found": 0,
                "total_pages_crawled": 0,
                "total_findings": len(blocked_findings),
                "critical_findings": sum(1 for f in blocked_findings if f["severity"] == "critical"),
                "high_findings": sum(1 for f in blocked_findings if f["severity"] == "high"),
                "medium_findings": sum(1 for f in blocked_findings if f["severity"] == "medium"),
                "low_findings": sum(1 for f in blocked_findings if f["severity"] == "low"),
            },
            "findings": blocked_findings,
            "audited_pages": [],
            "raw_observations": {
                "robots": robots_result,
            },
        }

    # 2. Sitemap check (discovers URLs & sitemap indexes)
    logger.info("[2/4] Discovering and validating sitemaps...")
    sitemap_result = await check_sitemap(target_url, robots_result=robots_result, options=opts)
    sitemap_urls = sitemap_result.get("discovered_urls", [])

    # 3. Initial Target URL Crawl & Link Extraction
    logger.info("[3/4] Crawling primary target page & extracting link graph...")
    primary_crawl = await crawl_url(target_url, options=opts)
    primary_raw_body = primary_crawl.get("body", "") if primary_crawl.get("success") else ""
    primary_raw_html = analyze_raw_html(
        html=primary_raw_body,
        target_url=primary_crawl.get("final_url") or target_url,
        response_metadata=primary_crawl,
        options=opts,
    )
    internal_links = primary_raw_html.get("links", {}).get("internal_links", [])
    canonical_val = primary_raw_html.get("canonical", {}).get("value")

    # 4. Representative Page Selection
    logger.info("[4/4] Selecting representative diverse page sample...")
    selected_pages = discover_and_select_representative_pages(
        target_url=target_url,
        sitemap_urls=sitemap_urls,
        internal_links=internal_links,
        canonical_url=canonical_val,
        options={"max_pages": max_pages, "allow_private_ips": opts.get("allow_private_ips", False)},
    )

    # Compute true unique discovery count across all normalized candidates
    discovered_set: set[str] = set()
    is_local = "127.0.0.1" in target_url or "localhost" in target_url or bool(opts.get("allow_private_ips"))
    norm_t = normalize_url(target_url, allow_private=is_local)
    if norm_t:
        discovered_set.add(norm_t)

    for sm_u in sitemap_urls:
        if isinstance(sm_u, str):
            norm_u = normalize_url(sm_u, base_url=target_url, allow_private=is_local)
            if norm_u and is_same_site(norm_u, target_url):
                discovered_set.add(norm_u)

    for il in internal_links:
        raw_u = il.get("url") or il.get("href") if isinstance(il, dict) else (il if isinstance(il, str) else "")
        if raw_u:
            norm_u = normalize_url(raw_u, base_url=target_url, allow_private=is_local)
            if norm_u and is_same_site(norm_u, target_url):
                discovered_set.add(norm_u)

    if canonical_val:
        norm_c = normalize_url(canonical_val, base_url=target_url, allow_private=is_local)
        if norm_c and is_same_site(norm_c, target_url):
            discovered_set.add(norm_c)

    total_discovered = max(len(discovered_set), len(selected_pages))
    logger.info("Selected %d representative pages for deep multi-page audit", len(selected_pages))

    audited_pages_list: list[dict[str, Any]] = []
    all_site_findings: list[dict[str, Any]] = []

    # Audit each selected page sequentially (with remaining budget enforcement)
    for idx, page_candidate in enumerate(selected_pages, 1):
        now = time.perf_counter()
        if now >= deadline - 3.0:
            logger.warning("Approaching global deadline. Stopping multi-page audit at page %d/%d", idx, len(selected_pages))
            break

        logger.info(
            "Auditing page [%d/%d] (%s): %s",
            idx,
            len(selected_pages),
            page_candidate.page_type,
            page_candidate.url,
        )

        is_primary = (page_candidate.url == target_url or page_candidate.url == primary_crawl.get("final_url"))
        pre_crawl = primary_crawl if is_primary else None
        pre_raw = primary_raw_html if is_primary else None

        page_report = await audit_single_page(
            page_url=page_candidate.url,
            page_type=page_candidate.page_type,
            options=opts,
            prefetched_crawl=pre_crawl,
            prefetched_raw_html=pre_raw,
            robots_result=robots_result if is_primary else None,
            sitemap_result=sitemap_result if is_primary else None,
        )

        audited_pages_list.append(page_report)
        all_site_findings.extend(page_report.get("findings", []))

    total_duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    successful_pages = [p for p in audited_pages_list if p.get("success")]
    page_types_covered = list(dict.fromkeys(p["page_type"] for p in audited_pages_list))

    # Primary observations for canonical evidence context
    primary_page_data = audited_pages_list[0] if audited_pages_list else {}
    primary_obs = primary_page_data.get("observations", {})

    logger.info("==================================================")
    logger.info(
        "Multi-Page Audit Completed in %.2f ms | Pages Crawled: %d/%d | Raw Findings: %d",
        total_duration_ms,
        len(audited_pages_list),
        len(selected_pages),
        len(all_site_findings),
    )
    logger.info("==================================================")

    cov_dict = {
        "pages_discovered": total_discovered,
        "pages_selected": len(selected_pages),
        "pages_crawled": len(audited_pages_list),
        "pages_successfully_audited": len(successful_pages),
        "page_types_covered": page_types_covered,
        "robots_checked": bool(robots_result),
        "sitemap_checked": bool(sitemap_result),
        "render_evaluated": bool(primary_obs.get("rendered")),
    }

    return {
        "skill": "crawl-render-audit",
        "site": site_ident,
        "target_url": target_url,
        "audit_duration_ms": total_duration_ms,
        "robots_allowed": robots_allowed,
        "coverage": cov_dict,
        "audit_metadata": cov_dict,
        "summary": {
            "status_code": primary_page_data.get("status_code"),
            "robots_allowed": robots_allowed,
            "sitemaps_found": len(sitemap_result.get("sitemaps", [])),
            "total_pages_crawled": len(audited_pages_list),
            "total_pages_successful": len(successful_pages),
            "total_findings": len(all_site_findings),
            "critical_findings": sum(1 for f in all_site_findings if f["severity"] == "critical"),
            "high_findings": sum(1 for f in all_site_findings if f["severity"] == "high"),
            "medium_findings": sum(1 for f in all_site_findings if f["severity"] == "medium"),
            "low_findings": sum(1 for f in all_site_findings if f["severity"] == "low"),
        },
        "findings": all_site_findings,
        "audited_pages": audited_pages_list,
        "raw_observations": primary_obs or {
            "crawler": primary_crawl,
            "robots": robots_result,
            "sitemap": sitemap_result,
            "raw_html": primary_raw_html,
        },
    }


async def run_complete_audit(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run complete audit. Defaults to bounded multi-page audit unless single_page option is True.
    """
    opts = options or {}
    if opts.get("single_page", False):
        res = await audit_single_page(target_url, options=opts)
        return {
            "skill": "crawl-render-audit",
            "target_url": target_url,
            "audit_duration_ms": res.get("duration_ms", 0),
            "summary": {
                "status_code": res.get("status_code"),
                "total_findings": len(res.get("findings", [])),
            },
            "findings": res.get("findings", []),
            "raw_observations": res.get("observations", {}),
        }
    return await run_complete_site_audit(target_url, options=opts)


def print_formatted_report(report: dict[str, Any]) -> None:
    """Print human-readable terminal audit summary."""
    summary = report["summary"]
    findings = report["findings"]

    print("\n" + "=" * 70)
    print(f" CRAWL-RENDER AUDIT REPORT: {report['target_url']}")
    print("=" * 70)
    print(f" Duration       : {report['audit_duration_ms']} ms")
    print(f" HTTP Status    : {summary['status_code']}")
    print(f" Robots Allowed : {summary['robots_allowed']}")
    print(f" Sitemaps Found : {summary['sitemaps_found']}")
    print(f" Raw Text Chars : {summary['raw_text_chars']:,} chars")
    print(f" Rendered Text  : {summary['rendered_text_chars']:,} chars")
    print(f" Render Gap     : {summary['render_gap_ratio']}x" if summary['render_gap_ratio'] else " Render Gap     : N/A")
    print(f" Total Findings : {summary['total_findings']} (Critical: {summary['critical_findings']}, High: {summary['high_findings']}, Medium: {summary['medium_findings']}, Low: {summary['low_findings']})")
    print("=" * 70)

    if not findings:
        print("\n🎉 [PASS] No accessibility, crawlability, or rendering issues detected!\n")
        return

    print("\nFINDINGS DETECTED:")
    print("-" * 70)
    for i, f in enumerate(findings, 1):
        sev = f["severity"].upper()
        badge = f"[{sev}]"
        print(f"{i}. {badge} ({f['id']}) - {f['title']}")
        print(f"   Category        : {f['category']}")
        print(f"   Description     : {f['description']}")
        print(f"   Suggested Action: {f['suggested_action']}")
        if f.get("evidence"):
            ev = f["evidence"][0]
            print(f"   Evidence        : {ev.get('details')}")
        print()
    print("=" * 70)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python run_audit.py <TARGET_URL> [--json] [--save-json <OUTPUT_PATH>]")
        print("Example: python run_audit.py https://example.com")
        sys.exit(0)

    target_url = sys.argv[1]
    is_json_output = "--json" in sys.argv
    save_path = None
    if "--save-json" in sys.argv:
        idx = sys.argv.index("--save-json")
        if idx + 1 < len(sys.argv):
            save_path = sys.argv[idx + 1]

    report = asyncio.run(run_complete_audit(target_url))

    if save_path:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n[Saved full audit report to {save_path}]")

    if is_json_output:
        print(json.dumps(report, indent=2))
    else:
        print_formatted_report(report)


if __name__ == "__main__":
    main()
