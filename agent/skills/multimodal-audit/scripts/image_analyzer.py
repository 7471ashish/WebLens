"""
Core Image Asset & Visual Quality Analyzer (image_analyzer.py)
==============================================================
Specialized deterministic analyzer within the independent Multimodal Audit subagent.

This module evaluates image validity, visual usability, rendering dimensions,
severe upscaling, aspect-ratio distortion, cropping anomalies, duplicate image assets,
and contextual relevance from structured image evidence and optional vision providers.

Strict Architectural Boundaries:
- Focuses exclusively on the IMAGE ASSET ITSELF.
- Does NOT perform alt-text quality analysis (delegated to alt_text_analyzer.py).
- Does NOT perform OCR detection (delegated to image_ocr_analyzer.py).
- Does NOT analyze EXIF / hidden metadata (delegated to image_metadata_analyzer.py).
- Does NOT analyze charts/infographics (delegated to chart_infographic_analyzer.py).
- Does NOT perform network I/O or browser rendering.
"""

from __future__ import annotations

import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from multimodal_state import (
    BoundingBox,
    EvidenceStatus,
    ImageEvidence,
    MultimodalCategoryResult,
    MultimodalFinding,
    MultimodalStatus,
    Severity,
    validate_confidence,
    validate_score,
)

logger = logging.getLogger("multimodal_audit.image")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default geometry and scaling thresholds
DEFAULT_MAX_UPSCALE_RATIO: float = 2.5  # Rendered size > 2.5x intrinsic size
DEFAULT_MAX_ASPECT_RATIO_DEVIATION: float = 0.25  # 25% aspect ratio distortion
DEFAULT_MIN_USEFUL_DIMENSION: float = 16.0  # px



# Image Analyzer Engine


