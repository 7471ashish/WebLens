"""
Browser-control layer for visual-accessibility-audit.

This module owns browser lifecycle management, safe HTTP/HTTPS navigation,
viewport configuration, geometry and DOM extraction, computed styles,
screenshot capture, and keyboard interaction primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import logging
import os
import socket
import time
from typing import Any
import urllib.parse

logger = logging.getLogger("visual_accessibility_audit.browser")

# Standard Viewport Profiles
DEFAULT_VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900, "device_scale_factor": 1.0},
    "tablet": {"width": 1024, "height": 768, "device_scale_factor": 2.0},
    "mobile": {"width": 390, "height": 844, "device_scale_factor": 3.0},
}

DEFAULT_NAVIGATION_TIMEOUT_MS = 15_000
DEFAULT_ACTION_TIMEOUT_MS = 5_000
DEFAULT_SCREENSHOT_TIMEOUT_MS = 10_000
DEFAULT_MAX_ELEMENTS = 5_000
DEFAULT_USER_AGENT = "VisualAccessibilityAudit/1.0"

# Approved Computed Style Properties Whitelist
ALLOWED_STYLE_PROPERTIES = {
    "color",
    "background-color",
    "background-image",
    "display",
    "visibility",
    "opacity",
    "font-size",
    "font-weight",
    "line-height",
    "width",
    "height",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "position",
    "overflow",
    "overflow-x",
    "overflow-y",
    "z-index",
    "text-overflow",
    "white-space",
    "outline",
    "outline-color",
    "outline-width",
    "outline-style",
}


def _to_kebab_case(s: str) -> str:
    """Convert camelCase to kebab-case."""
    import re
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s).lower()


def _is_private_or_local_ip(hostname: str) -> bool:
    """Detect loopback, link-local, private, or reserved addresses."""
    lower_host = hostname.lower().strip()
    if lower_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if lower_host.endswith(".local") or lower_host.endswith(".internal"):
        return True

    try:
        ip = ipaddress.ip_address(lower_host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        )
    except ValueError:
        pass

    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in resolved_ips:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or ip.is_reserved
            ):
                return True
    except Exception as exc:
        logger.debug("DNS lookup failed for %s: %s", hostname, exc)

    return False


def _validate_url(target_url: Any, allow_local: bool = False) -> tuple[bool, str | None, urllib.parse.ParseResult | None]:
    """Validate target URL format and enforce SSRF safety."""
    if not isinstance(target_url, str) or not target_url.strip():
        return False, "Target URL must be a non-empty string.", None

    trimmed = target_url.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
    except Exception as exc:
        return False, f"Malformed URL syntax: {exc}", None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{scheme}'. Only http and https allowed.", None

    if not parsed.netloc or not parsed.hostname:
        return False, "Target URL missing hostname or network location.", None

    if not allow_local and _is_private_or_local_ip(parsed.hostname):
        return False, f"Destination host '{parsed.hostname}' is a restricted private or local address.", None

    return True, None, parsed


@dataclass
class BrowserSession:
    """Lightweight browser session wrapper encapsulating state and page lifecycle."""
    playwright_instance: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    viewport: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_VIEWPORTS["desktop"]))
    target_url: str | None = None
    final_url: str | None = None
    navigation_metadata: dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.monotonic)
    config: dict[str, Any] = field(default_factory=dict)
    dialogs_encountered: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        close_browser(self)


def launch_browser(options: dict[str, Any] | None = None) -> BrowserSession:
    """
    Launch a headless browser instance and initialize a clean context and page.

    Args:
        options: Configuration options dictionary:
            - headless: bool (default: True)
            - viewport: dict with width, height, device_scale_factor
            - user_agent: str (default: VisualAccessibilityAudit/1.0)
            - locale: str (default: en-US)
            - timezone: str (default: UTC)
            - allow_local: bool (default: False)

    Returns:
        Initialized BrowserSession object.
    """
    opts = options or {}
    headless = bool(opts.get("headless", True))
    user_agent = str(opts.get("user_agent", DEFAULT_USER_AGENT))
    locale = str(opts.get("locale", "en-US"))
    timezone_id = str(opts.get("timezone", "UTC"))

    # Determine viewport settings
    viewport_opt = opts.get("viewport")
    if isinstance(viewport_opt, str) and viewport_opt in DEFAULT_VIEWPORTS:
        vp = dict(DEFAULT_VIEWPORTS[viewport_opt])
    elif isinstance(viewport_opt, dict):
        vp = {
            "width": int(viewport_opt.get("width", 1440)),
            "height": int(viewport_opt.get("height", 900)),
            "device_scale_factor": float(viewport_opt.get("device_scale_factor", 1.0)),
        }
    else:
        vp = dict(DEFAULT_VIEWPORTS["desktop"])

    session = BrowserSession(viewport=vp, config=opts)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        logger.error("Playwright is not installed: %s", exc)
        raise RuntimeError(
            "Browser automation capability missing: Playwright is not installed. "
            "Install playwright and required browser binaries (pip install playwright && playwright install chromium)."
        ) from exc

    try:
        pw = sync_playwright().start()
        session.playwright_instance = pw

        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
        )
        session.browser = browser

        allow_insecure_tls = bool(
            opts.get("allow_insecure_tls", False)
            or opts.get("ignore_https_errors", False)
            or os.environ.get("AUDIT_ALLOW_INSECURE_TLS", "").lower() in ("1", "true")
            or os.environ.get("AUDIT_ALLOW_INSECURE_LOCAL", "").lower() in ("1", "true")
        )
        context = browser.new_context(
            viewport={"width": vp["width"], "height": vp["height"]},
            device_scale_factor=vp.get("device_scale_factor", 1.0),
            user_agent=user_agent,
            locale=locale,
            timezone_id=timezone_id,
            ignore_https_errors=allow_insecure_tls,
            java_script_enabled=True,
        )


        session.context = context

        page = context.new_page()
        session.page = page

        # Auto-dismiss dialogs (alerts, prompts, confirms) without blocking execution
        def _handle_dialog(dialog: Any) -> None:
            logger.info("Dialog encountered (%s): %s", dialog.type, dialog.message)
            session.dialogs_encountered.append(
                {"type": dialog.type, "message": dialog.message}
            )
            dialog.dismiss()

        page.on("dialog", _handle_dialog)

        logger.info("Browser session successfully initialized.")
        return session
    except Exception as exc:
        logger.exception("Failed to launch browser: %s", exc)
        close_browser(session)
        raise RuntimeError(f"Browser launch failed: {exc}") from exc


def navigate(
    session: BrowserSession,
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Navigate the browser session page to target_url with strict timeouts and safety checks.

    Args:
        session: Active BrowserSession.
        target_url: Target HTTP/HTTPS URL.
        options: Configuration options (timeout_ms, wait_until, remaining_budget_ms, allow_local).

    Returns:
        Structured JSON-serializable dictionary with navigation results.
    """
    if session.closed or not session.page:
        return {
            "status": "failed",
            "error_type": "page_closed",
            "message": "Browser session or page has already been closed.",
            "operation": "navigate",
        }

    opts = options or {}
    allow_local = bool(opts.get("allow_local", session.config.get("allow_local", False)))
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_NAVIGATION_TIMEOUT_MS))
    remaining_budget_ms = opts.get("remaining_budget_ms")
    wait_until = str(opts.get("wait_until", "domcontentloaded"))

    if remaining_budget_ms is not None and int(remaining_budget_ms) <= 500:
        return {
            "status": "skipped_due_to_budget",
            "error_type": "budget_exhausted",
            "message": "Remaining execution budget is insufficient for navigation.",
            "operation": "navigate",
        }

    effective_timeout = timeout_ms
    if remaining_budget_ms is not None:
        effective_timeout = min(timeout_ms, max(100, int(remaining_budget_ms)))

    # SSRF & URL validation
    is_valid, err_msg, _ = _validate_url(target_url, allow_local=allow_local)
    if not is_valid:
        return {
            "status": "failed",
            "error_type": "blocked_target" if "restricted" in str(err_msg) else "invalid_url",
            "message": err_msg or "Invalid URL supplied.",
            "operation": "navigate",
        }

    session.target_url = target_url
    start_t = time.monotonic()

    try:
        response = session.page.goto(
            target_url,
            timeout=effective_timeout,
            wait_until=wait_until,
        )

        duration_ms = int((time.monotonic() - start_t) * 1000)
        final_url = session.page.url
        session.final_url = final_url

        # Check redirect target for SSRF
        if not allow_local:
            parsed_final = urllib.parse.urlparse(final_url)
            if parsed_final.hostname and _is_private_or_local_ip(parsed_final.hostname):
                return {
                    "status": "failed",
                    "error_type": "blocked_target",
                    "message": f"Redirected to restricted destination: {final_url}",
                    "operation": "navigate",
                }

        status_code = response.status if response else 200
        headers = response.headers if response else {}
        content_type = headers.get("content-type", "")

        nav_meta = {
            "requested_url": target_url,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "duration_ms": duration_ms,
            "redirect_occurred": final_url != target_url,
        }
        session.navigation_metadata = nav_meta

        # Optional short wait after navigation
        wait_after_ms = int(opts.get("wait_after_navigation_ms", 200))
        if wait_after_ms > 0:
            session.page.wait_for_timeout(wait_after_ms)

        return {
            "status": "completed",
            "operation": "navigate",
            "navigation": nav_meta,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_t) * 1000)
        err_str = str(exc)
        err_type = "navigation_timeout" if "timeout" in err_str.lower() else "inspection_failed"
        logger.warning("Navigation failed for %s: %s", target_url, exc)
        return {
            "status": "failed",
            "error_type": err_type,
            "message": err_str,
            "operation": "navigate",
            "duration_ms": duration_ms,
        }


