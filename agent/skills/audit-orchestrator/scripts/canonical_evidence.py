"""
Canonical Audit Evidence Model (canonical_evidence.py)
======================================================
Provides the unified, strictly observed evidence model for the Brand AI Readiness
and Technical Health Audit marketplace.

Architectural Mandates:
1. Zero Synthetic / Fabricated Measurements:
   - Coordinates, bounding boxes, and dimensions are strictly observed from real DOM or Playwright measurements.
   - When browser rendering is unavailable, geometry fields are None (never invented).
   - Intrinsic image dimensions are extracted from HTML attributes or image headers, never placeholder 800x600.
2. Truthful Availability:
   - When a data source or browser fails, it is explicitly marked as unavailable/failed.
3. Canonical Transformations:
   - Provides deterministic, lossless data adapters for downstream skills (EngagementAudit, MultimodalAudit, etc.).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
import json
import logging
import os
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger("audit.canonical_evidence")


def normalize_url(url: str | None) -> str:
    """
    Clean and sanitize a URL string, stripping out newlines, control characters,
    embedded stack traces, and Playwright call log fragments (e.g. '\\nCall log:...').
    """
    if not url or not isinstance(url, str):
        return ""

    cleaned = url.strip()
    if not cleaned:
        return ""

    # 1. Take only the portion before any newline, carriage return, or call log / trace
    if "\n" in cleaned or "\r" in cleaned:
        cleaned = re.split(r"[\r\n]+", cleaned)[0].strip()

    # If call log / stack trace text leaked into URL string
    cleaned = re.split(r"\s*(?:Call log|Call:|Page\.goto|Error:|Traceback)\b", cleaned, flags=re.IGNORECASE)[0].strip()

    # Strip surrounding quotes, brackets, parentheses, trailing punctuation
    cleaned = cleaned.strip("\"'<>()[];,")

    # Extract valid HTTP/HTTPS URL pattern
    match = re.match(r"^(https?://[^\s()\"'<>]+)", cleaned, re.IGNORECASE)
    if match:
        extracted = match.group(1)
        try:
            parsed = urlparse(extracted)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                clean_path = parsed.path or ("/" if not parsed.query else "")
                reconstructed = f"{parsed.scheme}://{parsed.netloc}{clean_path}"
                if parsed.query:
                    reconstructed += f"?{parsed.query}"
                if parsed.fragment:
                    reconstructed += f"#{parsed.fragment}"
                return reconstructed
        except Exception:
            pass
        return extracted.rstrip(".,;:)")

    return cleaned


def extract_clean_title(soup: BeautifulSoup, html_raw: str | None = None) -> tuple[bool, str | None, int]:
    """Extract only the contents of the HTML <title> element cleanly and deterministically."""
    title_tags = soup.find_all("title")
    if not title_tags:
        return False, None, 0

    duplicate_count = max(0, len(title_tags) - 1)
    first_tag = title_tags[0]

    # 1. If child elements (Tag instances) exist, extract only text before any child Tag
    extracted_parts: list[str] = []
    for child in first_tag.contents:
        if isinstance(child, str):
            extracted_parts.append(str(child))
        else:
            break

    raw_text = "".join(extracted_parts) if extracted_parts else (first_tag.string or "")

    # 2. Check if the <title> tag was properly closed in the source HTML
    html_src = html_raw if html_raw is not None else str(soup)
    m_open = re.search(r"<title\b[^>]*>", html_src, re.IGNORECASE)
    is_closed = False
    if m_open:
        rest = html_src[m_open.end():]
        m_close = re.search(r"</title\s*>", rest, re.IGNORECASE)
        m_body = re.search(r"<\s*(?:body|div|p|h[1-6]|main|header|footer|section|article|table|form|nav|head|title)\b", rest, re.IGNORECASE)
        if m_close and (not m_body or m_close.start() < m_body.start()):
            is_closed = True

    if not is_closed:
        tag_boundary_pattern = re.compile(r"<\s*(?:/?\s*[a-zA-Z][a-zA-Z0-9:-]*|!--)", re.DOTALL)
        match = tag_boundary_pattern.search(raw_text)
        if match:
            raw_text = raw_text[:match.start()]

    cleaned = re.sub(r"\s+", " ", raw_text).strip()
    return True, cleaned, duplicate_count


@dataclass
class BoundingRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
        }


@dataclass
class SiteObservation:
    requested_url: str
    final_url: str
    hostname: str
    http_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    fetch_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fetch_duration_ms: float = 0.0
    robots_txt_status: dict[str, Any] = field(default_factory=dict)
    sitemap_status: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


@dataclass
class HeadingObservation:
    level: int
    text: str
    bounding_box: BoundingRect | None = None


@dataclass
class CTAObservation:
    id: str
    text: str
    role: str
    href: str = ""
    bounding_box: BoundingRect | None = None


@dataclass
class NavLinkObservation:
    label: str
    href: str = ""
    visible: bool = True
    bounding_box: BoundingRect | None = None


@dataclass
class ImageObservation:
    id: str
    url: str
    filename: str
    alt_text: str | None
    alt_attribute_present: bool
    decorative: bool
    role: str
    mime_type: str
    format: str
    intrinsic_width: int | None = None
    intrinsic_height: int | None = None
    rendered_width: float | None = None
    rendered_height: float | None = None
    position: BoundingRect | None = None
    bytes_length: int | None = None


@dataclass
class FormObservation:
    name: str
    field_count: int
    fields: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentObservation:
    raw_html: str
    title: str
    lang: str = "en"
    meta_description: str | None = None
    canonical_url: str | None = None
    headings: list[HeadingObservation] = field(default_factory=list)
    ctas: list[CTAObservation] = field(default_factory=list)
    nav_links: list[NavLinkObservation] = field(default_factory=list)
    images: list[ImageObservation] = field(default_factory=list)
    forms: list[FormObservation] = field(default_factory=list)
    structured_data_blocks: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    full_text: str = ""
    popups: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RenderedObservation:
    has_browser_data: bool = False
    browser_error: str | None = None
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1366, "height": 768})
    viewports_measured: list[dict[str, Any]] = field(default_factory=list)
    mobile_evidence: dict[str, Any] | None = None
    horizontal_overflow: bool | None = None


@dataclass
class CrawlObservation:
    pages_visited: list[str] = field(default_factory=list)
    links_discovered: list[str] = field(default_factory=list)
    crawl_depth: int = 1
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    crawl_errors: list[str] = field(default_factory=list)


@dataclass
class FreshnessObservation:
    claims: list[dict[str, Any]] = field(default_factory=list)
    date_signals: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CanonicalAuditContext:
    site: SiteObservation
    document: DocumentObservation
    rendered: RenderedObservation
    crawl: CrawlObservation
    freshness: FreshnessObservation

    # --------------------------------------------------------------------------
    # Downstream Adapter 1: Engagement Audit
    # --------------------------------------------------------------------------
    def to_engagement_input(self) -> dict[str, Any]:
        """
        Produce the exact input dictionary expected by EngagementAudit.audit().
        Strictly respects measurement ground truth: if browser geometry is unavailable,
        bounding boxes and mobile responsiveness are passed as None/omitted rather than fake values.
        """
        headings_payload = []
        for h in self.document.headings:
            h_dict: dict[str, Any] = {
                "level": h.level,
                "text": h.text,
            }
            if h.bounding_box is not None:
                h_dict["bounding_box"] = h.bounding_box.to_dict()
            headings_payload.append(h_dict)

        ctas_payload = []
        for c in self.document.ctas:
            c_dict: dict[str, Any] = {
                "id": c.id,
                "text": c.text,
                "role": c.role,
                "href": c.href,
            }
            if c.bounding_box is not None:
                c_dict["bounding_box"] = c.bounding_box.to_dict()
            ctas_payload.append(c_dict)

        nav_payload = [
            {"label": n.label, "text": n.label, "href": n.href, "visible": n.visible}
            for n in self.document.nav_links
        ]

        forms_payload = [
            {"name": f.name, "field_count": f.field_count, "fields": f.fields}
            for f in self.document.forms
        ]

        primary_cta_dict = ctas_payload[0] if ctas_payload else None

        payload: dict[str, Any] = {
            "url": self.site.final_url or self.site.requested_url,
            "title": self.document.title,
            "language": self.document.lang,
            "viewport": self.rendered.viewport,
            "headings": headings_payload,
            "ctas": ctas_payload,
            "primary_cta": primary_cta_dict,
            "navigation": nav_payload,
            "nav_links": nav_payload,
            "content": {
                "language": self.document.lang,
                "main_text": self.document.full_text[:3000],
                "paragraphs": self.document.paragraphs[:15] or [self.document.title],
            },
            "forms": forms_payload,
            "popups": self.document.popups,
            "journey": {
                "name": "primary_conversion_path",
                "goal": f"Interact with {self.document.title}",
                "steps": [
                    {
                        "step_id": "landing",
                        "url": self.site.final_url or self.site.requested_url,
                        "has_primary_action": bool(ctas_payload),
                        "action_type": "page_view",
                    }
                ],
                "has_dead_end": False,
            },
        }

        # Only provide mobile viewport evidence if actually measured by browser
        if self.rendered.has_browser_data and self.rendered.mobile_evidence is not None:
            payload["mobile"] = self.rendered.mobile_evidence

        return payload

    # --------------------------------------------------------------------------
    # Downstream Adapter 2: Multimodal Audit
    # --------------------------------------------------------------------------
    def to_multimodal_input(self) -> dict[str, Any]:
        """
        Produce the exact input dictionary expected by MultimodalAudit.audit().
        Only emits observed dimensions (never placeholder 800x600).
        """
        images_payload = []
        for img in self.document.images:
            img_dict: dict[str, Any] = {
                "id": img.id,
                "url": img.url,
                "filename": img.filename,
                "alt_text": img.alt_text,
                "alt_attribute_present": img.alt_attribute_present,
                "decorative": img.decorative,
                "role": img.role,
                "mime_type": img.mime_type,
                "format": img.format,
                "visible": True,
            }
            # Only include intrinsic width/height if actually measured/declared in HTML or headers
            if img.intrinsic_width is not None and img.intrinsic_width > 0:
                img_dict["width"] = img.intrinsic_width
            if img.intrinsic_height is not None and img.intrinsic_height > 0:
                img_dict["height"] = img.intrinsic_height

            # Only include rendered dimensions if measured by browser
            if img.rendered_width is not None and img.rendered_width > 0:
                img_dict["rendered_width"] = img.rendered_width
            if img.rendered_height is not None and img.rendered_height > 0:
                img_dict["rendered_height"] = img.rendered_height

            if img.position is not None:
                img_dict["position"] = img.position.to_dict()

            images_payload.append(img_dict)

        first_heading = self.document.headings[0].text if self.document.headings else self.document.title

        return {
            "url": self.site.final_url or self.site.requested_url,
            "page_context": {
                "title": self.document.title,
                "heading": first_heading,
                "language": self.document.lang,
            },
            "images": images_payload,
            "images_checked": True,
            "charts": [],
            "charts_checked": True,
        }



# Canonical Evidence Builder Factory


def build_canonical_context_from_html(
    target_url: str,
    raw_html: str,
    final_url: str | None = None,
    http_status: int = 200,
    response_headers: dict[str, str] | None = None,
    fetch_duration_ms: float = 0.0,
    crawl_observations: dict[str, Any] | None = None,
    render_observations: dict[str, Any] | None = None,
) -> CanonicalAuditContext:
    """
    Construct a verified CanonicalAuditContext from live website observation.
    Extracts real DOM elements and respects ground-truth observation limits.
    """
    clean_target = target_url.strip()
    clean_final = (final_url or clean_target).strip()
    parsed_host = urlparse(clean_final).hostname or urlparse(clean_target).hostname or "unknown"

    site_obs = SiteObservation(
        requested_url=clean_target,
        final_url=clean_final,
        hostname=parsed_host,
        http_status=http_status,
        response_headers=response_headers or {},
        fetch_duration_ms=fetch_duration_ms,
    )

    soup = BeautifulSoup(raw_html or "<html><body></body></html>", "html.parser")
    title_exists, clean_title, _ = extract_clean_title(soup, raw_html)
    page_title = clean_title if (title_exists and clean_title) else parsed_host
    doc_lang = soup.html.get("lang", "en") if soup.html else "en"

    # Meta description
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_description = meta_desc_tag.get("content", "").strip() if meta_desc_tag else None

    # Canonical link
    canonical_tag = soup.find("link", attrs={"rel": re.compile(r"^canonical$", re.I)})
    canonical_url = canonical_tag.get("href", "").strip() if canonical_tag else None

    # Headings extraction
    headings_list: list[HeadingObservation] = []
    for h_tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        try:
            level = int(h_tag.name[1])
            txt = h_tag.get_text(strip=True)
            if txt:
                headings_list.append(HeadingObservation(level=level, text=txt, bounding_box=None))
        except (ValueError, IndexError):
            pass

    # CTAs extraction (buttons, styled conversion anchors, input submits/buttons)
    ctas_list: list[CTAObservation] = []
    cta_patterns = (
        r"\b(get started|start free|free trial|buy now|purchase|order now|checkout|"
        r"sign up|register|join now|contact us|contact|talk to sales|book a demo|request demo|"
        r"schedule call|download|subscribe|claim offer|get quote|call now|inquire|explore|"
        r"view details|learn more|read more|shop now|find out more|reach out|send message|submit)\b"
    )
    for idx, btn in enumerate(soup.find_all(["button", "a", "input"])):
        if btn.name == "input":
            input_type = btn.get("type", "").lower()
            if input_type not in ("submit", "button", "reset", "image"):
                continue
            txt = btn.get("value") or btn.get("aria-label") or btn.get("title") or ""
            txt = txt.strip()
            href = ""
        else:
            txt = btn.get_text(strip=True) or btn.get("aria-label") or btn.get("title") or ""
            txt = txt.strip()
            href = btn.get("href", "")

        if not txt:
            continue

        classes = " ".join(btn.get("class", [])) if isinstance(btn.get("class"), list) else str(btn.get("class", ""))
        is_button_tag = (btn.name in ("button", "input")) or (btn.get("role") == "button")
        is_cta_styled = any(c in classes.lower() for c in ("btn", "cta", "button", "primary", "action"))
        is_cta_phrased = bool(re.search(cta_patterns, txt, re.I))

        if is_button_tag or is_cta_styled or is_cta_phrased:
            is_primary = "primary" in classes.lower() or is_cta_phrased
            role = "primary" if is_primary else "secondary"
            ctas_list.append(
                CTAObservation(
                    id=f"cta-{len(ctas_list) + 1}",
                    text=txt,
                    role=role,
                    href=href,
                    bounding_box=None,
                )
            )

    # Navigation extraction
    nav_links: list[NavLinkObservation] = []
    nav_container = soup.find("nav") or soup.find("header")
    if nav_container:
        for a in nav_container.find_all("a"):
            label = a.get_text(strip=True)
            if label:
                nav_links.append(NavLinkObservation(label=label, href=a.get("href", ""), visible=True))
    else:
        for a in soup.find_all("a")[:12]:
            label = a.get_text(strip=True)
            if label:
                nav_links.append(NavLinkObservation(label=label, href=a.get("href", ""), visible=True))

    # Images extraction
    mime_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "svg": "image/svg+xml",
        "gif": "image/gif",
        "avif": "image/avif",
        "ico": "image/x-icon",
    }
    extracted_images: list[ImageObservation] = []
    for idx, img in enumerate(soup.find_all("img")):
        src = img.get("src", "").strip()
        if not src:
            continue
        full_src = urljoin(clean_final, src)
        alt_text = img.get("alt", None)
        alt_present = alt_text is not None
        role = img.get("role", "")
        is_decorative = (role in ("presentation", "none")) or (alt_present and alt_text == "")

        filename = os.path.basename(full_src.split("?")[0]) or f"image_{idx + 1}"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime_type = mime_map.get(ext, "image/svg+xml" if "svg" in full_src.lower() else "image/png")

        # Parse declared HTML width and height attributes if numeric
        intrinsic_w = None
        intrinsic_h = None
        raw_w = img.get("width")
        raw_h = img.get("height")
        if raw_w and str(raw_w).isdigit():
            intrinsic_w = int(raw_w)
        if raw_h and str(raw_h).isdigit():
            intrinsic_h = int(raw_h)

        extracted_images.append(
            ImageObservation(
                id=f"img-{len(extracted_images) + 1:03d}",
                url=full_src,
                filename=filename,
                alt_text=alt_text,
                alt_attribute_present=alt_present,
                decorative=is_decorative,
                role=role,
                mime_type=mime_type,
                format=ext or "png",
                intrinsic_width=intrinsic_w,
                intrinsic_height=intrinsic_h,
                rendered_width=None,
                rendered_height=None,
                position=None,
            )
        )

    # Forms extraction
    forms_list: list[FormObservation] = []
    for fidx, frm in enumerate(soup.find_all("form")):
        fields = [
            {"name": inp.get("name", f"field-{i}"), "type": inp.get("type", "text")}
            for i, inp in enumerate(frm.find_all(["input", "textarea", "select"]))
        ]
        forms_list.append(
            FormObservation(
                name=frm.get("name") or frm.get("id") or f"form-{fidx + 1}",
                field_count=len(fields),
                fields=fields,
            )
        )

    # Structured Data (JSON-LD)
    jsonld_blocks: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        content = script.string or script.get_text() or ""
        if content.strip():
            jsonld_blocks.append(content.strip())

    # Text extraction
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    full_text = " ".join(paragraphs) if paragraphs else page_title

    # Popups / Modals detection in DOM
    popups_list: list[dict[str, Any]] = []
    for elem in soup.find_all(["dialog", "div", "section"]):
        elem_classes = " ".join(elem.get("class", [])) if isinstance(elem.get("class"), list) else str(elem.get("class", ""))
        elem_id = str(elem.get("id", ""))
        is_modal = elem.name == "dialog" or any(k in elem_classes.lower() or k in elem_id.lower() for k in ["modal", "popup", "backdrop", "overlay"])
        if is_modal:
            has_close = bool(elem.find(["button", "a"], attrs={"aria-label": re.compile(r"close|dismiss", re.I)}) or elem.find(string=re.compile(r"✕|×|close", re.I)))
            popups_list.append({
                "id": elem_id or f"modal-{len(popups_list) + 1}",
                "is_blocking": True if "modal" in elem_classes.lower() else False,
                "has_close_button": has_close,
                "immediate": True,
            })

    doc_obs = DocumentObservation(
        raw_html=raw_html,
        title=page_title,
        lang=doc_lang,
        meta_description=meta_description,
        canonical_url=canonical_url,
        headings=headings_list,
        ctas=ctas_list[:25],
        nav_links=nav_links[:20],
        images=extracted_images,
        forms=forms_list,
        structured_data_blocks=jsonld_blocks,
        paragraphs=paragraphs,
        full_text=full_text,
        popups=popups_list,
    )

    # Rendered observations (if browser data provided)
    has_browser = False
    browser_err = None
    mobile_ev = None
    viewports_measured = []
    horiz_overflow = None

    if render_observations and isinstance(render_observations, dict):
        status = (
            render_observations.get("status")
            or render_observations.get("rendering", {}).get("status")
            or render_observations.get("render", {}).get("status")
            or render_observations.get("audit", {}).get("status")
        )
        if status in ("completed", "ok", "passed", "issues_found", "success", "partial"):
            has_browser = True
            # Multi-viewport measurements from visual accessibility audit or browser render
            viewports = render_observations.get("viewports", [])
            for vp in viewports:
                if isinstance(vp, dict):
                    viewports_measured.append(vp)
                    if vp.get("name") == "mobile":
                        mobile_ev = {
                            "horizontal_overflow": vp.get("horizontal_overflow", False),
                            "scroll_width": vp.get("scroll_width"),
                            "client_width": vp.get("client_width"),
                            "viewport": {"width": vp.get("width", 390), "height": vp.get("height", 844)},
                        }
                        horiz_overflow = vp.get("horizontal_overflow", False)
        else:
            errors = render_observations.get("errors", [])
            err_msg = errors[0].get("message") if (errors and isinstance(errors[0], dict)) else (errors[0] if errors else None)
            browser_err = err_msg or render_observations.get("error") or "Browser execution degraded or unavailable."

    rendered_obs = RenderedObservation(
        has_browser_data=has_browser,
        browser_error=browser_err,
        viewports_measured=viewports_measured,
        mobile_evidence=mobile_ev,
        horizontal_overflow=horiz_overflow,
    )

    # Crawl observations
    crawl_obs = CrawlObservation()
    if crawl_observations and isinstance(crawl_observations, dict):
        crawl_obs.pages_visited = [clean_final]
        crawl_obs.sitemaps = crawl_observations.get("sitemaps", [])
        crawl_obs.crawl_errors = crawl_observations.get("errors", [])

    return CanonicalAuditContext(
        site=site_obs,
        document=doc_obs,
        rendered=rendered_obs,
        crawl=crawl_obs,
        freshness=FreshnessObservation(),
    )



# Cross-Page Fact Extraction and Consistency Engine


def extract_page_facts(html: str, structured_data: Any = None, url: str = "") -> dict[str, Any]:
    """
    Extract key entity and organizational facts from a single page's HTML and structured data.
    Captures founding dates, brand identities, contact points, and location coordinates.
    """
    facts: dict[str, Any] = {}
    if not html or not isinstance(html, str):
        return facts

    # 1. Founding Year from text patterns
    text_match = re.search(
        r"\b(?:founded|established|since|est\.?)\s*(?:in\s*)?(\b(?:19|20)\d{2}\b)",
        html,
        re.IGNORECASE,
    )
    if text_match:
        facts["founding_year"] = text_match.group(1)

    # 2. Extract from structured_data if provided
    if isinstance(structured_data, dict):
        entities = structured_data.get("entities", []) or []
        for ent in entities:
            if isinstance(ent, dict):
                if ent.get("foundingDate") and "founding_year" not in facts:
                    fd_match = re.search(r"\b((?:19|20)\d{2})\b", str(ent.get("foundingDate")))
                    if fd_match:
                        facts["founding_year"] = fd_match.group(1)
                if ent.get("name") and ent.get("@type") in ("Organization", "Corporation", "LocalBusiness"):
                    facts["brand_name"] = str(ent.get("name")).strip()
                if ent.get("telephone"):
                    facts["telephone"] = str(ent.get("telephone")).strip()
                if ent.get("email"):
                    facts["email"] = str(ent.get("email")).strip()

    # 3. Search raw JSON-LD blocks in HTML
    try:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
            raw_c = script.string or script.get_text() or ""
            if not raw_c.strip():
                continue
            try:
                parsed = json.loads(raw_c)
                items = parsed if isinstance(parsed, list) else [parsed]
                for itm in items:
                    if not isinstance(itm, dict):
                        continue
                    if itm.get("foundingDate") and "founding_year" not in facts:
                        fd_m = re.search(r"\b((?:19|20)\d{2})\b", str(itm.get("foundingDate")))
                        if fd_m:
                            facts["founding_year"] = fd_m.group(1)
                    if itm.get("name") and itm.get("@type") in ("Organization", "Corporation", "LocalBusiness") and "brand_name" not in facts:
                        facts["brand_name"] = str(itm.get("name")).strip()
                    if itm.get("telephone") and "telephone" not in facts:
                        facts["telephone"] = str(itm.get("telephone")).strip()
                    if itm.get("email") and "email" not in facts:
                        facts["email"] = str(itm.get("email")).strip()
            except Exception:
                pass

        # Meta tags
        if "brand_name" not in facts:
            og_site = soup.find("meta", attrs={"property": "og:site_name"})
            if og_site and og_site.get("content"):
                facts["brand_name"] = og_site["content"].strip()

        # Tel / Mailto links
        if "telephone" not in facts:
            tel_link = soup.find("a", href=re.compile(r"^tel:", re.I))
            if tel_link:
                href = tel_link.get("href", "")
                raw_tel = re.sub(r"^tel:", "", href, flags=re.I).strip()
                if raw_tel:
                    facts["telephone"] = raw_tel

        if "email" not in facts:
            mail_link = soup.find("a", href=re.compile(r"^mailto:", re.I))
            if mail_link:
                href = mail_link.get("href", "")
                raw_mail = re.sub(r"^mailto:", "", href, flags=re.I).split("?")[0].strip()
                if raw_mail:
                    facts["email"] = raw_mail
    except Exception:
        pass

    return facts


def check_cross_page_fact_consistency(page_fact_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Compare verified factual claims across multiple audited pages on the same website.
    Detects contradictory founding dates, conflicting phone numbers, or inconsistent identities.
    """
    findings: list[dict[str, Any]] = []
    if not page_fact_records or len(page_fact_records) < 2:
        return findings

    # 1. Cross-page founding date conflict check
    years_by_val: dict[str, list[str]] = {}
    for record in page_fact_records:
        url = record.get("url", "unknown_page")
        facts = record.get("facts", {})
        fy = facts.get("founding_year")
        if fy:
            years_by_val.setdefault(str(fy), []).append(url)

    if len(years_by_val) > 1:
        details = " vs ".join(f"'{y}' on {', '.join(urls)}" for y, urls in sorted(years_by_val.items()))
        findings.append({
            "id": "FRESH-CONFLICT-001",
            "title": "Conflicting company founding year across audited pages",
            "severity": "medium",
            "evidence": f"Conflicting company founding year across audited pages: {details}.",
            "suggested_action": {
                "summary": "Standardize company founding dates and historical claims across all web templates and Schema.org markup.",
                "priority": "medium",
            },
            "confidence": 0.95,
            "evidence_tier": "tier_1",
        })

    # 2. Cross-page telephone contact conflict check
    phone_by_val: dict[str, list[str]] = {}
    for record in page_fact_records:
        url = record.get("url", "unknown_page")
        facts = record.get("facts", {})
        phone = facts.get("telephone")
        if phone:
            norm_phone = re.sub(r"[^\d+]", "", str(phone))
            if len(norm_phone) >= 7:
                phone_by_val.setdefault(norm_phone, []).append((str(phone), url))

    if len(phone_by_val) > 1:
        # Check if they are distinct numbers
        keys = list(phone_by_val.keys())
        if len(keys) > 1:
            details = " vs ".join(f"'{phone_by_val[k][0][0]}' on {phone_by_val[k][0][1]}" for k in keys[:3])
            findings.append({
                "id": "FRESH-CONFLICT-002",
                "title": "Conflicting primary telephone numbers across audited pages",
                "severity": "medium",
                "evidence": f"Conflicting primary telephone contact numbers declared across audited pages: {details}.",
                "suggested_action": {
                    "summary": "Consolidate official customer telephone contacts and annotate distinct department numbers explicitly.",
                    "priority": "medium",
                },
                "confidence": 0.90,
                "evidence_tier": "tier_1",
            })

    return findings
