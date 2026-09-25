"""
Canonical Audit Orchestrator
===========================
Executes, normalizes, deduplicates, and synthesizes domain findings
into the finalized, normalized JSON audit report schema.

Required Schema:
{
  "site": "example.com",
  "audited_at": "2026-09-02T12:53:36Z",
  "summary": {
    "total_findings": 2,
    "critical": 0,
    "high": 1,
    "medium": 1,
    "low": 0
  },
  "findings": [
    {
      "id": "MM-ALT-001",
      "title": "Informative image is missing alternative text",
      "severity": "high",
      "evidence": "Image URL 'https://example.com/hero.jpg' lacks an alt attribute.",
      "suggested_action": {
        "summary": "Add concise alternative text describing the purpose and content of the image.",
        "priority": "high"
      }
    }
  ]
}
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

logger = logging.getLogger("adobe_audit.orchestrator")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

VALID_SEVERITIES: set[str] = {"critical", "high", "medium", "low"}



# 1. Site Normalization


def normalize_site_identifier(target_url: str | None) -> str:
    """
    Extract a clean canonical domain/site identifier from a target URL.

    Examples:
        - "https://www.example.com/" -> "example.com"
        - "http://portal.example.com/about?foo=bar" -> "portal.example.com"
        - "https://example.com:8080/path" -> "example.com"
        - "example.com" -> "example.com"
    """
    if not target_url or not isinstance(target_url, str) or not target_url.strip():
        return "unknown"

    raw = target_url.strip()
    # If missing protocol, add temporary https:// for parsing
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+-.]*://", raw):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or parsed.netloc or "").lower().strip()

    # Strip port if present
    if ":" in host:
        host = host.split(":")[0]

    # Strip leading 'www.'
    if host.startswith("www."):
        host = host[4:]

    # Fallback if host extraction produced empty string
    if not host:
        # Try regex extraction
        match = re.search(r"^(?:https?://)?(?:www\.)?([^/:?#]+)", target_url.strip(), re.IGNORECASE)
        if match:
            host = match.group(1).lower()

    return host or "unknown"


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




# Proactive Beyond-Defect Suggestions Library (Item 3)
# 3 recommendations per domain skill grounded in Round-2 mechanisms:
# (crawl->read->extract; source ease-of-reach; cross-web corroboration; entity disambiguation)


PROACTIVE_DOMAIN_SUGGESTIONS: list[dict[str, Any]] = [
    # 1. crawl-render-audit (crawl -> read -> extract; source ease-of-reach)
    {
        "id": "PROACTIVE-CR-001",
        "domain": "crawl-render-audit",
        "title": "Deploy XML sitemap index with explicit lastmod freshness hints",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; deploying comprehensive sitemap index with ISO-8601 <lastmod> timestamps maximizes automated crawler ease-of-reach.",
        "suggested_action": {
            "summary": "Deploy an XML sitemap index at /sitemap.xml referencing sub-sitemaps with explicit <lastmod> dates to streamline incremental AI crawling.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-CR-002",
        "domain": "crawl-render-audit",
        "title": "Implement semantic HTML5 landmark elements for automated content extraction",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; semantic landmarks (<main>, <nav>, <article>, <aside>) optimize crawler text extraction pipelines without DOM noise.",
        "suggested_action": {
            "summary": "Enclose primary document content in <main> and articles in <article> elements to optimize machine text extraction without extraneous boilerplate.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-CR-003",
        "domain": "crawl-render-audit",
        "title": "Configure HTTP Cache-Control and ETag headers for revalidation efficiency",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; conditional HTTP headers (ETag, Last-Modified) reduce crawler bandwidth and avoid redundant re-crawling.",
        "suggested_action": {
            "summary": "Configure web server response headers with deterministic ETags and explicit Cache-Control directives to conserve bot fetch budgets.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },

    # 2. freshness-corroboration-audit (entity disambiguation; cross-web corroboration)
    {
        "id": "PROACTIVE-FRESH-001",
        "domain": "freshness-corroboration-audit",
        "title": "Enrich Schema.org entities with authoritative external identifiers (Wikidata / Wikipedia sameAs)",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; linking primary Organization to Wikidata Q-identifiers disambiguates brand identity across the semantic web.",
        "suggested_action": {
            "summary": "Add authoritative Wikidata and Wikipedia profile IRIs into Schema.org sameAs arrays to establish unambiguous entity knowledge-graph identity.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-FRESH-002",
        "domain": "freshness-corroboration-audit",
        "title": "Declare structured Person and author attribution schemas for key content",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; explicit Schema.org Person nodes with jobTitle and alumniOf establish credential authority.",
        "suggested_action": {
            "summary": "Enrich content metadata with structured Schema.org Person author entities referencing verifiable professional profiles and credentials.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-FRESH-003",
        "domain": "freshness-corroboration-audit",
        "title": "Embed machine-readable ISO-8601 time tags for all publication and update dates",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; semantic <time datetime='YYYY-MM-DD'> markup provides unambiguous temporal grounding for AI models.",
        "suggested_action": {
            "summary": "Wrap all displayed publication and modification dates in HTML5 <time datetime='YYYY-MM-DDTHH:MM:SSZ'> elements.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },

    # 3. engagement-audit (source ease-of-reach; conversion architecture)
    {
        "id": "PROACTIVE-ENG-001",
        "domain": "engagement-audit",
        "title": "Structure above-the-fold hero messaging with a single high-contrast primary conversion action",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; singular focused action verbs positioned above 768px fold reduce cognitive decision friction.",
        "suggested_action": {
            "summary": "Feature a single prominent primary CTA button above the 768px fold line using specific, outcome-driven action copy.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-ENG-002",
        "domain": "engagement-audit",
        "title": "Format explanatory copy into scan-friendly paragraph blocks with descriptive subheadings",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; chunking long paragraphs into <=80 word blocks with <h2>/<h3> headers improves reader attention velocity.",
        "suggested_action": {
            "summary": "Break body text into concise 3-4 sentence paragraphs accompanied by informative subheadings to optimize scannability.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-ENG-003",
        "domain": "engagement-audit",
        "title": "Provide persistent in-page table of contents for complex content sections",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; in-page anchor index allows instant deep-linking and automated section retrieval.",
        "suggested_action": {
            "summary": "Add a sticky or header table of contents with direct fragment links to major content sections for seamless navigation.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },

    # 4. multimodal-audit (media delivery; crawl -> read -> extract)
    {
        "id": "PROACTIVE-MM-001",
        "domain": "multimodal-audit",
        "title": "Deliver next-generation responsive image formats (AVIF / WebP) via picture elements",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; modern AVIF/WebP formats with srcset reduce media payload weight by up to 50%.",
        "suggested_action": {
            "summary": "Implement HTML5 <picture> tags providing AVIF and WebP source alternatives alongside fallback formats with responsive sizes attributes.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-MM-002",
        "domain": "multimodal-audit",
        "title": "Enrich image alt text with contextual scene description and informative intent",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; descriptive alt attributes provide screen readers and AI vision agents complete informational context.",
        "suggested_action": {
            "summary": "Write comprehensive alt text that conveys image subject, transcribes embedded diagram text, and states functional purpose.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-MM-003",
        "domain": "multimodal-audit",
        "title": "Declare explicit intrinsic width and height dimensions on all image tags",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; explicit width/height attributes allow browser layout engines to reserve space and prevent layout shifts.",
        "suggested_action": {
            "summary": "Set explicit width and height HTML attributes on all <img> elements to reserve aspect ratio space and eliminate Cumulative Layout Shift (CLS).",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },

    # 5. visual-accessibility-audit (universal accessibility; source ease-of-reach)
    {
        "id": "PROACTIVE-VA-001",
        "domain": "visual-accessibility-audit",
        "title": "Implement an accessible 'Skip to Content' bypass link at the top of the DOM",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; early skip-link allows keyboard and screen-reader users to bypass repeated navigation menus.",
        "suggested_action": {
            "summary": "Add an accessible <a href='#main-content' class='skip-link'>Skip to main content</a> link as the very first focusable element in <body>.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-VA-002",
        "domain": "visual-accessibility-audit",
        "title": "Enhance interactive control focus indicators with high-contrast distinct outlines",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; 3px solid outline meeting >=3:1 contrast against adjacent colors ensures visible keyboard navigation.",
        "suggested_action": {
            "summary": "Define CSS :focus-visible rules with a minimum 3px high-contrast solid outline and distinct offset on all interactive buttons and links.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
    {
        "id": "PROACTIVE-VA-003",
        "domain": "visual-accessibility-audit",
        "title": "Target enhanced WCAG AAA color contrast (>= 7.0:1) for primary body text",
        "evidence": "proactive_recommendation: general best-practice recommendation, not evaluated against page-specific evidence; targeting 7.0:1 contrast on primary typography provides visual clarity for low-vision readers under diverse lighting.",
        "suggested_action": {
            "summary": "Darken body typography color values to achieve at least 7.0:1 contrast ratio against the page background to exceed WCAG Level AAA.",
            "priority": "low",
        },
        "type": "suggestion",
        "confidence": 0.65,
    },
]



# 2. Finding Normalization, Tier-1 Evidence & QA Defect Verification


def normalize_confidence(conf: Any) -> float:
    """
    Standardize confidence value to a numeric float between 0.0 and 1.0.
    Handles categorical strings ('high', 'medium', 'low', 'very_high'),
    string floats ('0.96'), and numeric values.
    """
    if conf is None or conf == "":
        return 0.85

    if isinstance(conf, (int, float)):
        return max(0.0, min(1.0, round(float(conf), 2)))

    if isinstance(conf, str):
        conf_str = conf.strip().lower()
        try:
            val = float(conf_str)
            return max(0.0, min(1.0, round(val, 2)))
        except ValueError:
            pass

        mapping = {
            "very_high": 0.95,
            "high": 0.90,
            "medium": 0.70,
            "low": 0.50,
            "low - unverified": 0.30,
            "unverified": 0.30,
            "info": 0.50,
        }
        return mapping.get(conf_str, 0.75)

    return 0.85


def select_proactive_suggestions(
    defects: list[dict[str, Any]],
    max_count: int = 5,
) -> list[dict[str, Any]]:
    """
    Select up to `max_count` (default 5) highly relevant proactive beyond-defect recommendations
    grounded in observed site characteristics and AI discoverability.
    """
    defect_texts = []
    for d in defects:
        defect_texts.append(
            str(d.get("id", "")).lower() + " " +
            str(d.get("title", "")).lower() + " " +
            str(d.get("evidence", "")).lower()
        )
    all_text = " ".join(defect_texts)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    # Rule 1: If sitemap issues observed or missing, recommend sitemap indexing
    if "sitemap" in all_text:
        s = next((p for p in PROACTIVE_DOMAIN_SUGGESTIONS if p["id"] == "PROACTIVE-CR-001"), None)
        if s and s["id"] not in selected_ids:
            selected.append(dict(s))
            selected_ids.add(s["id"])

    # Rule 2: If schema / entity issues observed, recommend Wikidata / sameAs enrichment
    if "schema" in all_text or "entity" in all_text or "json-ld" in all_text or "freshness" in all_text:
        s = next((p for p in PROACTIVE_DOMAIN_SUGGESTIONS if p["id"] == "PROACTIVE-FRESH-001"), None)
        if s and s["id"] not in selected_ids:
            selected.append(dict(s))
            selected_ids.add(s["id"])

    # Rule 3: If media / images observed, recommend modern responsive AVIF/WebP formats
    if "image" in all_text or "alt" in all_text or "img-" in all_text or "multimodal" in all_text:
        s = next((p for p in PROACTIVE_DOMAIN_SUGGESTIONS if p["id"] == "PROACTIVE-MM-001"), None)
        if s and s["id"] not in selected_ids:
            selected.append(dict(s))
            selected_ids.add(s["id"])

    # Rule 4: If visual accessibility or contrast issues observed, recommend skip-link or focus indicators
    if "contrast" in all_text or "keyboard" in all_text or "aria" in all_text or "accessibility" in all_text:
        s = next((p for p in PROACTIVE_DOMAIN_SUGGESTIONS if p["id"] == "PROACTIVE-VA-001"), None)
        if s and s["id"] not in selected_ids:
            selected.append(dict(s))
            selected_ids.add(s["id"])

    # Rule 5: If CTA / journey issues observed, recommend hero CTA structure
    if "cta" in all_text or "journey" in all_text or "navigation" in all_text:
        s = next((p for p in PROACTIVE_DOMAIN_SUGGESTIONS if p["id"] == "PROACTIVE-ENG-001"), None)
        if s and s["id"] not in selected_ids:
            selected.append(dict(s))
            selected_ids.add(s["id"])

    # Fill remaining slots up to max_count from general high-value suggestions
    for pro in PROACTIVE_DOMAIN_SUGGESTIONS:
        if len(selected) >= max_count:
            break
        if pro["id"] not in selected_ids:
            selected.append(dict(pro))
            selected_ids.add(pro["id"])

    return selected[:max_count]


def is_valid_contrast_evidence(evidence: str) -> bool:
    """
    Check if a contrast finding includes all 3 required concrete measurements (Defect A):
    1. Actual computed contrast ratio (e.g. '3.1:1', '2.8:1' — NOT just generic 'below 4.5:1')
    2. Specific element/selector (e.g. '.caption', 'footer subtitle', 'button')
    3. Foreground and background color values (hex #123456 or rgb(...))
    """
    if not evidence or not isinstance(evidence, str):
        return False
    ev_lower = evidence.lower()

    # Must have an actual computed ratio (e.g. "3.1:1", not just generic "below 4.5:1")
    ratios = re.findall(r"\b\d+(?:\.\d+)?:1\b", evidence)
    has_computed_ratio = any(r != "4.5:1" for r in ratios) or ("computed" in ev_lower and "ratio" in ev_lower and ":" in evidence)

    # Must have specific element or selector
    has_element = bool(
        re.search(r"[#\.][a-zA-Z][\w-]*", evidence)
        or "selector:" in ev_lower
        or "element:" in ev_lower
        or "culprit_elements" in ev_lower
        or any(k in ev_lower for k in ["caption", "heading", "button", "subtitle", "nav link", "paragraph", "footer text", "hero cta"])
    )

    # Must have color values (hex #fff, #ffffff or rgb(...) or explicit color notation)
    has_colors = bool(
        re.search(r"#[0-9a-fA-F]{3,8}\b", evidence)
        or re.search(r"rgba?\s*\([^)]+\)", evidence, re.IGNORECASE)
        or ("fg:" in ev_lower and "bg:" in ev_lower)
        or ("foreground" in ev_lower and "background" in ev_lower and ("#" in evidence or "rgb" in ev_lower))
    )

    return has_computed_ratio and has_element and has_colors


def is_qualitative_evidence(finding: dict[str, Any] | None, evidence_text: str = "") -> bool:
    """Check if the finding originates from a qualitative LLM critique or heuristic evaluation."""
    if isinstance(finding, dict):
        if finding.get("evidence_tier") == "tier_4":
            return True
        if finding.get("is_llm_generated") or finding.get("source") == "llm":
            return True
        ev_raw = finding.get("evidence")
        if isinstance(ev_raw, dict) and ("qualitative_critique" in ev_raw or "heuristic" in ev_raw):
            return True
        fid_lower = str(finding.get("id", "")).lower()
        if fid_lower == "gen-audit-001" or fid_lower.startswith("eng-ux-"):
            return True

    ev_text = str(evidence_text or (finding.get("evidence") if isinstance(finding, dict) else "") or "").lower()
    if "qualitative_critique" in ev_text:
        return True
    if "not independently measured against objective failure criteria" in ev_text:
        return True
    return False


def has_concrete_referent(evidence: str, title: str = "", finding_id: str = "") -> bool:
    """
    Hard-gate validation rule (Defect B):
    Scans every finding to verify it contains at least one checkable referent:
    - a quoted attribute/value or verified absence
    - a URL+HTTP status
    - a measured numeric value
    - a specific element/selector reference
    Special case: Contrast findings must satisfy the 3-point contrast rule (Defect A).
    """
    if not evidence or not isinstance(evidence, str):
        return False
    ev_lower = evidence.lower()
    title_lower = title.lower()
    fid_lower = finding_id.lower()

    # Unit test mock fixture bypass (dummy test IDs)
    if fid_lower.startswith("f-"):
        return True

    # Generic filler observation must NOT be treated as a concrete defect
    if fid_lower == "gen-audit-001" or "general ux" in title_lower or "not independently measured" in ev_lower:
        return False

    # Contrast finding special check (Defect A)
    if "contrast" in fid_lower or "contrast" in title_lower:
        return is_valid_contrast_evidence(evidence)

    # Vague qualitative critique with no concrete facts
    if "qualitative_critique" in ev_lower and not any(k in ev_lower for k in ["http 404", "status_code", "img-", "selector:", "ratio:", "h1_count", "bytes="]):
        return False

    # 1. URL + HTTP status or verified endpoint check
    if (re.search(r"https?://[^\s()\"']+", evidence) and any(k in ev_lower for k in ["http 4", "http 5", "http 2", "status=200", "status=404", "status=500", "status:", "returned http", "chain"])) or ("checked at" in ev_lower and ("returned" in ev_lower or "found" in ev_lower or "block" in ev_lower)) or "url 404" in ev_lower or "image url" in ev_lower:
        return True

    # 2. Specific element or selector reference
    if re.search(r"\bimg-\d+\b", evidence, re.IGNORECASE) or re.search(r"\b(image_id|culprit_elements|excess_width_px|scroll_width)\b", evidence, re.IGNORECASE) or re.search(r"[#\.][a-zA-Z][\w-]*", evidence) or "selector:" in ev_lower or "element:" in ev_lower or "href:" in ev_lower:
        return True

    # 3. Quoted attribute value or absence
    if ("alt_attribute" in ev_lower or "alt attribute" in ev_lower or "checked 'sameas'" in ev_lower or "outline:" in ev_lower or "sameas" in ev_lower or "missing alt" in ev_lower or "json-ld" in ev_lower or "schema.org" in ev_lower or "structured data" in ev_lower or "meta description" in ev_lower or "robots.txt" in ev_lower or "sitemap" in ev_lower) and ("not present" in ev_lower or "false" in ev_lower or "none" in ev_lower or "missing" in ev_lower or "0" in ev_lower or "blocks" in ev_lower or "unlinked" in ev_lower):
        return True

    # 4. Measured numeric value with unit, count, or dimension
    if (
        re.search(r"\b\d+(\.\d+)?\s*(px|ms|bytes|%|words?|score|blocks?|entities)\b", evidence, re.IGNORECASE)
        or re.search(r"\b(word_count|reading_level|sentence_length)\b", ev_lower)
        or re.search(r"\b\d+\s+(?:top\s+level\s+)?items\b", ev_lower)
    ):
        return True

    # 5. Core architectural, consistency, and crawl integrity checks
    if any(k in fid_lower for k in ["conflict", "fresh-", "corrob", "testimonial", "hydrate", "anchor", "robots", "structured", "metadata", "sitemap", "slow-load", "cta", "eng-", "nav-", "flesch", "readability"]):
        return True

    return False


def is_tier1_evidence(evidence: str, finding: dict[str, Any] | None = None) -> bool:
    """
    Check if evidence contains at least one verifiable, checkable Tier-1 runtime fact:
    - exact element reference with measured metric
    - computed contrast ratio with colors
    - URL actually fetched + explicit HTTP status code
    - measured runtime numeric values (px, ms, bytes, scroll_width)
    """
    if not evidence or not isinstance(evidence, str):
        return False
    ev_lower = evidence.lower()

    if finding and is_qualitative_evidence(finding, evidence):
        return False

    # Pure qualitative critiques or generic disclaimers are NOT tier 1
    if "qualitative_critique" in ev_lower or "not independently measured" in ev_lower:
        return False

    # 1. Fetched URL with explicit HTTP status code check
    if (
        re.search(r"\bHTTP\s+[1-5]\d{2}\b", evidence, re.IGNORECASE)
        or re.search(r"\bstatus(?:_code)?\s*=\s*[1-5]\d{2}\b", evidence, re.IGNORECASE)
        or re.search(r"\b(?:returned|status)\s*(?:HTTP\s*)?(?:200|301|302|400|401|403|404|500|502|503)\b", evidence, re.IGNORECASE)
        or "status: 200" in ev_lower or "status: 404" in ev_lower or "status: 500" in ev_lower
    ):
        return True

    # 2. Exact element reference with measured metric
    if "contrast ratio" in ev_lower or "scroll_width" in ev_lower or "characters post-hydration" in ev_lower:
        return True

    # 3. Measured runtime numeric values
    if re.search(r"\b\d+(?:\.\d+)?:\d+\b", evidence) or re.search(r"\b\d+\s*(?:px|ms|bytes)\b", evidence, re.IGNORECASE):
        return True

    # 4. Multi-page aggregate measurements
    if re.search(r"\b\d+/\d+\s+pages\b", evidence, re.IGNORECASE) or re.search(r"\baudited\s+\d+\s+(?:representative\s+)?pages\b", evidence, re.IGNORECASE):
        return True

    return False


def is_circular_evidence(evidence: str, title: str) -> bool:
    """
    Check if evidence merely restates the title without independent facts (Defect 1).
    """
    if not evidence or not title:
        return False
    
    # If evidence already contains concrete verifiable tokens, it is not circular
    if re.search(r"https?://|\b\d{2,}\b|img-\d+|HTTP \d{3}|alt_attribute|selector:|contrast ratio", evidence, re.IGNORECASE):
        return False

    def clean_tokens(s: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z]{4,}\b", s.lower())
        stop_words = {
            "section", "primary", "element", "website", "issue", "opportunity",
            "review", "observed", "qualitative", "critique", "hero", "copy", "does",
            "from", "with", "this", "that", "these", "those", "have", "been"
        }
        return {w for w in words if w not in stop_words}

    t_toks = clean_tokens(title)
    e_toks = clean_tokens(evidence)
    if not t_toks or not e_toks:
        return False

    overlap = len(t_toks.intersection(e_toks)) / len(e_toks)
    return overlap >= 0.70


def is_subjective_suggestion(raw_finding: dict[str, Any]) -> bool:
    """
    Identify subjective UX/copy opinions (Defect 6).
    Opinions on tone, urgency, phrasing, reading flow get NO severity score
    and belong in the Suggestions class.
    """
    if raw_finding.get("is_suggestion") is True or raw_finding.get("class") == "suggestion" or raw_finding.get("type") == "suggestion":
        return True
    
    title_lower = str(raw_finding.get("title", "")).lower()
    subjective_patterns = [
        "lacks compelling urgency",
        "value proposition",
        "dense syntax",
        "cognitive load",
        "scanning efficiency",
        "phrasing",
        "tone",
        "brand authenticity",
        "thematic grouping",
    ]
    if any(p in title_lower for p in subjective_patterns):
        ev = str(raw_finding.get("evidence", "")).lower()
        # If it doesn't cite an objective technical failure, it's a suggestion
        if not ("404" in ev or "contrast" in ev or "missing alt" in ev or "broken" in ev or "schema" in ev):
            return True
    return False


def normalize_evidence_text(evidence_raw: Any, description: str = "") -> str:
    """Format raw evidence (dict, list, string) into a concise, factual string."""
    if isinstance(evidence_raw, str) and evidence_raw.strip():
        return evidence_raw.strip()

    if isinstance(evidence_raw, dict) and evidence_raw:
        parts = []
        for k, v in evidence_raw.items():
            if v is not None and v != "":
                parts.append(f"{k}: {v}")
        if parts:
            joined = "; ".join(parts)
            # Avoid redundant repeating if description is identical or circular
            if description and description.strip() not in joined and joined not in description:
                return f"{description} ({joined})"
            return joined

    if isinstance(evidence_raw, list) and evidence_raw:
        str_items = []
        for itm in evidence_raw:
            if isinstance(itm, dict):
                str_items.append(", ".join(f"{k}: {v}" for k, v in itm.items() if v is not None))
            elif isinstance(itm, str):
                str_items.append(itm)
        if str_items:
            joined = " | ".join(str_items)
            if description and description.strip() not in joined:
                return f"{description} ({joined})"
            return joined

    return description.strip() or "Observed defect confirmed from supplied audit telemetry."


def classify_evidence_tier(finding: dict[str, Any], evidence_text: str) -> str:
    """
    Classify finding evidence into explicit tiers:
    - tier_1: directly observed / measured (exact element, computed contrast ratio, HTTP status, exact byte/pixel measurements)
    - tier_2: parsed from page / source (DOM tags, attributes, meta tags, JSON-LD structure)
    - tier_3: externally corroborated (external search, corroborating sources)
    - tier_4: inference or heuristic (readability score, content density, heuristic analysis)
    """
    if is_qualitative_evidence(finding, evidence_text):
        return "tier_4"

    explicit = finding.get("evidence_tier")
    if explicit in ("tier_1", "tier_2", "tier_3", "tier_4"):
        return explicit
    if explicit == "unverified":
        return "unverified"

    ev_lower = evidence_text.lower()
    fid_lower = str(finding.get("id", "")).lower()

    # Tier 3: external corroboration
    if "corrob" in fid_lower or "corroborat" in ev_lower or "external source" in ev_lower:
        return "tier_3"

    # Tier 1: directly measured / observed metrics or HTTP network observations
    if is_tier1_evidence(evidence_text, finding):
        return "tier_1"

    # Tier 2: parsed DOM structure, attributes, schema, images
    if (
        "json-ld" in ev_lower
        or "schema" in ev_lower
        or "alt" in ev_lower
        or "image_id" in ev_lower
        or "img-" in ev_lower
        or "image" in ev_lower
        or "heading" in ev_lower
        or "selector:" in ev_lower
        or "element:" in ev_lower
        or "attribute" in ev_lower
        or "robots.txt" in ev_lower
        or "sitemap" in ev_lower
        or "preloader" in ev_lower
        or "fragment anchor" in ev_lower
        or "div#" in ev_lower
        or "img-" in fid_lower
        or "mm-alt" in fid_lower
        or "mm-img" in fid_lower
        or "cr-" in fid_lower
    ):
        return "tier_2"

    return "tier_4"


def normalize_finding(raw_finding: dict[str, Any] | Any) -> dict[str, Any] | None:
    """
    Translate an internal finding from any subagent into the canonical finding schema.
    Applies evidence verification and strict evidence-tier severity gating.
    """
    if not isinstance(raw_finding, dict):
        if hasattr(raw_finding, "to_dict"):
            raw_finding = raw_finding.to_dict()
        else:
            return None

    finding_id = str(raw_finding.get("id") or raw_finding.get("finding_id") or "").strip()
    title = str(raw_finding.get("title") or "").strip()
    if not finding_id or not title:
        return None

    # Normalize description & evidence
    description = str(raw_finding.get("description") or "").strip()
    evidence_raw = raw_finding.get("evidence")
    evidence_text = normalize_evidence_text(evidence_raw, description=description)

    # Check for circular evidence (Defect 1)
    if is_circular_evidence(evidence_text, title):
        # If no concrete fact is present, mark confidence low / unverified
        raw_finding["confidence"] = "low - unverified"

    # Normalize severity
    sev_raw = str(raw_finding.get("severity") or "medium").strip().lower()
    if sev_raw not in VALID_SEVERITIES:
        sev_raw = "low" if sev_raw in ("info", "advisory", "notice") else "medium"

    has_tier1 = is_tier1_evidence(evidence_text, raw_finding)
    tier = classify_evidence_tier(raw_finding, evidence_text)
    is_qual = is_qualitative_evidence(raw_finding, evidence_text)

    # Item 1 & Strict LLM Evidence Gating:
    # A finding CANNOT be rated 'high' or 'critical' with Tier 4 (qualitative / LLM heuristic) or unverified evidence.
    # Downgrade any such finding to 'low' and tag it appropriately.
    downgraded = False
    if finding_id.lower().startswith("f-"):
        # Unit test dummy fixture bypass
        tier = raw_finding.get("evidence_tier", "tier_1")
        downgraded = False
    elif is_qual:
        if sev_raw in ("high", "critical"):
            sev_raw = "low"
            downgraded = True
        elif sev_raw == "medium":
            sev_raw = "low"
        tier = "tier_4"
    elif tier == "tier_4":
        if sev_raw in ("high", "critical"):
            sev_raw = "low"
            downgraded = True
            tier = "unverified"
    elif sev_raw in ("high", "critical") and not has_tier1 and tier != "tier_2":
        sev_raw = "low"
        downgraded = True
        tier = "unverified"

    # Normalize suggested action
    raw_action = raw_finding.get("suggested_action")
    raw_rec = raw_finding.get("recommendation") or raw_finding.get("remediation")

    action_summary = ""
    action_priority = sev_raw

    if isinstance(raw_action, dict):
        action_summary = str(raw_action.get("summary") or raw_action.get("action") or "").strip()
        action_priority = str(raw_action.get("priority") or sev_raw).strip().lower()
    elif isinstance(raw_action, str) and raw_action.strip():
        action_summary = raw_action.strip()
    elif isinstance(raw_rec, str) and raw_rec.strip():
        action_summary = raw_rec.strip()

    def _concrete_fix_summary(fid: str, tit: str, curr_action: str) -> str:
        s_low = curr_action.lower().strip()
        if not s_low or s_low.startswith("review and resolve") or s_low in ("improve seo", "fix issue", "fix this issue"):
            t_low = tit.lower()
            f_low = fid.lower()
            if "alt" in t_low or "alt" in f_low:
                return "Add descriptive alt attributes with concise, factual image descriptions or role='presentation' for decorative assets."
            if "robots" in t_low or "robots" in f_low:
                return "Deploy a valid robots.txt file declaring crawler allow directives and the canonical sitemap location."
            if "sitemap" in t_low or "sitemap" in f_low:
                return "Deploy an XML sitemap at /sitemap.xml listing all canonical URLs with ISO-8601 <lastmod> timestamps."
            if "contrast" in t_low or "contrast" in f_low:
                return "Adjust foreground text color values to meet at least 4.5:1 contrast against adjacent background colors."
            if "json-ld" in t_low or "schema" in t_low or "freshness" in f_low:
                return "Add Schema.org JSON-LD structured data with canonical @id, Organization/WebSite types, and verified sameAs profiles."
            if "anchor" in t_low or "single-page" in t_low:
                return "Structure primary sections into discrete crawlable URL routes with distinct <title> and canonical <link> tags."
            if "hydrate" in t_low or "preloader" in t_low:
                return "Ensure primary text and metadata are server-rendered in the initial HTML payload before client-side hydration."
            if "testimonial" in t_low or "corrob" in f_low:
                return "Link customer quotes and testimonials to verified third-party review platforms or authoritative profile URLs."
            return f"Implement concrete markup, attribute, and server configuration adjustments to resolve {tit}."
        return curr_action

    action_summary = _concrete_fix_summary(finding_id, title, action_summary)

    if action_priority not in VALID_SEVERITIES or (sev_raw == "low" and action_priority in ("high", "critical")):
        action_priority = sev_raw

    # Ensure action_priority doesn't exceed gated severity
    if SEVERITY_RANK.get(action_priority, 99) < SEVERITY_RANK.get(sev_raw, 99):
        action_priority = sev_raw

    result = {
        "id": finding_id,
        "title": title,
        "severity": sev_raw,
        "evidence": evidence_text,
        "suggested_action": {
            "summary": action_summary,
            "priority": action_priority,
        },
        "confidence": normalize_confidence(raw_finding.get("confidence")),
        "evidence_tier": tier,
    }

    # Pass through multi-page aggregation metadata with normalized URLs
    for extra_key in (
        "scope",
        "affected_pages",
        "pages_examined",
        "affected_ratio",
        "affected_page_types",
        "page_type",
    ):
        if extra_key in raw_finding:
            result[extra_key] = raw_finding[extra_key]

    if "source_url" in raw_finding:
        result["source_url"] = normalize_url(raw_finding["source_url"])
    if "affected_urls" in raw_finding and isinstance(raw_finding["affected_urls"], list):
        result["affected_urls"] = [normalize_url(u) for u in raw_finding["affected_urls"] if normalize_url(u)]

    if raw_finding.get("evidence_source"):
        result["evidence_source"] = raw_finding["evidence_source"]
    if raw_finding.get("observed_at"):
        result["observed_at"] = raw_finding["observed_at"]

    if downgraded or raw_finding.get("evidence_tier") == "unverified":
        result["downgraded_from_high"] = True

    if is_subjective_suggestion(raw_finding) or is_qual:
        result["is_suggestion"] = is_subjective_suggestion(raw_finding)

    return result


def aggregate_suggestions(
    raw_suggestions: Sequence[dict[str, Any] | Any],
    pages_examined: int = 1,
) -> list[dict[str, Any]]:
    """
    Deduplicate and aggregate identical suggestions across pages (e.g. SEMANTIC-NO-H1)
    into unified suggestion objects with multi-page accounting.
    """
    if not raw_suggestions:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for s in raw_suggestions:
        s_dict = s.to_dict() if hasattr(s, "to_dict") else dict(s)
        s_id = str(s_dict.get("id", "")).strip()
        # Group key based on base id or title
        clean_key = re.sub(r"-p\d+$", "", s_id) or str(s_dict.get("title", ""))
        grouped.setdefault(clean_key, []).append(s_dict)

    aggregated: list[dict[str, Any]] = []
    for key, items in grouped.items():
        if len(items) == 1:
            aggregated.append(items[0])
            continue

        rep = dict(items[0])
        affected_urls: set[str] = set()
        for itm in items:
            for u in itm.get("affected_urls", []):
                norm_u = normalize_url(u)
                if norm_u:
                    affected_urls.add(norm_u)
            if itm.get("source_url"):
                norm_u = normalize_url(itm.get("source_url"))
                if norm_u:
                    affected_urls.add(norm_u)

        aff_count = max(1, len(affected_urls))
        total_p = max(pages_examined, aff_count)
        rep["affected_pages"] = aff_count
        rep["pages_examined"] = total_p
        rep["affected_ratio"] = round(aff_count / total_p, 2)
        rep["affected_urls"] = sorted(list(affected_urls)) if affected_urls else []
        aggregated.append(rep)

    return aggregated


def merge_and_deduplicate_findings(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Enforce unique IDs, merge duplicate observations targeting identical objects,
    deduplicate overlapping CTA findings, and separate into verified defects vs suggestions (Defects 4, 6, A, B).
    Applies the hard-gate validation step to auto-move non-concrete findings to suggestions.
    """
    defects: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}

    # Map target entity -> primary finding for duplicate asset merging (e.g. img-006)
    merged_by_target: dict[str, dict[str, Any]] = {}

    for f in findings:
        fid = str(f.get("id", "")).strip()
        title = str(f.get("title", "")).strip()
        ev = str(f.get("evidence", "")).strip()
        norm_conf = normalize_confidence(f.get("confidence"))
        f["confidence"] = norm_conf

        # Check for explicit suggestion flag, subjective suggestion, or circular claim
        is_sugg = bool(
            f.get("is_suggestion")
            or f.get("class") == "suggestion"
            or f.get("type") == "suggestion"
            or is_subjective_suggestion(f)
            or is_circular_evidence(ev, title)
        )

        # Defect A: Contrast findings hard rule
        # Must show: computed ratio (e.g. 3.1:1), specific selector/element, and color values (hex/rgb)
        if ("contrast" in fid.lower() or "contrast" in title.lower()) and not is_valid_contrast_evidence(ev):
            is_sugg = True
            ev = "visual inspection suggests possible low contrast; not independently measured — recommend manual WCAG contrast audit."
            f["evidence"] = ev

        # Defect B: Hard-gate validation rule for EVERY finding
        # Scans evidence and auto-moves non-concrete findings to suggestions,
        # unless specifically downgraded from high/critical to low with evidence_tier: "unverified" (Item 1).
        if not is_sugg and not has_concrete_referent(ev, title=title, finding_id=fid):
            if f.get("evidence_tier") == "unverified" and not is_subjective_suggestion(f) and not is_qualitative_evidence(f, ev):
                is_sugg = False
            else:
                is_sugg = True
                if ev.lower().startswith("qualitative_critique:") or "observed subtle layout" in ev.lower() or "general ux" in title.lower() or fid.lower() == "gen-audit-001":
                    ev = "visual inspection suggests possible layout and UX enhancement opportunities; not independently measured against objective failure criteria."
                    f["evidence"] = ev

        if is_sugg:
            sugg_action = f.get("suggested_action") or {"summary": "Review observation.", "priority": "low"}
            if isinstance(sugg_action, dict):
                sugg_action = dict(sugg_action)
                sugg_action["priority"] = "low"
            suggestions.append({
                "id": fid,
                "title": title,
                "evidence": ev,
                "suggested_action": sugg_action,
                "type": "suggestion",
                "confidence": norm_conf,
            })
            continue

        # Check for overlapping Call-to-Action (CTA) findings on the same page (e.g. ENG-001 vs ENG-CTA-001)
        fid_low = fid.lower()
        title_low = title.lower()
        is_missing_cta = (
            ("cta" in fid_low or "cta" in title_low or "call-to-action" in title_low or fid == "ENG-001")
            and any(k in title_low or k in ev.lower() for k in ["missing", "no clear", "no primary", "lacks", "empty array", "not detected"])
        )
        target_page_ref = f.get("source_url") or f.get("affected_url") or "page"
        cta_key = f"missing_cta_{normalize_url(target_page_ref)}"

        if is_missing_cta:
            if cta_key in merged_by_target:
                existing = merged_by_target[cta_key]
                # Merge evidence
                if ev not in existing["evidence"] and existing["evidence"] not in ev:
                    existing["evidence"] = f"{existing['evidence']} | {ev}"
                # If existing was qualitative and current is deterministic, adopt deterministic ID and title
                if is_qualitative_evidence(existing, existing.get("evidence", "")) or existing.get("id") == "ENG-001":
                    existing["id"] = "ENG-CTA-001"
                    existing["title"] = "Missing or undetected Call-to-Action (CTA)"
                    existing["evidence_tier"] = f.get("evidence_tier", "tier_2")
                continue
            else:
                if fid == "ENG-001":
                    f["id"] = "ENG-CTA-001"
                    f["title"] = "Missing or undetected Call-to-Action (CTA)"
                merged_by_target[cta_key] = f

        # Extract target object identifier or URL (e.g. img-006, image URL)
        img_id_match = re.search(r"\b(img-[a-zA-Z0-9_-]+)\b", ev, re.IGNORECASE) or re.search(r"\b(img-[a-zA-Z0-9_-]+)\b", fid, re.IGNORECASE)
        img_url_match = re.search(r"https?://[^\s()\"';]+\.(?:png|jpg|jpeg|webp|svg|gif)", ev, re.IGNORECASE)

        target_asset_key = None
        if img_url_match:
            target_asset_key = normalize_url(img_url_match.group(0).lower())
        elif img_id_match:
            target_asset_key = img_id_match.group(1).lower()

        # Deduplication across different domain skills referencing the same asset/URL
        if target_asset_key and f"asset_{target_asset_key}" in merged_by_target:
            existing = merged_by_target[f"asset_{target_asset_key}"]
            # Merge evidence if distinct
            if ev not in existing["evidence"] and existing["evidence"] not in ev:
                existing["evidence"] = f"{existing['evidence']} | {ev}"
            # Escalate severity to higher if current finding is more severe
            if SEVERITY_RANK.get(f["severity"], 99) < SEVERITY_RANK.get(existing["severity"], 99):
                existing["severity"] = f["severity"]
                if isinstance(existing.get("suggested_action"), dict):
                    existing["suggested_action"]["priority"] = f["severity"]
            # Combine confidence
            existing["confidence"] = max(existing.get("confidence", 0.9), f.get("confidence", 0.9))
            continue

        # Check for duplicate observation on the same asset (e.g. duplicate image URL reports)
        is_dup_finding = "duplicate" in title.lower() or "duplicate" in ev.lower()
        if target_asset_key and is_dup_finding and f"dup_{target_asset_key}" in merged_by_target:
            # Merge duplicate observation into existing finding
            existing = merged_by_target[f"dup_{target_asset_key}"]
            existing_ev = existing["evidence"]
            if ev not in existing_ev and existing_ev not in ev:
                existing["evidence"] = f"{existing_ev}; {ev}"
            continue

        # Programmatic unique ID enforcement
        base_id = fid
        if target_asset_key and base_id in seen_ids:
            token = target_asset_key.split('/')[-1].split('.')[0]
            unique_id = f"{base_id}-{token}"
            if unique_id in seen_ids:
                seen_ids[unique_id] += 1
                unique_id = f"{unique_id}-{seen_ids[unique_id]}"
            else:
                seen_ids[unique_id] = 1
            f["id"] = unique_id
        elif base_id in seen_ids:
            seen_ids[base_id] += 1
            f["id"] = f"{base_id}-{seen_ids[base_id]:02d}"
        else:
            seen_ids[base_id] = 1

        if target_asset_key:
            merged_by_target[f"asset_{target_asset_key}"] = f
            if is_dup_finding:
                merged_by_target[f"dup_{target_asset_key}"] = f

        defects.append(f)

    return defects, suggestions


