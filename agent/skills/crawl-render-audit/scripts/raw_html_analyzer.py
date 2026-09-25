"""
Raw HTML Analyzer Module (raw_html_analyzer.py)
-----------------------------------------------
Deterministic, in-memory parser and structural analyzer for RAW HTML payloads
captured directly from server responses prior to JavaScript execution.

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
            +---- raw_html_analyzer.py  <-- (THIS MODULE)
            |       |
            |       v
            |   RAW HTML baseline observations
            |
            +---- render_analyzer.py
            |       |
            |       v
            |   rendered DOM observations
            |
            +---- dom_comparator.py (Compares raw vs rendered)
            |
            +---- finding_builder.py
                    |
                    v
            final structured findings

This module does NOT execute JavaScript, make network calls, or calculate audit
severities/recommendations. It provides purely factual observations of raw HTML content.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, Tag

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.raw_html_analyzer")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Standard SPA root container element IDs
KNOWN_SPA_ROOT_IDS: tuple[str, ...] = (
    "root",
    "app",
    "__next",
    "app-root",
    "___gatsby",
    "mount",
    "main-app",
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



# Helper Functions: Extraction & Classification


def _clean_text(text: str | None) -> str:
    """Normalize whitespace in text content."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_clean_title(soup: BeautifulSoup, html_raw: str | None = None) -> tuple[bool, str | None, int]:
    """
    Extract only the contents of the HTML <title> element cleanly and deterministically.

    Requirements:
    - Extract only the contents of the HTML <title> element.
    - Do not append body text or descendant content because of malformed/unclosed HTML.
    - Handle normal HTML.
    - Handle missing title (returns (False, None, 0)).
    - Handle malformed HTML.
    - Handle multiple title tags deterministically (takes first declared title, reports duplicate count).
    - Strip irrelevant whitespace.
    - Preserve current behavior for valid pages.
    """
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
            # Child tag encountered (e.g. <body>, <div>, <h1> swallowed due to unclosed <title>)
            break

    raw_text = "".join(extracted_parts) if extracted_parts else (first_tag.string or "")

    # 2. Check if the <title> tag was properly closed in the source HTML
    # In html.parser, an unclosed <title> swallows subsequent markup as raw text (e.g. <body>, <div>, <h1>, <p>).
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
        # Tag was unclosed; truncate at any swallowed markup boundary
        tag_boundary_pattern = re.compile(r"<\s*(?:/?\s*[a-zA-Z][a-zA-Z0-9:-]*|!--)", re.DOTALL)
        match = tag_boundary_pattern.search(raw_text)
        if match:
            raw_text = raw_text[:match.start()]

    cleaned = re.sub(r"\s+", " ", raw_text).strip()
    return True, cleaned, duplicate_count


def _is_absolute_url(url: str) -> bool:
    """Check if URL has an explicit scheme (http/https)."""
    if not url:
        return False
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def _classify_link(
    href: str | None,
    base_url: str,
) -> dict[str, bool]:
    """Classify a link as internal, external, relative, empty, or fragment-only."""
    if href is None or href.strip() == "":
        return {"is_empty": True, "is_fragment": False, "is_internal": False, "is_external": False, "is_relative": False}

    href_clean = href.strip()
    if href_clean.startswith("#"):
        return {"is_empty": False, "is_fragment": True, "is_internal": True, "is_external": False, "is_relative": False}

    parsed_target = urlparse(href_clean)
    parsed_base = urlparse(base_url)

    if not parsed_target.scheme and not parsed_target.netloc:
        # Relative link
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

        # Handle @graph
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                types.extend(_extract_json_ld_types(item))

        # Check nested dict values
        for val in data.values():
            if isinstance(val, (dict, list)):
                types.extend(_extract_json_ld_types(val))

    elif isinstance(data, list):
        for item in data:
            types.extend(_extract_json_ld_types(item))

    return list(dict.fromkeys(types))  # Deduplicate preserving order



