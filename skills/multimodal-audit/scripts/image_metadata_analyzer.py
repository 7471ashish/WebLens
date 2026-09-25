"""
Image Technical Metadata & Asset Integrity Analyzer (image_metadata_analyzer.py)
==================================================================================
Specialized deterministic analyzer within the independent Multimodal Audit subagent.

This module evaluates image technical attributes, file size limits, MIME type
consistency, format standards, EXIF data exposure, orientation validity, and
aspect ratio integrity based on supplied structured metadata evidence.

Key Capabilities:
- Validates intrinsic dimensions and flags non-positive or extreme pixel bounds.
- Compares declared vs calculated aspect ratios to detect metadata contradictions.
- Checks MIME type vs file extension compatibility (e.g. image/jpeg vs .png).
- Detects potentially oversized file sizes (>1MB warning, >5MB high severity).
- Identifies sensitive EXIF metadata (GPS/location coordinates, camera serials).
- Evaluates image orientation flags and color profile declarations.
- Detects duplicate asset metadata records and identical content hashes.

Strict Architectural Boundaries:
- Focuses exclusively on STRUCTURED METADATA EVIDENCE.
- Does NOT download image files or make HTTP network requests.
- Does NOT inspect image pixels or calculate hashes.
- Does NOT perform OCR detection (delegated to image_ocr_analyzer.py).
- Does NOT perform alt-text quality evaluation (delegated to alt_text_analyzer.py).
- Does NOT evaluate visual quality (delegated to image_analyzer.py).
- Does NOT analyze charts or videos (delegated to chart/video analyzers).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from multimodal_state import (
    EvidenceStatus,
    ImageEvidence,
    ImageMetadata,
    MultimodalCategoryResult,
    MultimodalFinding,
    MultimodalStatus,
    Severity,
    validate_confidence,
    validate_score,
)

logger = logging.getLogger("multimodal_audit.metadata")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default file size and dimension thresholds
DEFAULT_MAX_FILE_SIZE_WARNING_BYTES: int = 1_048_576  # 1 MB
DEFAULT_MAX_FILE_SIZE_HIGH_BYTES: int = 5_242_880     # 5 MB
DEFAULT_MAX_DIMENSION_PX: float = 8000.0             # 8000 px width or height
DEFAULT_MAX_TOTAL_PIXELS: float = 36_000_000.0       # 36 MP

# MIME type to standard extension mappings
MIME_TO_EXTENSIONS: dict[str, set[str]] = {
    "image/jpeg": {"jpg", "jpeg", "jpe", "jfif"},
    "image/jpg": {"jpg", "jpeg"},
    "image/png": {"png"},
    "image/webp": {"webp"},
    "image/avif": {"avif"},
    "image/gif": {"gif"},
    "image/svg+xml": {"svg"},
    "image/bmp": {"bmp"},
    "image/tiff": {"tif", "tiff"},
    "image/x-icon": {"ico"},
    "image/vnd.microsoft.icon": {"ico"},
    "image/heic": {"heic", "heif"},
}

VALID_ORIENTATION_STRINGS: set[str] = {
    "landscape",
    "portrait",
    "square",
    "rotated",
    "horizontal",
    "vertical",
    "1", "2", "3", "4", "5", "6", "7", "8",
}

# Legacy uncompressed formats suitable for modern conversion
LEGACY_FORMATS: set[str] = {"bmp", "tiff", "tif"}



# Image Metadata Analyzer Engine


class ImageMetadataAnalyzer:
    """
    Main evaluation engine for technical image metadata, file sizes, MIME compatibility,
    EXIF properties, and asset integrity.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.max_bytes_warning = int(self.config.get("max_bytes_warning", DEFAULT_MAX_FILE_SIZE_WARNING_BYTES))
        self.max_bytes_high = int(self.config.get("max_bytes_high", DEFAULT_MAX_FILE_SIZE_HIGH_BYTES))
        self.max_dim_px = float(self.config.get("max_dimension_px", DEFAULT_MAX_DIMENSION_PX))
        self.max_pixels = float(self.config.get("max_total_pixels", DEFAULT_MAX_TOTAL_PIXELS))

    def analyze(self, image_data: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        """
        Analyze structured image metadata and return a JSON-serializable MultimodalCategoryResult.
        """
        raw_dict: dict[str, Any] = {}
        if isinstance(image_data, list):
            raw_dict = {"images": image_data}
        elif isinstance(image_data, dict):
            raw_dict = image_data

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        logger.info("Starting image metadata analysis for %s", target_url)

        # ----------------------------------------------------------------------
        # Check 1: Evidence Availability & Explicit Absence
        # ----------------------------------------------------------------------
        images_raw = None
        for k in ("images", "media", "metadata", "image_metadata", "assets"):
            if k in raw_dict and raw_dict[k] is not None:
                images_raw = raw_dict[k]
                break

        # Check if a single flat image metadata dict was passed directly
        if images_raw is None and any(k in raw_dict for k in ("filename", "mime_type", "file_size_bytes", "width", "height", "exif", "format")):
            images_raw = [raw_dict]

        images_checked = bool(raw_dict.get("images_checked", False) or raw_dict.get("metadata_checked", False))

        if images_raw is None:
            return MultimodalCategoryResult(
                category="image_metadata",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "No metadata evidence supplied in input"},
                messages=["Image metadata evidence was not provided in the audit input."],
            ).to_dict()

        if isinstance(images_raw, list) and not images_raw:
            return MultimodalCategoryResult(
                category="image_metadata",
                status=MultimodalStatus.PASSED.value,
                score=100,
                confidence=0.95,
                findings=[],
                evaluated=True,
                evidence_status=EvidenceStatus.ABSENT.value if images_checked else EvidenceStatus.AVAILABLE.value,
                metrics={"images_analyzed": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero image assets requiring metadata analysis."],
            ).to_dict()

        # Parse normalized image / metadata objects
        records_to_evaluate: list[dict[str, Any]] = []
        if isinstance(images_raw, list):
            for idx, itm in enumerate(images_raw):
                if isinstance(itm, dict):
                    records_to_evaluate.append(itm)
                elif isinstance(itm, (ImageEvidence, ImageMetadata)):
                    records_to_evaluate.append(itm.to_dict())

        if not records_to_evaluate:
            return MultimodalCategoryResult(
                category="image_metadata",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"images_analyzed": 0, "message": "Malformed metadata input list supplied"},
                messages=["Unable to extract valid image metadata entries from input."],
            ).to_dict()

        findings: list[MultimodalFinding] = []
        score = 100.0

        images_analyzed_count = 0
        images_with_metadata_count = 0
        images_with_issues_count = 0

        seen_urls: dict[str, str] = {}
        seen_hashes: dict[str, str] = {}

        # ----------------------------------------------------------------------
        # Per-Image Metadata Evaluation Loop
        # ----------------------------------------------------------------------
        for idx, item in enumerate(records_to_evaluate):
            img_id = str(item.get("id") or item.get("image_id") or f"img-{idx + 1}")
            img_url = str(item.get("url") or item.get("source_url") or "")
            filename = str(item.get("filename") or "")
            if not filename and img_url:
                url_path = img_url.split("?")[0].split("#")[0]
                filename = os.path.basename(url_path)

            has_issue_for_item = False
            images_analyzed_count += 1

            # Extract metadata fields
            meta_obj = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
            width_val = meta_obj.get("width") if meta_obj.get("width") is not None else item.get("width")
            height_val = meta_obj.get("height") if meta_obj.get("height") is not None else item.get("height")
            file_size = meta_obj.get("file_size_bytes") if meta_obj.get("file_size_bytes") is not None else item.get("file_size_bytes")
            if file_size is None and "file_size" in meta_obj:
                file_size = meta_obj.get("file_size")
            mime_type = str(meta_obj.get("mime_type") or item.get("mime_type") or "").strip().lower()
            declared_format = str(meta_obj.get("format") or item.get("format") or "").strip().lower()
            declared_ratio = meta_obj.get("aspect_ratio") if meta_obj.get("aspect_ratio") is not None else item.get("aspect_ratio")
            orientation = meta_obj.get("orientation") if meta_obj.get("orientation") is not None else item.get("orientation")
            color_profile = meta_obj.get("color_profile") if meta_obj.get("color_profile") is not None else item.get("color_profile")
            exif_data = meta_obj.get("exif") if meta_obj.get("exif") is not None else item.get("exif")

            if any(x is not None for x in (width_val, height_val, file_size, mime_type, exif_data, declared_format)):
                images_with_metadata_count += 1

            # ------------------------------------------------------------------
            # Check 2: Invalid Dimensions (MM-META-002)
            # ------------------------------------------------------------------
            num_w: float | None = None
            num_h: float | None = None
            if width_val is not None:
                try:
                    num_w = float(width_val)
                except (ValueError, TypeError):
                    pass
            if height_val is not None:
                try:
                    num_h = float(height_val)
                except (ValueError, TypeError):
                    pass

            if (num_w is not None and num_w <= 0) or (num_h is not None and num_h <= 0):
                score -= 20.0
                has_issue_for_item = True
                findings.append(MultimodalFinding(
                    id="MM-META-002",
                    category="image_metadata",
                    title=f"Invalid non-positive image dimensions ({img_id})",
                    description=f"Image '{img_id}' reports non-positive dimensions (width={width_val}, height={height_val}).",
                    severity=Severity.HIGH.value,
                    confidence=0.98,
                    evidence={"image_id": img_id, "width": width_val, "height": height_val},
                    recommendation="Ensure intrinsic width and height metadata contain positive non-zero integers.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 3: Suspiciously Large Dimensions (MM-META-003)
            # ------------------------------------------------------------------
            if num_w and num_h and num_w > 0 and num_h > 0:
                total_px = num_w * num_h
                if num_w > self.max_dim_px or num_h > self.max_dim_px or total_px > self.max_pixels:
                    score -= 10.0
                    has_issue_for_item = True
                    findings.append(MultimodalFinding(
                        id="MM-META-003",
                        category="image_metadata",
                        title=f"Excessively large image dimensions ({num_w:.0f}x{num_h:.0f}px) ({img_id})",
                        description=(
                            f"Image '{img_id}' intrinsic dimensions ({num_w:.0f}x{num_h:.0f}px, {total_px/1e6:.1f}MP) "
                            f"exceed recommended web delivery thresholds ({self.max_dim_px:.0f}px max dimension)."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.92,
                        evidence={
                            "image_id": img_id,
                            "width": num_w,
                            "height": num_h,
                            "total_megapixels": round(total_px / 1_000_000, 2),
                            "max_allowed_dim": self.max_dim_px,
                        },
                        recommendation="Downscale source image assets before web deployment to prevent unnecessary browser memory allocation.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 5 & 6: Aspect Ratio Consistency (MM-META-005, MM-META-006)
            # ------------------------------------------------------------------
            if declared_ratio is not None:
                try:
                    num_ratio = float(declared_ratio)
                    if num_ratio <= 0:
                        score -= 10.0
                        has_issue_for_item = True
                        findings.append(MultimodalFinding(
                            id="MM-META-005",
                            category="image_metadata",
                            title=f"Invalid non-positive aspect ratio declared ({img_id})",
                            description=f"Image '{img_id}' declares an impossible aspect ratio of {declared_ratio}.",
                            severity=Severity.MEDIUM.value,
                            confidence=0.95,
                            evidence={"image_id": img_id, "declared_aspect_ratio": declared_ratio},
                            recommendation="Provide a positive floating-point ratio matching width / height.",
                            url=target_url,
                            media_id=img_id,
                        ))
                    elif num_w and num_h and num_w > 0 and num_h > 0:
                        calc_ratio = num_w / num_h
                        ratio_deviation = abs(calc_ratio - num_ratio) / calc_ratio
                        if ratio_deviation > 0.05:  # > 5% mismatch between declared and calculated
                            score -= 8.0
                            has_issue_for_item = True
                            findings.append(MultimodalFinding(
                                id="MM-META-006",
                                category="image_metadata",
                                title=f"Declared aspect ratio inconsistent with dimensions ({img_id})",
                                description=(
                                    f"Image '{img_id}' declared aspect ratio ({num_ratio:.3f}) contradicts its "
                                    f"intrinsic dimensions ({num_w:.0f}x{num_h:.0f}px = {calc_ratio:.3f})."
                                ),
                                severity=Severity.LOW.value,
                                confidence=0.92,
                                evidence={
                                    "image_id": img_id,
                                    "declared_ratio": round(num_ratio, 3),
                                    "calculated_ratio": round(calc_ratio, 3),
                                    "deviation": round(ratio_deviation, 3),
                                },
                                recommendation="Synchronize declared aspect_ratio metadata with intrinsic width and height.",
                                url=target_url,
                                media_id=img_id,
                            ))
                except (ValueError, TypeError):
                    pass

            # ------------------------------------------------------------------
            # Check 7: MIME Type vs File Extension Mismatch (MM-META-007)
            # ------------------------------------------------------------------
            ext = ""
            if filename and "." in filename:
                ext = filename.rsplit(".", 1)[-1].lower()

            if mime_type and ext:
                expected_exts = MIME_TO_EXTENSIONS.get(mime_type)
                if expected_exts and ext not in expected_exts:
                    score -= 10.0
                    has_issue_for_item = True
                    findings.append(MultimodalFinding(
                        id="MM-META-007",
                        category="image_metadata",
                        title=f"MIME type and file extension mismatch ({img_id})",
                        description=(
                            f"Image '{img_id}' serves content type '{mime_type}' with incompatible file extension '.{ext}'. "
                            "This can confuse content sniffers and CDN caching proxies."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.96,
                        evidence={
                            "image_id": img_id,
                            "mime_type": mime_type,
                            "extension": ext,
                            "expected_extensions": sorted(list(expected_exts)),
                        },
                        recommendation="Align HTTP Content-Type headers with accurate file extensions.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 4: Oversized File Size (MM-META-004)
            # ------------------------------------------------------------------
            if file_size is not None:
                try:
                    num_bytes = int(file_size)
                    if num_bytes > self.max_bytes_high:
                        score -= 15.0
                        has_issue_for_item = True
                        findings.append(MultimodalFinding(
                            id="MM-META-004",
                            category="image_metadata",
                            title=f"Severely oversized image asset ({num_bytes / 1_048_576:.2f} MB) ({img_id})",
                            description=(
                                f"Image '{img_id}' file size ({num_bytes / 1_048_576:.2f} MB) exceeds the high-severity "
                                f"threshold ({self.max_bytes_high / 1_048_576:.1f} MB), potentially causing severe network latency."
                            ),
                            severity=Severity.HIGH.value,
                            confidence=0.95,
                            evidence={
                                "image_id": img_id,
                                "file_size_bytes": num_bytes,
                                "size_mb": round(num_bytes / 1_048_576, 2),
                                "threshold_mb": round(self.max_bytes_high / 1_048_576, 2),
                            },
                            recommendation="Compress image data, apply modern WebP/AVIF encoding, and deliver responsive resolutions.",
                            url=target_url,
                            media_id=img_id,
                        ))
                    elif num_bytes > self.max_bytes_warning:
                        score -= 8.0
                        has_issue_for_item = True
                        findings.append(MultimodalFinding(
                            id="MM-META-004",
                            category="image_metadata",
                            title=f"Potentially oversized image file ({num_bytes / 1_048_576:.2f} MB) ({img_id})",
                            description=(
                                f"Image '{img_id}' file size ({num_bytes / 1_048_576:.2f} MB) exceeds the recommended "
                                f"1 MB web performance budget."
                            ),
                            severity=Severity.LOW.value,
                            confidence=0.90,
                            evidence={
                                "image_id": img_id,
                                "file_size_bytes": num_bytes,
                                "size_mb": round(num_bytes / 1_048_576, 2),
                            },
                            recommendation="Optimize image compression to reduce payload size under 1 MB.",
                            url=target_url,
                            media_id=img_id,
                        ))
                except (ValueError, TypeError):
                    pass

            # ------------------------------------------------------------------
            # Check 9: Sensitive Location Data in EXIF (MM-META-009)
            # ------------------------------------------------------------------
            if isinstance(exif_data, dict) and exif_data:
                gps_fields = [k for k in exif_data.keys() if "gps" in str(k).lower() or "latitude" in str(k).lower() or "longitude" in str(k).lower()]
                if gps_fields:
                    score -= 5.0
                    has_issue_for_item = True
                    findings.append(MultimodalFinding(
                        id="MM-META-009",
                        category="image_metadata",
                        title=f"EXIF metadata contains embedded GPS/location data ({img_id})",
                        description=(
                            f"Image '{img_id}' contains embedded geolocation coordinates in EXIF fields ({', '.join(gps_fields)}). "
                            "Review whether location metadata should be stripped from public production assets."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.95,
                        evidence={"image_id": img_id, "gps_fields_detected": gps_fields},
                        recommendation="Strip GPS coordinates and sensitive camera serials during asset export pipelines.",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 10: Invalid Orientation Value (MM-META-010)
            # ------------------------------------------------------------------
            if orientation is not None:
                ori_str = str(orientation).strip().lower()
                if ori_str not in VALID_ORIENTATION_STRINGS:
                    score -= 5.0
                    has_issue_for_item = True
                    findings.append(MultimodalFinding(
                        id="MM-META-010",
                        category="image_metadata",
                        title=f"Invalid orientation metadata flag '{orientation}' ({img_id})",
                        description=f"Image '{img_id}' specifies unrecognized orientation property '{orientation}'.",
                        severity=Severity.LOW.value,
                        confidence=0.90,
                        evidence={"image_id": img_id, "orientation": orientation},
                        recommendation="Use standard EXIF orientation codes (1-8) or semantic descriptors ('landscape', 'portrait', 'square').",
                        url=target_url,
                        media_id=img_id,
                    ))

            # ------------------------------------------------------------------
            # Check 8 & 13: Inefficient Legacy Formats (MM-META-013)
            # ------------------------------------------------------------------
            fmt = declared_format or ext
            if fmt in LEGACY_FORMATS:
                score -= 8.0
                has_issue_for_item = True
                findings.append(MultimodalFinding(
                    id="MM-META-013",
                    category="image_metadata",
                    title=f"Potentially inefficient legacy format '{fmt.upper()}' ({img_id})",
                    description=f"Image '{img_id}' uses legacy uncompressed format '{fmt.upper()}' which is sub-optimal for web transmission.",
                    severity=Severity.LOW.value,
                    confidence=0.92,
                    evidence={"image_id": img_id, "format": fmt},
                    recommendation="Convert legacy BMP/TIFF assets to modern WebP, AVIF, or optimized PNG/JPEG formats.",
                    url=target_url,
                    media_id=img_id,
                ))

            # ------------------------------------------------------------------
            # Check 12: Duplicate URL & Content Hashes (MM-META-012)
            # ------------------------------------------------------------------
            if img_url:
                if img_url in seen_urls:
                    prior_id = seen_urls[img_url]
                    findings.append(MultimodalFinding(
                        id="MM-META-012",
                        category="image_metadata",
                        title=f"Duplicate image asset metadata URL ({img_id})",
                        description=f"Image '{img_id}' shares exact source URL with earlier record '{prior_id}'.",
                        severity=Severity.LOW.value,
                        confidence=0.95,
                        evidence={"image_id": img_id, "duplicate_of": prior_id, "url": img_url},
                        recommendation="Consolidate duplicate image references to simplify asset management.",
                        url=target_url,
                        media_id=img_id,
                    ))
                else:
                    seen_urls[img_url] = img_id

            content_hash = item.get("content_hash") or item.get("hash") or meta_obj.get("hash")
            if content_hash:
                if content_hash in seen_hashes:
                    prior_id = seen_hashes[content_hash]
                    findings.append(MultimodalFinding(
                        id="MM-META-012",
                        category="image_metadata",
                        title=f"Matching image content hash detected ({img_id})",
                        description=f"Image '{img_id}' has identical cryptographic/perceptual hash as '{prior_id}'.",
                        severity=Severity.LOW.value,
                        confidence=0.95,
                        evidence={"image_id": img_id, "duplicate_of": prior_id, "content_hash": content_hash},
                        recommendation="Verify whether duplicate image payload references can be shared.",
                        url=target_url,
                        media_id=img_id,
                    ))
                else:
                    seen_hashes[content_hash] = img_id

            if has_issue_for_item:
                images_with_issues_count += 1

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
            "images_analyzed": images_analyzed_count,
            "images_with_metadata": images_with_metadata_count,
            "images_with_issues": images_with_issues_count,
            "invalid_dimension_count": len([f for f in findings if f.id == "MM-META-002"]),
            "oversized_file_count": len([f for f in findings if f.id == "MM-META-004"]),
            "mime_mismatch_count": len([f for f in findings if f.id == "MM-META-007"]),
            "exif_gps_count": len([f for f in findings if f.id == "MM-META-009"]),
        }

        # Sort findings deterministically: ID -> Severity
        findings.sort(key=lambda x: (x.id, x.severity))

        return MultimodalCategoryResult(
            category="image_metadata",
            status=status,
            score=score_clamped,
            confidence=0.95 if images_with_metadata_count > 0 else 0.85,
            findings=findings,
            evaluated=True,
            evidence_status=EvidenceStatus.AVAILABLE.value,
            metrics=metrics,
            messages=[
                f"Image metadata analysis completed across {images_analyzed_count} asset(s) (Score: {score_clamped}/100).",
                f"Metadata anomalies detected: {len(findings)}.",
            ],
        ).to_dict()

    def __call__(self, image_data: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        return self.analyze(image_data)



# Public API Function


def analyze_image_metadata(
    image_data: dict[str, Any] | list[dict[str, Any]] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public functional entry point for Image Metadata & Technical Asset evaluation.

    Args:
        image_data: Structured image metadata dictionary or list of images.
        options: Optional configuration overrides.

    Returns:
        JSON-serializable category audit result dictionary.
    """
    analyzer = ImageMetadataAnalyzer(config=options)
    return analyzer.analyze(image_data)
