"""
Render Analyzer Module (render_analyzer.py)
-------------------------------------------
Deterministic, Playwright-based browser rendering and post-hydration DOM analyzer
for the `crawl-render-audit` skill.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            +---- crawler.py
            |       |
            |       v
            |   HTTP response
            |
            +---- robots_checker.py
            |
            +---- sitemap_checker.py
            |
            +---- raw_html_analyzer.py
            |       |
            |       v
            |   RAW HTML observations
            |
            +---- render_analyzer.py  <-- (THIS MODULE)
            |       |
            |       v
            |   RENDERED DOM observations
            |
            +---- dom_comparator.py (Compares raw vs rendered)
            |
            +---- finding_builder.py
                    |
                    v
            final structured findings

This module executes client-side JavaScript in a real headless Chromium browser,
captures post-hydration DOM metrics, evaluates visible text (innerText), detects runtime
JS errors, and collects factual observations without assigning audit severity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
try:
    from raw_html_analyzer import extract_clean_title
except ImportError:
    from .raw_html_analyzer import extract_clean_title
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Response as PlaywrightResponse,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Import safe URL and SSRF validators from crawler
try:
    from crawl_crawler import is_safe_target, validate_and_normalize_url
except ImportError:
    from .crawl_crawler import is_safe_target, validate_and_normalize_url

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.render_analyzer")
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
    "timeout_ms": 20000,
    "navigation_timeout_ms": 20000,
    "wait_until": "networkidle",  # "load", "domcontentloaded", "networkidle"
    "wait_after_load_ms": 1000,
    "viewport_width": 1280,
    "viewport_height": 720,
    "ignore_https_errors": False,
    "max_dom_size_bytes": 5_000_000,  # 5 MB
    "capture_screenshot": False,
    "allow_private_ips": False,  # SSRF protection
}



# Known SPA root container selectors
KNOWN_SPA_ROOT_SELECTORS: tuple[str, ...] = (
    "#root",
    "#app",
    "#__next",
    "#__nuxt",
    "#app-root",
    "#___gatsby",
    "[data-reactroot]",
    "#mount",
    "#main-app",
)

# Common semantic HTML5 tag names
SEMANTIC_TAGS: tuple[str, ...] = (
    "main",
    "article",
    "section",
    "nav",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
)



# Helper Functions: Classification & Extraction


def _clean_text(text: str | None) -> str:
    """Normalize whitespace in text content."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _is_absolute_url(url: str) -> bool:
    """Check if URL has an explicit scheme (http/https)."""
    if not url:
        return False
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def _classify_link(href: str | None, base_url: str) -> dict[str, bool]:
    """Classify a link as internal, external, relative, empty, or fragment-only."""
    if href is None or href.strip() == "":
        return {"is_empty": True, "is_fragment": False, "is_internal": False, "is_external": False, "is_relative": False}

    href_clean = href.strip()
    if href_clean.startswith("#"):
        return {"is_empty": False, "is_fragment": True, "is_internal": True, "is_external": False, "is_relative": False}

    parsed_target = urlparse(href_clean)
    parsed_base = urlparse(base_url)

    if not parsed_target.scheme and not parsed_target.netloc:
        return {"is_empty": False, "is_fragment": False, "is_internal": True, "is_external": False, "is_relative": True}

    base_netloc = parsed_base.netloc.lower()
    target_netloc = parsed_target.netloc.lower()

    if target_netloc == base_netloc or target_netloc.endswith("." + base_netloc):
        return {"is_empty": False, "is_fragment": False, "is_internal": True, "is_external": False, "is_relative": False}

    return {"is_empty": False, "is_fragment": False, "is_internal": False, "is_external": True, "is_relative": False}


def _extract_json_ld_types(data: Any) -> list[str]:
    """Recursively extract schema.org @type values from JSON-LD structures."""
    types: list[str] = []
    if isinstance(data, dict):
        t = data.get("@type")
        if isinstance(t, str) and t.strip():
            types.append(t.strip())
        elif isinstance(t, list):
            types.extend(str(item).strip() for item in t if item)

        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                types.extend(_extract_json_ld_types(item))

        for val in data.values():
            if isinstance(val, (dict, list)):
                types.extend(_extract_json_ld_types(val))

    elif isinstance(data, list):
        for item in data:
            types.extend(_extract_json_ld_types(item))

    return list(dict.fromkeys(types))


