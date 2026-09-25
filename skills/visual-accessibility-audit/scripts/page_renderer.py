"""
Rendering and evidence-collection layer for visual-accessibility-audit.

This module coordinates multi-viewport webpage rendering, document and DOM geometry
measurement, fixed/sticky element detection, iframe auditing, and screenshot capture
using browser.py as its sole browser interface.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any

# Package/local sibling imports
try:
    from .browser import (
        BrowserSession,
        capture_screenshot,
        close_browser,
        get_computed_style,
        get_dom_snapshot,
        get_page_info,
        launch_browser,
        navigate,
        set_viewport,
    )
except ImportError:
    from browser import (
        BrowserSession,
        capture_screenshot,
        close_browser,
        get_computed_style,
        get_dom_snapshot,
        get_page_info,
        launch_browser,
        navigate,
        set_viewport,
    )

logger = logging.getLogger("visual_accessibility_audit.page_renderer")

DEFAULT_TOTAL_BUDGET_MS = 300_000
DEFAULT_NAVIGATION_TIMEOUT_MS = 15_000
DEFAULT_WAIT_AFTER_NAV_MS = 500
DEFAULT_MAX_ELEMENTS = 5_000
DEFAULT_MAX_TEXT_LENGTH = 500

DEFAULT_VIEWPORT_PROFILES = [
    {"name": "desktop", "width": 1440, "height": 900, "device_scale_factor": 1.0},
    {"name": "tablet", "width": 1024, "height": 768, "device_scale_factor": 2.0},
    {"name": "mobile", "width": 390, "height": 844, "device_scale_factor": 3.0},
]


def _format_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_text(text: str | None, max_len: int = DEFAULT_MAX_TEXT_LENGTH) -> str | None:
    if not text:
        return None
    normalized = " ".join(text.split())
    if len(normalized) > max_len:
        return normalized[:max_len] + "... [truncated]"
    return normalized


def render_viewport(
    session: BrowserSession,
    target_url: str,
    viewport: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Render target webpage at a specific viewport dimension and collect visual/DOM evidence.

    Args:
        session: Active BrowserSession instance from browser.py.
        target_url: Webpage URL to navigate to and render.
        viewport: Dictionary specifying name, width, height, and optional device_scale_factor.
        options: Optional configuration dictionary.

    Returns:
        Structured dictionary containing page metadata, document geometry, DOM elements,
        layout measurements, iframe observations, and screenshot reference.
    """
    opts = options or {}
    vp_name = str(viewport.get("name", "custom"))
    vp_width = int(viewport.get("width", 1440))
    vp_height = int(viewport.get("height", 900))
    vp_scale = float(viewport.get("device_scale_factor", 1.0))

    vp_spec = {
        "name": vp_name,
        "width": vp_width,
        "height": vp_height,
        "device_scale_factor": vp_scale,
    }

    logger.info("Rendering viewport '%s' (%dx%d)", vp_name, vp_width, vp_height)
    vp_start_time = time.monotonic()

    # Step 1: Set Viewport Dimensions
    set_vp_res = set_viewport(session, width=vp_width, height=vp_height, device_scale_factor=vp_scale)
    if set_vp_res.get("status") == "failed":
        return {
            "name": vp_name,
            "status": "failed",
            "viewport": vp_spec,
            "error": {
                "type": "ViewportConfigurationError",
                "message": set_vp_res.get("message", "Failed to set viewport dimensions."),
            },
        }

    # Step 2: Navigate to Target URL
    nav_opts = dict(opts)
    nav_opts["wait_after_navigation_ms"] = int(opts.get("wait_after_navigation_ms", DEFAULT_WAIT_AFTER_NAV_MS))
    nav_res = navigate(session, target_url, nav_opts)

    if nav_res.get("status") != "completed":
        err_type = nav_res.get("error_type", "NavigationError")
        err_msg = nav_res.get("message", "Navigation failed.")
        logger.warning("Viewport '%s' navigation failed: %s (%s)", vp_name, err_type, err_msg)
        return {
            "name": vp_name,
            "status": nav_res.get("status", "failed"),
            "viewport": vp_spec,
            "error": {
                "type": err_type,
                "message": err_msg,
            },
            "navigation": nav_res.get("navigation", {}),
        }

    nav_meta = nav_res.get("navigation", {})

    # Step 3: Optional Lazy Load Scroll
    enable_scroll = bool(opts.get("enable_lazy_load_scroll", False))
    if enable_scroll and session.page:
        max_steps = min(10, max(1, int(opts.get("max_scroll_steps", 5))))
        try:
            session.page.evaluate(
                """(steps) => {
                    for (let i = 1; i <= steps; i++) {
                        window.scrollTo(0, (document.body.scrollHeight / steps) * i);
                    }
                    window.scrollTo(0, 0);
                }""",
                max_steps,
            )
            session.page.wait_for_timeout(200)
        except Exception as exc:
            logger.debug("Lazy load scroll encountered: %s", exc)

    # Step 4: Extract Document & Page Metadata via session.page
    page_data: dict[str, Any] = {
        "url": target_url,
        "final_url": nav_meta.get("final_url") or target_url,
        "title": "",
        "lang": None,
        "status": nav_meta.get("status_code", 200),
        "content_type": nav_meta.get("content_type", ""),
    }
    doc_geometry: dict[str, Any] = {
        "viewport_width": vp_width,
        "viewport_height": vp_height,
        "scroll_width": vp_width,
        "scroll_height": vp_height,
        "client_width": vp_width,
        "client_height": vp_height,
        "body_bounding_box": None,
        "html_bounding_box": None,
    }
    layout_signals: dict[str, Any] = {}
    fixed_elements: list[dict[str, Any]] = []
    sticky_elements: list[dict[str, Any]] = []
    iframe_elements: list[dict[str, Any]] = []
    cookie_banner_detected = False

    max_elements = int(opts.get("max_elements", DEFAULT_MAX_ELEMENTS))
    extracted_elements: list[dict[str, Any]] = []
    total_elements_available = 0
    is_truncated = False

    if session.page:
        try:
            inspect_script = f"""(args) => {{
                const maxEls = args.maxElements;
                const vpWidth = args.vpWidth;
                const vpHeight = args.vpHeight;

                const docEl = document.documentElement;
                const body = document.body;

                const bodyRect = body ? body.getBoundingClientRect() : null;
                const htmlRect = docEl ? docEl.getBoundingClientRect() : null;

                const docGeo = {{
                    viewport_width: vpWidth,
                    viewport_height: vpHeight,
                    scroll_width: docEl ? docEl.scrollWidth : 0,
                    scroll_height: docEl ? docEl.scrollHeight : 0,
                    client_width: docEl ? docEl.clientWidth : 0,
                    client_height: docEl ? docEl.clientHeight : 0,
                    body_bounding_box: bodyRect ? {{
                        x: bodyRect.x, y: bodyRect.y, width: bodyRect.width, height: bodyRect.height
                    }} : null,
                    html_bounding_box: htmlRect ? {{
                        x: htmlRect.x, y: htmlRect.y, width: htmlRect.width, height: htmlRect.height
                    }} : null
                }};

                const pageMeta = {{
                    title: document.title || '',
                    lang: docEl ? (docEl.getAttribute('lang') || null) : null
                }};

                // Collect iframes
                const iframes = Array.from(document.querySelectorAll('iframe')).map((ifr, idx) => {{
                    const r = ifr.getBoundingClientRect();
                    return {{
                        index: idx,
                        src: ifr.getAttribute('src') || null,
                        title: ifr.getAttribute('title') || null,
                        bounds: {{ x: r.x, y: r.y, width: r.width, height: r.height }},
                        cross_origin: 'not_inspected'
                    }};
                }});

                // Detect cookie banner cues
                let hasCookieBanner = false;
                const bannerSels = ['#cookie-banner', '.cookie-banner', '#consent-banner', '.consent-banner', '#onetrust-banner-sdk', '.qc-cmp2-container'];
                for (const sel of bannerSels) {{
                    if (document.querySelector(sel)) {{
                        hasCookieBanner = true;
                        break;
                    }}
                }}

                // Query all elements bounded
                const allEls = document.querySelectorAll('*');
                const totalCount = allEls.length;
                const targetEls = Array.from(allEls).slice(0, maxEls);

                const elementsList = [];
                const fixedList = [];
                const stickyList = [];

                let elementsExtendingBeyondViewport = 0;
                let elementsWiderThanViewport = 0;
                let negativePositionCount = 0;
                let zeroDimensionCount = 0;

                for (let i = 0; i < targetEls.length; i++) {{
                    const el = targetEls[i];
                    const tag = el.tagName.toLowerCase();
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();

                    const pos = style.position;
                    const zIndex = style.zIndex;
                    const isVisible = !(style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0');

                    const bounds = {{
                        x: Math.round(rect.x * 100) / 100,
                        y: Math.round(rect.y * 100) / 100,
                        width: Math.round(rect.width * 100) / 100,
                        height: Math.round(rect.height * 100) / 100,
                        top: Math.round(rect.top * 100) / 100,
                        right: Math.round(rect.right * 100) / 100,
                        bottom: Math.round(rect.bottom * 100) / 100,
                        left: Math.round(rect.left * 100) / 100
                    }};

                    // Layout signals
                    if (isVisible) {{
                        if (bounds.right > vpWidth + 2) elementsExtendingBeyondViewport++;
                        if (bounds.width > vpWidth + 2) elementsWiderThanViewport++;
                        if (bounds.x < -2 || bounds.y < -2) negativePositionCount++;
                        if (bounds.width === 0 && bounds.height === 0) zeroDimensionCount++;
                    }}

                    // Fixed / Sticky elements
                    if (pos === 'fixed') {{
                        fixedList.push({{
                            tag: tag,
                            id: el.id || null,
                            classes: Array.from(el.classList),
                            bounds: bounds,
                            z_index: zIndex,
                            intersects_viewport: !(bounds.bottom <= 0 || bounds.top >= vpHeight || bounds.right <= 0 || bounds.left >= vpWidth)
                        }});
                    }} else if (pos === 'sticky') {{
                        stickyList.push({{
                            tag: tag,
                            id: el.id || null,
                            classes: Array.from(el.classList),
                            bounds: bounds,
                            z_index: zIndex,
                            intersects_viewport: !(bounds.bottom <= 0 || bounds.top >= vpHeight || bounds.right <= 0 || bounds.left >= vpWidth)
                        }});
                    }}

                    // Relevant elements whitelist
                    const isSemanticOrControl = [
                        'header', 'nav', 'main', 'footer', 'aside', 'section', 'article',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'button', 'input',
                        'textarea', 'select', 'img', 'video', 'dialog', 'form', 'table',
                        'ul', 'ol', 'li', 'iframe'
                    ].includes(tag) || el.hasAttribute('role') || el.hasAttribute('tabindex');

                    if (isSemanticOrControl) {{
                        const ariaAttrs = {{}};
                        for (let j = 0; j < el.attributes.length; j++) {{
                            const a = el.attributes[j];
                            if (a.name.startsWith('aria-')) ariaAttrs[a.name] = a.value;
                        }}

                        let textSnippet = null;
                        if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') {{
                            textSnippet = (el.textContent || '').trim().substring(0, 500);
                        }}

                        elementsList.push({{
                            index: i,
                            tag: tag,
                            id: el.id || null,
                            classes: Array.from(el.classList),
                            role: el.getAttribute('role') || null,
                            aria_attributes: ariaAttrs,
                            href: el.getAttribute('href') || null,
                            src: el.getAttribute('src') || null,
                            alt: el.getAttribute('alt'),
                            title: el.getAttribute('title') || null,
                            name: el.getAttribute('name') || null,
                            type: el.getAttribute('type') || null,
                            disabled: el.hasAttribute('disabled'),
                            hidden: el.hasAttribute('hidden'),
                            tabindex: el.getAttribute('tabindex'),
                            bounds: bounds,
                            visibility: {{
                                is_visible: isVisible,
                                display: style.display,
                                visibility: style.visibility,
                                opacity: style.opacity
                            }},
                            position: pos,
                            z_index: zIndex,
                            text: textSnippet
                        }});
                    }}
                }}

                return {{
                    pageMeta: pageMeta,
                    docGeo: docGeo,
                    iframes: iframes,
                    cookieBanner: hasCookieBanner,
                    totalCount: totalCount,
                    elements: elementsList,
                    fixed: fixedList,
                    sticky: stickyList,
                    signals: {{
                        elements_extending_beyond_viewport: elementsExtendingBeyondViewport,
                        elements_wider_than_viewport: elementsWiderThanViewport,
                        negative_position_count: negativePositionCount,
                        zero_dimension_count: zeroDimensionCount
                    }}
                }};
            }}"""

            eval_res = session.page.evaluate(
                inspect_script,
                {
                    "maxElements": max_elements,
                    "vpWidth": vp_width,
                    "vpHeight": vp_height,
                },
            )

            p_meta = eval_res.get("pageMeta", {})
            page_data["title"] = p_meta.get("title", "")
            page_data["lang"] = p_meta.get("lang")

            doc_geometry = eval_res.get("docGeo", doc_geometry)
            iframe_elements = eval_res.get("iframes", [])
            cookie_banner_detected = bool(eval_res.get("cookieBanner", False))
            total_elements_available = eval_res.get("totalCount", 0)
            extracted_elements = eval_res.get("elements", [])
            fixed_elements = eval_res.get("fixed", [])
            sticky_elements = eval_res.get("sticky", [])
            layout_signals = eval_res.get("signals", {})
            is_truncated = total_elements_available > max_elements
        except Exception as exc:
            logger.exception("Error during DOM evaluation for viewport %s: %s", vp_name, exc)

    # Step 5: Screenshot Capture (Optional)
    screenshot_enabled = bool(opts.get("screenshot_enabled", True))
    full_page = bool(opts.get("full_page_screenshot", False))
    screenshot_info: dict[str, Any] = {"status": "disabled"}

    if screenshot_enabled:
        audit_id = opts.get("audit_id") or "audit"
        shot_dir = opts.get("screenshot_dir") or os.path.join("scratch", "visual_accessibility", audit_id)
        shot_path = os.path.join(shot_dir, f"{vp_name}.png")

        shot_res = capture_screenshot(session, path=shot_path, full_page=full_page)
        if shot_res.get("status") == "completed":
            screenshot_info = {
                "status": "completed",
                "path": shot_res.get("path"),
                "width": shot_res.get("width", vp_width),
                "height": shot_res.get("height", vp_height),
                "full_page": full_page,
            }
        else:
            screenshot_info = {
                "status": "failed",
                "error": shot_res.get("message", "Screenshot capture failed."),
            }

    duration_ms = int((time.monotonic() - vp_start_time) * 1000)

    return {
        "name": vp_name,
        "status": "completed",
        "duration_ms": duration_ms,
        "viewport": vp_spec,
        "page": page_data,
        "document": doc_geometry,
        "elements_summary": {
            "elements_collected": len(extracted_elements),
            "elements_available": total_elements_available,
            "truncated": is_truncated,
        },
        "elements": extracted_elements,
        "layout_signals": layout_signals,
        "iframes": {
            "count": len(iframe_elements),
            "items": iframe_elements,
        },
        "fixed_elements": fixed_elements,
        "sticky_elements": sticky_elements,
        "cookie_banner_detected": cookie_banner_detected,
        "screenshot": screenshot_info,
    }


