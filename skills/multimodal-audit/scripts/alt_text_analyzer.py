"""
Alternative Text & Image Description Analyzer (alt_text_analyzer.py)
=====================================================================
Specialized deterministic analyzer within the independent Multimodal Audit subagent.

This module evaluates the presence, semantic quality, conciseness, and contextual
appropriateness of alternative text (alt attributes, ARIA labels, and accessible descriptions)
attached to webpage images.

Strict Architectural Boundaries:
- Focuses exclusively on the SUPPLIED ALTERNATIVE TEXT EVIDENCE.
- Does NOT download images or process visual pixels (delegated to image_analyzer.py).
- Does NOT perform OCR detection (delegated to image_ocr_analyzer.py).
- Does NOT analyze EXIF / hidden metadata (delegated to image_metadata_analyzer.py).
- Does NOT analyze charts/infographics (delegated to chart_infographic_analyzer.py).
- Does NOT make blanket legal or WCAG accessibility certification claims.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from multimodal_state import (
    AltTextEvidence,
    EvidenceStatus,
    ImageEvidence,
    MultimodalCategoryResult,
    MultimodalFinding,
    MultimodalStatus,
    Severity,
    validate_confidence,
    validate_score,
)

logger = logging.getLogger("multimodal_audit.alt_text")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default text thresholds
DEFAULT_MAX_ALT_TEXT_LENGTH: int = 250  # characters
DEFAULT_MIN_USEFUL_ALT_WORDS: int = 2

# Known generic uninformative words (case-insensitive)
GENERIC_ALT_WORDS: set[str] = {
    "image",
    "photo",
    "picture",
    "graphic",
    "img",
    "icon",
    "photograph",
    "banner",
    "pic",
    "asset",
    "media",
}

# Known placeholder text strings (case-insensitive)
PLACEHOLDER_ALT_PATTERNS: list[str] = [
    r"^image\s*here$",
    r"^insert\s*image$",
    r"^placeholder$",
    r"^test(\s*image)?$",
    r"^foo$",
    r"^bar$",
    r"^temp(\s*image)?$",
    r"^lorem\s*ipsum.*$",
    r"^sample(\s*image)?$",
    r"^untitled(\s*image)?$",
    r"^dummy(\s*image)?$",
    r"^null$",
    r"^undefined$",
    r"^n/a$",
    r"^none$",
    r"^todo$",
    r"^fixme$",
]

# File extension patterns indicating raw filename in alt text
FILENAME_ALT_PATTERN = re.compile(
    r"^[\w,\s-]+\.(jpg|jpeg|png|gif|webp|svg|bmp|tiff|avif|heic|ico)$",
    re.IGNORECASE,
)

# Common camera / raw export prefixes
RAW_CAMERA_PREFIX_PATTERN = re.compile(
    r"^(IMG_|DSC_|DCIM_|PEXELS_|UNSPLASH_|SHUTTERSTOCK_|SCREENSHOT_|\d{8,})",
    re.IGNORECASE,
)



# Alt Text Analyzer Engine


class AltTextAnalyzer:
    """
    Main evaluation engine for alternative text quality, missing attributes,
    placeholders, filename leaks, decorative tagging, and context consistency.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.max_alt_length = int(self.config.get("max_alt_length", DEFAULT_MAX_ALT_TEXT_LENGTH))

    def analyze(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        """
        Analyze structured alt text evidence and return a JSON-serializable MultimodalCategoryResult.
        """
        raw_dict = evidence if isinstance(evidence, dict) else {}
        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        page_context = raw_dict.get("page_context") or raw_dict.get("page") or {}

        logger.info("Starting alt-text accessibility analysis for %s", target_url)

        # ----------------------------------------------------------------------
        # Check: Evidence Availability & Explicit Absence
        # ----------------------------------------------------------------------
        images_raw = None
        for k in ("images", "alt_texts", "alts", "media"):
            if k in raw_dict and raw_dict[k] is not None:
                images_raw = raw_dict[k]
                break

        images_checked = bool(raw_dict.get("images_checked", False))

        if images_raw is None:
            return MultimodalCategoryResult(
                category="alt_text",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "No alt text evidence supplied in input"},
                messages=["Alt text evidence was not provided in the audit input."],
            ).to_dict()

        if isinstance(images_raw, list) and not images_raw:
            return MultimodalCategoryResult(
                category="alt_text",
                status=MultimodalStatus.PASSED.value,
                score=100,
                confidence=0.95,
                findings=[],
                evaluated=True,
                evidence_status=EvidenceStatus.ABSENT.value if images_checked else EvidenceStatus.AVAILABLE.value,
                metrics={"images_analyzed": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero image assets requiring alt text."],
            ).to_dict()

        # Parse normalized image / alt text records
        images_list: list[dict[str, Any]] = []
        if isinstance(images_raw, list):
            for idx, item in enumerate(images_raw):
                if isinstance(item, dict):
                    images_list.append(item)
                elif isinstance(item, (ImageEvidence, AltTextEvidence)):
                    images_list.append(item.to_dict())

        if not images_list:
            return MultimodalCategoryResult(
                category="alt_text",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "Malformed alt text list supplied"},
                messages=["Unable to extract valid image records from input list."],
            ).to_dict()

        findings: list[MultimodalFinding] = []
        score = 100.0

        images_analyzed_count = 0
        images_with_alt_count = 0
        images_missing_alt_count = 0
        images_decorative_count = 0

        # Frequency tracker for duplicate generic descriptions across distinct images
        alt_text_frequency: dict[str, list[str]] = {}

        # ----------------------------------------------------------------------
        # Per-Image Alt Text Evaluation Loop
        # ----------------------------------------------------------------------
        for idx, img_dict in enumerate(images_list):
            img_id = str(img_dict.get("id") or img_dict.get("image_id") or f"img-{idx + 1}")
            img_url = str(img_dict.get("url") or img_dict.get("source_url") or "")
            role = str(img_dict.get("role") or "").lower()
            is_decorative = bool(img_dict.get("decorative") or img_dict.get("is_decorative", False) or role == "decorative")
            is_visible = bool(img_dict.get("visible", True))

            # Retrieve alt values
            raw_alt = img_dict.get("alt_text") if "alt_text" in img_dict else img_dict.get("alt")
            alt_attr_present = img_dict.get("alt_attribute_present")
            if alt_attr_present is None and raw_alt is not None:
                alt_attr_present = True

            # If completely uncollected, skip without false penalties
            if raw_alt is None and alt_attr_present is None:
                continue

            images_analyzed_count += 1
            if is_decorative:
                images_decorative_count += 1

            # ------------------------------------------------------------------
            # Check 1: Missing Alt Attribute
            # ------------------------------------------------------------------
            if alt_attr_present is False and not is_decorative:
                images_missing_alt_count += 1
                score -= 20.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-001",
                    category="alt_text",
                    title=f"Image is missing an alt attribute ({img_id})",
                    description=(
                        f"Image asset '{img_id}' has no alt attribute defined. Screen readers will read raw URLs or "
                        "fail to communicate the presence of the visual asset."
                    ),
                    severity=Severity.HIGH.value if role in ("primary", "hero", "product", "functional") else Severity.MEDIUM.value,
                    confidence=0.96,
                    evidence={
                        "image_id": img_id,
                        "url": img_url,
                        "alt_attribute_present": False,
                        "decorative": is_decorative,
                        "role": role or "unspecified",
                    },
                    recommendation="Add an alt attribute with a concise, factual description of the image content or function.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue  # Skip further text checks for missing alt attributes

            # Normalize alt text for evaluation
            alt_str = str(raw_alt) if raw_alt is not None else ""
            normalized_alt = " ".join(alt_str.split())
            alt_lower = normalized_alt.lower()

            # ------------------------------------------------------------------
            # Check 2 & 3: Empty / Whitespace-Only Alt Text
            # ------------------------------------------------------------------
            if normalized_alt == "":
                if not is_decorative:
                    images_missing_alt_count += 1
                    score -= 15.0
                    findings.append(MultimodalFinding(
                        id="MM-ALT-002",
                        category="alt_text",
                        title=f"Informative image has empty alt text ({img_id})",
                        description=(
                            f"Image '{img_id}' is non-decorative but provides an empty alt attribute (alt=\"\"), "
                            "causing screen readers to skip it entirely."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.95,
                        evidence={
                            "image_id": img_id,
                            "url": img_url,
                            "alt_text": raw_alt,
                            "decorative": is_decorative,
                            "role": role or "informative",
                        },
                        recommendation="Provide descriptive alternative text conveying the image's information or intent.",
                        url=target_url,
                        media_id=img_id,
                    ))
                continue  # Valid empty alt on decorative image, or handled missing text

            images_with_alt_count += 1

            # ------------------------------------------------------------------
            # Check 3B: Decorative Image with Unnecessary Alt Text
            # ------------------------------------------------------------------
            if is_decorative:
                if alt_lower in ("decorative image", "decorative", "decoration", "spacer", "divider"):
                    findings.append(MultimodalFinding(
                        id="MM-ALT-003",
                        category="alt_text",
                        title=f"Decorative image contains literal 'decorative' alt text ({img_id})",
                        description=f"Image '{img_id}' is marked decorative but uses redundant alt text '{normalized_alt}'.",
                        severity=Severity.LOW.value,
                        confidence=0.90,
                        evidence={"image_id": img_id, "alt_text": normalized_alt, "decorative": True},
                        recommendation="Set alt=\"\" (empty string) so assistive technologies gracefully ignore purely decorative graphics.",
                        url=target_url,
                        media_id=img_id,
                    ))
                # Skip further semantic checks on decorative images
                continue

            # ------------------------------------------------------------------
            # Check 5: Placeholder Alt Text
            # ------------------------------------------------------------------
            is_placeholder = False
            for pat in PLACEHOLDER_ALT_PATTERNS:
                if re.match(pat, alt_lower):
                    is_placeholder = True
                    break

            if is_placeholder:
                score -= 15.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-005",
                    category="alt_text",
                    title=f"Placeholder alternative text detected ({img_id})",
                    description=f"Image '{img_id}' uses temporary placeholder text '{normalized_alt}' instead of a real description.",
                    severity=Severity.MEDIUM.value,
                    confidence=0.95,
                    evidence={"image_id": img_id, "alt_text": normalized_alt},
                    recommendation="Replace placeholder text with an accurate description of what the image depicts.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue

            # ------------------------------------------------------------------
            # Check 6: Filename / URL-Based Alt Text
            # ------------------------------------------------------------------
            if FILENAME_ALT_PATTERN.match(normalized_alt) or RAW_CAMERA_PREFIX_PATTERN.match(normalized_alt):
                score -= 10.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-006",
                    category="alt_text",
                    title=f"Filename or asset ID used as alternative text ({img_id})",
                    description=f"Image '{img_id}' uses a raw file path or filename '{normalized_alt}' as its alt description.",
                    severity=Severity.MEDIUM.value,
                    confidence=0.95,
                    evidence={"image_id": img_id, "alt_text": normalized_alt},
                    recommendation="Replace the raw filename with human-readable descriptive text.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue

            # ------------------------------------------------------------------
            # Check 4 & 10: Extremely Short / Generic Uninformative Words
            # ------------------------------------------------------------------
            if alt_lower in GENERIC_ALT_WORDS:
                score -= 10.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-004",
                    category="alt_text",
                    title=f"Generic uninformative alternative text '{normalized_alt}' ({img_id})",
                    description=(
                        f"Image '{img_id}' uses a generic non-descriptive word '{normalized_alt}'. "
                        "Screen readers already announce image elements, making generic labels redundant."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.90,
                    evidence={"image_id": img_id, "alt_text": normalized_alt},
                    recommendation="Describe the subject or specific action rather than simply stating it is an image.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue

            # ------------------------------------------------------------------
            # Check 7: Excessively Long Alt Text
            # ------------------------------------------------------------------
            if len(normalized_alt) > self.max_alt_length and role not in ("diagram", "chart", "infographic", "complex"):
                score -= 5.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-007",
                    category="alt_text",
                    title=f"Excessively verbose alternative text ({len(normalized_alt)} chars) ({img_id})",
                    description=(
                        f"Image '{img_id}' alt text length ({len(normalized_alt)} characters) exceeds the recommended "
                        f"{self.max_alt_length}-character threshold. Long descriptions should be placed in body text or captions."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.85,
                    evidence={
                        "image_id": img_id,
                        "alt_text_length": len(normalized_alt),
                        "max_recommended_length": self.max_alt_length,
                    },
                    recommendation="Keep alt text concise (under 250 characters) and move detailed explanations into surrounding copy or captions.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 14 & 15: Functional Image / Action Affordances
            # ------------------------------------------------------------------
            href = img_dict.get("href") or img_dict.get("link_target")
            if (role in ("functional", "button", "link", "icon") or href) and not is_decorative:
                if alt_lower in ("arrow", "chevron", "magnifying glass", "search icon", "cart icon", "hamburger", "bars"):
                    findings.append(MultimodalFinding(
                        id="MM-ALT-009",
                        category="alt_text",
                        title=f"Functional image describes appearance instead of action ({img_id})",
                        description=(
                            f"Linked or functional image '{img_id}' uses appearance-based alt text '{normalized_alt}' "
                            f"instead of communicating its destination or interactive action."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.85,
                        evidence={
                            "image_id": img_id,
                            "alt_text": normalized_alt,
                            "role": role,
                            "href": href,
                        },
                        recommendation="Describe the interactive function (e.g. 'Next page', 'Search catalog', 'View cart') rather than the graphic shape.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 18: Consistency with Supplied Visual Summary
            # ------------------------------------------------------------------
            visual_summary = img_dict.get("visual_summary")
            if visual_summary and isinstance(visual_summary, str) and len(visual_summary.strip()) > 10:
                v_sum_lower = visual_summary.strip().lower()
                # Basic contradiction heuristic when explicit mismatch keywords appear
                if "error" in v_sum_lower and "success" in alt_lower:
                    findings.append(MultimodalFinding(
                        id="MM-ALT-010",
                        category="alt_text",
                        title=f"Potential mismatch between alt text and visual content ({img_id})",
                        description=f"Alt text '{normalized_alt}' appears inconsistent with reported visual summary '{visual_summary}'.",
                        severity=Severity.MEDIUM.value,
                        confidence=0.75,
                        evidence={"image_id": img_id, "alt_text": normalized_alt, "visual_summary": visual_summary},
                        recommendation="Verify that the alternative text matches the actual visual representation.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # Track frequency for multi-image checks
            if not is_decorative and role != "logo":
                alt_text_frequency.setdefault(alt_lower, []).append(img_id)

        # ----------------------------------------------------------------------
        # Multi-Image Check 8: Repetitive Alt Text Across Separate Images
        # ----------------------------------------------------------------------
        for alt_phrase, img_ids in alt_text_frequency.items():
            if len(img_ids) >= 3 and len(alt_phrase.split()) <= 3:
                score -= 10.0
                findings.append(MultimodalFinding(
                    id="MM-ALT-008",
                    category="alt_text",
                    title=f"Repetitive generic alt text '{alt_phrase}' used across {len(img_ids)} images",
                    description=(
                        f"The exact same short description '{alt_phrase}' is shared across multiple distinct images "
                        f"({', '.join(img_ids[:4])}), which reduces descriptive value for screen reader users."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.90,
                    evidence={"shared_alt_text": alt_phrase, "image_ids": img_ids},
                    recommendation="Provide distinct, contextual descriptions distinguishing the unique subject of each image.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Score & Status Calculation
        # ----------------------------------------------------------------------
        score_clamped = max(0, min(100, int(round(score))))
        status = MultimodalStatus.PASSED.value
        if any(f.severity in (Severity.CRITICAL.value, Severity.HIGH.value) for f in findings):
            status = MultimodalStatus.FAILED.value
        elif findings:
            status = MultimodalStatus.WARNING.value

        metrics = {
            "images_analyzed": images_analyzed_count,
            "images_with_alt": images_with_alt_count,
            "images_missing_alt": images_missing_alt_count,
            "images_decorative": images_decorative_count,
            "missing_alt_findings_count": len([f for f in findings if f.id == "MM-ALT-001"]),
            "empty_alt_findings_count": len([f for f in findings if f.id == "MM-ALT-002"]),
            "placeholder_findings_count": len([f for f in findings if f.id == "MM-ALT-005"]),
            "filename_findings_count": len([f for f in findings if f.id == "MM-ALT-006"]),
        }

        # Sort findings deterministically: ID -> Severity
        findings.sort(key=lambda x: (x.id, x.severity))

        return MultimodalCategoryResult(
            category="alt_text",
            status=status,
            score=score_clamped,
            confidence=0.95 if images_analyzed_count > 0 else 0.85,
            findings=findings,
            evaluated=True,
            evidence_status=EvidenceStatus.AVAILABLE.value,
            metrics=metrics,
            messages=[
                f"Alt-text analysis completed across {images_analyzed_count} image asset(s) (Score: {score_clamped}/100).",
                f"Accessibility defects identified: {len(findings)}.",
            ],
        ).to_dict()

    def __call__(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        return self.analyze(evidence)



# Public API Function


def analyze_alt_text(
    evidence: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public functional entry point for Alternative Text quality evaluation.

    Args:
        evidence: Structured multimodal evidence dictionary.
        options: Optional configuration overrides.

    Returns:
        JSON-serializable category audit result dictionary.
    """
    analyzer = AltTextAnalyzer(config=options)
    return analyzer.analyze(evidence)