def set_viewport(
    session: BrowserSession,
    width: int,
    height: int,
    device_scale_factor: float = 1.0,
) -> dict[str, Any]:
    """Update active page viewport dimensions dynamically."""
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    try:
        session.page.set_viewport_size({"width": width, "height": height})
        session.viewport = {
            "width": width,
            "height": height,
            "device_scale_factor": device_scale_factor,
        }
        return {"status": "completed", "viewport": session.viewport}
    except Exception as exc:
        return {"status": "failed", "error_type": "viewport_error", "message": str(exc)}


def get_page_info(session: BrowserSession, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Retrieve top-level page metadata including title, URL, viewport, and scroll geometry."""
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    try:
        title = session.page.title()
        url = session.page.url
        geometry = session.page.evaluate(
            """() => ({
                scrollWidth: document.documentElement.scrollWidth || 0,
                scrollHeight: document.documentElement.scrollHeight || 0,
                clientWidth: document.documentElement.clientWidth || 0,
                clientHeight: document.documentElement.clientHeight || 0,
                iframeCount: document.querySelectorAll('iframe').length
            })"""
        )
        return {
            "status": "completed",
            "url": url,
            "title": title,
            "viewport": session.viewport,
            "geometry": geometry,
            "navigation": session.navigation_metadata,
        }
    except Exception as exc:
        return {"status": "failed", "error_type": "inspection_failed", "message": str(exc)}


def get_dom_snapshot(session: BrowserSession, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Retrieve structured, bounded DOM element snapshot with geometry, attributes, and accessibility markers.

    Returns:
        Structured dictionary containing element nodes up to max_elements bound.
    """
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    opts = options or {}
    max_elements = int(opts.get("max_elements", DEFAULT_MAX_ELEMENTS))

    script = f"""(maxElements) => {{
        const elements = Array.from(document.querySelectorAll('*')).slice(0, maxElements);
        const results = [];

        for (let i = 0; i < elements.length; i++) {{
            const el = elements[i];
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            const isVisible = !(
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                style.opacity === '0' ||
                (rect.width === 0 && rect.height === 0)
            );

            // Collect aria attributes
            const ariaAttrs = {{}};
            for (let j = 0; j < el.attributes.length; j++) {{
                const attr = el.attributes[j];
                if (attr.name.startsWith('aria-')) {{
                    ariaAttrs[attr.name] = attr.value;
                }}
            }}

            // Sanitize safe values
            let safeVal = null;
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {{
                const inputType = (el.getAttribute('type') || '').toLowerCase();
                if (!['password', 'hidden'].includes(inputType)) {{
                    safeVal = (el.value || '').substring(0, 100);
                }}
            }}

            results.push({{
                index: i,
                tag: el.tagName.toLowerCase(),
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
                value: safeVal,
                disabled: el.hasAttribute('disabled'),
                hidden: el.hasAttribute('hidden'),
                tabindex: el.getAttribute('tabindex'),
                lang: el.getAttribute('lang') || null,
                bounding_box: {{
                    x: Math.round(rect.x * 100) / 100,
                    y: Math.round(rect.y * 100) / 100,
                    width: Math.round(rect.width * 100) / 100,
                    height: Math.round(rect.height * 100) / 100,
                    top: Math.round(rect.top * 100) / 100,
                    right: Math.round(rect.right * 100) / 100,
                    bottom: Math.round(rect.bottom * 100) / 100,
                    left: Math.round(rect.left * 100) / 100
                }},
                visibility: {{
                    is_visible: isVisible,
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity
                }},
                text_snippet: (el.textContent || '').trim().substring(0, 100)
            }});
        }}

        return results;
    }}"""

    try:
        nodes = session.page.evaluate(script, max_elements)
        return {
            "status": "completed",
            "total_elements": len(nodes),
            "max_elements_bound": max_elements,
            "elements": nodes,
            "viewport": session.viewport,
        }
    except Exception as exc:
        return {"status": "failed", "error_type": "inspection_failed", "message": str(exc)}


def get_element_info(
    session: BrowserSession,
    selector: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve detailed geometry, visibility, attributes, and styles for a single selector."""
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    opts = options or {}
    req_props = opts.get("properties") or []

    script = """([sel, props]) => {
        const el = document.querySelector(sel);
        if (!el) return { exists: false };

        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);

        const computed = {};
        for (const p of props) {
            computed[p] = style.getPropertyValue(p) || '';
        }

        const aria = {};
        for (let j = 0; j < el.attributes.length; j++) {
            const attr = el.attributes[j];
            if (attr.name.startsWith('aria-')) aria[attr.name] = attr.value;
        }

        return {
            exists: true,
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            classes: Array.from(el.classList),
            role: el.getAttribute('role') || null,
            aria_attributes: aria,
            bounding_box: {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                left: rect.left
            },
            visibility: {
                is_visible: !(style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0'),
                display: style.display,
                visibility: style.visibility,
                opacity: style.opacity
            },
            computed_styles: computed
        };
    }"""

    try:
        res = session.page.evaluate(script, [selector, req_props])
        res["status"] = "completed"
        return res
    except Exception as exc:
        return {"status": "failed", "error_type": "selector_error", "message": str(exc), "selector": selector}


def get_computed_style(
    session: BrowserSession,
    selector: str,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    """
    Retrieve computed CSS properties for an element, filtered against the allowed whitelist.

    Args:
        session: Active BrowserSession.
        selector: CSS selector identifying target element.
        properties: Optional list of desired CSS property names.

    Returns:
        Structured dictionary of resolved computed properties.
    """
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    # Normalize property names to kebab-case and validate against whitelist
    target_props: list[str] = []
    if properties:
        for p in properties:
            kebab = _to_kebab_case(p)
            if kebab in ALLOWED_STYLE_PROPERTIES:
                target_props.append(kebab)
    else:
        target_props = list(ALLOWED_STYLE_PROPERTIES)

    script = """([sel, props]) => {
        const el = document.querySelector(sel);
        if (!el) return { exists: false, properties: {} };

        const style = window.getComputedStyle(el);
        const result = {};
        for (const p of props) {
            result[p] = style.getPropertyValue(p) || '';
        }
        return { exists: true, properties: result };
    }"""

    try:
        res = session.page.evaluate(script, [selector, target_props])
        res["status"] = "completed"
        res["selector"] = selector
        return res
    except Exception as exc:
        return {"status": "failed", "error_type": "selector_error", "message": str(exc), "selector": selector}


def press_key(
    session: BrowserSession,
    key: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Simulate pressing an interactive keyboard key (e.g. 'Tab', 'Shift+Tab', 'Enter', 'Escape').

    Returns:
        Result dictionary including active element info post-keypress.
    """
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    opts = options or {}
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_ACTION_TIMEOUT_MS))

    try:
        # Standardize key combination
        if key.lower() == "shift+tab":
            session.page.keyboard.press("Shift+Tab")
        else:
            session.page.keyboard.press(key)

        # Brief pause for focus change
        session.page.wait_for_timeout(50)
        active_el = get_active_element_info(session)

        return {
            "status": "completed",
            "key_pressed": key,
            "active_element": active_el.get("element"),
        }
    except Exception as exc:
        return {"status": "failed", "error_type": "keyboard_error", "message": str(exc), "key": key}