def parse_rendered_dom_structure(
    rendered_html: str,
    final_url: str,
    visible_body_text: str,
    main_visible_text: str,
    article_visible_text: str,
    max_dom_size_bytes: int = 5_000_000,
) -> dict[str, Any]:
    """
    Parse the captured rendered DOM string into structured factual observations.
    """
    dom_bytes_len = len(rendered_html.encode("utf-8"))
    truncated = False

    if dom_bytes_len > max_dom_size_bytes:
        rendered_html = rendered_html[:max_dom_size_bytes]
        truncated = True

    soup = BeautifulSoup(rendered_html, "html.parser")

    # 1. Title
    title_exists, title_val, title_dup_count = extract_clean_title(soup, rendered_html)
    title_char_count = len(title_val) if title_val else 0

    # 2. Meta Description
    meta_desc_tag = soup.find(
        "meta",
        attrs={"name": lambda n: bool(n and n.strip().lower() == "description")},
    )
    meta_desc_exists = False
    meta_desc_content: str | None = None
    meta_desc_char_count = 0
    if meta_desc_tag and meta_desc_tag.get("content"):
        meta_desc_exists = True
        meta_desc_content = meta_desc_tag.get("content", "").strip()
        meta_desc_char_count = len(meta_desc_content)

    # 3. Canonical Link
    canonical_tags = soup.find_all(
        "link",
        attrs={"rel": lambda r: bool(r and "canonical" in (r if isinstance(r, list) else [r]))},
    )
    canonical_exists = len(canonical_tags) > 0
    canonical_val: str | None = None
    canonical_is_abs = False
    canonical_valid = False
    canonical_normalized: str | None = None

    if canonical_exists:
        canonical_val = canonical_tags[0].get("href", "").strip()
        if canonical_val:
            canonical_is_abs = _is_absolute_url(canonical_val)
            try:
                if canonical_is_abs:
                    canonical_normalized = canonical_val
                    canonical_valid = True
                else:
                    canonical_normalized = urljoin(final_url, canonical_val)
                    canonical_valid = bool(urlparse(canonical_normalized).scheme)
            except Exception:
                canonical_valid = False

    # 4. Language
    html_tag = soup.find("html")
    lang_val = html_tag.get("lang", "").strip() if html_tag and html_tag.get("lang") else None

    # 5. Headings
    headings_data = {
        "h1_count": len(soup.find_all("h1")),
        "h2_count": len(soup.find_all("h2")),
        "h3_count": len(soup.find_all("h3")),
        "h4_count": len(soup.find_all("h4")),
        "h5_count": len(soup.find_all("h5")),
        "h6_count": len(soup.find_all("h6")),
    }
    h1_tags = soup.find_all("h1")
    headings_data["h1_text"] = [_clean_text(h.get_text()) for h in h1_tags if _clean_text(h.get_text())]
    headings_data["multiple_h1"] = headings_data["h1_count"] > 1

    all_headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    headings_data["empty_headings_count"] = sum(1 for h in all_headings if not _clean_text(h.get_text()))

    # 6. Text Content Metrics
    clean_visible = _clean_text(visible_body_text)
    visible_char_count = len(clean_visible)
    visible_word_count = len(clean_visible.split()) if clean_visible else 0

    main_tag = soup.find("main")
    article_tag = soup.find("article")

    content_location = {
        "main_present": bool(main_tag),
        "article_present": bool(article_tag),
        "main_visible_text_characters": len(_clean_text(main_visible_text)),
        "article_visible_text_characters": len(_clean_text(article_visible_text)),
    }

    text_content = {
        "visible_character_count": visible_char_count,
        "visible_word_count": visible_word_count,
        "meaningful_text_available": visible_char_count >= 200,
    }

    # 7. Links
    a_tags = soup.find_all("a")
    link_stats = {"total": len(a_tags), "internal": 0, "external": 0, "relative": 0, "empty": 0, "fragment_only": 0}
    for a in a_tags:
        href = a.get("href")
        classified = _classify_link(href, final_url)
        if classified["is_empty"]:
            link_stats["empty"] += 1
        elif classified["is_fragment"]:
            link_stats["fragment_only"] += 1
        else:
            if classified["is_relative"]:
                link_stats["relative"] += 1
            if classified["is_internal"]:
                link_stats["internal"] += 1
            elif classified["is_external"]:
                link_stats["external"] += 1

    # 8. Images
    img_tags = soup.find_all("img")
    missing_alt_count = 0
    empty_alt_count = 0
    images_list = []

    for img in img_tags:
        src = img.get("src", "").strip() if img.get("src") else None
        has_alt = "alt" in img.attrs
        alt_val = img.get("alt", "")

        if not has_alt:
            missing_alt_count += 1
        elif alt_val == "":
            empty_alt_count += 1

        if len(images_list) < 50:
            entry = {
                "src": src,
                "alt_present": has_alt,
                "alt": alt_val if has_alt else None,
            }
            if img.get("width"):
                entry["width"] = img.get("width")
            if img.get("height"):
                entry["height"] = img.get("height")
            if img.get("loading"):
                entry["loading"] = img.get("loading")
            images_list.append(entry)

    images_data = {
        "count": len(img_tags),
        "missing_alt_count": missing_alt_count,
        "empty_alt_count": empty_alt_count,
        "images": images_list,
    }

    # 9. Structured Data (JSON-LD)
    json_ld_scripts = soup.find_all(
        "script",
        attrs={"type": lambda t: bool(t and "application/ld+json" in t.lower())},
    )
    valid_json_ld = 0
    invalid_json_ld = 0
    detected_types: list[str] = []

    for s in json_ld_scripts:
        script_text = (s.string or s.get_text() or "").strip()
        if not script_text:
            continue
        try:
            data = json.loads(script_text)
            valid_json_ld += 1
            detected_types.extend(_extract_json_ld_types(data))
        except Exception:
            invalid_json_ld += 1

    structured_data = {
        "json_ld_blocks": len(json_ld_scripts),
        "valid_json_ld_blocks": valid_json_ld,
        "invalid_json_ld_blocks": invalid_json_ld,
        "types": list(dict.fromkeys(detected_types)),
    }

    # 10. Meta Robots
    robots_meta_tag = soup.find(
        "meta",
        attrs={"name": lambda n: bool(n and n.strip().lower() in ("robots", "googlebot", "bingbot"))},
    )
    robots_meta_exists = False
    robots_directives: list[str] = []
    if robots_meta_tag and robots_meta_tag.get("content"):
        robots_meta_exists = True
        robots_directives = [d.strip().lower() for d in re.split(r"[,;]+", robots_meta_tag.get("content", "")) if d.strip()]

    # 11. Semantic Structure
    semantic_counts = {tag: len(soup.find_all(tag)) for tag in SEMANTIC_TAGS}

    # 12. Application Roots
    detected_roots: list[str] = []
    for sel in KNOWN_SPA_ROOT_SELECTORS:
        if sel.startswith("#"):
            if soup.find(id=sel[1:]):
                detected_roots.append(sel[1:])
        elif sel.startswith("["):
            attr_name = sel[1:-1]
            if soup.find(attrs={attr_name: True}):
                detected_roots.append(attr_name)

    # 13. Empty / Shell Page Detection
    empty_reasons = []
    is_empty_page = False

    if visible_char_count < 100:
        is_empty_page = True
        empty_reasons.append(f"Rendered visible text volume is very low ({visible_char_count} characters)")

    if len(all_headings) == 0 and len(a_tags) == 0 and visible_char_count < 200:
        is_empty_page = True
        empty_reasons.append("No headings or links present after browser rendering")

    content_status = {
        "meaningful_content_available": visible_char_count >= 200,
        "possible_empty_rendered_page": is_empty_page,
        "reasons": empty_reasons,
    }

    client_rendering = {
        "application_root_detected": len(detected_roots) > 0,
        "rendered_content_available": visible_char_count >= 200,
    }

    all_elements = soup.find_all(True)

    return {
        "dom": {
            "size_bytes": dom_bytes_len,
            "truncated": truncated,
            "element_count": len(all_elements),
        },
        "title": {
            "exists": title_exists,
            "value": title_val,
            "character_count": title_char_count,
            "duplicate_count": title_dup_count,
        },
        "meta_description": {
            "exists": meta_desc_exists,
            "content": meta_desc_content,
            "character_count": meta_desc_char_count,
        },
        "canonical": {
            "exists": canonical_exists,
            "value": canonical_val,
            "is_absolute": canonical_is_abs,
            "is_valid": canonical_valid,
            "normalized_url": canonical_normalized,
            "duplicate_count": max(0, len(canonical_tags) - 1),
        },
        "language": {
            "declared": bool(lang_val),
            "value": lang_val,
        },
        "content_location": content_location,
        "text_content": text_content,
        "headings": headings_data,
        "links": link_stats,
        "images": images_data,
        "structured_data": structured_data,
        "robots_meta": {
            "exists": robots_meta_exists,
            "directives": robots_directives,
        },
        "semantic_structure": semantic_counts,
        "application_roots": detected_roots,
        "client_rendering": client_rendering,
        "content_status": content_status,
    }



