"""
Image OCR & Embedded Text Analyzer (image_ocr_analyzer.py)
==========================================================
Specialized deterministic analyzer within the independent Multimodal Audit subagent.

This module detects, extracts, structures, and evaluates text embedded INSIDE
images using supplied OCR evidence or an optional injected OCR provider.

Key Capabilities:
- Evaluates OCR presence, extraction confidence, and text completeness.
- Identifies low-confidence OCR extractions requiring manual verification.
- Identifies text-heavy images containing substantial embedded copywriting.
- Validates text-region bounding boxes against image coordinate boundaries.
- Detects obvious OCR noise patterns and corrupted character streams.
- Preserves detected language, word/character metrics, and region coordinates.

Strict Architectural Boundaries:
- Focuses exclusively on TEXT DETECTED INSIDE IMAGES.
- Does NOT perform alt-text quality evaluation (delegated to alt_text_analyzer.py).
- Does NOT perform image visual quality analysis (delegated to image_analyzer.py).
- Does NOT analyze EXIF / hidden metadata (delegated to image_metadata_analyzer.py).
- Does NOT analyze charts/infographics (delegated to chart_infographic_analyzer.py).
- Does NOT perform network I/O or direct image downloading.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from multimodal_state import (
    BoundingBox,
    EvidenceStatus,
    ImageEvidence,
    MultimodalCategoryResult,
    MultimodalFinding,
    MultimodalStatus,
    OCREvidence,
    OCRTextRegion,
    Severity,
    validate_confidence,
    validate_score,
)

logger = logging.getLogger("multimodal_audit.ocr")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default OCR quality thresholds
DEFAULT_LOW_OCR_CONFIDENCE: float = 0.60
DEFAULT_VERY_LOW_OCR_CONFIDENCE: float = 0.40
DEFAULT_TEXT_HEAVY_WORD_THRESHOLD: int = 50

# Obvious OCR noise / garbage character heuristics
REPEATED_CHAR_NOISE_PATTERN = re.compile(r"([a-zA-Z])\1{4,}")
GARBAGE_SYMBOL_NOISE_PATTERN = re.compile(r"[#\$%\^&\*~`|\\]{3,}")



# OCR Provider Protocol Definition


class OCRProviderProtocol(Protocol):
    """Generic interface for injected OCR extraction engines."""

    def extract_text(self, image_input: Any) -> dict[str, Any]:
        """Extract text, confidence, and regions from supplied image asset."""
        ...



# Image OCR Analyzer Engine


class ImageOCRAnalyzer:
    """
    Main evaluation engine for text embedded inside graphics, OCR confidence levels,
    text region spatial validity, text density, and noise detection.
    """

    def __init__(
        self,
        ocr_provider: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.ocr_provider = ocr_provider
        self.config = config or {}
        self.low_confidence_threshold = float(self.config.get("low_confidence_threshold", DEFAULT_LOW_OCR_CONFIDENCE))
        self.very_low_confidence_threshold = float(self.config.get("very_low_confidence_threshold", DEFAULT_VERY_LOW_OCR_CONFIDENCE))
        self.text_heavy_threshold = int(self.config.get("text_heavy_word_threshold", DEFAULT_TEXT_HEAVY_WORD_THRESHOLD))

    def analyze(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        """
        Analyze structured OCR evidence and return a JSON-serializable MultimodalCategoryResult.
        """
        raw_dict = evidence if isinstance(evidence, dict) else {}
        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        page_context = raw_dict.get("page_context") or raw_dict.get("page") or {}

        logger.info("Starting image OCR analysis for %s", target_url)

        # ----------------------------------------------------------------------
        # Check 1: Evidence Availability & Routing
        # ----------------------------------------------------------------------
        # Support either top-level "ocr" list or nested "ocr" objects within "images"
        images_raw = None
        for k in ("images", "media", "ocr", "ocr_results"):
            if k in raw_dict and raw_dict[k] is not None:
                images_raw = raw_dict[k]
                break

        images_checked = bool(raw_dict.get("images_checked", False) or raw_dict.get("ocr_checked", False))

        if images_raw is None and self.ocr_provider is None:
            return MultimodalCategoryResult(
                category="ocr",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "No OCR evidence or provider supplied in input"},
                messages=["OCR evidence was not provided in the audit input."],
            ).to_dict()

        if isinstance(images_raw, list) and not images_raw:
            return MultimodalCategoryResult(
                category="ocr",
                status=MultimodalStatus.PASSED.value,
                score=100,
                confidence=0.95,
                findings=[],
                evaluated=True,
                evidence_status=EvidenceStatus.ABSENT.value if images_checked else EvidenceStatus.AVAILABLE.value,
                metrics={"images_analyzed": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero image assets requiring OCR."],
            ).to_dict()

        # Normalize incoming image/OCR objects
        items_to_evaluate: list[dict[str, Any]] = []
        if isinstance(images_raw, list):
            for idx, itm in enumerate(images_raw):
                if isinstance(itm, dict):
                    items_to_evaluate.append(itm)
                elif isinstance(itm, (ImageEvidence, OCREvidence)):
                    items_to_evaluate.append(itm.to_dict())

        if not items_to_evaluate and self.ocr_provider is None:
            return MultimodalCategoryResult(
                category="ocr",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "Malformed OCR input list supplied"},
                messages=["Unable to extract valid image OCR entries from input."],
            ).to_dict()

        findings: list[MultimodalFinding] = []
        score = 100.0

        images_analyzed_count = 0
        images_with_text_count = 0
        images_without_text_count = 0
        images_with_low_confidence_count = 0
        images_with_partial_ocr_count = 0
        total_confidence_sum = 0.0

        # ----------------------------------------------------------------------
        # Per-Image OCR Evaluation Loop
        # ----------------------------------------------------------------------
        for idx, item in enumerate(items_to_evaluate):
            img_id = str(item.get("id") or item.get("image_id") or f"img-{idx + 1}")
            img_url = str(item.get("url") or item.get("source_url") or "")
            img_width = float(item["width"]) if item.get("width") is not None else None
            img_height = float(item["height"]) if item.get("height") is not None else None

            # Extract OCR payload
            ocr_payload = item.get("ocr")
            if ocr_payload is None and ("text" in item or "confidence" in item or "text_regions" in item):
                ocr_payload = item

            # If no precomputed OCR but provider exists, invoke injected provider
            if (ocr_payload is None or (isinstance(ocr_payload, dict) and "text" not in ocr_payload and not ocr_payload.get("detected") is False)) and self.ocr_provider is not None:
                try:
                    ocr_payload = self._invoke_provider(item)
                except Exception as ex:
                    logger.warning("Injected OCR provider failed for %s: %s", img_id, ex)
                    ocr_payload = {"ocr_status": "error", "error_type": type(ex).__name__}

            if not isinstance(ocr_payload, dict):
                continue

            # Check for explicit provider execution error
            if ocr_payload.get("ocr_status") == "error" or ocr_payload.get("status") == "error":
                findings.append(MultimodalFinding(
                    id="MM-OCR-005",
                    category="ocr",
                    title=f"OCR execution failure on image ({img_id})",
                    description=f"OCR extraction engine encountered an error processing image '{img_id}' ({ocr_payload.get('error_type', 'engine_error')}).",
                    severity=Severity.LOW.value,
                    confidence=0.90,
                    evidence={"image_id": img_id, "error": ocr_payload.get("error_type", "engine_error")},
                    recommendation="Verify that the image format is supported by the OCR pipeline.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue

            # Parse OCR properties
            raw_text = ocr_payload.get("text")
            confidence_val = ocr_payload.get("confidence")
            language = str(ocr_payload.get("language") or "en")
            is_detected = ocr_payload.get("detected")
            if is_detected is None:
                is_detected = bool(raw_text and str(raw_text).strip())
            else:
                is_detected = bool(is_detected)

            is_complete = ocr_payload.get("ocr_complete")
            is_partial = bool(ocr_payload.get("partial", False) or (is_complete is False))

            images_analyzed_count += 1

            # ------------------------------------------------------------------
            # Check 2 & 5: Text Detection & Empty OCR Results
            # ------------------------------------------------------------------
            if not is_detected or not raw_text or str(raw_text).strip() == "":
                images_without_text_count += 1
                total_confidence_sum += 1.0  # High confidence in absence of text
                continue  # Successfully confirmed zero text; valid state, no penalty

            images_with_text_count += 1
            extracted_text = str(raw_text).strip()
            normalized_text = " ".join(extracted_text.split())
            word_count = len(normalized_text.split())
            char_count = len(normalized_text)

            # ------------------------------------------------------------------
            # Check 3 & 4: OCR Confidence Evaluation
            # ------------------------------------------------------------------
            num_conf = None
            if confidence_val is not None:
                try:
                    num_conf = max(0.0, min(1.0, float(confidence_val)))
                    total_confidence_sum += num_conf
                except (ValueError, TypeError):
                    pass

            if num_conf is not None:
                if num_conf < self.very_low_confidence_threshold:
                    images_with_low_confidence_count += 1
                    score -= 15.0
                    findings.append(MultimodalFinding(
                        id="MM-OCR-001",
                        category="ocr",
                        title=f"Extremely low OCR extraction confidence ({num_conf:.0%}) ({img_id})",
                        description=(
                            f"OCR detected text with very low confidence ({num_conf:.2f}), indicating severe blur, "
                            f"complex typography, or low contrast: '{normalized_text[:80]}'."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.92,
                        evidence={
                            "image_id": img_id,
                            "ocr_confidence": round(num_conf, 2),
                            "detected_text": normalized_text[:120],
                            "evidence_type": "ocr_provider",
                        },
                        recommendation="Verify the detected text against the original visual image before relying on it.",
                        url=target_url,
                        media_id=img_id,
                    ))
                elif num_conf < self.low_confidence_threshold:
                    images_with_low_confidence_count += 1
                    score -= 8.0
                    findings.append(MultimodalFinding(
                        id="MM-OCR-001",
                        category="ocr",
                        title=f"Low OCR extraction confidence ({num_conf:.0%}) ({img_id})",
                        description=(
                            f"OCR detected text with low reliability ({num_conf:.2f}) on image '{img_id}': "
                            f"'{normalized_text[:80]}'."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.88,
                        evidence={
                            "image_id": img_id,
                            "ocr_confidence": round(num_conf, 2),
                            "detected_text": normalized_text[:120],
                            "evidence_type": "ocr_provider",
                        },
                        recommendation="Review the detected text to confirm accurate character recognition.",
                        url=target_url,
                        media_id=img_id,
                    ))
            else:
                total_confidence_sum += 0.85

            # ------------------------------------------------------------------
            # Check 17 & 18: Incomplete / Partial OCR
            # ------------------------------------------------------------------
            if is_partial:
                images_with_partial_ocr_count += 1
                score -= 10.0
                findings.append(MultimodalFinding(
                    id="MM-OCR-002",
                    category="ocr",
                    title=f"Partial / truncated OCR text extraction ({img_id})",
                    description=(
                        f"OCR evidence reports that text extraction was partial or truncated for image '{img_id}'. "
                        "The returned text may not represent the complete copywriting contained in the image."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.90,
                    evidence={
                        "image_id": img_id,
                        "partial": True,
                        "extracted_word_count": word_count,
                        "evidence_type": "ocr_metadata",
                    },
                    recommendation="Re-run full-resolution OCR or inspect the image directly for remaining text content.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 10: Text-Heavy Images
            # ------------------------------------------------------------------
            if word_count >= self.text_heavy_threshold:
                score -= 5.0
                findings.append(MultimodalFinding(
                    id="MM-OCR-003",
                    category="ocr",
                    title=f"Image contains substantial embedded text ({word_count} words) ({img_id})",
                    description=(
                        f"Image '{img_id}' contains a large amount of embedded text ({word_count} words / {char_count} chars). "
                        "Crucial copy embedded purely in raster images cannot be translated or selected by users."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.95,
                    evidence={
                        "image_id": img_id,
                        "word_count": word_count,
                        "char_count": char_count,
                        "threshold": self.text_heavy_threshold,
                        "text_sample": normalized_text[:150],
                    },
                    recommendation="Render lengthy text content as semantic HTML body text rather than embedding it inside raster graphics.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 7 & 8: Text Region Coordinates & Spatial Validation
            # ------------------------------------------------------------------
            text_regions_raw = ocr_payload.get("text_regions") or ocr_payload.get("regions") or []
            if isinstance(text_regions_raw, list) and text_regions_raw:
                invalid_coords = False
                for r in text_regions_raw:
                    if isinstance(r, dict):
                        rx = float(r.get("x", 0.0) or 0.0)
                        ry = float(r.get("y", 0.0) or 0.0)
                        rw = float(r.get("width", 0.0) or 0.0)
                        rh = float(r.get("height", 0.0) or 0.0)

                        if rw < 0 or rh < 0:
                            invalid_coords = True
                            break

                        # If image dimensions known, check for egregious out-of-bounds (> 20% beyond dimensions)
                        if img_width and img_height and img_width > 0 and img_height > 0:
                            if (rx + rw) > (img_width * 1.25) or (ry + rh) > (img_height * 1.25):
                                invalid_coords = True
                                break

                if invalid_coords:
                    score -= 5.0
                    findings.append(MultimodalFinding(
                        id="MM-OCR-004",
                        category="ocr",
                        title=f"Inconsistent or out-of-bounds OCR region coordinates ({img_id})",
                        description=f"Text bounding box coordinates reported for image '{img_id}' extend beyond image bounds or contain negative dimensions.",
                        severity=Severity.LOW.value,
                        confidence=0.90,
                        evidence={"image_id": img_id, "image_dimensions": {"width": img_width, "height": img_height}},
                        recommendation="Verify coordinate scaling factors between OCR detection models and rendered image frames.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 20: OCR Noise Heuristics
            # ------------------------------------------------------------------
            if REPEATED_CHAR_NOISE_PATTERN.search(normalized_text) or GARBAGE_SYMBOL_NOISE_PATTERN.search(normalized_text):
                score -= 8.0
                findings.append(MultimodalFinding(
                    id="MM-OCR-005",
                    category="ocr",
                    title=f"Potential OCR noise or unreadable character artifact ({img_id})",
                    description=f"Detected text stream on image '{img_id}' contains repeated character glitches or irregular symbol clusters.",
                    severity=Severity.LOW.value,
                    confidence=0.85,
                    evidence={"image_id": img_id, "text_sample": normalized_text[:100]},
                    recommendation="Review the source graphic resolution and contrast to ensure clean text recognition.",
                    url=target_url,
                    media_id=img_id,
                ))

        # ----------------------------------------------------------------------
        # Score & Status Aggregation
        # ----------------------------------------------------------------------
        if images_analyzed_count == 0:
            return MultimodalCategoryResult(
                category="ocr",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "No valid OCR records could be evaluated"},
                messages=["No OCR observations were found in the supplied input."],
            ).to_dict()

        score_clamped = max(0, min(100, int(round(score))))
        avg_confidence = round(total_confidence_sum / max(1, images_analyzed_count), 2)

        status = MultimodalStatus.PASSED.value
        if any(f.severity in (Severity.CRITICAL.value, Severity.HIGH.value) for f in findings):
            status = MultimodalStatus.FAILED.value
        elif findings:
            status = MultimodalStatus.WARNING.value

        metrics = {
            "images_analyzed": images_analyzed_count,
            "images_with_text": images_with_text_count,
            "images_without_text": images_without_text_count,
            "images_with_low_confidence": images_with_low_confidence_count,
            "images_with_partial_ocr": images_with_partial_ocr_count,
            "low_confidence_findings_count": len([f for f in findings if f.id == "MM-OCR-001"]),
            "partial_ocr_findings_count": len([f for f in findings if f.id == "MM-OCR-002"]),
            "text_heavy_findings_count": len([f for f in findings if f.id == "MM-OCR-003"]),
        }

        # Sort findings deterministically: ID -> Severity
        findings.sort(key=lambda x: (x.id, x.severity))

        return MultimodalCategoryResult(
            category="ocr",
            status=status,
            score=score_clamped,
            confidence=avg_confidence,
            findings=findings,
            evaluated=True,
            evidence_status=EvidenceStatus.AVAILABLE.value,
            metrics=metrics,
            messages=[
                f"OCR evaluation completed across {images_analyzed_count} image(s) (Score: {score_clamped}/100).",
                f"Images with detected text: {images_with_text_count}/{images_analyzed_count}.",
            ],
        ).to_dict()

    def __call__(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        return self.analyze(evidence)

    def _invoke_provider(self, item: dict[str, Any]) -> dict[str, Any]:
        """Safely invoke injected OCR provider."""
        if hasattr(self.ocr_provider, "extract_text"):
            return self.ocr_provider.extract_text(item)
        if callable(self.ocr_provider):
            return self.ocr_provider(item)
        return {}



# Public API Function


def analyze_ocr(
    evidence: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public functional entry point for Image OCR & Embedded Text evaluation.

    Args:
        evidence: Structured multimodal evidence dictionary.
        options: Optional configuration overrides.

    Returns:
        JSON-serializable category audit result dictionary.
    """
    analyzer = ImageOCRAnalyzer(config=options)
    return analyzer.analyze(evidence)