def get_active_element_info(session: BrowserSession) -> dict[str, Any]:
    """Inspect currently focused element in the page context (document.activeElement)."""
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    script = """() => {
        const el = document.activeElement;
        if (!el) return null;

        const isBody = (el.tagName === 'BODY' || el.tagName === 'HTML');
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);

        return {
            is_body: isBody,
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            classes: Array.from(el.classList),
            role: el.getAttribute('role') || null,
            aria_label: el.getAttribute('aria-label') || null,
            tabindex: el.getAttribute('tabindex'),
            disabled: el.hasAttribute('disabled'),
            bounding_box: {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
            },
            outline: style.outline,
            outline_width: style.outlineWidth,
            outline_style: style.outlineStyle,
            outline_color: style.outlineColor
        };
    }"""

    try:
        el_info = session.page.evaluate(script)
        return {"status": "completed", "element": el_info}
    except Exception as exc:
        return {"status": "failed", "error_type": "inspection_failed", "message": str(exc)}


def capture_screenshot(
    session: BrowserSession,
    path: str | None = None,
    full_page: bool = False,
) -> dict[str, Any]:
    """
    Capture a PNG screenshot of the current page viewport or full document.

    Args:
        session: Active BrowserSession.
        path: File destination path. If None, saves to scratch directory.
        full_page: If True, scrolls document to capture complete page height.

    Returns:
        Structured reference dictionary with file path, dimensions, and viewport metadata.
    """
    if session.closed or not session.page:
        return {"status": "failed", "error_type": "page_closed", "message": "Browser page is closed."}

    vp = session.viewport
    out_path = path
    if not out_path:
        os.makedirs("scratch", exist_ok=True)
        out_path = os.path.join("scratch", f"screenshot_{vp['width']}x{vp['height']}_{int(time.time()*1000)}.png")

    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(os.path.abspath(out_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        session.page.screenshot(path=out_path, full_page=full_page)
        return {
            "status": "completed",
            "path": os.path.abspath(out_path),
            "width": vp["width"],
            "height": vp["height"],
            "full_page": full_page,
            "viewport": vp,
        }
    except Exception as exc:
        logger.warning("Failed to capture screenshot: %s", exc)
        return {"status": "failed", "error_type": "screenshot_failed", "message": str(exc)}


def close_browser(session: BrowserSession) -> None:
    """Safely release and close page, context, browser, and playwright instance."""
    if session.closed:
        return

    session.closed = True

    try:
        if session.page:
            session.page.close()
    except Exception as exc:
        logger.debug("Error closing page: %s", exc)
    finally:
        session.page = None

    try:
        if session.context:
            session.context.close()
    except Exception as exc:
        logger.debug("Error closing context: %s", exc)
    finally:
        session.context = None

    try:
        if session.browser:
            session.browser.close()
    except Exception as exc:
        logger.debug("Error closing browser: %s", exc)
    finally:
        session.browser = None

    try:
        if session.playwright_instance:
            session.playwright_instance.stop()
    except Exception as exc:
        logger.debug("Error stopping playwright: %s", exc)
    finally:
        session.playwright_instance = None

    logger.info("Browser session resources cleaned up.")