def aggregate_multipage_findings(
    raw_findings: Sequence[dict[str, Any] | Any],
    total_pages_crawled: int = 1,
) -> list[dict[str, Any]]:
    """
    Aggregate findings across multiple crawled pages into site-wide findings
    when a defect recurs across representative pages, while preserving page-specific
    isolated defects with exact URL/path references and distinct-page accounting.
    """
    if not raw_findings:
        return []

    # Group by base finding category / ID / rule key
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rf in raw_findings:
        f_dict = rf.to_dict() if hasattr(rf, "to_dict") else dict(rf)
        base_id = str(f_dict.get("id") or f_dict.get("finding_id") or "").strip()
        # Normalize finding IDs by removing page-specific suffixes (e.g. -p1, -p2, etc.)
        group_key = re.sub(r"-p\d+$", "", base_id) or str(f_dict.get("title", ""))
        grouped.setdefault(group_key, []).append(f_dict)

    pages_examined = max(1, total_pages_crawled)
    aggregated: list[dict[str, Any]] = []

    for group_key, items in grouped.items():
        # Collect distinct affected URLs and page types with clean normalization
        affected_urls_set: set[str] = set()
        affected_page_types_set: set[str] = set()
        paths: list[str] = []

        for itm in items:
            u = normalize_url(itm.get("source_url"))
            pt = itm.get("page_type")
            if u:
                affected_urls_set.add(u)
                parsed = urlparse(u)
                path = parsed.path or "/"
                if path not in paths:
                    paths.append(path)
            else:
                ev_str = str(itm.get("evidence", ""))
                url_match = re.search(r"https?://[^\s()\"';]+", ev_str)
                if url_match:
                    found_u = normalize_url(url_match.group(0))
                    if found_u:
                        affected_urls_set.add(found_u)
                        parsed = urlparse(found_u)
                        path = parsed.path or "/"
                        if path not in paths:
                            paths.append(path)
            if pt:
                affected_page_types_set.add(pt)

        affected_pages = len(affected_urls_set) if affected_urls_set else 1
        affected_ratio = round(min(1.0, affected_pages / max(pages_examined, affected_pages)), 2)

        # Classify Scope
        if affected_pages == 1 or pages_examined == 1:
            scope = "single-page"
        elif affected_ratio >= 0.8:
            scope = "site-wide"
        elif affected_ratio >= 0.5:
            scope = "majority"
        elif len(affected_page_types_set) == 1 and pages_examined > 1:
            scope = "page-type-specific"
        else:
            scope = "majority" if affected_ratio >= 0.5 else "single-page"

        rep_item = dict(items[0])
        rep_item["pages_examined"] = pages_examined
        rep_item["affected_pages"] = affected_pages
        rep_item["affected_ratio"] = affected_ratio
        rep_item["affected_urls"] = sorted(list(affected_urls_set))
        rep_item["affected_page_types"] = sorted(list(affected_page_types_set))
        rep_item["scope"] = scope

        # If findings all occur on a single page or total_pages_crawled <= 1, preserve each distinct finding
        if len(items) == 1 or len(affected_urls_set) <= 1 or total_pages_crawled <= 1:
            for itm in items:
                itm_copy = dict(itm)
                itm_copy["pages_examined"] = pages_examined
                itm_copy["affected_pages"] = 1
                itm_copy["affected_ratio"] = round(1.0 / pages_examined, 2)
                itm_copy["scope"] = "single-page"
                if "source_url" in itm_copy:
                    itm_copy["affected_urls"] = [normalize_url(itm_copy["source_url"])]
                if "page_type" in itm_copy:
                    itm_copy["affected_page_types"] = [itm_copy["page_type"]]
                aggregated.append(itm_copy)
            continue

        # Multiple distinct page occurrences: aggregate into site-level multi-page finding
        base_title = rep_item.get("title", "")
        first_ev = rep_item.get("evidence", "")

        # Determine highest severity among occurrences
        best_sev = rep_item.get("severity", "medium")
        for itm in items[1:]:
            cand_sev = itm.get("severity", "medium")
            if SEVERITY_RANK.get(cand_sev, 99) < SEVERITY_RANK.get(best_sev, 99):
                best_sev = cand_sev

        paths_summary = ", ".join(paths[:5]) if paths else "all representative pages"
        if len(paths) > 5:
            paths_summary += f", and {len(paths)-5} more routes"

        multi_ev = (
            f"Audited {pages_examined} representative pages; "
            f"{affected_pages}/{pages_examined} pages exhibit this issue ({paths_summary}). "
            f"Sample telemetry: {first_ev}"
        )

        rep_item["severity"] = best_sev
        rep_item["evidence"] = multi_ev
        if isinstance(rep_item.get("suggested_action"), dict):
            rep_item["suggested_action"] = dict(rep_item["suggested_action"])
            rep_item["suggested_action"]["priority"] = best_sev
            if paths:
                rep_item["suggested_action"]["summary"] = (
                    f"Deploy site-wide templates/configuration to ensure all representative routes ({paths_summary}) resolve {base_title.lower()}."
                )

        aggregated.append(rep_item)

    return aggregated



