"""
Multimodal Audit State & Data Contracts (multimodal_state.py)
=============================================================
Shared typed data models, enums, validation functions, and serialization helpers
for the independent Multimodal Audit subagent.

This module defines the standardized evidence schemas and audit state contracts
shared across all specialized media analyzers:
- image_analyzer.py
- alt_text_analyzer.py
- image_ocr_analyzer.py
- image_metadata_analyzer.py
- chart_infographic_analyzer.py
- video_analyzer.py
- transcript_analyzer.py
- caption_analyzer.py
- multimodal_audit.py (Coordinator)

Design Principles:
- Data Only: Contains purely structured dataclasses, enums, and validation helpers.
  Does NOT perform image processing, OCR, or heuristic quality analysis.
- Unknown vs. Absent: Distinguishes between uncollected/missing evidence (UNKNOWN)
  and explicitly verified non-existence (ABSENT).
- Pure Serialization: Completely JSON-serializable to standard Python dicts/lists.
- LangGraph Ready: Stateless and immutable data models suitable for graph workflows.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence



# Enumerations


class MultimodalStatus(str, Enum):
    """Execution and evaluation status for multimodal categories and overall audit."""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


class Severity(str, Enum):
    """Impact and priority severity levels for multimodal audit findings."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    def __str__(self) -> str:
        return self.value