# Main Asynchronous Render Analyzer Function


async def analyze_rendered_page(
    target_url: str,
    options: dict[str, Any] | None = None,
    browser_instance: Browser | None = None,
    context_instance: BrowserContext | None = None,
    page_instance: Page | None = None,
) -> dict[str, Any]:
    """
    Launch headless Chromium via Playwright, execute client-side scripts,
    and capture rendered DOM observations.

    Args:
        target_url: Target URL to navigate to.
        options: Configuration options (timeout_ms, wait_until, wait_after_load_ms, etc.).
        browser_instance: Optional existing Playwright Browser instance (for testing).

    Returns:
        Structured JSON-serializable dictionary with factual rendered observations.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    nav_timeout_ms = int(opts.get("navigation_timeout_ms", DEFAULT_OPTIONS["navigation_timeout_ms"]))
    wait_until = str(opts.get("wait_until", DEFAULT_OPTIONS["wait_until"]))
    wait_after_load_ms = int(opts.get("wait_after_load_ms", DEFAULT_OPTIONS["wait_after_load_ms"]))
    viewport_w = int(opts.get("viewport_width", DEFAULT_OPTIONS["viewport_width"]))
    viewport_h = int(opts.get("viewport_height", DEFAULT_OPTIONS["viewport_height"]))
    ignore_https_errors = bool(opts.get("ignore_https_errors", DEFAULT_OPTIONS["ignore_https_errors"]))
    max_dom_size_bytes = int(opts.get("max_dom_size_bytes", DEFAULT_OPTIONS["max_dom_size_bytes"]))
    user_agent = str(opts.get("user_agent", DEFAULT_OPTIONS["user_agent"]))
    allow_private_ips = bool(opts.get("allow_private_ips", DEFAULT_OPTIONS["allow_private_ips"]))

    # Step 1: URL & SSRF Validation
    is_valid, norm_target, val_err = validate_and_normalize_url(target_url)
    if not is_valid or norm_target is None:
        logger.warning("Invalid URL supplied to analyze_rendered_page: %s (%s)", target_url, val_err)
        return {
            "skill": "crawl-render-audit",
            "component": "render_analyzer",
            "target_url": target_url,
            "navigation": {
                "requested_url": target_url,
                "final_url": None,
                "status_code": None,
                "redirected": False,
                "redirect_count": 0,
            },
            "rendering": {
                "status": "navigation_error",
                "navigation_completed": False,
                "javascript_executed": False,
                "navigation_time_ms": 0,
                "wait_after_load_ms": wait_after_load_ms,
            },
            "errors": [{
                "type": "invalid_url",
                "message": val_err or "Invalid URL supplied",
            }],
        }

    parsed_target = urlparse(norm_target)
    is_safe, ssrf_err = is_safe_target(parsed_target.netloc, allow_private_ips)
    if not is_safe:
        logger.warning("SSRF check failed for %s: %s", norm_target, ssrf_err)
        return {
            "skill": "crawl-render-audit",
            "component": "render_analyzer",
            "target_url": norm_target,
            "navigation": {
                "requested_url": norm_target,
                "final_url": None,
                "status_code": None,
                "redirected": False,
                "redirect_count": 0,
            },
            "rendering": {
                "status": "navigation_error",
                "navigation_completed": False,
                "javascript_executed": False,
                "navigation_time_ms": 0,
                "wait_after_load_ms": wait_after_load_ms,
            },
            "errors": [{
                "type": "security_error",
                "message": ssrf_err or "Access to restricted target prohibited",
            }],
        }

    logger.info("Starting browser rendering for %s (timeout: %d ms, wait: %s)", norm_target, nav_timeout_ms, wait_until)

    # Event Collectors
    js_errors: list[dict[str, str]] = []
    console_errors: list[str] = []
    console_warnings: list[str] = []
    failed_resources: list[dict[str, str]] = []
    response_headers: dict[str, str] = {}

    own_playwright = False
    own_context = False
    own_page = False
    playwright_ctx = None
    browser: Browser | None = browser_instance
    context: BrowserContext | None = context_instance
    page: Page | None = page_instance

    start_mono = time.perf_counter()
    nav_status = "success"
    nav_completed = False
    status_code: int | None = None
    final_url = norm_target
    redirect_count = 0

    browser_launched = (page is not None)

    try:
        if page is None:
            if context is None:
                if browser is None:
                    own_playwright = True
                    playwright_ctx = await async_playwright().start()
                    browser = await playwright_ctx.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                    )

                own_context = True
                context = await browser.new_context(
                    user_agent=user_agent,
                    viewport={"width": viewport_w, "height": viewport_h},
                    ignore_https_errors=ignore_https_errors,
                )

            own_page = True
            page = await context.new_page()

        browser_launched = True

        # Listen for JavaScript errors
        def on_page_error(err: Any) -> None:
            msg = str(err)
            if len(js_errors) < 20:
                js_errors.append({"message": msg[:300]})

        page.on("pageerror", on_page_error)

        # Listen for Console messages
        def on_console(msg: Any) -> None:
            m_type = msg.type.lower()
            text = msg.text[:300]
            if m_type == "error" and len(console_errors) < 20:
                console_errors.append(text)
            elif m_type == "warning" and len(console_warnings) < 20:
                console_warnings.append(text)

        page.on("console", on_console)

        # Listen for Failed Network Resources
        def on_request_failed(req: Any) -> None:
            if len(failed_resources) < 20:
                failure = req.failure
                failed_resources.append({
                    "url": req.url[:200],
                    "resource_type": req.resource_type,
                    "error": str(failure)[:200] if failure else "Failed",
                })

        page.on("requestfailed", on_request_failed)

        # Execute Navigation with Fallback
        response: PlaywrightResponse | None = None
        try:
            response = await page.goto(
                norm_target,
                timeout=nav_timeout_ms,
                wait_until=wait_until,
            )
            nav_completed = True
        except PlaywrightTimeoutError:
            logger.warning("Playwright navigation timeout (%d ms) on %s for wait_until=%s", nav_timeout_ms, norm_target, wait_until)
            nav_status = "partial"
            # Try waiting for domcontentloaded if networkidle timed out
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
                nav_completed = True
            except Exception:
                nav_completed = False

        if response is not None:
            status_code = response.status
            try:
                response_headers = {k.lower(): v for k, v in (await response.all_headers()).items()}
            except Exception:
                pass

        final_url = page.url
        redirected = (final_url != norm_target)
        if redirected:
            redirect_count = 1

        # Post-load stabilization delay
        if wait_after_load_ms > 0:
            await asyncio.sleep(wait_after_load_ms / 1000.0)

        # Capture Rendered DOM
        rendered_html = await page.content()

        # Evaluate Visible Text (innerText obeys CSS visibility rules)
        visible_text_data = await page.evaluate(
            """() => {
                const bodyText = document.body ? document.body.innerText : '';
                const mainElem = document.querySelector('main');
                const articleElem = document.querySelector('article');
                return {
                    body: bodyText || '',
                    main: mainElem ? mainElem.innerText : '',
                    article: articleElem ? articleElem.innerText : ''
                };
            }"""
        )

        nav_duration_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)

        # Parse the captured rendered DOM
        parsed_obs = parse_rendered_dom_structure(
            rendered_html=rendered_html,
            final_url=final_url,
            visible_body_text=visible_text_data.get("body", ""),
            main_visible_text=visible_text_data.get("main", ""),
            article_visible_text=visible_text_data.get("article", ""),
            max_dom_size_bytes=max_dom_size_bytes,
        )

        x_robots_tag = response_headers.get("x-robots-tag")

        logger.info(
            "Render completed for %s in %.2f ms (status=%s, visible_chars=%d)",
            norm_target,
            nav_duration_ms,
            nav_status,
            parsed_obs["text_content"]["visible_character_count"],
        )

        return {
            "skill": "crawl-render-audit",
            "component": "render_analyzer",
            "target_url": norm_target,
            "navigation": {
                "requested_url": norm_target,
                "final_url": final_url,
                "status_code": status_code or 200,
                "redirected": redirected,
                "redirect_count": redirect_count,
            },
            "rendering": {
                "status": nav_status,
                "navigation_completed": nav_completed,
                "javascript_executed": True,
                "navigation_time_ms": nav_duration_ms,
                "wait_after_load_ms": wait_after_load_ms,
            },
            **parsed_obs,
            "robots_headers": {
                "x_robots_tag": x_robots_tag,
            },
            "javascript": {
                "error_count": len(js_errors),
                "errors": js_errors,
            },
            "console": {
                "error_count": len(console_errors),
                "warning_count": len(console_warnings),
            },
            "resources": {
                "failed_count": len(failed_resources),
                "failed": failed_resources,
            },
            "errors": [],
        }

    except Exception as exc:
        nav_duration_ms = round((time.perf_counter() - start_mono) * 1000.0, 2)
        err_msg = str(exc)
        
        # Clean single-line summary of error without multiline Call log or stack pollution
        clean_err_msg = re.split(r"[\r\n]+", err_msg)[0].strip()
        clean_err_msg = re.split(r"\s*(?:Call log|Call:)\b", clean_err_msg, flags=re.IGNORECASE)[0].strip()

        safe_final_url = re.split(r"[\r\n]+", str(final_url or norm_target))[0].strip()
        safe_final_url = re.split(r"\s*(?:Call log|Call:|Page\.goto)\b", safe_final_url, flags=re.IGNORECASE)[0].strip()

        is_launch_failure = (not browser_launched) or any(
            hint in err_msg.lower()
            for hint in (
                "executable doesn't exist",
                "browsertype.launch",
                "failed to launch",
                "chromium binary missing",
                "playwright is not installed",
            )
        )
        is_env_network_failure = any(
            hint in err_msg.lower()
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
                "navigation timeout",
            )
        )
        if is_launch_failure:
            status_val = "browser_launch_failure"
            err_type = "launch_error"
        elif is_env_network_failure:
            status_val = "environment_error"
            err_type = "environment_error"
        else:
            status_val = "browser_error"
            err_type = "render_error"

        logger.warning("Rendering failed for %s (%.2f ms, status=%s): %s", norm_target, nav_duration_ms, status_val, clean_err_msg)
        return {
            "skill": "crawl-render-audit",
            "component": "render_analyzer",
            "target_url": norm_target,
            "navigation": {
                "requested_url": norm_target,
                "final_url": safe_final_url,
                "status_code": status_code,
                "redirected": (safe_final_url != norm_target),
                "redirect_count": redirect_count,
            },
            "rendering": {
                "status": status_val,
                "navigation_completed": False,
                "javascript_executed": False,
                "navigation_time_ms": nav_duration_ms,
                "wait_after_load_ms": wait_after_load_ms,
            },
            "errors": [{
                "type": err_type,
                "message": clean_err_msg,
                "raw_details": err_msg[:300],
            }],
        }

    finally:
        # Guarantee clean resource shutdown
        if own_page and page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if own_context and context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if own_playwright and browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if own_playwright and playwright_ctx is not None:
            try:
                await playwright_ctx.stop()
            except Exception:
                pass


def analyze_rendered_page_sync(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous convenience wrapper around `analyze_rendered_page`."""
    return asyncio.run(analyze_rendered_page(target_url, options))



# CLI Testing Interface


def _cli_entrypoint() -> None:
    """CLI runner for direct command-line testing."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python render_analyzer.py <TARGET_URL> [--timeout N] [--wait-after N]")
        print("Example: python render_analyzer.py https://example.com")
        sys.exit(0)

    url = sys.argv[1]
    opts: dict[str, Any] = {}

    for i, arg in enumerate(sys.argv):
        if arg == "--timeout" and i + 1 < len(sys.argv):
            opts["navigation_timeout_ms"] = int(sys.argv[i + 1])
        elif arg == "--wait-after" and i + 1 < len(sys.argv):
            opts["wait_after_load_ms"] = int(sys.argv[i + 1])

    result = analyze_rendered_page_sync(url, options=opts)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