class ImageAnalyzer:
    """
    Main evaluation engine for webpage image assets, resolution scaling,
    aspect ratio preservation, and visual integrity.
    """

    def __init__(
        self,
        vision_provider: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.vision_provider = vision_provider
        self.config = config or {}
        self.max_upscale_ratio = float(self.config.get("max_upscale_ratio", DEFAULT_MAX_UPSCALE_RATIO))
        self.max_aspect_deviation = float(self.config.get("max_aspect_deviation", DEFAULT_MAX_ASPECT_RATIO_DEVIATION))

    def analyze(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        """
        Analyze structured image evidence and return a JSON-serializable MultimodalCategoryResult.
        """
        raw_dict = evidence if isinstance(evidence, dict) else {}
        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        page_context = raw_dict.get("page_context") or raw_dict.get("page") or {}

        logger.info("Starting image asset analysis for %s", target_url)

        # ----------------------------------------------------------------------
        # Check 1: Image Availability & Explicit Absence
        # ----------------------------------------------------------------------
        images_raw = raw_dict.get("images")
        images_checked = raw_dict.get("images_checked", False)

        if images_raw is None:
            return MultimodalCategoryResult(
                category="image",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "No image evidence supplied in input"},
                messages=["Image evidence was not provided in the audit input."],
            ).to_dict()

        if isinstance(images_raw, list) and not images_raw:
            # Explicitly checked: zero images present on page
            return MultimodalCategoryResult(
                category="image",
                status=MultimodalStatus.PASSED.value,
                score=100,
                confidence=0.95,
                findings=[],
                evaluated=True,
                evidence_status=EvidenceStatus.ABSENT.value if images_checked else EvidenceStatus.AVAILABLE.value,
                metrics={"images_analyzed": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero image assets on the page."],
            ).to_dict()

        # Parse normalized image evidence objects
        images: list[ImageEvidence] = []
        if isinstance(images_raw, list):
            for idx, item in enumerate(images_raw):
                if isinstance(item, dict):
                    images.append(ImageEvidence.from_dict(item))
                elif isinstance(item, ImageEvidence):
                    images.append(item)

        if not images:
            return MultimodalCategoryResult(
                category="image",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "Malformed image list supplied"},
                messages=["Unable to extract valid image entries from input list."],
            ).to_dict()

        findings: list[MultimodalFinding] = []
        score = 100.0

        images_with_visual_analysis = 0
        images_with_structured_analysis = 0

        # Track duplicates across images
        seen_urls: dict[str, str] = {}
        seen_hashes: dict[str, str] = {}
        affected_duplicate_ids: set[str] = set()
        affected_duplicate_urls: set[str] = set()
        duplicate_url_pairs: list[dict[str, Any]] = []
        duplicate_hash_pairs: list[dict[str, Any]] = []

        # ----------------------------------------------------------------------
        # Per-Image Evaluation Loop
        # ----------------------------------------------------------------------
        for idx, img in enumerate(images):
            raw_item = images_raw[idx] if isinstance(images_raw, list) and idx < len(images_raw) and isinstance(images_raw[idx], dict) else {}
            img_id = img.id or f"img-{idx + 1}"
            images_with_structured_analysis += 1

            # ------------------------------------------------------------------
            # Check 2: Image Validity & Load Failures
            # ------------------------------------------------------------------
            load_status = str(img.extra.get("load_status") or raw_item.get("load_status") or raw_item.get("status") or "").lower()
            is_broken = bool(
                img.extra.get("broken")
                or img.extra.get("is_broken")
                or raw_item.get("broken")
                or raw_item.get("is_broken")
                or load_status in ("failed", "error", "404")
            )
            decode_error = img.extra.get("error") or raw_item.get("error") or img.extra.get("decode_error")

            if is_broken or decode_error:
                score -= 30.0
                findings.append(MultimodalFinding(
                    id="MM-IMG-001",
                    category="image",
                    title=f"Broken or unreadable image asset ({img_id})",
                    description=(
                        f"Image asset '{img_id}' (URL: '{img.url or 'unknown'}') failed to load, "
                        f"decode, or returned an HTTP error state ({decode_error or load_status or 'load_failed'})."
                    ),
                    severity=Severity.HIGH.value,
                    confidence=0.95,
                    evidence={
                        "image_id": img_id,
                        "url": img.url,
                        "load_status": load_status,
                        "error": decode_error,
                        "evidence_type": "explicit_load_status",
                    },
                    recommendation="Repair the broken image source URL or remove the obsolete image element.",
                    url=target_url,
                    media_id=img_id,
                ))
                continue  # Skip further geometric analysis on broken images

            # ------------------------------------------------------------------
            # Check 3: Invalid / Zero Dimensions
            # ------------------------------------------------------------------
            has_zero_dim = False
            if img.width is not None and img.width <= 0:
                has_zero_dim = True
            if img.height is not None and img.height <= 0:
                has_zero_dim = True

            if has_zero_dim:
                score -= 15.0
                findings.append(MultimodalFinding(
                    id="MM-IMG-002",
                    category="image",
                    title=f"Invalid zero or negative image dimension ({img_id})",
                    description=f"Image '{img_id}' reports invalid dimensions (width={img.width}, height={img.height}).",
                    severity=Severity.MEDIUM.value,
                    confidence=0.95,
                    evidence={
                        "image_id": img_id,
                        "width": img.width,
                        "height": img.height,
                        "evidence_type": "structured_dimensions",
                    },
                    recommendation="Ensure image tags specify positive non-zero intrinsic and rendered dimensions.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 4: Severe Upscaling / Resolution Mismatch
            # ------------------------------------------------------------------
            rendered_box = img.bounding_box or (
                BoundingBox.from_dict(raw_item.get("position") or raw_item.get("bbox") or img.extra.get("position"))
            )

            if img.width and img.height and img.width > 0 and img.height > 0 and rendered_box:
                if rendered_box.width > 0 and rendered_box.height > 0:
                    width_upscale = rendered_box.width / img.width
                    height_upscale = rendered_box.height / img.height
                    max_upscale = max(width_upscale, height_upscale)

                    if max_upscale > self.max_upscale_ratio and not img.decorative:
                        score -= 15.0
                        findings.append(MultimodalFinding(
                            id="MM-IMG-003",
                            category="image",
                            title=f"Severe image upscaling detected ({img_id})",
                            description=(
                                f"Image '{img_id}' has an intrinsic resolution of {img.width:.0f}x{img.height:.0f}px "
                                f"but is rendered at {rendered_box.width:.0f}x{rendered_box.height:.0f}px "
                                f"({max_upscale:.1f}x scaling ratio), causing visible blurriness and pixelation."
                            ),
                            severity=Severity.MEDIUM.value,
                            confidence=0.90,
                            evidence={
                                "image_id": img_id,
                                "intrinsic_width": img.width,
                                "intrinsic_height": img.height,
                                "rendered_width": rendered_box.width,
                                "rendered_height": rendered_box.height,
                                "upscale_ratio": round(max_upscale, 2),
                                "evidence_type": "rendered_geometry",
                            },
                            recommendation="Provide a higher-resolution asset (or responsive srcset) matching the rendered display dimensions.",
                            url=target_url,
                            media_id=img_id,
                        ))

            # ------------------------------------------------------------------
            # Check 5: Aspect Ratio Distortion & Squashing
            # ------------------------------------------------------------------
            if img.width and img.height and img.width > 0 and img.height > 0 and rendered_box:
                if rendered_box.width > 0 and rendered_box.height > 0:
                    intrinsic_ratio = img.width / img.height
                    rendered_ratio = rendered_box.width / rendered_box.height
                    ratio_diff = abs(intrinsic_ratio - rendered_ratio) / intrinsic_ratio

                    # Ensure it's not marked as object-fit: cover or intentional crop
                    object_fit = str(raw_item.get("object_fit") or img.extra.get("object_fit") or "").lower()
                    is_intentional_fit = object_fit in ("cover", "contain", "scale-down")

                    if ratio_diff > self.max_aspect_deviation and not is_intentional_fit and not img.decorative:
                        score -= 15.0
                        findings.append(MultimodalFinding(
                            id="MM-IMG-004",
                            category="image",
                            title=f"Aspect ratio distortion / squashing detected ({img_id})",
                            description=(
                                f"Image '{img_id}' intrinsic aspect ratio ({intrinsic_ratio:.2f}) differs significantly "
                                f"from rendered aspect ratio ({rendered_ratio:.2f}), causing visual stretching or squashing."
                            ),
                            severity=Severity.MEDIUM.value,
                            confidence=0.90,
                            evidence={
                                "image_id": img_id,
                                "intrinsic_aspect_ratio": round(intrinsic_ratio, 2),
                                "rendered_aspect_ratio": round(rendered_ratio, 2),
                                "distortion_deviation": round(ratio_diff, 2),
                                "evidence_type": "rendered_geometry",
                            },
                            recommendation="Set CSS 'object-fit: cover' or allow height/width to scale proportionally using 'height: auto'.",
                            url=target_url,
                            media_id=img_id,
                        ))

            # ------------------------------------------------------------------
            # Check 6: Problematic Cropping
            # ------------------------------------------------------------------
            crop_evidence = img.extra.get("crop_issue") or raw_item.get("crop_issue") or img.extra.get("cropped_subject") or raw_item.get("cropped_subject")
            if crop_evidence:
                score -= 10.0
                findings.append(MultimodalFinding(
                    id="MM-IMG-005",
                    category="image",
                    title=f"Subject truncation / problematic cropping ({img_id})",
                    description=(
                        f"Image '{img_id}' contains cropped visual subjects or truncated focal content "
                        f"({str(crop_evidence)})."
                    ),
                    severity=Severity.LOW.value,
                    confidence=0.85,
                    evidence={
                        "image_id": img_id,
                        "crop_details": crop_evidence,
                        "evidence_type": "crop_metadata",
                    },
                    recommendation="Adjust focal point coordinates (e.g. 'object-position: center') to keep key visual subjects in frame.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 10: Context-Aware Duplicate Image Assets Tracking Across Page
            # ------------------------------------------------------------------
            is_shared_branding = bool(
                img.decorative
                or (img.width and img.height and img.width <= 48 and img.height <= 48)
                or any(
                    marker in " ".join([
                        str(img.url or ""),
                        str(img.alt_text or ""),
                        str(raw_item.get("class") or ""),
                        str(raw_item.get("id") or ""),
                        str(raw_item.get("selector") or ""),
                        str(raw_item.get("role") or ""),
                    ]).lower()
                    for marker in ("logo", "brand", "favicon", "icon", "nav", "header", "footer", "social", "badge", "avatar")
                )
            )

            if not is_shared_branding:
                if img.url:
                    if img.url in seen_urls:
                        prior_id = seen_urls[img.url]
                        affected_duplicate_ids.add(img_id)
                        affected_duplicate_ids.add(prior_id)
                        affected_duplicate_urls.add(img.url)
                        duplicate_url_pairs.append({
                            "image_id": img_id,
                            "duplicate_of": prior_id,
                            "url": img.url,
                            "evidence_type": "duplicate_url",
                        })
                    else:
                        seen_urls[img.url] = img_id

                # Content hash duplicate check
                content_hash = img.extra.get("content_hash") or raw_item.get("content_hash") or img.extra.get("hash") or raw_item.get("hash")
                if content_hash:
                    if content_hash in seen_hashes:
                        prior_id = seen_hashes[content_hash]
                        affected_duplicate_ids.add(img_id)
                        affected_duplicate_ids.add(prior_id)
                        duplicate_hash_pairs.append({
                            "image_id": img_id,
                            "duplicate_of": prior_id,
                            "content_hash": content_hash,
                            "evidence_type": "supplied_content_hash",
                        })
                    else:
                        seen_hashes[content_hash] = img_id


            # ------------------------------------------------------------------
            # Check 7 & 8: Vision Model / Visual Provider Analysis (Level 2)
            # ------------------------------------------------------------------
            if self.vision_provider is not None:
                try:
                    v_res = self._execute_vision_analysis(img, page_context)
                    if v_res:
                        images_with_visual_analysis += 1
                        # Evaluate vision model findings if returned
                        if v_res.get("is_low_quality"):
                            score -= 15.0
                            findings.append(MultimodalFinding(
                                id="MM-IMG-007",
                                category="image",
                                title=f"Low visual clarity / severe artifacts ({img_id})",
                                description=f"Vision analysis identified severe compression artifacts or blurriness: {v_res.get('quality_reason')}",
                                severity=Severity.MEDIUM.value,
                                confidence=float(v_res.get("confidence", 0.85)),
                                evidence={"image_id": img_id, "vision_analysis": v_res, "evidence_type": "vision_analysis"},
                                recommendation="Replace with an uncompressed, high-fidelity visual asset.",
                                url=target_url,
                                media_id=img_id,
                            ))
                        if v_res.get("is_irrelevant") and not img.decorative:
                            score -= 10.0
                            findings.append(MultimodalFinding(
                                id="MM-IMG-008",
                                category="image",
                                title=f"Image content appears disconnected from page context ({img_id})",
                                description=f"Image depicts '{v_res.get('depiction')}', which has no apparent connection to page context.",
                                severity=Severity.LOW.value,
                                confidence=float(v_res.get("confidence", 0.75)),
                                evidence={"image_id": img_id, "vision_analysis": v_res, "evidence_type": "vision_analysis"},
                                recommendation="Use visual imagery that directly reinforces the surrounding topic and value proposition.",
                                url=target_url,
                                media_id=img_id,
                            ))
                except Exception as ex:
                    logger.warning("Vision provider execution failed for image %s: %s", img_id, ex)

        # ----------------------------------------------------------------------
        # Check 10 Aggregated: Duplicate / Reused Image Finding
        # ----------------------------------------------------------------------
        if duplicate_url_pairs:
            count_dups = len(affected_duplicate_ids)
            score -= min(20.0, 5.0 * len(duplicate_url_pairs))
            findings.append(MultimodalFinding(
                id="MM-IMG-006",
                category="image",
                title=f"{count_dups} reused/placeholder image assets detected across the site",
                description=f"Detected {count_dups} reused or duplicate image asset references ({', '.join(sorted(affected_duplicate_ids)[:5])}) sharing identical source URLs across the page.",
                severity=Severity.LOW.value,
                confidence=0.95,
                evidence={
                    "affected_image_ids": sorted(list(affected_duplicate_ids)),
                    "affected_urls": sorted(list(affected_duplicate_urls)),
                    "duplicate_count": count_dups,
                    "duplicate_pairs": duplicate_url_pairs[:15],
                    "evidence_type": "aggregated_duplicate_urls",
                },
                recommendation="Consolidate duplicate image elements or ensure distinct visual assets for separate content sections.",
                url=target_url,
                media_id=",".join(sorted(affected_duplicate_ids)[:3]),
            ))
        elif duplicate_hash_pairs:
            count_dups = len(affected_duplicate_ids)
            score -= min(15.0, 5.0 * len(duplicate_hash_pairs))
            findings.append(MultimodalFinding(
                id="MM-IMG-006",
                category="image",
                title=f"{count_dups} duplicate visual content hashes detected across images",
                description=f"Detected {count_dups} image instances sharing identical visual content hashes.",
                severity=Severity.LOW.value,
                confidence=0.95,
                evidence={
                    "affected_image_ids": sorted(list(affected_duplicate_ids)),
                    "duplicate_count": count_dups,
                    "duplicate_pairs": duplicate_hash_pairs[:15],
                    "evidence_type": "aggregated_duplicate_hashes",
                },
                recommendation="Verify whether duplicate image rendering is intentional.",
                url=target_url,
                media_id=",".join(sorted(affected_duplicate_ids)[:3]),
            ))

        # ----------------------------------------------------------------------
        # Score & Status Aggregation
        # ----------------------------------------------------------------------
        score_clamped = max(0, min(100, int(round(score))))

        status = MultimodalStatus.PASSED.value
        if any(f.severity in (Severity.CRITICAL.value, Severity.HIGH.value) for f in findings):
            status = MultimodalStatus.FAILED.value
        elif findings:
            status = MultimodalStatus.WARNING.value

        metrics = {
            "images_analyzed": len(images),
            "images_with_structured_analysis": images_with_structured_analysis,
            "images_with_visual_analysis": images_with_visual_analysis,
            "broken_images_count": len([f for f in findings if f.id == "MM-IMG-001"]),
            "upscaled_images_count": len([f for f in findings if f.id == "MM-IMG-003"]),
            "distorted_images_count": len([f for f in findings if f.id == "MM-IMG-004"]),
            "duplicate_images_count": len([f for f in findings if f.id == "MM-IMG-006"]),
        }

        # Sort findings deterministically: ID -> Severity
        findings.sort(key=lambda x: (x.id, x.severity))

        return MultimodalCategoryResult(
            category="image",
            status=status,
            score=score_clamped,
            confidence=0.95 if images_with_structured_analysis > 0 else 0.85,
            findings=findings,
            evaluated=True,
            evidence_status=EvidenceStatus.AVAILABLE.value,
            metrics=metrics,
            messages=[
                f"Image analysis completed across {len(images)} asset(s) (Score: {score_clamped}/100).",
                f"Visual defects identified: {len(findings)}.",
            ],
        ).to_dict()

    def __call__(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        return self.analyze(evidence)

    def _execute_vision_analysis(self, img: ImageEvidence, page_context: dict[str, Any]) -> dict[str, Any] | None:
        """Call injected vision provider safely."""
        if hasattr(self.vision_provider, "analyze_image"):
            return self.vision_provider.analyze_image(img, page_context)
        if callable(self.vision_provider):
            return self.vision_provider(img, page_context)
        return None



# Public API Function


def analyze_images(
    evidence: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public functional entry point for Image Asset & Quality evaluation.

    Args:
        evidence: Structured multimodal evidence dictionary.
        options: Optional configuration overrides.

    Returns:
        JSON-serializable category audit result dictionary.
    """
    analyzer = ImageAnalyzer(config=options)
    return analyzer.analyze(evidence)
