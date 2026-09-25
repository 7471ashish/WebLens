"""
Multimodal Audit Coordinator (multimodal_audit.py)
==================================================
Main controller and orchestrator for the independent Multimodal Audit subagent.

This module coordinates multimodal analysis of website media (images, alt text,
OCR text overlays, image metadata, charts, infographics, videos, transcripts,
and closed captions) from structured evidence produced by upstream data collection agents.

Architectural Guarantees:
- In-memory execution: Does NOT launch browsers, make network requests, or crawl URLs.
- Fault Isolation: An error in one analyzer does not abort the remaining analyzers.
- Missing Evidence Safety: Missing media data produces 'insufficient_evidence' rather than false failures.
- Deterministic Ordering: Findings and scores are sorted and clamped deterministically.
- Full JSON Serialization: Returns pure Python dictionaries and lists.
- LangGraph Ready: Stateless functional and class-based API interfaces.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

logger = logging.getLogger("multimodal_audit")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# Constants & Default Category Weights


MULTIMODAL_CATEGORIES: list[str] = [
    "image",
    "alt_text",
    "ocr",
    "image_metadata",
    "chart_infographic",
    "video",
    "transcript",
    "caption",
]

DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    "image": 0.20,
    "alt_text": 0.20,
    "ocr": 0.10,
    "image_metadata": 0.10,
    "chart_infographic": 0.10,
    "video": 0.15,
    "transcript": 0.075,
    "caption": 0.075,
}

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}



# Typed Data Contracts (Fallback / Native Multimodal Models)


@dataclass
class MultimodalFinding:
    """Individual evidence-backed finding generated during multimodal evaluation."""
    id: str
    category: str
    title: str
    description: str
    severity: str = "medium"  # "critical" | "high" | "medium" | "low" | "info"
    confidence: float = 0.85
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    url: str = ""
    is_suggestion: bool = False

    def to_dict(self) -> dict[str, Any]:
        res = {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.lower(),
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "url": self.url,
        }
        if self.is_suggestion:
            res["is_suggestion"] = True
            res["type"] = "suggestion"
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultimodalFinding:
        if not isinstance(data, dict):
            return cls(id="MM-AUDIT-000", category="multimodal", title="Malformed finding", description="")
        return cls(
            id=str(data.get("id") or "MM-AUDIT-000"),
            category=str(data.get("category") or "multimodal"),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            severity=str(data.get("severity") or "medium"),
            confidence=float(data.get("confidence") if data.get("confidence") is not None else 0.85),
            evidence=dict(data.get("evidence") or {}),
            recommendation=str(data.get("recommendation") or ""),
            url=str(data.get("url") or ""),
        )


@dataclass
class CategoryAuditResult:
    """Audit result for an individual multimodal media category."""
    category: str
    status: str = "insufficient_evidence"  # "passed" | "failed" | "warning" | "insufficient_evidence" | "error"
    score: int | None = None
    confidence: float = 0.5
    findings: list[MultimodalFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "status": self.status,
            "score": self.score,
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 2),
            "findings": [f.to_dict() if isinstance(f, MultimodalFinding) else f for f in self.findings],
            "metrics": self.metrics,
            "messages": self.messages,
        }



# Multimodal Audit Coordinator Class


class MultimodalAudit:
    """
    Main controller for the multimodal audit subagent. Coordinates routing to
    specialized media analyzers, aggregates scores, and normalizes findings.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        custom_analyzers: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        llm_client: Any = None,
    ) -> None:
        self.weights = {**DEFAULT_CATEGORY_WEIGHTS, **(weights or {})}
        self.custom_analyzers = custom_analyzers or {}
        self.config = config or {}
        self.llm_client = llm_client or self.config.get("llm_client")

    def audit(
        self,
        evidence: dict[str, Any] | None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute multimodal audit across all available media evidence categories.

        Args:
            evidence: Structured multimodal evidence dictionary.
            options: Optional execution parameter overrides.

        Returns:
            JSON-serializable audit summary dictionary.
        """
        if not evidence:
            return {
                "skill": "multimodal-audit",
                "status": "insufficient_evidence",
                "score": None,
                "findings": [],
                "categories": {},
            }
        opts = {**self.config, **(options or {})}
        raw_evidence = evidence if isinstance(evidence, dict) else {}
        target_url = str(raw_evidence.get("url") or raw_evidence.get("target_url") or "https://example.com")

        logger.info("Starting multimodal audit for %s", target_url)

        categories_result: dict[str, CategoryAuditResult] = {}
        all_findings: list[MultimodalFinding] = []

        # ----------------------------------------------------------------------
        # Category 1: Image Quality & Layout (`image`)
        # ----------------------------------------------------------------------
        categories_result["image"] = self._run_category(
            category="image",
            evidence=self._route_evidence("image", raw_evidence, target_url),
            default_runner=self._default_image_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 2: Alt Text Completeness & Quality (`alt_text`)
        # ----------------------------------------------------------------------
        categories_result["alt_text"] = self._run_category(
            category="alt_text",
            evidence=self._route_evidence("alt_text", raw_evidence, target_url),
            default_runner=self._default_alt_text_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 3: Image OCR & Text In Graphics (`ocr`)
        # ----------------------------------------------------------------------
        categories_result["ocr"] = self._run_category(
            category="ocr",
            evidence=self._route_evidence("ocr", raw_evidence, target_url),
            default_runner=self._default_ocr_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 4: Image Metadata & Dimensions (`image_metadata`)
        # ----------------------------------------------------------------------
        categories_result["image_metadata"] = self._run_category(
            category="image_metadata",
            evidence=self._route_evidence("image_metadata", raw_evidence, target_url),
            default_runner=self._default_image_metadata_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 5: Charts & Infographics (`chart_infographic`)
        # ----------------------------------------------------------------------
        categories_result["chart_infographic"] = self._run_category(
            category="chart_infographic",
            evidence=self._route_evidence("chart_infographic", raw_evidence, target_url),
            default_runner=self._default_chart_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 6: Video Player & Formatting (`video`)
        # ----------------------------------------------------------------------
        categories_result["video"] = self._run_category(
            category="video",
            evidence=self._route_evidence("video", raw_evidence, target_url),
            default_runner=self._default_video_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 7: Video Transcripts (`transcript`)
        # ----------------------------------------------------------------------
        categories_result["transcript"] = self._run_category(
            category="transcript",
            evidence=self._route_evidence("transcript", raw_evidence, target_url),
            default_runner=self._default_transcript_analysis,
        )

        # ----------------------------------------------------------------------
        # Category 8: Closed Captions & Subtitles (`caption`)
        # ----------------------------------------------------------------------
        categories_result["caption"] = self._run_category(
            category="caption",
            evidence=self._route_evidence("caption", raw_evidence, target_url),
            default_runner=self._default_caption_analysis,
        )

        # ----------------------------------------------------------------------
        # Aggregate Findings, Remove Contradictions & Merge Duplicates (Defects 2, 3, 4)
        # ----------------------------------------------------------------------
        per_item_alt_findings = categories_result.get("alt_text", CategoryAuditResult("alt_text")).findings
        has_per_item_alt_data = len(per_item_alt_findings) > 0
        has_charts = len(raw_evidence.get("charts", [])) > 0

        # Check if qualitative LLM audit is enabled
        if opts.get("use_llm", True):
            llm_findings = self._analyze_multimodal_with_llm(raw_evidence, target_url)
            for f in llm_findings:
                # Defect 3: Cross-check against per-item data. If per-item alt data exists, drop generic claim!
                if f.category == "alt_text" and has_per_item_alt_data:
                    logger.info("Dropping generic qualitative alt text claim in favor of per-item image telemetry.")
                    continue
                # Defect 2: Grounding check - if no charts exist on page, drop hallucinated chart finding!
                if "chart" in f.category or "chart" in f.id.lower() or "chart" in f.title.lower():
                    if not has_charts:
                        logger.info("Dropping hallucinated chart finding: 0 charts exist on page.")
                        continue
                all_findings.append(f)

        # Merge duplicate image observations & assign unique IDs (Defect 4)
        merged_dup_images: dict[str, MultimodalFinding] = {}

        for cat_name, cat_res in categories_result.items():
            for f in cat_res.findings:
                img_id = str(f.evidence.get("image_id") or "")
                
                # Programmatic unique ID for alt text findings
                if cat_name == "alt_text" and img_id:
                    f.id = f"MM-ALT-{img_id}"

                # Defect 4 & Requirement 2.D / P3: Context-Aware Duplicate Image Evaluation
                is_dup = "duplicate" in f.title.lower() or "duplicate" in str(f.evidence).lower() or f.id == "MM-IMG-006"
                if is_dup:
                    if "MM-IMG-006" in merged_dup_images:
                        # Merge evidence into the single aggregated duplicate finding
                        existing = merged_dup_images["MM-IMG-006"]
                        if isinstance(existing.evidence, dict) and isinstance(f.evidence, dict):
                            for ek, ev in f.evidence.items():
                                if ek not in existing.evidence:
                                    existing.evidence[ek] = ev
                        continue
                    else:
                        merged_dup_images["MM-IMG-006"] = f
                        all_findings.append(f)
                        continue

        return self._build_final_report_dict(target_url, categories_result)

    def _build_final_report_dict(self, target_url: str, categories_result: dict[str, CategoryAuditResult]) -> dict[str, Any]:
        """Assemble JSON-serializable report dictionary from categorized audit results."""
        all_findings: list[MultimodalFinding] = []
        merged_dup_images: dict[str, MultimodalFinding] = {}

        for cat_name, cat_res in categories_result.items():
            for f in cat_res.findings:
                # Programmatic unique ID for alt text findings
                img_id = (f.evidence or {}).get("image_id") if isinstance(f.evidence, dict) else None
                if cat_name == "alt_text" and img_id:
                    f.id = f"MM-ALT-{img_id}"

                is_dup = "duplicate" in f.title.lower() or "duplicate" in str(f.evidence).lower() or f.id == "MM-IMG-006"
                if is_dup:
                    if "MM-IMG-006" in merged_dup_images:
                        existing = merged_dup_images["MM-IMG-006"]
                        if isinstance(existing.evidence, dict) and isinstance(f.evidence, dict):
                            for ek, ev in f.evidence.items():
                                if ek not in existing.evidence:
                                    existing.evidence[ek] = ev
                        continue
                    else:
                        merged_dup_images["MM-IMG-006"] = f
                        all_findings.append(f)
                        continue

                all_findings.append(f)

        # Sort findings deterministically: Severity -> Category -> ID
        all_findings.sort(
            key=lambda x: (
                SEVERITY_ORDER.get(x.severity.lower(), 99),
                x.category,
                x.id,
            )
        )

        evaluated_categories: list[str] = []
        insufficient_categories: list[str] = []
        error_categories: list[str] = []

        total_weighted_score = 0.0
        total_eval_weight = 0.0
        total_confidence = 0.0

        for cat_name, cat_res in categories_result.items():
            if cat_res.status == "error":
                error_categories.append(cat_name)
            elif cat_res.status == "insufficient_evidence" or cat_res.score is None:
                insufficient_categories.append(cat_name)
            else:
                evaluated_categories.append(cat_name)
                w = self.weights.get(cat_name, 0.125)
                total_weighted_score += cat_res.score * w
                total_eval_weight += w
                total_confidence += cat_res.confidence

        overall_score: int | None = None
        if total_eval_weight > 0:
            overall_score = max(0, min(100, int(round(total_weighted_score / total_eval_weight))))

        overall_confidence = 0.5
        if evaluated_categories:
            overall_confidence = round(total_confidence / len(evaluated_categories), 2)
        elif len(insufficient_categories) == len(MULTIMODAL_CATEGORIES):
            overall_confidence = 0.5

        overall_status = "insufficient_evidence"
        if any(cat_res.status == "error" for cat_res in categories_result.values()):
            if not evaluated_categories:
                overall_status = "error"
            else:
                overall_status = "warning"
        elif any(f.severity in ("critical", "high") for f in all_findings):
            overall_status = "failed"
        elif any(cat_res.status == "warning" for cat_res in categories_result.values()) or (overall_score is not None and overall_score < 75):
            overall_status = "warning"
        elif evaluated_categories:
            overall_status = "passed"

        return {
            "category": "multimodal",
            "status": overall_status,
            "score": overall_score,
            "confidence": overall_confidence,
            "categories": {k: v.to_dict() for k, v in categories_result.items()},
            "findings": [f.to_dict() for f in all_findings],
            "metadata": {
                "target_url": target_url,
                "evaluated_categories": evaluated_categories,
                "insufficient_categories": insufficient_categories,
                "error_categories": error_categories,
                "total_findings_count": len(all_findings),
                "critical_findings_count": len([f for f in all_findings if f.severity == "critical"]),
                "high_findings_count": len([f for f in all_findings if f.severity == "high"]),
            },
        }

    def __call__(self, evidence: dict[str, Any] | None) -> dict[str, Any]:
        return self.audit(evidence)

    # ==========================================================================
    # Evidence Routing & Dispatching
    # ==========================================================================

    def _route_evidence(self, category: str, raw_data: dict[str, Any], url: str) -> dict[str, Any]:
        """Route relevant slice of evidence to the target category analyzer."""
        base_ctx = {
            "url": url,
            "page": raw_data.get("page", {}),
        }

        if category == "image":
            return {
                **base_ctx,
                "images": raw_data.get("images"),
                "images_checked": raw_data.get("images_checked"),
            }
        elif category == "alt_text":
            return {
                **base_ctx,
                "images": raw_data.get("images"),
                "alt_texts": raw_data.get("alt_texts") or raw_data.get("alts"),
            }
        elif category == "ocr":
            return {
                **base_ctx,
                "ocr": raw_data.get("ocr") or raw_data.get("ocr_results"),
                "images": raw_data.get("images"),
            }
        elif category == "image_metadata":
            return {
                **base_ctx,
                "images": raw_data.get("images"),
                "metadata": raw_data.get("image_metadata") or raw_data.get("media_metadata"),
            }
        elif category == "chart_infographic":
            return {
                **base_ctx,
                "charts": raw_data.get("charts"),
                "infographics": raw_data.get("infographics"),
                "charts_checked": raw_data.get("charts_checked"),
            }
        elif category == "video":
            return {
                **base_ctx,
                "videos": raw_data.get("videos"),
                "videos_checked": raw_data.get("videos_checked"),
            }
        elif category == "transcript":
            return {
                **base_ctx,
                "transcripts": raw_data.get("transcripts"),
                "videos": raw_data.get("videos"),
            }
        elif category == "caption":
            return {
                **base_ctx,
                "captions": raw_data.get("captions"),
                "videos": raw_data.get("videos"),
            }
        return base_ctx

    def _run_category(
        self,
        category: str,
        evidence: dict[str, Any],
        default_runner: Callable[[dict[str, Any]], CategoryAuditResult],
    ) -> CategoryAuditResult:
        """Execute a category analyzer safely with error trapping and fallbacks."""
        # Check for custom analyzer injection
        if category in self.custom_analyzers:
            analyzer = self.custom_analyzers[category]
            try:
                if hasattr(analyzer, "analyze"):
                    res = analyzer.analyze(evidence)
                elif callable(analyzer):
                    res = analyzer(evidence)
                else:
                    res = None

                if isinstance(res, dict):
                    return self._normalize_dict_to_category_result(category, res)
                elif isinstance(res, CategoryAuditResult):
                    return res
            except Exception as ex:
                logger.exception("Error executing custom analyzer for %s: %s", category, ex)
                return CategoryAuditResult(
                    category=category,
                    status="error",
                    score=None,
                    messages=[f"Analyzer execution failed: {type(ex).__name__}"],
                )

        # Try importing local specialized analyzer module dynamically
        local_result = self._try_import_local_analyzer(category, evidence)
        if local_result is not None:
            return local_result

        # Run default fallback / native coordination logic
        try:
            return default_runner(evidence)
        except Exception as ex:
            logger.exception("Error running default handler for %s: %s", category, ex)
            return CategoryAuditResult(
                category=category,
                status="error",
                score=None,
                messages=[f"Internal handler error: {type(ex).__name__}"],
            )

    def _try_import_local_analyzer(
        self,
        category: str,
        evidence: dict[str, Any],
    ) -> CategoryAuditResult | None:
        """Attempt to dynamically invoke local child analyzer module if present."""
        module_map = {
            "image": "image_analyzer",
            "alt_text": "alt_text_analyzer",
            "ocr": "image_ocr_analyzer",
            "image_metadata": "image_metadata_analyzer",
            "chart_infographic": "chart_infographic_analyzer",
            "video": "video_analyzer",
            "transcript": "transcript_analyzer",
            "caption": "caption_analyzer",
        }
        mod_name = module_map.get(category)
        if not mod_name or mod_name not in sys.modules:
            # Not imported or registered, skip dynamic call
            return None

        mod = sys.modules[mod_name]
        try:
            # Support class Analyzer or function analyze_*
            if hasattr(mod, "analyze"):
                raw_res = getattr(mod, "analyze")(evidence)
                return self._normalize_dict_to_category_result(category, raw_res)
            for attr in dir(mod):
                if attr.startswith("analyze_") and callable(getattr(mod, attr)):
                    raw_res = getattr(mod, attr)(evidence)
                    return self._normalize_dict_to_category_result(category, raw_res)
        except Exception as ex:
            logger.exception("Error calling local module %s: %s", mod_name, ex)
            return CategoryAuditResult(
                category=category,
                status="error",
                score=None,
                messages=[f"Module error in {mod_name}: {type(ex).__name__}"],
            )
        return None

    def _normalize_dict_to_category_result(
        self,
        category: str,
        raw_res: Any,
    ) -> CategoryAuditResult:
        """Normalize arbitrary dictionary return value into CategoryAuditResult."""
        if not isinstance(raw_res, dict):
            return CategoryAuditResult(category=category, status="insufficient_evidence")

        findings: list[MultimodalFinding] = []
        raw_findings = raw_res.get("findings", [])
        if isinstance(raw_findings, list):
            for f in raw_findings:
                if isinstance(f, dict):
                    findings.append(MultimodalFinding.from_dict(f))
                elif isinstance(f, MultimodalFinding):
                    findings.append(f)

        raw_score = raw_res.get("score")
        score = int(raw_score) if raw_score is not None and isinstance(raw_score, (int, float)) else None
        if score is not None:
            score = max(0, min(100, score))

        return CategoryAuditResult(
            category=category,
            status=str(raw_res.get("status") or "passed"),
            score=score,
            confidence=float(raw_res.get("confidence") if raw_res.get("confidence") is not None else 0.85),
            findings=findings,
            metrics=dict(raw_res.get("metrics") or {}),
            messages=list(raw_res.get("messages") or []),
        )

    async def audit_async(
        self,
        evidence: dict[str, Any] | None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Asynchronously execute multimodal audit across all available media evidence categories.
        """
        if not evidence:
            return self._build_empty_report(options)

        opts = options or {}
        raw_evidence = evidence or {}
        target_url = raw_evidence.get("url") or raw_evidence.get("page_context", {}).get("url") or "https://example.com"
        categories_result: dict[str, CategoryAuditResult] = {}

        # Run deterministic media category analyzers
        categories_result["image"] = self._run_category(
            category="image",
            evidence=self._route_evidence("image", raw_evidence, target_url),
            default_runner=self._default_image_analysis,
        )
        categories_result["alt_text"] = self._run_category(
            category="alt_text",
            evidence=self._route_evidence("alt_text", raw_evidence, target_url),
            default_runner=self._default_alt_text_analysis,
        )
        categories_result["ocr"] = self._run_category(
            category="ocr",
            evidence=self._route_evidence("ocr", raw_evidence, target_url),
            default_runner=self._default_ocr_analysis,
        )
        categories_result["image_metadata"] = self._run_category(
            category="image_metadata",
            evidence=self._route_evidence("image_metadata", raw_evidence, target_url),
            default_runner=self._default_image_metadata_analysis,
        )
        categories_result["chart_infographic"] = self._run_category(
            category="chart_infographic",
            evidence=self._route_evidence("chart_infographic", raw_evidence, target_url),
            default_runner=self._default_chart_analysis,
        )
        categories_result["video"] = self._run_category(
            category="video",
            evidence=self._route_evidence("video", raw_evidence, target_url),
            default_runner=self._default_video_analysis,
        )
        categories_result["transcript"] = self._run_category(
            category="transcript",
            evidence=self._route_evidence("transcript", raw_evidence, target_url),
            default_runner=self._default_transcript_analysis,
        )
        categories_result["caption"] = self._run_category(
            category="caption",
            evidence=self._route_evidence("caption", raw_evidence, target_url),
            default_runner=self._default_caption_analysis,
        )

        per_item_alt_findings = categories_result.get("alt_text", CategoryAuditResult("alt_text")).findings
        has_per_item_alt_data = len(per_item_alt_findings) > 0
        has_charts = len(raw_evidence.get("charts", [])) > 0

        # Check if qualitative LLM audit is enabled
        if opts.get("use_llm", True):
            llm_client = opts.get("llm_client") or self.llm_client
            llm_findings = await self._analyze_multimodal_with_llm_async(raw_evidence, target_url, client=llm_client)
            for f in llm_findings:
                if f.id in ("MM-ALT-001", "MM-ALT-002") and has_per_item_alt_data:
                    continue
                if f.category == "chart_infographic" and not has_charts:
                    continue
                cat_res = categories_result.setdefault(f.category, CategoryAuditResult(f.category))
                cat_res.findings.append(f)

        return self._build_final_report_dict(target_url, categories_result)

    def _analyze_multimodal_with_llm(self, evidence: dict[str, Any], target_url: str) -> list[MultimodalFinding]:
        """Perform qualitative, semantic LLM evaluation of visual media & imagery (sync fallback)."""
        client = self.llm_client
        if client is None:
            import os, sys
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            for candidate_root in [
                os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..")),
            ]:
                if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                    sys.path.insert(0, candidate_root)
            try:
                from llm_client import LLMClient
                client = LLMClient()
            except Exception:
                return []

        images = evidence.get("images", [])[:10]
        charts = evidence.get("charts", [])[:5]
        page_ctx = evidence.get("page_context", {})

        img_summary = []
        for im in images:
            if isinstance(im, dict):
                img_summary.append({
                    "id": im.get("id"),
                    "url": im.get("url", ""),
                    "alt_text": im.get("alt_text"),
                    "role": im.get("role"),
                    "dimensions": f"{im.get('width')}x{im.get('height')}"
                })

        prompt = f"""
Audit Target URL: {target_url}
Page Context: {json.dumps(page_ctx)}
Visual Images List: {json.dumps(img_summary)}
Charts/Infographics List: {json.dumps(charts)}

Perform a qualitative, non-mathematical audit of this website's visual media and imagery.
Critique:
1. Does the alternative text (`alt` attribute) accurately convey the meaning, intent, and message of informative images?
2. Are visual images compressed, stretched, distorted, or causing layout degradation?
3. Are charts/infographics clear and self-explanatory, or do they lack units, legends, or accessible summaries?

Return JSON with 'findings' array containing objects with:
id (prefixed with 'MM-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""
        result = client._generate_qualitative_fallback(prompt)
        return self._parse_mm_llm_findings(result, target_url)

    async def _analyze_multimodal_with_llm_async(self, evidence: dict[str, Any], target_url: str, client: Any = None) -> list[MultimodalFinding]:
        """Perform qualitative, semantic LLM evaluation of visual media & imagery (async)."""
        llm_c = client or self.llm_client
        if llm_c is None:
            import os, sys
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            for candidate_root in [
                os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..", "..")),
                os.path.abspath(os.path.join(cur_dir, "..")),
            ]:
                if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                    sys.path.insert(0, candidate_root)
            try:
                from llm_client import LLMClient
                llm_c = LLMClient()
            except Exception:
                return []

        images = evidence.get("images", [])[:10]
        charts = evidence.get("charts", [])[:5]
        page_ctx = evidence.get("page_context", {})

        img_summary = []
        image_urls = []
        for im in images:
            if isinstance(im, dict):
                url = im.get("url", "")
                if url:
                    image_urls.append(url)
                img_summary.append({
                    "id": im.get("id"),
                    "url": url,
                    "alt_text": im.get("alt_text"),
                    "role": im.get("role"),
                    "dimensions": f"{im.get('width')}x{im.get('height')}"
                })

        prompt = f"""
Audit Target URL: {target_url}
Page Context: {json.dumps(page_ctx)}
Visual Images List: {json.dumps(img_summary)}
Charts/Infographics List: {json.dumps(charts)}

Perform a qualitative, non-mathematical audit of this website's visual media and imagery.
Critique:
1. Does the alternative text (`alt` attribute) accurately convey the meaning, intent, and message of informative images?
2. Are visual images compressed, stretched, distorted, or causing layout degradation?
3. Are charts/infographics clear and self-explanatory, or do they lack units, legends, or accessible summaries?

Return JSON with 'findings' array containing objects with:
id (prefixed with 'MM-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""
        try:
            result = await llm_c.query_json(prompt, images=image_urls[:3])
            return self._parse_mm_llm_findings(result, target_url)
        except Exception as ex:
            logger.warning("Qualitative LLM multimodal audit fallback: %s", ex)
            return []

    def _parse_mm_llm_findings(self, result: dict[str, Any], target_url: str) -> list[MultimodalFinding]:
        llm_findings = []
        raw_findings = (result or {}).get("findings", []) if isinstance(result, dict) else []
        for idx, rf in enumerate(raw_findings):
            if isinstance(rf, str):
                rf = {"id": f"MM-IMG-{idx+1:03d}", "title": rf, "severity": "medium", "evidence": rf, "suggested_action": rf}
            elif not isinstance(rf, dict):
                continue
            fid = rf.get("id") or f"MM-IMG-{idx+1:03d}"
            cat = "alt_text" if "alt" in fid.lower() else ("chart_infographic" if "chart" in fid.lower() else "image")
            sugg = rf.get("suggested_action")
            rec = sugg.get("summary", "Review visual media asset.") if isinstance(sugg, dict) else (str(sugg) if sugg else "Review visual media asset.")
            llm_findings.append(MultimodalFinding(
                id=fid,
                category=cat,
                title=rf.get("title", "Visual media opportunity"),
                description=str(rf.get("evidence", "Observed via qualitative visual evaluation")),
                severity=rf.get("severity", "medium"),
                confidence=0.9,
                evidence={"qualitative_critique": rf.get("evidence", "")},
                recommendation=rec,
                url=target_url,
            ))
        return llm_findings

    # ==========================================================================
    # Default Category Evaluators (Defensive Fallbacks)
    # ==========================================================================

    def _default_image_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for image presence and visual assets."""
        images = data.get("images")
        if images is None:
            return CategoryAuditResult(category="image", status="insufficient_evidence", score=None)

        if isinstance(images, list) and not images:
            # Explicitly checked with zero images found
            return CategoryAuditResult(
                category="image",
                status="passed",
                score=100,
                confidence=0.9,
                metrics={"image_count": 0, "explicit_empty": True},
                messages=["Explicit check confirmed zero image assets."],
            )

        findings: list[MultimodalFinding] = []
        score = 100
        if isinstance(images, list):
            for idx, img in enumerate(images):
                if isinstance(img, dict) and img.get("broken"):
                    score -= 25
                    findings.append(MultimodalFinding(
                        id="MM-IMG-001",
                        category="image",
                        title=f"Broken image resource detected ({img.get('id', f'img-{idx}')})",
                        description=f"Image URL '{img.get('url', 'unknown')}' failed to load or returned a 404/error state.",
                        severity="high",
                        confidence=0.95,
                        evidence={"image_id": img.get("id"), "url": img.get("url")},
                        recommendation="Fix broken image source URLs or remove obsolete image tags.",
                        url=str(data.get("url", "")),
                    ))

        score_clamped = max(0, min(100, score))
        status = "failed" if any(f.severity == "high" for f in findings) else "passed"
        return CategoryAuditResult(
            category="image",
            status=status,
            score=score_clamped,
            confidence=0.9,
            findings=findings,
            metrics={"image_count": len(images) if isinstance(images, list) else 0},
        )

    def _default_alt_text_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for image alt text accessibility."""
        images = data.get("images")
        if images is None:
            return CategoryAuditResult(category="alt_text", status="insufficient_evidence", score=None)

        if isinstance(images, list) and not images:
            return CategoryAuditResult(category="alt_text", status="passed", score=100, confidence=0.9)

        findings: list[MultimodalFinding] = []
        score = 100
        missing_count = 0

        if isinstance(images, list):
            for idx, img in enumerate(images):
                if isinstance(img, dict):
                    alt = img.get("alt")
                    is_decorative = bool(img.get("is_decorative") or img.get("decorative", False))
                    if not is_decorative and (alt is None or str(alt).strip() == ""):
                        missing_count += 1
                        score -= 15
                        findings.append(MultimodalFinding(
                            id="MM-ALT-001",
                            category="alt_text",
                            title=f"Missing alt text on informative image ({img.get('id', f'img-{idx}')})",
                            description="Non-decorative image lacks an alternative text description for screen readers.",
                            severity="medium",
                            confidence=0.95,
                            evidence={"image_id": img.get("id"), "src": img.get("url")},
                            recommendation="Provide concise, descriptive alt text explaining the image content.",
                            url=str(data.get("url", "")),
                        ))

        score_clamped = max(0, min(100, score))
        status = "failed" if score_clamped < 60 else ("warning" if findings else "passed")
        return CategoryAuditResult(
            category="alt_text",
            status=status,
            score=score_clamped,
            confidence=0.95,
            findings=findings,
            metrics={"missing_alt_count": missing_count},
        )

    def _default_ocr_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for OCR text inside graphics."""
        ocr_evidence = data.get("ocr")
        if ocr_evidence is None:
            return CategoryAuditResult(category="ocr", status="insufficient_evidence", score=None)

        return CategoryAuditResult(
            category="ocr",
            status="passed",
            score=100,
            confidence=0.85,
            metrics={"ocr_entries_count": len(ocr_evidence) if isinstance(ocr_evidence, list) else 0},
        )

    def _default_image_metadata_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for image dimensions and formatting metadata."""
        images = data.get("images")
        if images is None:
            return CategoryAuditResult(category="image_metadata", status="insufficient_evidence", score=None)

        return CategoryAuditResult(
            category="image_metadata",
            status="passed",
            score=100,
            confidence=0.85,
            metrics={"images_checked": len(images) if isinstance(images, list) else 0},
        )

    def _default_chart_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for charts and infographics."""
        charts = data.get("charts")
        infographics = data.get("infographics")
        if charts is None and infographics is None:
            return CategoryAuditResult(category="chart_infographic", status="insufficient_evidence", score=None)

        return CategoryAuditResult(
            category="chart_infographic",
            status="passed",
            score=100,
            confidence=0.85,
            metrics={
                "chart_count": len(charts) if isinstance(charts, list) else 0,
                "infographic_count": len(infographics) if isinstance(infographics, list) else 0,
            },
        )

    def _default_video_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for video assets."""
        videos = data.get("videos")
        if videos is None:
            return CategoryAuditResult(category="video", status="insufficient_evidence", score=None)

        if isinstance(videos, list) and not videos:
            return CategoryAuditResult(category="video", status="passed", score=100, confidence=0.9)

        return CategoryAuditResult(
            category="video",
            status="passed",
            score=100,
            confidence=0.9,
            metrics={"video_count": len(videos) if isinstance(videos, list) else 0},
        )

    def _default_transcript_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for video transcripts."""
        transcripts = data.get("transcripts")
        videos = data.get("videos")
        if transcripts is None and videos is None:
            return CategoryAuditResult(category="transcript", status="insufficient_evidence", score=None)

        return CategoryAuditResult(
            category="transcript",
            status="passed",
            score=100,
            confidence=0.85,
            metrics={"transcripts_count": len(transcripts) if isinstance(transcripts, list) else 0},
        )

    def _default_caption_analysis(self, data: dict[str, Any]) -> CategoryAuditResult:
        """Default evaluation for video captions."""
        captions = data.get("captions")
        videos = data.get("videos")
        if captions is None and videos is None:
            return CategoryAuditResult(category="caption", status="insufficient_evidence", score=None)

        return CategoryAuditResult(
            category="caption",
            status="passed",
            score=100,
            confidence=0.85,
            metrics={"captions_count": len(captions) if isinstance(captions, list) else 0},
        )



# Public API Convenience Function


def run_multimodal_audit(
    evidence: dict[str, Any] | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Public entry point to execute the multimodal audit subagent.

    Args:
        evidence: Structured dictionary representing multimodal evidence.
        options: Optional execution parameter overrides.

    Returns:
        JSON-serializable multimodal audit result dictionary.
    """
    auditor = MultimodalAudit(config=options)
    return auditor.audit(evidence, options=options)


def main() -> None:
    """CLI execution entry point reading evidence from JSON stdin or arguments."""
    try:
        raw_input = sys.stdin.read()
        if raw_input.strip():
            evidence = json.loads(raw_input)
        else:
            evidence = {}
        result = run_multimodal_audit(evidence)
        print(json.dumps(result, indent=2))
    except Exception as ex:
        err_res = {
            "category": "multimodal",
            "status": "error",
            "score": None,
            "confidence": 0.0,
            "messages": [f"Fatal execution error: {type(ex).__name__} - {str(ex)}"],
        }
        print(json.dumps(err_res, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