def render_page(
    target_url: str,
    options: dict[str, Any] | None = None,
    browser_session: Any | None = None,
) -> dict[str, Any]:
    """
    Render target webpage across all configured viewports and collect structured visual/DOM evidence.

    Args:
        target_url: Webpage URL to render and inspect.
        options: Optional configuration dictionary:
            - viewports: list of viewport dicts or profile names (default: desktop, tablet, mobile).
            - total_budget_ms: Total audit execution budget (default: 300,000 ms).
            - remaining_budget_ms: Optional remaining time.
            - deadline: Optional monotonic deadline timestamp.
            - screenshot_enabled: bool (default: True).
            - full_page_screenshot: bool (default: False).
            - max_elements: Maximum elements to collect (default: 5,000).
            - allow_local: Allow local/private IPs (default: False).

    Returns:
        Structured JSON-serializable dictionary matching the Visual & Accessibility Audit render contract.
    """
    start_monotonic = time.monotonic()
    started_at_str = _format_utc_timestamp()

    opts = options or {}
    total_budget_ms = int(opts.get("total_budget_ms", DEFAULT_TOTAL_BUDGET_MS))
    deadline = opts.get("deadline") or (start_monotonic + (total_budget_ms / 1000.0))

    # Determine viewport list
    raw_viewports = opts.get("viewports")
    target_viewports: list[dict[str, Any]] = []

    if isinstance(raw_viewports, list) and raw_viewports:
        for vp in raw_viewports:
            if isinstance(vp, dict):
                target_viewports.append(vp)
            elif isinstance(vp, str):
                matched = next((p for p in DEFAULT_VIEWPORT_PROFILES if p["name"].lower() == vp.lower()), None)
                if matched:
                    target_viewports.append(matched)
                else:
                    target_viewports.append({"name": vp, "width": 1440, "height": 900})
    else:
        target_viewports = list(DEFAULT_VIEWPORT_PROFILES)

    rendered_viewports: list[dict[str, Any]] = []
    render_errors: list[dict[str, Any]] = []
    final_url = target_url

    session: BrowserSession | None = browser_session or opts.get("browser_session")
    owns_session = False
    try:
        # Launch shared browser session using browser.py if not provided
        if not session:
            owns_session = True
            launch_opts = dict(opts)
            session = launch_browser(launch_opts)

        for vp in target_viewports:
            now_m = time.monotonic()
            remaining_ms = int((deadline - now_m) * 1000)

            # Enforce Monotonic Audit Budget
            if remaining_ms <= 2_000:
                logger.warning("Remaining audit budget insufficient (%d ms). Skipping viewport '%s'", remaining_ms, vp.get("name"))
                rendered_viewports.append(
                    {
                        "name": str(vp.get("name", "custom")),
                        "status": "skipped_due_to_budget",
                        "viewport": vp,
                        "error": {
                            "type": "budget_exhausted",
                            "message": f"Global audit budget expired before rendering viewport '{vp.get('name')}'.",
                        },
                    }
                )
                continue

            vp_opts = dict(opts)
            vp_opts["remaining_budget_ms"] = remaining_ms

            vp_result = render_viewport(session, target_url, vp, vp_opts)
            rendered_viewports.append(vp_result)

            if vp_result.get("status") == "completed":
                final_url = vp_result.get("page", {}).get("final_url") or final_url
            elif vp_result.get("error"):
                render_errors.append(
                    {
                        "viewport": vp.get("name"),
                        "error_type": vp_result["error"].get("type"),
                        "message": vp_result["error"].get("message"),
                    }
                )

    except Exception as exc:
        logger.exception("Unexpected exception in render_page: %s", exc)
        render_errors.append(
            {
                "stage": "render_page",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    finally:
        if owns_session and session:
            close_browser(session)

    duration_ms = int((time.monotonic() - start_monotonic) * 1000)
    successful_count = sum(1 for v in rendered_viewports if v.get("status") == "completed")
    failed_count = sum(1 for v in rendered_viewports if v.get("status") in ("failed", "skipped_due_to_budget"))

    overall_status = "completed"
    if successful_count == 0 and rendered_viewports:
        overall_status = "failed"
    elif failed_count > 0:
        overall_status = "partial_success"

    return {
        "status": overall_status,
        "target_url": target_url,
        "final_url": final_url,
        "render_metadata": {
            "started_at": started_at_str,
            "completed_at": _format_utc_timestamp(),
            "duration_ms": duration_ms,
            "viewport_count": len(rendered_viewports),
            "successful_viewports": successful_count,
            "failed_viewports": failed_count,
            "budget_exhausted": time.monotonic() >= deadline or any(
                v.get("status") == "skipped_due_to_budget" for v in rendered_viewports
            ),
        },
        "viewports": rendered_viewports,
        "comparison": {
            "viewport_count": len(rendered_viewports),
            "viewports_rendered": [v.get("name") for v in rendered_viewports],
        },
        "errors": render_errors,
    }