# Main Analyzer Function


def analyze_raw_html(
    html: str | None,
    target_url: str,
    response_metadata: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Perform a complete, deterministic structural and textual analysis of raw HTML.

    Args:
        html: Raw HTML string from server response body.
        target_url: The requested or final URL of the document.
        response_metadata: Optional HTTP response details (headers, status, time).
        options: Optional configuration options.

    Returns:
        Structured dictionary of factual observations conforming to the audit contract.
    """
    opts = options or {}
    meta = response_metadata or {}
    html_raw = html if html is not None else ""
    target_url_clean = target_url or meta.get("final_url") or ""

    html_bytes_len = len(html_raw.encode("utf-8"))
    html_chars_len = len(html_raw)

    # Empty payload handling
    if not html_raw.strip():
        logger.info("Empty HTML payload provided for %s", target_url_clean)
        return {
            "skill": "crawl-render-audit",
            "component": "raw_html_analyzer",
            "target_url": target_url_clean,
            "response": {
                "status_code": meta.get("status_code", 200),
                "content_type": meta.get("content_type"),
                "final_url": meta.get("final_url", target_url_clean),
                "response_time_ms": meta.get("response_time_ms"),
            },
            "html_metrics": {
                "html_bytes": 0,
                "html_characters": 0,
                "element_count": 0,
                "script_count": 0,
                "stylesheet_count": 0,
                "inline_style_count": 0,
                "form_count": 0,
                "link_count": 0,
                "image_count": 0,
                "iframe_count": 0,
                "heading_count": 0,
                "paragraph_count": 0,
                "list_count": 0,
                "table_count": 0,
            },
            "title": {"exists": False, "value": None, "character_count": 0, "duplicate_count": 0},
            "meta_description": {"exists": False, "content": None, "character_count": 0},
            "canonical": {"exists": False, "value": None, "is_absolute": False, "is_valid": False, "duplicate_count": 0},
            "language": {"declared": False, "value": None},
            "headings": {
                "h1_count": 0,
                "h2_count": 0,
                "h3_count": 0,
                "h4_count": 0,
                "h5_count": 0,
                "h6_count": 0,
                "h1_text": [],
                "multiple_h1": False,
                "empty_headings_count": 0,
            },
            "text_content": {
                "character_count": 0,
                "word_count": 0,
                "main_exists": False,
                "main_text_characters": 0,
                "article_exists": False,
                "article_text_characters": 0,
                "body_text_characters": 0,
            },
            "links": {"total": 0, "internal": 0, "external": 0, "relative": 0, "empty": 0, "fragment_only": 0},
            "images": {"count": 0, "missing_alt_count": 0, "empty_alt_count": 0, "images": []},
            "structured_data": {"json_ld_blocks": 0, "valid_json_ld_blocks": 0, "invalid_json_ld_blocks": 0, "types": []},
            "robots_meta": {"exists": False, "content": None, "directives": []},
            "robots_headers": {
                "x_robots_tag": meta.get("x_robots_tag") or meta.get("headers", {}).get("x-robots-tag"),
            },
            "semantic_structure": {tag: 0 for tag in SEMANTIC_TAGS},
            "spa_indicators": {
                "root_containers": [],
                "body_text_characters": 0,
                "script_count": 0,
                "possible_client_rendered_shell": True,
                "reasons": ["Empty HTML body returned from server"],
            },
            "machine_readability": {
                "has_meaningful_text": False,
                "has_title": False,
                "has_headings": False,
                "has_links": False,
                "has_structured_data": False,
                "has_semantic_main": False,
                "content_available_in_raw_html": False,
            },
            "scripts": {
                "total_script_count": 0,
                "external_script_count": 0,
                "inline_script_count": 0,
                "script_srcs": [],
                "inline_script_bytes": 0,
            },
            "noscript": {"count": 0, "text_characters": 0},
            "forms": {"count": 0, "forms": []},
            "structural_quality": {
                "missing_html_tag": True,
                "missing_head_tag": True,
                "missing_body_tag": True,
                "empty_body": True,
            },
            "errors": ["HTML content is empty"],
        }

    # Step 1: Parse HTML with BeautifulSoup
    soup = BeautifulSoup(html_raw, "html.parser")

    # Step 2: Structural Tag Presence Check
    has_html_tag = bool(soup.find("html"))
    has_head_tag = bool(soup.find("head"))
    has_body_tag = bool(soup.find("body"))

    # Step 3: Title Extraction
    title_exists, title_val, title_dup_count = extract_clean_title(soup, html_raw)
    title_char_count = len(title_val) if title_val else 0

    # Step 4: Meta Description Extraction
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

    # Step 5: Canonical Link Extraction
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
            # Try normalizing
            try:
                if canonical_is_abs:
                    canonical_normalized = canonical_val
                    canonical_valid = True
                else:
                    canonical_normalized = urljoin(target_url_clean, canonical_val)
                    canonical_valid = bool(urlparse(canonical_normalized).scheme)
            except Exception:
                canonical_valid = False

    # Step 6: Language Extraction
    html_tag = soup.find("html")
    lang_val = html_tag.get("lang", "").strip() if html_tag and html_tag.get("lang") else None
    language_info = {
        "declared": bool(lang_val),
        "value": lang_val,
    }

    # Step 7: Headings Extraction
    headings_data: dict[str, Any] = {
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
    empty_headings_count = sum(1 for h in all_headings if not _clean_text(h.get_text()))
    headings_data["empty_headings_count"] = empty_headings_count

    # Step 8: Clean Text Extraction (Exclude scripts, styles, noscript, comments)
    text_soup = BeautifulSoup(html_raw, "html.parser")
    for elem in text_soup(["script", "style", "noscript", "template", "svg"]):
        elem.extract()
    for comment in text_soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    body_tag = text_soup.find("body")
    body_text = _clean_text(body_tag.get_text()) if body_tag else _clean_text(text_soup.get_text())
    total_text_chars = len(body_text)
    word_count = len(body_text.split()) if body_text else 0

    main_tag = text_soup.find("main")
    main_text = _clean_text(main_tag.get_text()) if main_tag else ""
    article_tag = text_soup.find("article")
    article_text = _clean_text(article_tag.get_text()) if article_tag else ""

    text_content_info = {
        "character_count": total_text_chars,
        "word_count": word_count,
        "main_exists": bool(main_tag),
        "main_text_characters": len(main_text),
        "article_exists": bool(article_tag),
        "article_text_characters": len(article_text),
        "body_text_characters": total_text_chars,
    }

    # Step 9: Links Extraction & Classification
    a_tags = soup.find_all("a")
    link_stats = {"total": len(a_tags), "internal": 0, "external": 0, "relative": 0, "empty": 0, "fragment_only": 0}
    discovered_internal_urls: list[str] = []
    internal_links_list: list[dict[str, str]] = []

    for a in a_tags:
        href = a.get("href")
        anchor_text = _clean_text(a.get_text())
        classified = _classify_link(href, target_url_clean)
        if classified["is_empty"]:
            link_stats["empty"] += 1
        elif classified["is_fragment"]:
            link_stats["fragment_only"] += 1
        else:
            if classified["is_relative"]:
                link_stats["relative"] += 1
            if classified["is_internal"]:
                link_stats["internal"] += 1
                try:
                    resolved = urljoin(target_url_clean, str(href or ""))
                    resolved_clean = resolved.split("#")[0].strip()
                    lower_res = resolved_clean.lower()
                    media_exts = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".css", ".js", ".zip", ".mp4", ".mp3", ".woff", ".woff2")
                    if (
                        resolved_clean
                        and resolved_clean.startswith("http")
                        and not any(lower_res.endswith(ext) for ext in media_exts)
                        and resolved_clean not in discovered_internal_urls
                    ):
                        discovered_internal_urls.append(resolved_clean)
                        internal_links_list.append({"url": resolved_clean, "text": anchor_text[:100]})
                except Exception:
                    pass
            elif classified["is_external"]:
                link_stats["external"] += 1

    link_stats["discovered_urls"] = discovered_internal_urls
    link_stats["internal_links"] = internal_links_list

    # Step 10: Images Extraction
    img_tags = soup.find_all("img")
    missing_alt_count = 0
    empty_alt_count = 0
    images_list: list[dict[str, Any]] = []

    for img in img_tags:
        src = img.get("src", "").strip() if img.get("src") else None
        has_alt_attr = "alt" in img.attrs
        alt_val = img.get("alt", "")

        if not has_alt_attr:
            missing_alt_count += 1
        elif alt_val == "":
            empty_alt_count += 1

        if len(images_list) < 50:  # Sample up to 50 images
            img_entry = {
                "src": src,
                "alt_present": has_alt_attr,
                "alt": alt_val if has_alt_attr else None,
            }
            if img.get("width"):
                img_entry["width"] = img.get("width")
            if img.get("height"):
                img_entry["height"] = img.get("height")
            if img.get("loading"):
                img_entry["loading"] = img.get("loading")
            images_list.append(img_entry)

    images_info = {
        "count": len(img_tags),
        "missing_alt_count": missing_alt_count,
        "empty_alt_count": empty_alt_count,
        "images": images_list,
    }

    # Step 11: Structured Data (JSON-LD)
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

    structured_data_info = {
        "json_ld_blocks": len(json_ld_scripts),
        "valid_json_ld_blocks": valid_json_ld,
        "invalid_json_ld_blocks": invalid_json_ld,
        "types": list(dict.fromkeys(detected_types)),
    }

    # Step 12: Meta Robots
    robots_meta_tag = soup.find(
        "meta",
        attrs={"name": lambda n: bool(n and n.strip().lower() in ("robots", "googlebot", "bingbot"))},
    )
    robots_meta_exists = False
    robots_content: str | None = None
    robots_directives: list[str] = []

    if robots_meta_tag and robots_meta_tag.get("content"):
        robots_meta_exists = True
        robots_content = robots_meta_tag.get("content", "").strip()
        robots_directives = [d.strip().lower() for d in re.split(r"[,;]+", robots_content) if d.strip()]

    # HTTP X-Robots-Tag from response metadata headers
    headers_map = meta.get("headers", {})
    x_robots_header = meta.get("x_robots_tag") or headers_map.get("x-robots-tag")

    # Step 13: Semantic Structure Counts
    semantic_counts = {tag: len(soup.find_all(tag)) for tag in SEMANTIC_TAGS}

    # Step 14: Scripts and Stylesheets
    scripts = soup.find_all("script")
    external_scripts = [s.get("src") for s in scripts if s.get("src")]
    inline_scripts = [s for s in scripts if not s.get("src")]
    inline_script_bytes = sum(len((s.string or s.get_text() or "").encode("utf-8")) for s in inline_scripts)

    scripts_info = {
        "total_script_count": len(scripts),
        "external_script_count": len(external_scripts),
        "inline_script_count": len(inline_scripts),
        "script_srcs": external_scripts[:20],
        "inline_script_bytes": inline_script_bytes,
    }

    # Step 15: Noscript
    noscripts = soup.find_all("noscript")
    noscript_chars = sum(len(_clean_text(ns.get_text())) for ns in noscripts)

    # Step 16: Forms
    forms = soup.find_all("form")
    forms_list = []
    for f in forms:
        forms_list.append({
            "action": f.get("action", "").strip() if f.get("action") else None,
            "method": f.get("method", "get").upper(),
        })

    # Step 17: SPA Shell Detection Heuristics
    detected_root_containers: list[str] = []
    for root_id in KNOWN_SPA_ROOT_IDS:
        if soup.find(id=root_id):
            detected_root_containers.append(root_id)

    spa_reasons: list[str] = []
    is_spa_shell = False

    # Heuristic 1: Minimal text (< 300 chars) with application root container
    if total_text_chars < 300 and detected_root_containers:
        is_spa_shell = True
        spa_reasons.append(f"Low raw text volume ({total_text_chars} chars) combined with SPA root element (#{detected_root_containers[0]})")

    # Heuristic 2: Extremely low text (< 150 chars) with multiple script tags
    elif total_text_chars < 150 and len(scripts) >= 2:
        is_spa_shell = True
        spa_reasons.append(f"Minimal raw text ({total_text_chars} chars) with {len(scripts)} script tags")

    # Heuristic 3: Empty body element despite non-empty HTML
    elif body_tag and not body_text and len(scripts) > 0:
        is_spa_shell = True
        spa_reasons.append("Empty body tag with client-side script tags")

    preloader_match = (
        soup.find(id=re.compile(r"preloader|loader|spinner", re.I))
        or soup.find(class_=re.compile(r"preloader|loader|spinner", re.I))
    )
    has_preloader = bool(preloader_match)
    preloader_details: dict[str, Any] | None = None

    if preloader_match:
        p_tag = preloader_match.name or "div"
        p_id_raw = preloader_match.get("id")
        p_id = " ".join(p_id_raw) if isinstance(p_id_raw, list) else (str(p_id_raw) if p_id_raw else None)
        
        p_cls_raw = preloader_match.get("class")
        if isinstance(p_cls_raw, list):
            p_class_str = " ".join(p_cls_raw)
            p_classes = p_cls_raw
        elif isinstance(p_cls_raw, str):
            p_class_str = p_cls_raw
            p_classes = p_cls_raw.split()
        else:
            p_class_str = ""
            p_classes = []

        selector_parts = [p_tag]
        if p_id:
            selector_parts.append(f"#{p_id}")
        if p_classes:
            selector_parts.append("." + ".".join(p_classes))
        p_selector = "".join(selector_parts) if (p_id or p_classes) else p_tag

        p_style = str(preloader_match.get("style") or "").lower()
        combined_cues = f"{p_tag} {p_id or ''} {p_class_str} {p_style}".lower()

        is_progress_bar = bool(
            p_tag in ("progress", "meter")
            or any(kw in combined_cues for kw in ["progress", "progressbar", "meter", "mini", "inline", "tiny", "btn", "button"])
            or re.search(r"height\s*:\s*[1-9]px", p_style)
        )

        has_blocking_style = bool(
            re.search(r"position\s*:\s*(fixed|absolute)", p_style)
            or re.search(r"(width|min-width)\s*:\s*(100%|100vw)", p_style)
            or re.search(r"(height|min-height)\s*:\s*(100%|100vh)", p_style)
            or re.search(r"inset\s*:\s*0", p_style)
            or re.search(r"z-index\s*:\s*[1-9]\d{2,}", p_style)
        )
        has_blocking_cues = bool(
            any(kw in combined_cues for kw in ["overlay", "fullscreen", "screen", "backdrop", "cover", "preloader", "page-loader", "site-loader", "full"])
        )

        blocks_viewport = (has_blocking_style or has_blocking_cues) and not is_progress_bar

        preloader_details = {
            "tag": p_tag,
            "id": p_id,
            "class": p_class_str,
            "classes": p_classes,
            "selector": p_selector,
            "style": p_style,
            "is_progress_bar": is_progress_bar,
            "blocks_viewport": blocks_viewport,
        }

    spa_indicators_info = {
        "root_containers": detected_root_containers,
        "body_text_characters": total_text_chars,
        "script_count": len(scripts),
        "possible_client_rendered_shell": is_spa_shell or has_preloader,
        "has_preloader": has_preloader,
        "preloader_details": preloader_details,
        "reasons": spa_reasons,
    }

    # Step 18: Basic Elements Metrics
    all_elements = soup.find_all(True)
    stylesheets = soup.find_all("link", attrs={"rel": lambda r: bool(r and "stylesheet" in (r if isinstance(r, list) else [r]))})
    inline_styles = soup.find_all("style") + soup.find_all(attrs={"style": True})
    iframes = soup.find_all("iframe")
    paragraphs = soup.find_all("p")
    lists = soup.find_all(["ul", "ol"])
    tables = soup.find_all("table")

    html_metrics = {
        "html_bytes": html_bytes_len,
        "html_characters": html_chars_len,
        "element_count": len(all_elements),
        "script_count": len(scripts),
        "stylesheet_count": len(stylesheets),
        "inline_style_count": len(inline_styles),
        "form_count": len(forms),
        "link_count": len(a_tags),
        "image_count": len(img_tags),
        "iframe_count": len(iframes),
        "heading_count": len(all_headings),
        "paragraph_count": len(paragraphs),
        "list_count": len(lists),
        "table_count": len(tables),
    }

    # Step 19: Machine Readability Summary
    has_meaningful_text = total_text_chars >= 200
    has_title = title_exists and bool(title_val)
    has_headings = len(all_headings) > 0
    has_links = len(a_tags) > 0
    has_structured_data = valid_json_ld > 0
    has_semantic_main = bool(main_tag)

    content_available_in_raw = (
        has_meaningful_text
        and (has_title or has_headings)
        and not is_spa_shell
    )

    machine_readability = {
        "has_meaningful_text": has_meaningful_text,
        "has_title": has_title,
        "has_headings": has_headings,
        "has_links": has_links,
        "has_structured_data": has_structured_data,
        "has_semantic_main": has_semantic_main,
        "content_available_in_raw_html": content_available_in_raw,
    }

    # Step 20: Structural Quality Observations
    structural_quality = {
        "missing_html_tag": not has_html_tag,
        "missing_head_tag": not has_head_tag,
        "missing_body_tag": not has_body_tag,
        "empty_body": bool(body_tag and not body_text),
        "duplicate_title_count": title_dup_count,
        "duplicate_canonical_count": max(0, len(canonical_tags) - 1),
    }

    return {
        "skill": "crawl-render-audit",
        "component": "raw_html_analyzer",
        "target_url": target_url_clean,
        "response": {
            "status_code": meta.get("status_code", 200),
            "content_type": meta.get("content_type", "text/html"),
            "final_url": meta.get("final_url", target_url_clean),
            "response_time_ms": meta.get("response_time_ms"),
        },
        "html_metrics": html_metrics,
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
        "language": language_info,
        "headings": headings_data,
        "text_content": text_content_info,
        "links": link_stats,
        "images": images_info,
        "structured_data": structured_data_info,
        "robots_meta": {
            "exists": robots_meta_exists,
            "content": robots_content,
            "directives": robots_directives,
        },
        "robots_headers": {
            "x_robots_tag": x_robots_header,
        },
        "semantic_structure": semantic_counts,
        "spa_indicators": spa_indicators_info,
        "machine_readability": machine_readability,
        "scripts": scripts_info,
        "noscript": {
            "count": len(noscripts),
            "text_characters": noscript_chars,
        },
        "forms": {
            "count": len(forms),
            "forms": forms_list,
        },
        "structural_quality": structural_quality,
        "errors": [],
    }



# CLI Interface


def _cli_entrypoint() -> None:
    """CLI runner for direct testing with an HTML file or raw string."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python raw_html_analyzer.py <HTML_FILE_PATH> [TARGET_URL]")
        print("Example: python raw_html_analyzer.py sample.html https://example.com/page")
        sys.exit(0)

    filepath = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else "https://example.com"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as exc:
        print(json.dumps({"error": f"Failed to read file: {exc}"}, indent=2))
        sys.exit(1)

    result = analyze_raw_html(html_content, target_url=url)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
