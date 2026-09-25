"""
Chart, Graph, & Infographic Visual Content Analyzer (chart_infographic_analyzer.py)
=====================================================================================
Specialized deterministic analyzer within the independent Multimodal Audit subagent.

This module evaluates charts, statistical data graphics, process flowcharts,
diagrams, embedded tables, and infographics based on structured visual evidence.

Key Capabilities:
- Chart type classification (bar, column, line, pie, donut, scatter, area, diagram).
- Validates structural components: titles, axis labels, legends, units, data labels.
- Detects visual-only information dependencies lacking accessible text equivalents.
- Checks data consistency (e.g. category label count vs numeric value count).
- Evaluates infographic visual hierarchy, section groupings, and flow sequences.
- Flags embedded raster tables lacking semantic HTML table equivalents.
- Context-aware compensation using surrounding headings, captions, and summaries.

Strict Architectural Boundaries:
- Focuses exclusively on CHARTS, INFOGRAPHICS, & STRUCTURED VISUAL INFORMATION.
- Does NOT perform OCR detection internally (delegated to image_ocr_analyzer.py).
- Does NOT download images or make network requests.
- Does NOT perform generic image visual quality analysis (delegated to image_analyzer.py).
- Does NOT evaluate alt-text quality (delegated to alt_text_analyzer.py).
- Does NOT inspect EXIF / hidden metadata (delegated to image_metadata_analyzer.py).
- Does NOT analyze videos or captions (delegated to video/caption analyzers).
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from multimodal_state import (
    ChartEvidence,
    EvidenceStatus,
    ImageEvidence,
    InfographicEvidence,
    MultimodalCategoryResult,
    MultimodalFinding,
    MultimodalStatus,
    Severity,
    validate_confidence,
    validate_score,
)

logger = logging.getLogger("multimodal_audit.chart")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Normalized chart types
AXIS_BASED_CHART_TYPES: set[str] = {"bar", "column", "line", "area", "scatter", "histogram", "combo"}
RADIAL_CHART_TYPES: set[str] = {"pie", "donut", "radar", "polar"}
DIAGRAM_TYPES: set[str] = {"diagram", "flowchart", "process", "timeline", "organization_chart", "map"}
TABLE_TYPES: set[str] = {"table", "embedded_table", "tabular"}



# Chart & Infographic Analyzer Engine


class ChartInfographicAnalyzer:
    """
    Main evaluation engine for charts, data visualizations, process diagrams,
    and business infographics.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.max_pie_slices_warning = int(self.config.get("max_pie_slices_warning", 10))

    def analyze(self, evidence: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        """
        Analyze structured chart/infographic evidence and return a JSON-serializable MultimodalCategoryResult.
        """
        raw_dict: dict[str, Any] = {}
        if isinstance(evidence, list):
            raw_dict = {"charts": evidence}
        elif isinstance(evidence, dict):
            raw_dict = evidence

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        page_context = raw_dict.get("page_context") or raw_dict.get("page") or {}
        logger.info("Starting chart & infographic analysis for %s", target_url)

        # ----------------------------------------------------------------------
        # Check 1: Evidence Availability & Routing
        # ----------------------------------------------------------------------
        items_raw = None
        for k in ("charts", "infographics", "diagrams", "data_visualizations", "visuals"):
            if k in raw_dict and raw_dict[k] is not None:
                items_raw = raw_dict[k]
                break

        # Check nested "chart" / "infographic" objects in "images" or "media"
        if items_raw is None and "images" in raw_dict and isinstance(raw_dict["images"], list):
            chart_candidates = []
            for img in raw_dict["images"]:
                if isinstance(img, dict):
                    role = str(img.get("role") or "").lower()
                    media_type = str(img.get("media_type") or "").lower()
                    if role in ("chart", "infographic", "diagram", "graph") or media_type in ("chart", "infographic", "diagram") or "chart" in img or "infographic" in img:
                        chart_candidates.append(img)
            if chart_candidates:
                items_raw = chart_candidates

        # Single flat chart/infographic record passed directly
        if items_raw is None and any(k in raw_dict for k in ("chart_type", "infographic_type", "has_axis_labels", "has_legend", "labels", "values")):
            items_raw = [raw_dict]

        charts_checked = bool(raw_dict.get("charts_checked", False) or raw_dict.get("visuals_checked", False))

        if items_raw is None:
            return MultimodalCategoryResult(
                category="chart_infographic",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"visuals_analyzed": 0, "message": "No chart or infographic evidence supplied in input"},
                messages=["Chart and infographic evidence was not provided in the audit input."],
            ).to_dict()

        if isinstance(items_raw, list) and not items_raw:
            return MultimodalCategoryResult(
                category="chart_infographic",
                status=MultimodalStatus.PASSED.value,
                score=100,
                confidence=0.95,
                findings=[],
                evaluated=True,
                evidence_status=EvidenceStatus.ABSENT.value if charts_checked else EvidenceStatus.AVAILABLE.value,
                metrics={"visuals_analyzed": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero charts or infographics on the page."],
            ).to_dict()

        records_to_evaluate: list[dict[str, Any]] = []
        if isinstance(items_raw, list):
            for idx, itm in enumerate(items_raw):
                if isinstance(itm, dict):
                    records_to_evaluate.append(itm)
                elif isinstance(itm, (ChartEvidence, InfographicEvidence)):
                    records_to_evaluate.append(itm.to_dict())

        if not records_to_evaluate:
            return MultimodalCategoryResult(
                category="chart_infographic",
                status=MultimodalStatus.INSUFFICIENT_EVIDENCE.value,
                score=None,
                confidence=0.5,
                findings=[],
                evaluated=False,
                evidence_status=EvidenceStatus.UNKNOWN.value,
                metrics={"visuals_analyzed": 0, "message": "Malformed visual items list supplied"},
                messages=["Unable to extract valid chart/infographic records from input."],
            ).to_dict()

        findings: list[MultimodalFinding] = []
        score = 100.0

        charts_count = 0
        infographics_count = 0
        diagrams_count = 0
        tables_count = 0
        issues_count = 0

        # ----------------------------------------------------------------------
        # Per-Visual Item Evaluation Loop
        # ----------------------------------------------------------------------
        for idx, item in enumerate(records_to_evaluate):
            media_id = str(item.get("media_id") or item.get("id") or item.get("image_id") or f"chart-{idx + 1}")
            img_url = str(item.get("url") or item.get("source_url") or "")

            # Extract chart or infographic sub-object
            chart_obj = item.get("chart") if isinstance(item.get("chart"), dict) else item
            info_obj = item.get("infographic") if isinstance(item.get("infographic"), dict) else item

            # Determine visual classification
            chart_type_raw = str(chart_obj.get("chart_type") or chart_obj.get("type") or "").strip().lower()
            media_type = str(item.get("media_type") or item.get("role") or "").strip().lower()

            is_table = chart_type_raw in TABLE_TYPES or media_type in TABLE_TYPES or bool(item.get("is_table"))
            is_diagram = chart_type_raw in DIAGRAM_TYPES or media_type in DIAGRAM_TYPES
            is_infographic = media_type == "infographic" or "infographic" in item or chart_type_raw == "infographic"

            if is_table:
                tables_count += 1
            elif is_diagram:
                diagrams_count += 1
            elif is_infographic:
                infographics_count += 1
            else:
                charts_count += 1

            has_issue = False

            # Extract structural properties
            title = chart_obj.get("title") or item.get("title")
            has_title = item.get("has_title") if "has_title" in item else (bool(title and str(title).strip()))
            caption = item.get("caption") or page_context.get("caption") or page_context.get("heading")
            has_text_equivalent = bool(item.get("has_text_equivalent") or item.get("text_summary") or item.get("data_table"))

            labels = chart_obj.get("labels") or item.get("labels") or []
            values = chart_obj.get("values") or item.get("values") or []
            legend = chart_obj.get("legend") or item.get("legend") or []
            series = chart_obj.get("series") or item.get("series") or []

            has_axis_labels = item.get("has_axis_labels") if "has_axis_labels" in item else chart_obj.get("has_axis_labels")
            has_legend = item.get("has_legend") if "has_legend" in item else chart_obj.get("has_legend")
            units = chart_obj.get("units") or chart_obj.get("unit") or item.get("units")

            # ------------------------------------------------------------------
            # Check 20: Embedded Table in Raster Image (MM-CHART-010)
            # ------------------------------------------------------------------
            if is_table:
                if not has_text_equivalent:
                    score -= 15.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-010",
                        category="chart_infographic",
                        title=f"Embedded tabular data lacks structured HTML representation ({media_id})",
                        description=(
                            f"Visual '{media_id}' displays tabular numerical data as a static image without an "
                            "accompanying semantic HTML <table> or accessible structured data summary."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.95,
                        evidence={"media_id": media_id, "visual_type": "embedded_table", "has_text_equivalent": False},
                        recommendation="Provide an HTML <table> element or downloadable spreadsheet data equivalent.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 18: Infographic Structure & Hierarchy (MM-CHART-009)
            # ------------------------------------------------------------------
            if is_infographic:
                sections = info_obj.get("sections") or item.get("sections") or []
                unclear_flow = bool(info_obj.get("unclear_reading_order") or item.get("unclear_hierarchy"))
                if unclear_flow or (isinstance(sections, list) and len(sections) > 6 and not has_text_equivalent):
                    score -= 10.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-009",
                        category="chart_infographic",
                        title=f"Complex infographic lacks structured reading order or text summary ({media_id})",
                        description=(
                            f"Infographic '{media_id}' contains dense multi-section content ({len(sections)} sections) "
                            "without a structured reading flow or textual narrative equivalent."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.85,
                        evidence={"media_id": media_id, "sections_count": len(sections), "has_text_equivalent": has_text_equivalent},
                        recommendation="Provide structured section headings and an accompanying text summary for complex infographic narratives.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 9: Chart Title & Context (MM-CHART-002)
            # ------------------------------------------------------------------
            if not is_table and not is_diagram:
                if has_title is False and not caption:
                    score -= 10.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-002",
                        category="chart_infographic",
                        title=f"Chart is missing a descriptive title or caption ({media_id})",
                        description=(
                            f"Chart '{media_id}' (type: '{chart_type_raw or 'chart'}') lacks an internal title and has no "
                            "surrounding figure caption, making its data subject ambiguous."
                        ),
                        severity=Severity.MEDIUM.value,
                        confidence=0.92,
                        evidence={"media_id": media_id, "has_title": False, "caption_available": bool(caption)},
                        recommendation="Include a clear title (e.g. '<figcaption>' or visual chart header) explaining the metric and timeframe.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 10: Axis Labels for Axis-Based Charts (MM-CHART-003)
            # ------------------------------------------------------------------
            if chart_type_raw in AXIS_BASED_CHART_TYPES:
                if has_axis_labels is False:
                    score -= 15.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-003",
                        category="chart_infographic",
                        title=f"Missing or unclear axis labels on {chart_type_raw} chart ({media_id})",
                        description=(
                            f"Chart '{media_id}' is an axis-based {chart_type_raw} graphic but lacks clear horizontal (X) "
                            "or vertical (Y) axis dimension labels."
                        ),
                        severity=Severity.HIGH.value,
                        confidence=0.95,
                        evidence={"media_id": media_id, "chart_type": chart_type_raw, "has_axis_labels": False},
                        recommendation="Add explicit X and Y axis titles clarifying the measured variables and time periods.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 11: Multi-Series Legend Identification (MM-CHART-005)
            # ------------------------------------------------------------------
            multi_series = (isinstance(series, list) and len(series) > 1) or (isinstance(legend, list) and len(legend) > 1) or bool(item.get("multi_series"))
            if multi_series and has_legend is False:
                score -= 12.0
                has_issue = True
                findings.append(MultimodalFinding(
                    id="MM-CHART-005",
                    category="chart_infographic",
                    title=f"Multiple data series lack legend or series key ({media_id})",
                    description=(
                        f"Chart '{media_id}' presents multiple comparative data series without a corresponding legend "
                        "or color key, preventing users from distinguishing series identities."
                    ),
                    severity=Severity.MEDIUM.value,
                    confidence=0.95,
                    evidence={"media_id": media_id, "has_legend": False, "multi_series": True},
                    recommendation="Provide an explicit color/pattern legend mapping each visual series to its dataset name.",
                    url=target_url,
                    media_id=media_id,
                ))

            # ------------------------------------------------------------------
            # Check 12: Units and Scale (MM-CHART-004)
            # ------------------------------------------------------------------
            if not is_diagram and not is_table:
                if units is None and item.get("has_units") is False:
                    score -= 8.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-004",
                        category="chart_infographic",
                        title=f"Unclear numerical units or scale on chart ({media_id})",
                        description=(
                            f"Chart '{media_id}' displays numerical values without specifying measurement units "
                            "(e.g. USD $, percentages %, thousands, metric tons)."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.90,
                        evidence={"media_id": media_id, "has_units": False},
                        recommendation="Specify unit symbols (e.g. $, %, k, M) on axis titles or data labels.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 23: Data Consistency (MM-CHART-011)
            # ------------------------------------------------------------------
            if isinstance(labels, list) and isinstance(values, list) and labels and values:
                if len(labels) != len(values) and not multi_series:
                    score -= 10.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-011",
                        category="chart_infographic",
                        title=f"Chart category labels count contradicts values count ({media_id})",
                        description=(
                            f"Chart '{media_id}' data evidence contains {len(labels)} category labels but {len(values)} values, "
                            "indicating incomplete or mismatched data extraction."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.98,
                        evidence={"media_id": media_id, "labels_count": len(labels), "values_count": len(values)},
                        recommendation="Ensure category label arrays align one-to-one with plotted numerical data series.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 24: Pie / Donut Slice Overcrowding (MM-CHART-013)
            # ------------------------------------------------------------------
            if chart_type_raw in RADIAL_CHART_TYPES:
                num_slices = len(labels) or len(values)
                if num_slices > self.max_pie_slices_warning:
                    score -= 8.0
                    has_issue = True
                    findings.append(MultimodalFinding(
                        id="MM-CHART-013",
                        category="chart_infographic",
                        title=f"Pie/donut chart contains excessive slice count ({num_slices} categories) ({media_id})",
                        description=(
                            f"Pie chart '{media_id}' contains {num_slices} distinct slices (recommended limit: {self.max_pie_slices_warning}), "
                            "creating visual clutter and making small percentage distinctions difficult to perceive."
                        ),
                        severity=Severity.LOW.value,
                        confidence=0.90,
                        evidence={"media_id": media_id, "slice_count": num_slices, "max_recommended": self.max_pie_slices_warning},
                        recommendation="Consolidate minor slices into an 'Other' category or use a horizontal bar chart for high-category datasets.",
                        url=target_url,
                        media_id=media_id,
                    ))

            # ------------------------------------------------------------------
            # Check 15 & 16: Text-In-Image Dependency Without Text Equivalent (MM-CHART-008)
            # ------------------------------------------------------------------
            if (is_infographic or is_diagram or (isinstance(values, list) and len(values) >= 4)) and not has_text_equivalent:
                # If no text summary, table, or caption is present
                score -= 10.0
                has_issue = True
                findings.append(MultimodalFinding(
                    id="MM-CHART-008",
                    category="chart_infographic",
                    title=f"Complex data visual lacks accessible textual or tabular equivalent ({media_id})",
                    description=(
                        f"Visual graphic '{media_id}' presents essential quantitative or process information solely "
                        "inside the image without an equivalent structured text summary or HTML table."
                    ),
                    severity=Severity.MEDIUM.value,
                    confidence=0.90,
                    evidence={"media_id": media_id, "has_text_equivalent": False, "visual_type": chart_type_raw or media_type or "chart"},
                    recommendation="Provide an accompanying summary paragraph or HTML data table conveying the core trends and findings.",
                    url=target_url,
                    media_id=media_id,
                ))

            if has_issue:
                issues_count += 1

        # ----------------------------------------------------------------------
        # Score & Status Aggregation
        # ----------------------------------------------------------------------
        total_visuals = len(records_to_evaluate)
        score_clamped = max(0, min(100, int(round(score))))

        status = MultimodalStatus.PASSED.value
        if any(f.severity in (Severity.CRITICAL.value, Severity.HIGH.value) for f in findings):
            status = MultimodalStatus.FAILED.value
        elif findings:
            status = MultimodalStatus.WARNING.value

        metrics = {
            "visuals_analyzed": total_visuals,
            "charts_count": charts_count,
            "infographics_count": infographics_count,
            "diagrams_count": diagrams_count,
            "embedded_tables_count": tables_count,
            "visuals_with_issues": issues_count,
            "missing_axis_count": len([f for f in findings if f.id == "MM-CHART-003"]),
            "missing_legend_count": len([f for f in findings if f.id == "MM-CHART-005"]),
            "embedded_table_issues_count": len([f for f in findings if f.id == "MM-CHART-010"]),
        }

        # Sort findings deterministically: ID -> Severity
        findings.sort(key=lambda x: (x.id, x.severity))

        return MultimodalCategoryResult(
            category="chart_infographic",
            status=status,
            score=score_clamped,
            confidence=0.95 if total_visuals > 0 else 0.85,
            findings=findings,
            evaluated=True,
            evidence_status=EvidenceStatus.AVAILABLE.value,
            metrics=metrics,
            messages=[
                f"Chart & infographic evaluation completed across {total_visuals} visual graphic(s) (Score: {score_clamped}/100).",
                f"Visual structural defects identified: {len(findings)}.",
            ],
        ).to_dict()

    def __call__(self, evidence: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        return self.analyze(evidence)



# Public API Function


def analyze_charts(
    evidence: dict[str, Any] | list[dict[str, Any]] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public functional entry point for Chart & Infographic visual content evaluation.

    Args:
        evidence: Structured visual evidence dictionary or list of visual items.
        options: Optional configuration overrides.

    Returns:
        JSON-serializable category audit result dictionary.
    """
    analyzer = ChartInfographicAnalyzer(config=options)
    return analyzer.analyze(evidence)