class MediaType(str, Enum):
    """Classifies the primary modality and representation format of media assets."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    CHART = "chart"
    INFOGRAPHIC = "infographic"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


class EvidenceStatus(str, Enum):
    """
    Evidence availability classification:
    - AVAILABLE: Evidence was actively collected and provided.
    - ABSENT: Checked by upstream pipeline and confirmed to not exist on the page.
    - UNKNOWN: Evidence was not collected or was omitted from the audit input.
    """
    AVAILABLE = "available"
    ABSENT = "absent"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value



# Lightweight Validation Functions


def validate_score(score: Any) -> int | None:
    """Validate and clamp numeric audit score between 0 and 100."""
    if score is None:
        return None
    try:
        val = int(round(float(score)))
        return max(0, min(100, val))
    except (ValueError, TypeError):
        return None


def validate_confidence(confidence: Any) -> float:
    """Validate and clamp confidence score between 0.0 and 1.0."""
    if confidence is None:
        return 0.85
    try:
        val = float(confidence)
        return max(0.0, min(1.0, val))
    except (ValueError, TypeError):
        return 0.85


def validate_severity(severity: Any) -> str:
    """Validate and normalize severity level string."""
    if isinstance(severity, Severity):
        return severity.value
    if isinstance(severity, str):
        s_lower = severity.strip().lower()
        if s_lower in ("critical", "high", "medium", "low", "info"):
            return s_lower
    return Severity.MEDIUM.value


def validate_status(status: Any) -> str:
    """Validate and normalize status string."""
    if isinstance(status, MultimodalStatus):
        return status.value
    if isinstance(status, str):
        st_lower = status.strip().lower()
        if st_lower in ("passed", "failed", "warning", "insufficient_evidence", "error"):
            return st_lower
    return MultimodalStatus.INSUFFICIENT_EVIDENCE.value


def validate_evidence_status(status: Any) -> str:
    """Validate and normalize evidence status."""
    if isinstance(status, EvidenceStatus):
        return status.value
    if isinstance(status, str):
        st_lower = status.strip().lower()
        if st_lower in ("available", "absent", "unknown"):
            return st_lower
    return EvidenceStatus.UNKNOWN.value



# Geometry & Spatial Positioning


@dataclass
class BoundingBox:
    """2D Geometric spatial bounding box of a media asset in viewport pixels."""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def __post_init__(self) -> None:
        self.x = float(self.x)
        self.y = float(self.y)
        self.width = max(0.0, float(self.width))
        self.height = max(0.0, float(self.height))

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "right": round(self.right, 2),
            "bottom": round(self.bottom, 2),
        }

    @classmethod
    def from_dict(cls, data: Any) -> BoundingBox | None:
        if not data or not isinstance(data, dict):
            return None
        return cls(
            x=float(data.get("x", 0.0) or 0.0),
            y=float(data.get("y", 0.0) or 0.0),
            width=float(data.get("width", 0.0) or 0.0),
            height=float(data.get("height", 0.0) or 0.0),
        )



# Specialized Media Evidence Models


@dataclass
class ImageMetadata:
    """Technical formatting and header metadata for an image asset."""
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    color_mode: str | None = None
    orientation: str | None = None
    exif_present: bool = False
    exif_fields: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    modified_at: str | None = None
    source: str = "rendered_dom"
    metadata_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "color_mode": self.color_mode,
            "orientation": self.orientation,
            "exif_present": self.exif_present,
            "exif_fields": self.exif_fields,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "source": self.source,
            "metadata_available": self.metadata_available,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ImageMetadata:
        if not isinstance(data, dict):
            return cls(metadata_available=False)
        return cls(
            width=int(data["width"]) if data.get("width") is not None else None,
            height=int(data["height"]) if data.get("height") is not None else None,
            format=str(data["format"]) if data.get("format") else None,
            mime_type=str(data["mime_type"]) if data.get("mime_type") else None,
            file_size=int(data["file_size"]) if data.get("file_size") is not None else None,
            color_mode=str(data["color_mode"]) if data.get("color_mode") else None,
            orientation=str(data["orientation"]) if data.get("orientation") else None,
            exif_present=bool(data.get("exif_present", False)),
            exif_fields=dict(data.get("exif_fields") or {}),
            created_at=str(data["created_at"]) if data.get("created_at") else None,
            modified_at=str(data["modified_at"]) if data.get("modified_at") else None,
            source=str(data.get("source") or "rendered_dom"),
            metadata_available=bool(data.get("metadata_available", True)),
        )


@dataclass
class ImageEvidence:
    """Structured representation of an image element discovered on a webpage."""
    id: str
    url: str | None = None
    source_url: str | None = None
    alt_text: str | None = None
    title: str | None = None
    width: float | None = None
    height: float | None = None
    format: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    visible: bool = True
    decorative: bool = False
    rendered: bool = True
    bounding_box: BoundingBox | None = None
    page_url: str | None = None
    evidence_status: str = EvidenceStatus.AVAILABLE.value
    metadata: ImageMetadata | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "source_url": self.source_url or self.url,
            "alt_text": self.alt_text,
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "visible": self.visible,
            "decorative": self.decorative,
            "rendered": self.rendered,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "page_url": self.page_url,
            "evidence_status": validate_evidence_status(self.evidence_status),
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ImageEvidence:
        if not isinstance(data, dict):
            return cls(id="img-unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        bbox = BoundingBox.from_dict(data.get("bounding_box") or data.get("bbox"))
        meta = ImageMetadata.from_dict(data.get("metadata")) if data.get("metadata") else None
        return cls(
            id=str(data.get("id") or "img-unknown"),
            url=str(data["url"]) if data.get("url") else None,
            source_url=str(data["source_url"]) if data.get("source_url") else None,
            alt_text=data.get("alt_text") if data.get("alt_text") is not None else data.get("alt"),
            title=str(data["title"]) if data.get("title") else None,
            width=float(data["width"]) if data.get("width") is not None else None,
            height=float(data["height"]) if data.get("height") is not None else None,
            format=str(data["format"]) if data.get("format") else None,
            mime_type=str(data["mime_type"]) if data.get("mime_type") else None,
            file_size=int(data["file_size"]) if data.get("file_size") is not None else None,
            visible=bool(data.get("visible", True)),
            decorative=bool(data.get("decorative") or data.get("is_decorative", False)),
            rendered=bool(data.get("rendered", True)),
            bounding_box=bbox,
            page_url=str(data["page_url"]) if data.get("page_url") else None,
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
            metadata=meta,
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class AltTextEvidence:
    """Alternative text observation attached to an image or graphic element."""
    image_id: str
    alt_text: str | None = None
    alt_attribute_present: bool = True
    alt_text_length: int = 0
    decorative: bool = False
    aria_label: str | None = None
    role: str | None = None
    source: str = "html_attribute"
    confidence: float = 1.0
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def __post_init__(self) -> None:
        if self.alt_text is not None:
            self.alt_text_length = len(str(self.alt_text).strip())
        else:
            self.alt_text_length = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "alt_text": self.alt_text,
            "alt_attribute_present": self.alt_attribute_present,
            "alt_text_length": self.alt_text_length,
            "decorative": self.decorative,
            "aria_label": self.aria_label,
            "role": self.role,
            "source": self.source,
            "confidence": round(validate_confidence(self.confidence), 2),
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> AltTextEvidence:
        if not isinstance(data, dict):
            return cls(image_id="unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        alt_str = data.get("alt_text") if data.get("alt_text") is not None else data.get("alt")
        return cls(
            image_id=str(data.get("image_id") or data.get("id") or "unknown"),
            alt_text=str(alt_str) if alt_str is not None else None,
            alt_attribute_present=bool(data.get("alt_attribute_present", alt_str is not None)),
            decorative=bool(data.get("decorative") or data.get("is_decorative", False)),
            aria_label=str(data["aria_label"]) if data.get("aria_label") else None,
            role=str(data["role"]) if data.get("role") else None,
            source=str(data.get("source") or "html_attribute"),
            confidence=float(data.get("confidence", 1.0)),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class OCRTextRegion:
    """Bounding box region and recognized text snippet extracted via OCR."""
    text: str
    confidence: float = 0.95
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(validate_confidence(self.confidence), 2),
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
        }

    @classmethod
    def from_dict(cls, data: Any) -> OCRTextRegion:
        if not isinstance(data, dict):
            return cls(text="")
        return cls(
            text=str(data.get("text") or ""),
            confidence=float(data.get("confidence", 0.95)),
            x=float(data.get("x", 0.0) or 0.0),
            y=float(data.get("y", 0.0) or 0.0),
            width=float(data.get("width", 0.0) or 0.0),
            height=float(data.get("height", 0.0) or 0.0),
        )


@dataclass
class OCREvidence:
    """Optical Character Recognition (OCR) extracted text observation from an image."""
    image_id: str
    text: str = ""
    confidence: float = 0.90
    language: str = "en"
    text_regions: list[OCRTextRegion] = field(default_factory=list)
    source: str = "ocr_engine"
    detected: bool = True
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "text": self.text,
            "confidence": round(validate_confidence(self.confidence), 2),
            "language": self.language,
            "text_regions": [r.to_dict() if isinstance(r, OCRTextRegion) else r for r in self.text_regions],
            "source": self.source,
            "detected": self.detected,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> OCREvidence:
        if not isinstance(data, dict):
            return cls(image_id="unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        regions_raw = data.get("text_regions", [])
        regions = [OCRTextRegion.from_dict(r) for r in regions_raw if isinstance(r, dict)]
        return cls(
            image_id=str(data.get("image_id") or data.get("id") or "unknown"),
            text=str(data.get("text") or ""),
            confidence=float(data.get("confidence", 0.90)),
            language=str(data.get("language") or "en"),
            text_regions=regions,
            source=str(data.get("source") or "ocr_engine"),
            detected=bool(data.get("detected", True)),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class ChartEvidence:
    """Data visualization chart asset observation."""
    id: str
    image_id: str | None = None
    chart_type: str = "unknown"  # "bar", "line", "pie", "scatter", "histogram", etc.
    title: str | None = None
    description: str | None = None
    labels: list[str] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)
    legend: list[str] = field(default_factory=list)
    source: str = "svg_or_image"
    visible: bool = True
    text_content: str | None = None
    structured_data: dict[str, Any] = field(default_factory=dict)
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "image_id": self.image_id,
            "chart_type": self.chart_type,
            "title": self.title,
            "description": self.description,
            "labels": self.labels,
            "values": self.values,
            "legend": self.legend,
            "source": self.source,
            "visible": self.visible,
            "text_content": self.text_content,
            "structured_data": self.structured_data,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> ChartEvidence:
        if not isinstance(data, dict):
            return cls(id="chart-unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        return cls(
            id=str(data.get("id") or "chart-unknown"),
            image_id=str(data["image_id"]) if data.get("image_id") else None,
            chart_type=str(data.get("chart_type") or "unknown"),
            title=str(data["title"]) if data.get("title") else None,
            description=str(data["description"]) if data.get("description") else None,
            labels=list(data.get("labels") or []),
            values=list(data.get("values") or []),
            legend=list(data.get("legend") or []),
            source=str(data.get("source") or "svg_or_image"),
            visible=bool(data.get("visible", True)),
            text_content=str(data["text_content"]) if data.get("text_content") else None,
            structured_data=dict(data.get("structured_data") or {}),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class InfographicEvidence:
    """Complex multi-panel infographic asset observation."""
    id: str
    image_id: str | None = None
    title: str | None = None
    description: str | None = None
    text_content: str | None = None
    sections: list[dict[str, Any]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    source: str = "visual_asset"
    visible: bool = True
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "image_id": self.image_id,
            "title": self.title,
            "description": self.description,
            "text_content": self.text_content,
            "sections": self.sections,
            "labels": self.labels,
            "source": self.source,
            "visible": self.visible,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> InfographicEvidence:
        if not isinstance(data, dict):
            return cls(id="info-unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        return cls(
            id=str(data.get("id") or "info-unknown"),
            image_id=str(data["image_id"]) if data.get("image_id") else None,
            title=str(data["title"]) if data.get("title") else None,
            description=str(data["description"]) if data.get("description") else None,
            text_content=str(data["text_content"]) if data.get("text_content") else None,
            sections=list(data.get("sections") or []),
            labels=list(data.get("labels") or []),
            source=str(data.get("source") or "visual_asset"),
            visible=bool(data.get("visible", True)),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class VideoEvidence:
    """Video media stream or embedded player asset observation."""
    id: str
    url: str | None = None
    source_url: str | None = None
    title: str | None = None
    duration_seconds: float | None = None
    width: float | None = None
    height: float | None = None
    format: str | None = None
    mime_type: str | None = None
    poster_url: str | None = None
    visible: bool = True
    autoplay: bool = False
    controls_present: bool = True
    muted: bool = False
    transcript_available: bool | None = None
    captions_available: bool | None = None
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "source_url": self.source_url or self.url,
            "title": self.title,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mime_type": self.mime_type,
            "poster_url": self.poster_url,
            "visible": self.visible,
            "autoplay": self.autoplay,
            "controls_present": self.controls_present,
            "muted": self.muted,
            "transcript_available": self.transcript_available,
            "captions_available": self.captions_available,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> VideoEvidence:
        if not isinstance(data, dict):
            return cls(id="vid-unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        return cls(
            id=str(data.get("id") or "vid-unknown"),
            url=str(data["url"]) if data.get("url") else None,
            source_url=str(data["source_url"]) if data.get("source_url") else None,
            title=str(data["title"]) if data.get("title") else None,
            duration_seconds=float(data["duration_seconds"]) if data.get("duration_seconds") is not None else None,
            width=float(data["width"]) if data.get("width") is not None else None,
            height=float(data["height"]) if data.get("height") is not None else None,
            format=str(data["format"]) if data.get("format") else None,
            mime_type=str(data["mime_type"]) if data.get("mime_type") else None,
            poster_url=str(data["poster_url"]) if data.get("poster_url") else None,
            visible=bool(data.get("visible", True)),
            autoplay=bool(data.get("autoplay", False)),
            controls_present=bool(data.get("controls_present", True)),
            muted=bool(data.get("muted", False)),
            transcript_available=bool(data["transcript_available"]) if data.get("transcript_available") is not None else None,
            captions_available=bool(data["captions_available"]) if data.get("captions_available") is not None else None,
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class TranscriptEvidence:
    """Text transcript associated with audio or video recordings."""
    video_id: str
    available: bool = True
    text: str = ""
    language: str = "en"
    duration_seconds: float | None = None
    source: str = "vtt_or_api"
    word_count: int = 0
    timestamped: bool = False
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def __post_init__(self) -> None:
        if self.text and not self.word_count:
            self.word_count = len(self.text.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "available": self.available,
            "text": self.text,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "source": self.source,
            "word_count": self.word_count,
            "timestamped": self.timestamped,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> TranscriptEvidence:
        if not isinstance(data, dict):
            return cls(video_id="unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        return cls(
            video_id=str(data.get("video_id") or data.get("id") or "unknown"),
            available=bool(data.get("available", True)),
            text=str(data.get("text") or ""),
            language=str(data.get("language") or "en"),
            duration_seconds=float(data["duration_seconds"]) if data.get("duration_seconds") is not None else None,
            source=str(data.get("source") or "vtt_or_api"),
            word_count=int(data.get("word_count") or 0),
            timestamped=bool(data.get("timestamped", False)),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class CaptionEvidence:
    """Closed captions or subtitle tracks attached to a video asset."""
    video_id: str
    available: bool = True
    language: str = "en"
    text: str = ""
    source: str = "track_element"
    synchronized: bool = True
    format: str = "vtt"  # "vtt", "srt", "ttml"
    evidence_status: str = EvidenceStatus.AVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "available": self.available,
            "language": self.language,
            "text": self.text,
            "source": self.source,
            "synchronized": self.synchronized,
            "format": self.format,
            "evidence_status": validate_evidence_status(self.evidence_status),
        }

    @classmethod
    def from_dict(cls, data: Any) -> CaptionEvidence:
        if not isinstance(data, dict):
            return cls(video_id="unknown", evidence_status=EvidenceStatus.UNKNOWN.value)
        return cls(
            video_id=str(data.get("video_id") or data.get("id") or "unknown"),
            available=bool(data.get("available", True)),
            language=str(data.get("language") or "en"),
            text=str(data.get("text") or ""),
            source=str(data.get("source") or "track_element"),
            synchronized=bool(data.get("synchronized", True)),
            format=str(data.get("format") or "vtt"),
            evidence_status=validate_evidence_status(data.get("evidence_status", EvidenceStatus.AVAILABLE.value)),
        )


@dataclass
class MediaElement:
    """Generic unified media asset container."""
    id: str
    media_type: str = MediaType.IMAGE.value
    url: str | None = None
    page_url: str | None = None
    visible: bool = True
    width: float | None = None
    height: float | None = None
    bounding_box: BoundingBox | None = None
    evidence_status: str = EvidenceStatus.AVAILABLE.value
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "media_type": self.media_type,
            "url": self.url,
            "page_url": self.page_url,
            "visible": self.visible,
            "width": self.width,
            "height": self.height,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "evidence_status": validate_evidence_status(self.evidence_status),
            "metadata": self.metadata,
        }



# Finding & Audit Results Data Models


@dataclass
class MultimodalFinding:
    """Individual evidence-backed issue or recommendation discovered during multimodal audit."""
    id: str
    category: str
    title: str
    description: str
    severity: str = Severity.MEDIUM.value
    confidence: float = 0.85
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    url: str = ""
    media_id: str | None = None
    analyzer: str | None = None
    source: str = "multimodal_analyzer"
    rule: str | None = None
    evidence_status: str = EvidenceStatus.AVAILABLE.value
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.severity = validate_severity(self.severity)
        self.confidence = validate_confidence(self.confidence)
        self.evidence_status = validate_evidence_status(self.evidence_status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "url": self.url,
            "media_id": self.media_id,
            "analyzer": self.analyzer,
            "source": self.source,
            "rule": self.rule,
            "evidence_status": self.evidence_status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MultimodalFinding:
        if not isinstance(data, dict):
            return cls(id="MM-AUDIT-000", category="multimodal", title="Malformed finding", description="")
        return cls(
            id=str(data.get("id") or "MM-AUDIT-000"),
            category=str(data.get("category") or "multimodal"),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            severity=validate_severity(data.get("severity")),
            confidence=validate_confidence(data.get("confidence")),
            evidence=dict(data.get("evidence") or {}),
            recommendation=str(data.get("recommendation") or ""),
            url=str(data.get("url") or ""),
            media_id=str(data["media_id"]) if data.get("media_id") else None,
            analyzer=str(data["analyzer"]) if data.get("analyzer") else None,
            source=str(data.get("source") or "multimodal_analyzer"),
            rule=str(data["rule"]) if data.get("rule") else None,
            evidence_status=validate_evidence_status(data.get("evidence_status")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class MultimodalCategoryResult:
    """Evaluation result for one specialized multimodal category."""
    category: str
    status: str = MultimodalStatus.INSUFFICIENT_EVIDENCE.value
    score: int | None = None
    confidence: float = 0.5
    findings: list[MultimodalFinding] = field(default_factory=list)
    evaluated: bool = False
    evidence_status: str = EvidenceStatus.AVAILABLE.value
    metrics: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = validate_status(self.status)
        self.score = validate_score(self.score)
        self.confidence = validate_confidence(self.confidence)
        self.evidence_status = validate_evidence_status(self.evidence_status)
        if self.score is not None and self.status != MultimodalStatus.INSUFFICIENT_EVIDENCE.value:
            self.evaluated = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "status": self.status,
            "score": self.score,
            "confidence": round(self.confidence, 2),
            "findings": [f.to_dict() if isinstance(f, MultimodalFinding) else f for f in self.findings],
            "evaluated": self.evaluated,
            "evidence_status": self.evidence_status,
            "metrics": self.metrics,
            "messages": self.messages,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MultimodalCategoryResult:
        if not isinstance(data, dict):
            return cls(category="unknown", status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value)
        raw_f = data.get("findings", [])
        findings = [MultimodalFinding.from_dict(f) if isinstance(f, dict) else f for f in raw_f]
        return cls(
            category=str(data.get("category") or "unknown"),
            status=validate_status(data.get("status")),
            score=validate_score(data.get("score")),
            confidence=validate_confidence(data.get("confidence")),
            findings=findings,
            evaluated=bool(data.get("evaluated", data.get("score") is not None)),
            evidence_status=validate_evidence_status(data.get("evidence_status")),
            metrics=dict(data.get("metrics") or {}),
            messages=list(data.get("messages") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class MultimodalPageContext:
    """High-level target page context supplied to the audit subagent."""
    url: str
    title: str | None = None
    page_id: str | None = None
    source: str = "audit_orchestrator"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "page_id": self.page_id,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class MultimodalAuditState:
    """
    Complete state container representing the multimodal audit session, input evidence,
    individual category results, and aggregated findings.
    """
    url: str
    page_id: str | None = None
    page_context: MultimodalPageContext | None = None
    images: list[ImageEvidence] = field(default_factory=list)
    alt_text: list[AltTextEvidence] = field(default_factory=list)
    ocr: list[OCREvidence] = field(default_factory=list)
    image_metadata: list[ImageMetadata] = field(default_factory=list)
    charts: list[ChartEvidence] = field(default_factory=list)
    infographics: list[InfographicEvidence] = field(default_factory=list)
    videos: list[VideoEvidence] = field(default_factory=list)
    transcripts: list[TranscriptEvidence] = field(default_factory=list)
    captions: list[CaptionEvidence] = field(default_factory=list)
    category_results: dict[str, MultimodalCategoryResult] = field(default_factory=dict)
    findings: list[MultimodalFinding] = field(default_factory=list)
    score: int | None = None
    confidence: float = 0.5
    status: str = MultimodalStatus.INSUFFICIENT_EVIDENCE.value
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = validate_status(self.status)
        self.score = validate_score(self.score)
        self.confidence = validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_id": self.page_id,
            "page_context": self.page_context.to_dict() if self.page_context else None,
            "images": [i.to_dict() if isinstance(i, ImageEvidence) else i for i in self.images],
            "alt_text": [a.to_dict() if isinstance(a, AltTextEvidence) else a for a in self.alt_text],
            "ocr": [o.to_dict() if isinstance(o, OCREvidence) else o for o in self.ocr],
            "image_metadata": [m.to_dict() if isinstance(m, ImageMetadata) else m for m in self.image_metadata],
            "charts": [c.to_dict() if isinstance(c, ChartEvidence) else c for c in self.charts],
            "infographics": [info.to_dict() if isinstance(info, InfographicEvidence) else info for info in self.infographics],
            "videos": [v.to_dict() if isinstance(v, VideoEvidence) else v for v in self.videos],
            "transcripts": [t.to_dict() if isinstance(t, TranscriptEvidence) else t for t in self.transcripts],
            "captions": [cap.to_dict() if isinstance(cap, CaptionEvidence) else cap for cap in self.captions],
            "category_results": {k: v.to_dict() if isinstance(v, MultimodalCategoryResult) else v for k, v in self.category_results.items()},
            "findings": [f.to_dict() if isinstance(f, MultimodalFinding) else f for f in self.findings],
            "score": self.score,
            "confidence": round(self.confidence, 2),
            "status": self.status,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize complete audit state to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Any) -> MultimodalAuditState:
        if not isinstance(data, dict):
            return cls(url="https://example.com")

        p_ctx = MultimodalPageContext(url=data.get("url", "https://example.com"), title=data.get("title")) if data.get("title") else None
        return cls(
            url=str(data.get("url") or "https://example.com"),
            page_id=str(data["page_id"]) if data.get("page_id") else None,
            page_context=p_ctx,
            images=[ImageEvidence.from_dict(i) for i in data.get("images", []) if isinstance(i, dict)],
            alt_text=[AltTextEvidence.from_dict(a) for a in data.get("alt_text", []) if isinstance(a, dict)],
            ocr=[OCREvidence.from_dict(o) for o in data.get("ocr", []) if isinstance(o, dict)],
            image_metadata=[ImageMetadata.from_dict(m) for m in data.get("image_metadata", []) if isinstance(m, dict)],
            charts=[ChartEvidence.from_dict(c) for c in data.get("charts", []) if isinstance(c, dict)],
            infographics=[InfographicEvidence.from_dict(info) for info in data.get("infographics", []) if isinstance(info, dict)],
            videos=[VideoEvidence.from_dict(v) for v in data.get("videos", []) if isinstance(v, dict)],
            transcripts=[TranscriptEvidence.from_dict(t) for t in data.get("transcripts", []) if isinstance(t, dict)],
            captions=[CaptionEvidence.from_dict(cap) for cap in data.get("captions", []) if isinstance(cap, dict)],
            category_results={
                k: MultimodalCategoryResult.from_dict(v)
                for k, v in data.get("category_results", {}).items()
                if isinstance(v, dict)
            },
            findings=[MultimodalFinding.from_dict(f) for f in data.get("findings", []) if isinstance(f, dict)],
            score=validate_score(data.get("score")),
            confidence=validate_confidence(data.get("confidence")),
            status=validate_status(data.get("status")),
            metadata=dict(data.get("metadata") or {}),
        )
