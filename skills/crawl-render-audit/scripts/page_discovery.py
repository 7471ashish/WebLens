"""
Page Discovery and Representative Page Selection Module (page_discovery.py)
----------------------------------------------------------------------------
Implements bounded, intelligent URL discovery, normalization, filtering,
and page-type diversity scoring for multi-page website audits.

Architecture Role:
    crawl-render-audit / run_master_audit
            │
            ├── robots_checker.py (permission check)
            ├── sitemap_checker.py (sitemap discovery & URL parsing)
            ├── raw_html_analyzer.py (internal link graph extraction)
            │
            ▼
    page_discovery.py (THIS MODULE)
            │
            ▼
    Representative Page Sample (<= MAX_PAGES, diverse page types)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Sequence
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger("crawl_render_audit.page_discovery")

# Maximum default page budget per audit
DEFAULT_MAX_PAGES = 8

# Binary / Media / Asset file extensions to strictly avoid crawling
IGNORED_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".bz2",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".wmv", ".webm", ".mkv",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".css", ".js", ".json", ".xml", ".txt", ".csv",
)

# Page type classification rules (regex patterns on URL path + anchor text)
PAGE_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("homepage", re.compile(r"^/?$|^/index\.(html|htm|php|aspx?)?$", re.IGNORECASE)),
    ("pricing", re.compile(r"pricing|plans|subscription|tiers|rates|quote|fees|cost|package", re.IGNORECASE)),
    ("product_service", re.compile(r"product|products|service|services|solution|solutions|feature|features|platform|item|catalog|shop|store|category|categories|menu|collection|inventory|courses", re.IGNORECASE)),
    ("about_company", re.compile(r"about|company|who-we-are|team|leadership|story|mission|history|overview|values|culture", re.IGNORECASE)),
    ("contact", re.compile(r"contact|get-in-touch|reach-us|support|help|feedback|inquiry|location|offices", re.IGNORECASE)),
    ("blog_content", re.compile(r"blog|news|article|articles|post|posts|press|insights|updates|stories|journal|media", re.IGNORECASE)),
    ("docs", re.compile(r"docs|documentation|api|guide|guides|manual|faq|knowledge-base|help-center|learn|resources|whitepaper", re.IGNORECASE)),
    ("legal", re.compile(r"terms|privacy|disclaimer|policy|legal|cookie|security|compliance", re.IGNORECASE)),
]


@dataclass
class DiscoveredPage:
    """Represents a discovered and normalized candidate page."""
    url: str
    page_type: str
    source: str  # "input" | "homepage" | "sitemap" | "internal_link" | "canonical"
    score: float = 0.0
    anchor_text: str = ""
    depth: int = 0


def normalize_url(url: str, base_url: str | None = None, allow_private: bool = False) -> str | None:
    """
    Normalize, sanitize, and validate URL string.
    - Strips fragments and whitespace.
    - Resolves relative URLs against base_url if provided.
    - Normalizes scheme and host to lower-case.
    - Removes trailing default index files (/index.html).
    - Ensures standard HTTP/HTTPS schemes.
    """
    if not url or not isinstance(url, str):
        return None

    url_str = url.strip()
    if not url_str or url_str.startswith("#"):
        return None

    # Reject unsupported protocols
    lower_val = url_str.lower()
    if any(lower_val.startswith(proto) for proto in ("mailto:", "tel:", "javascript:", "data:", "ftp:", "file:")):
        return None

    # Resolve relative URL
    if base_url:
        try:
            url_str = urljoin(base_url, url_str)
        except Exception:
            return None

    try:
        parsed = urlparse(url_str)
    except Exception:
        return None

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    # Reject loopback or private hostnames for SSRF safety unless explicitly allowed
    allow_local = allow_private or bool(os.environ.get("AUDIT_ALLOW_LOCAL", "").lower() in ("1", "true"))
    if not allow_local and hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        return None

    # Check for ignored media / asset extensions in path
    path = parsed.path or "/"
    path_lower = path.lower()
    for ext in IGNORED_EXTENSIONS:
        if path_lower.endswith(ext):
            return None

    # Normalize default index filenames
    if path.endswith("/index.html") or path.endswith("/index.htm") or path.endswith("/index.php"):
        path = path.rsplit("index.", 1)[0]
        if not path:
            path = "/"

    # Normalize trailing slash: normalize /about/ to /about (except root /)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Reconstruct normalized URL without fragment
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        parsed.query,
        "",  # No fragment
    ))
    return normalized


def is_same_site(url: str, target_url: str) -> bool:
    """Check whether candidate URL belongs to the same domain/origin as target."""
    try:
        p_cand = urlparse(url)
        p_target = urlparse(target_url)
        
        cand_host = (p_cand.hostname or "").lower()
        target_host = (p_target.hostname or "").lower()

        if cand_host == target_host:
            return True

        # Handle subdomains or www prefix variants (e.g. www.example.com vs example.com)
        cand_root = ".".join(cand_host.split(".")[-2:]) if "." in cand_host else cand_host
        target_root = ".".join(target_host.split(".")[-2:]) if "." in target_host else target_host

        return bool(cand_root and target_root and cand_root == target_root)
    except Exception:
        return False


def classify_page_type(url: str, anchor_text: str = "") -> str:
    """Classify candidate page into representative semantic category."""
    parsed = urlparse(url)
    path = (parsed.path or "/").strip()
    query = (parsed.query or "").strip()
    search_str = f"{path} {query} {anchor_text}".strip()

    if path in ("/", "") or re.match(r"^/index\.(html|htm|php|aspx?)?$", path, re.I):
        return "homepage"

    for page_type, pattern in PAGE_TYPE_PATTERNS:
        if pattern.search(search_str):
            return page_type

    return "general"


def score_candidate(
    page: DiscoveredPage,
    target_url: str,
    homepage_url: str,
) -> float:
    """
    Score a candidate URL based on priority, page type importance, and path depth.
    Higher score indicates higher priority for inclusion in the representative sample.
    """
    score = 0.0

    # Explicit input URL has highest priority
    if page.url == target_url:
        score += 100.0

    # Homepage has very high priority
    if page.url == homepage_url or page.page_type == "homepage":
        score += 80.0

    # Category value ranking
    category_weights: dict[str, float] = {
        "pricing": 50.0,
        "product_service": 45.0,
        "about_company": 40.0,
        "contact": 35.0,
        "blog_content": 30.0,
        "docs": 30.0,
        "legal": 15.0,
        "general": 20.0,
    }
    score += category_weights.get(page.page_type, 10.0)

    # Source bonus: Sitemaps and direct root internal links are authoritative
    if page.source == "sitemap":
        score += 15.0
    elif page.source == "internal_link":
        score += 10.0
    elif page.source == "canonical":
        score += 5.0

    # Depth penalty: Prefer shallow, prominent pages (e.g. /pricing over /a/b/c/d/pricing)
    parsed = urlparse(page.url)
    segments = [s for s in (parsed.path or "").split("/") if s]
    depth = len(segments)
    score -= min(depth * 3.0, 15.0)

    # Short clean URLs preferred over overly long query string URLs
    if parsed.query:
        score -= 5.0

    return score


def discover_and_select_representative_pages(
    target_url: str,
    sitemap_urls: Sequence[str] | None = None,
    internal_links: Sequence[dict[str, Any] | str] | None = None,
    canonical_url: str | None = None,
    options: dict[str, Any] | None = None,
) -> list[DiscoveredPage]:
    """
    Discover candidate URLs from all available sources and select a representative,
    diverse sample of pages up to max_pages budget.

    Args:
        target_url: The primary input target URL.
        sitemap_urls: URLs discovered from sitemap.xml / sitemap indexes.
        internal_links: URLs/anchors extracted from raw HTML / rendered DOM.
        canonical_url: Canonical link tag target.
        options: Optional configuration overrides (max_pages, allowed_page_types).

    Returns:
        List of selected DiscoveredPage instances sorted with target_url first.
    """
    opts = options or {}
    max_pages = int(opts.get("max_pages", DEFAULT_MAX_PAGES))
    if max_pages <= 0:
        max_pages = DEFAULT_MAX_PAGES

    is_local = "127.0.0.1" in target_url or "localhost" in target_url or bool(opts.get("allow_private_ips")) or bool(os.environ.get("AUDIT_ALLOW_LOCAL", "").lower() in ("1", "true"))
    norm_target = normalize_url(target_url, allow_private=is_local)
    if not norm_target:
        return []

    # Derive homepage URL (root of origin)
    p = urlparse(norm_target)
    homepage_url = normalize_url(urlunparse((p.scheme, p.netloc, "/", "", "", "")), allow_private=is_local) or norm_target

    candidates: dict[str, DiscoveredPage] = {}

    def _add_candidate(url: str, source: str, anchor_text: str = "", depth: int = 0) -> None:
        norm = normalize_url(url, base_url=norm_target, allow_private=is_local)
        if not norm or not is_same_site(norm, norm_target):
            return
        if norm in candidates:
            # Upgrade source if higher fidelity
            if source in ("input", "homepage", "sitemap") and candidates[norm].source not in ("input", "homepage"):
                candidates[norm].source = source
            if anchor_text and not candidates[norm].anchor_text:
                candidates[norm].anchor_text = anchor_text
            return

        ptype = classify_page_type(norm, anchor_text)
        candidates[norm] = DiscoveredPage(
            url=norm,
            page_type=ptype,
            source=source,
            anchor_text=anchor_text,
            depth=depth,
        )

    # 1. Add Target URL
    _add_candidate(norm_target, source="input", depth=0)

    # 2. Add Homepage (if separate)
    if homepage_url != norm_target:
        _add_candidate(homepage_url, source="homepage", depth=0)

    # 3. Add Canonical URL
    if canonical_url:
        _add_candidate(canonical_url, source="canonical", depth=0)

    # 4. Ingest Sitemap URLs
    if sitemap_urls:
        for sm_url in sitemap_urls:
            if isinstance(sm_url, str):
                _add_candidate(sm_url, source="sitemap", depth=1)

    # 5. Ingest Internal Links
    if internal_links:
        for itm in internal_links:
            if isinstance(itm, dict):
                u = itm.get("url") or itm.get("href") or ""
                txt = itm.get("text") or itm.get("anchor") or ""
                _add_candidate(u, source="internal_link", anchor_text=txt, depth=1)
            elif isinstance(itm, str):
                _add_candidate(itm, source="internal_link", depth=1)

    # If only 1 page was discovered in total, return it immediately
    if len(candidates) <= 1:
        selected_single = list(candidates.values())
        return selected_single

    # Score all candidates
    for page in candidates.values():
        page.score = score_candidate(page, norm_target, homepage_url)

    # Selection Algorithm: Maximize Page-Type Diversity
    selected_map: dict[str, DiscoveredPage] = {}

    # Mandatory picks: target_url always included
    if norm_target in candidates:
        selected_map[norm_target] = candidates[norm_target]

    # Include homepage if distinct and space allows
    if homepage_url in candidates and homepage_url not in selected_map and len(selected_map) < max_pages:
        selected_map[homepage_url] = candidates[homepage_url]

    # Group remaining candidates by page type
    by_type: dict[str, list[DiscoveredPage]] = {}
    for page in candidates.values():
        if page.url not in selected_map:
            by_type.setdefault(page.page_type, []).append(page)

    # Sort candidates within each page type by score descending
    for ptype, plist in by_type.items():
        plist.sort(key=lambda p: p.score, reverse=True)

    # Desired diversity order for balanced site representation
    diversity_order = [
        "pricing",
        "product_service",
        "about_company",
        "contact",
        "blog_content",
        "docs",
        "legal",
        "general",
    ]

    # Round 1: Select top candidate from each distinct page type category
    for ptype in diversity_order:
        if len(selected_map) >= max_pages:
            break
        available = by_type.get(ptype, [])
        if available:
            top_pick = available.pop(0)
            selected_map[top_pick.url] = top_pick

    # Round 2: Fill remaining budget with highest-scoring remaining candidates
    remaining_pool: list[DiscoveredPage] = []
    for plist in by_type.values():
        remaining_pool.extend(plist)
    remaining_pool.sort(key=lambda p: p.score, reverse=True)

    # Ensure path prefix diversity when filling remainder (avoid picking 5 identical blog posts)
    chosen_prefixes: set[str] = set()
    for p in selected_map.values():
        parts = [seg for seg in urlparse(p.url).path.split("/") if seg]
        if len(parts) >= 2:
            chosen_prefixes.add(parts[0])

    for cand in remaining_pool:
        if len(selected_map) >= max_pages:
            break
        if cand.url not in selected_map:
            # If candidate shares a path prefix with 2 already chosen pages, deprioritize unless necessary
            parts = [seg for seg in urlparse(cand.url).path.split("/") if seg]
            prefix = parts[0] if parts else ""
            prefix_count = sum(1 for sel in selected_map.values() if prefix and prefix in sel.url)
            if prefix_count < 2 or len(selected_map) + len(remaining_pool) <= max_pages:
                selected_map[cand.url] = cand

    # Final fallback if still under budget
    if len(selected_map) < max_pages:
        for cand in remaining_pool:
            if cand.url not in selected_map and len(selected_map) < max_pages:
                selected_map[cand.url] = cand

    final_selected = list(selected_map.values())
    
    # Ensure target_url is first in the list
    final_selected.sort(key=lambda p: (0 if p.url == norm_target else (1 if p.url == homepage_url else 2), -p.score))

    logger.info(
        "Page discovery for %s: total_discovered=%d, selected=%d, page_types=%s",
        norm_target,
        len(candidates),
        len(final_selected),
        list({p.page_type for p in final_selected}),
    )

    return final_selected