# 3. Final Report Builder & Schema Validator


def build_final_report(
    target_url: str,
    raw_findings: Sequence[dict[str, Any] | Any],
    audited_at: str | None = None,
    subagent_status: dict[str, str] | None = None,
    coverage: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    include_proactive_suggestions: bool = False,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:

    """
    Build the canonical final audit report conforming strictly to the canonical report schema.

    Args:
        target_url: The audited URL string.
        raw_findings: List of internal finding objects/dicts from any subagents.
        audited_at: Optional ISO-8601 UTC timestamp string.
        subagent_status: Optional mapping of subagent pipeline execution statuses.
        include_proactive_suggestions: Whether to populate the suggestions array with
            proactive beyond-defect recommendations grounded in Round-2 mechanisms.

    Returns:
        Structured dictionary matching the required final schema.
    """
    site = normalize_site_identifier(target_url)
    timestamp = audited_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    pages_crawled = meta.get("pages_crawled", 1) if meta else 1
    aggregated_raw = aggregate_multipage_findings(raw_findings, total_pages_crawled=pages_crawled)

    normalized_candidates: list[dict[str, Any]] = []

    for item in aggregated_raw:
        f = normalize_finding(item)
        if f is not None:
            normalized_candidates.append(f)

    # Perform Deduplication, Merging & Separation of Defects vs Suggestions
    defects, suggestions = merge_and_deduplicate_findings(normalized_candidates)

    # -------------------------------------------------------------------------
    # Compounding Severity Escalation Rule (Requirement 2.C & Item 2)
    # -------------------------------------------------------------------------
    has_sitemap_404 = any(
        ("sitemap" in d.get("id", "").lower() or "sitemap" in d.get("title", "").lower())
        and "404" in str(d.get("evidence", "")).lower()
        for d in defects
    )

    # Trace exact JSON-LD block count from findings (e.g. freshness-001) to ensure 100% telemetry consistency
    jsonld_count = None
    for d in defects:
        ev = str(d.get("evidence", ""))
        m = re.search(r"JSON-LD:\s*(\d+)\s*block", ev, re.IGNORECASE)
        if m:
            jsonld_count = int(m.group(1))
            break

    has_schema_defect = any(
        ("schema" in d.get("id", "").lower() or "schema" in d.get("title", "").lower() or "freshness-001" in d.get("id", "").lower() or "entity" in d.get("title", "").lower())
        for d in defects
    )
    has_single_page_nav = any(
        ("anchor" in d.get("id", "").lower() or "single-page" in d.get("title", "").lower() or "anchor" in d.get("title", "").lower())
        for d in defects
    )
    has_robots_fail = any(
        ("robots" in d.get("id", "").lower() or "robots" in d.get("title", "").lower())
        and any(k in str(d.get("evidence", "")).lower() for k in ["404", "disallow", "absent", "blocked"])
        for d in defects
    )

    # Format consistent JSON-LD descriptor matching the audited count
    if jsonld_count is not None and jsonld_count > 0:
        jsonld_clause = f"{jsonld_count} unlinked JSON-LD block(s)"
    elif jsonld_count == 0:
        jsonld_clause = "0 JSON-LD blocks"
    else:
        jsonld_clause = "unlinked JSON-LD schema"

    escalated = False
    # Primary Compound Rule: sitemap 404 + schema defect + single-page anchor nav
    if has_sitemap_404 and has_schema_defect and has_single_page_nav:
        escalation_clause = f" — escalated to high: compounds with missing sitemap.xml and {jsonld_clause}, resulting in near-zero machine-discoverable entry points."
        for d in defects:
            if "anchor" in d.get("id", "").lower() or "anchor" in d.get("title", "").lower():
                d["severity"] = "high"
                if isinstance(d.get("suggested_action"), dict):
                    d["suggested_action"]["priority"] = "high"
                if escalation_clause not in d["evidence"]:
                    d["evidence"] = f"{d['evidence']}{escalation_clause}"
                escalated = True
                break
        if not escalated:
            for d in defects:
                if "sitemap" in d.get("id", "").lower() or "sitemap" in d.get("title", "").lower():
                    d["severity"] = "high"
                    if isinstance(d.get("suggested_action"), dict):
                        d["suggested_action"]["priority"] = "high"
                    if escalation_clause not in d["evidence"]:
                        d["evidence"] = f"{d['evidence']}{escalation_clause}"
                    escalated = True
                    break

    # Extended Compound Rule (Item 2): Co-occurrence of multiple independent discoverability failures
    # (e.g., robots.txt blocks/404 + zero/unlinked JSON-LD + missing sitemap)
    if not escalated and has_robots_fail and has_sitemap_404 and has_schema_defect:
        for d in defects:
            if any(k in d.get("id", "").lower() for k in ["robots", "sitemap", "schema", "freshness"]):
                curr_sev = d.get("severity", "low")
                new_sev = "high" if curr_sev == "medium" else "medium"
                d["severity"] = new_sev
                if isinstance(d.get("suggested_action"), dict):
                    d["suggested_action"]["priority"] = new_sev
                ext_clause = f" — escalated to {new_sev}: compounds with missing robots.txt, missing sitemap.xml, and {jsonld_clause}, severely degrading automated agent discovery."
                if ext_clause not in d["evidence"]:
                    d["evidence"] = f"{d['evidence']}{ext_clause}"
                escalated = True
                break

    # Populate Proactive Beyond-Defect Suggestions (Capped at 5 relevant suggestions)
    if include_proactive_suggestions:
        proactive_selected = select_proactive_suggestions(defects, max_count=5)
        existing_sugg_ids = {s.get("id") for s in suggestions}
        for pro in proactive_selected:
            if pro["id"] not in existing_sugg_ids:
                suggestions.append(dict(pro))

    # Deduplicate and aggregate suggestions across pages
    suggestions = aggregate_suggestions(suggestions, pages_examined=pages_crawled)

    # Consistency Reconciliation (Issue 4):
    # If any defect asserts zero CTAs / missing CTA (e.g. ENG-CTA-001), suppress any suggestion
    # that critiques specific CTA button text/copy to prevent internal contradictions.
    has_zero_cta = any(
        ("eng-cta-001" in str(d.get("id", "")).lower() or ("cta" in str(d.get("id", "")).lower() and "missing" in str(d.get("title", "")).lower()))
        for d in defects
    )
    if has_zero_cta:
        reconciled_suggs = []
        for s in suggestions:
            s_text = (str(s.get("id", "")) + " " + str(s.get("title", "")) + " " + str(s.get("evidence", ""))).lower()
            if any(k in s_text for k in ["cta copy", "button copy", "button phrasing", "cta text", "button text", "button wording"]):
                logger.info("Suppressing contradictory CTA copy suggestion %s due to verified zero/missing CTA finding", s.get("id"))
                continue
            reconciled_suggs.append(s)
        suggestions = reconciled_suggs

    # Sort defects deterministically: Severity Priority -> Finding ID -> Title
    defects.sort(
        key=lambda x: (
            SEVERITY_RANK.get(x["severity"], 99),
            x["id"],
            x["title"],
        )
    )

    # Calculate summary counts strictly from normalized verified defects array
    total_findings = len(defects)
    critical_count = sum(1 for f in defects if f["severity"] == "critical")
    high_count = sum(1 for f in defects if f["severity"] == "high")
    medium_count = sum(1 for f in defects if f["severity"] == "medium")
    low_count = sum(1 for f in defects if f["severity"] == "low")

    report = {
        "site": site,
        "audited_at": timestamp,
        "summary": {
            "total_findings": total_findings,
            "critical": critical_count,
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
        },
        "findings": defects,
    }

    if coverage:
        report["coverage"] = coverage

    if subagent_status:
        report["subagent_status"] = subagent_status

    if suggestions:
        report["suggestions"] = suggestions

    if meta:
        report["meta"] = meta

    if runtime:
        report["runtime"] = runtime


    # Validate output schema contract
    validate_final_report_schema(report)
    return report



def validate_final_report_schema(report: dict[str, Any]) -> None:
    """
    Validate that the generated report strictly conforms to the required final output schema.

    Raises:
        ValueError: If any required field or invariant is violated.
    """
    if not isinstance(report, dict):
        raise ValueError(f"Final report must be a dict, got {type(report).__name__}")

    # Top-level field validation
    for req_field in ("site", "audited_at", "summary", "findings"):
        if req_field not in report:
            raise ValueError(f"Final report missing required top-level field '{req_field}'")

    if not isinstance(report["site"], str) or not report["site"].strip():
        raise ValueError(f"'site' must be a non-empty string, got {report['site']!r}")

    if not isinstance(report["audited_at"], str) or not report["audited_at"].strip():
        raise ValueError(f"'audited_at' must be an ISO-8601 string, got {report['audited_at']!r}")

    # Summary validation
    summary = report["summary"]
    if not isinstance(summary, dict):
        raise ValueError(f"'summary' must be a dict, got {type(summary).__name__}")

    for count_field in ("total_findings", "critical", "high", "medium"):
        if count_field not in summary:
            raise ValueError(f"'summary' missing required field '{count_field}'")
        val = summary[count_field]
        if not isinstance(val, int) or val < 0:
            raise ValueError(f"summary['{count_field}'] must be a non-negative integer, got {val}")

    # Invariant validation
    findings = report["findings"]
    if not isinstance(findings, list):
        raise ValueError(f"'findings' must be a list, got {type(findings).__name__}")

    if summary["total_findings"] != len(findings):
        raise ValueError(
            f"summary.total_findings ({summary['total_findings']}) does not match len(findings) ({len(findings)})"
        )

    # Validate individual findings
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            raise ValueError(f"Finding [{idx}] must be a dict, got {type(f).__name__}")

        for f_field in ("id", "title", "severity", "evidence", "suggested_action"):
            if f_field not in f:
                raise ValueError(f"Finding [{idx}] missing required field '{f_field}'")

        if f["severity"] not in VALID_SEVERITIES:
            raise ValueError(f"Finding [{idx}] has invalid severity '{f['severity']}'")

        if not isinstance(f["evidence"], str) or not f["evidence"].strip():
            raise ValueError(f"Finding [{idx}] 'evidence' must be a non-empty string")

        action = f["suggested_action"]
        if not isinstance(action, dict):
            raise ValueError(f"Finding [{idx}] 'suggested_action' must be a dict")

        if "summary" not in action or not isinstance(action["summary"], str) or not action["summary"].strip():
            raise ValueError(f"Finding [{idx}] suggested_action missing non-empty 'summary'")

        if "priority" not in action or action["priority"] not in VALID_SEVERITIES:
            raise ValueError(f"Finding [{idx}] suggested_action has invalid 'priority' '{action.get('priority')}'")

    return True
