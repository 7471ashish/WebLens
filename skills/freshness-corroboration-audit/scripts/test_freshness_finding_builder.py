"""
Unit tests for the independent finding builder module.

Tests all 27 requirements without external network access or LLM inference.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from freshness_finding_builder import build_findings


# 1. No observations -> no findings
def test_no_observations_no_findings() -> None:
    res = build_findings("https://example.com")
    assert res["summary"]["total_findings"] == 0
    assert res["findings"] == []


# 2. Malformed JSON-LD finding
def test_malformed_jsonld_finding() -> None:
    jsonld_data = {
        "summary": {"json_ld_blocks": 1, "invalid_blocks": 1, "valid_blocks": 0},
        "errors": [{"error": "Invalid syntax"}],
    }
    res = build_findings("https://example.com", jsonld=jsonld_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "malformed_jsonld"
    assert res["findings"][0]["severity"] == "high"


# 3. Missing JSON-LD with require_jsonld=false (default: no finding)
def test_missing_jsonld_with_require_false() -> None:
    jsonld_data = {"summary": {"json_ld_blocks": 0, "invalid_blocks": 0, "valid_blocks": 0}}
    res = build_findings("https://example.com", jsonld=jsonld_data, options={"require_jsonld": False})
    assert res["summary"]["total_findings"] == 0


# 4. Missing JSON-LD with require_jsonld=true
def test_missing_jsonld_with_require_true() -> None:
    jsonld_data = {"summary": {"json_ld_blocks": 0, "invalid_blocks": 0, "valid_blocks": 0}}
    res = build_findings("https://example.com", jsonld=jsonld_data, options={"require_jsonld": True})
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "missing_expected_structured_data"


# 5. Missing date metadata not reported by default
def test_missing_date_not_reported_by_default() -> None:
    freshness_data = {
        "summary": {"entities_without_temporal_metadata": 1},
        "observations": [],
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    assert res["summary"]["total_findings"] == 0


# 6. Stale Article metadata
def test_stale_article_metadata() -> None:
    entities_data = {
        "entities": [{"internal_id": "e1", "types": ["Article"], "name": "Deep Dive"}]
    }
    freshness_data = {
        "observations": [
            {
                "code": "dateModified_old",
                "entity_id": "e1",
                "field": "dateModified",
                "age_days": 400,
                "threshold_days": 365,
            }
        ]
    }
    res = build_findings("https://example.com", entities=entities_data, freshness=freshness_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "stale_content_metadata"


# 7. Old evergreen WebPage not automatically reported
def test_old_evergreen_webpage_not_reported() -> None:
    entities_data = {
        "entities": [{"internal_id": "e-page", "types": ["WebPage"], "name": "Terms of Service"}]
    }
    freshness_data = {
        "observations": [
            {
                "code": "dateModified_old",
                "entity_id": "e-page",
                "field": "dateModified",
                "age_days": 400,
                "threshold_days": 365,
            }
        ]
    }
    res = build_findings("https://example.com", entities=entities_data, freshness=freshness_data)
    # Evergreen WebPage should not trigger stale finding
    assert res["summary"]["total_findings"] == 0


# 8. Very stale content
def test_very_stale_content() -> None:
    entities_data = {
        "entities": [{"internal_id": "e-news", "types": ["NewsArticle"], "name": "Breaking News"}]
    }
    freshness_data = {
        "observations": [
            {
                "code": "dateModified_very_old",
                "entity_id": "e-news",
                "field": "dateModified",
                "age_days": 900,
                "threshold_days": 730,
            }
        ]
    }
    res = build_findings("https://example.com", entities=entities_data, freshness=freshness_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "very_stale_content_metadata"


# 9. Future date finding
def test_future_date_finding() -> None:
    freshness_data = {
        "observations": [
            {
                "code": "future_temporal_value",
                "entity_id": "e-fut",
                "field": "datePublished",
                "date": "2030-01-01T00:00:00Z",
                "reference_time": "2026-09-02T00:00:00Z",
            }
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "future_temporal_value"


# 10. Temporal inconsistency finding
def test_temporal_inconsistency_finding() -> None:
    freshness_data = {
        "observations": [
            {
                "code": "created_after_published",
                "entity_id": "e-incon",
                "details": {"dateCreated": "2026-05-01", "datePublished": "2026-01-01"},
            }
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "inconsistent_temporal_order"


# 11. sameAs unreachable
def test_sameas_unreachable() -> None:
    corrob_data = {
        "identity_checks": [
            {"entity_id": "e-sa", "reference_url": "https://broken.org", "status": "unreachable"}
        ]
    }
    # With option enabled: produces low-severity finding
    res = build_findings("https://example.com", corroboration=corrob_data, options={"report_unreachable_sameas": True})
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "sameas_unreachable"
    assert res["findings"][0]["severity"] == "low"

    # With option disabled (default): maps to observation only
    res_default = build_findings("https://example.com", corroboration=corrob_data)
    assert res_default["summary"]["total_findings"] == 0
    assert len(res_default["observations"]) == 1


# 12. sameAs identity mismatch
def test_sameas_identity_mismatch() -> None:
    conflict_data = {
        "conflicts": [
            {
                "conflict_id": "c-0",
                "conflict_type": "identity",
                "code": "sameas_identity_mismatch",
                "field": "sameAs",
                "result": "conflict",
                "confidence": "high",
                "target_evidence": {"entity_id": "e-mis", "name": "Company Alpha"},
                "external_evidence": {"entity_names": ["Company Beta"]},
                "reason": "Target Company Alpha links to Company Beta profile.",
            }
        ]
    }
    res = build_findings("https://example.com", conflicts=conflict_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "sameas_identity_mismatch"
    assert res["findings"][0]["severity"] == "high"


# 13. Insufficient external evidence (no finding)
def test_insufficient_external_evidence_no_finding() -> None:
    corrob_data = {
        "identity_checks": [
            {"entity_id": "e-insuf", "reference_url": "https://sparse.org", "status": "reachable_no_identity_evidence"}
        ]
    }
    res = build_findings("https://example.com", corroboration=corrob_data)
    assert res["summary"]["total_findings"] == 0


# 14. High-confidence identity conflict
def test_high_confidence_identity_conflict() -> None:
    conflict_data = {
        "conflicts": [
            {
                "conflict_id": "c-id",
                "conflict_type": "identity",
                "code": "identity_conflict",
                "field": "name",
                "result": "conflict",
                "confidence": "high",
                "target_evidence": {"name": "Brand A"},
                "external_evidence": {"name": "Brand B"},
            }
        ]
    }
    res = build_findings("https://example.com", conflicts=conflict_data)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["severity"] == "high"


# 15. Low-confidence conflict filtered out
def test_low_confidence_conflict_filtered_out() -> None:
    conflict_data = {
        "conflicts": [
            {
                "conflict_id": "c-low",
                "conflict_type": "attribute",
                "field": "brand",
                "result": "conflict",
                "confidence": "low",
                "target_evidence": {},
                "external_evidence": {},
            }
        ]
    }
    # Minimum confidence default is 'medium'
    res = build_findings("https://example.com", conflicts=conflict_data)
    assert res["summary"]["total_findings"] == 0


# 16. Temporal disagreement not automatically treated as conflict
def test_temporal_disagreement_not_conflict() -> None:
    freshness_data = {
        "observations": [
            {"code": "http_last_modified_differs_from_content_date", "entity_id": "e1"}
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    assert res["summary"]["total_findings"] == 0


# 17. External-vs-external disagreement
def test_external_vs_external_disagreement() -> None:
    # Observations from external_vs_external disagreement do not trigger findings by default
    conflict_data = {
        "conflicts": [],
        "observations": [{"code": "external_source_disagreement", "result": "disagreement"}],
    }
    res = build_findings("https://example.com", conflicts=conflict_data)
    assert res["summary"]["total_findings"] == 0


# 18. Duplicate observations deduplicated
def test_duplicate_observations_deduplicated() -> None:
    freshness_data = {
        "observations": [
            {"code": "invalid_temporal_value", "entity_id": "e-dup", "field": "datePublished"},
            {"code": "invalid_temporal_value", "entity_id": "e-dup", "field": "datePublished"},
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    assert res["summary"]["total_findings"] == 1


# 19. max_findings limit
def test_max_findings_limit() -> None:
    freshness_data = {
        "observations": [
            {"code": "invalid_temporal_value", "entity_id": f"e-{i}", "field": "datePublished"}
            for i in range(25)
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data, options={"max_findings": 5})
    assert res["summary"]["total_findings"] == 5
    assert len(res["findings"]) == 5


# 20. Deterministic finding IDs
def test_deterministic_finding_ids() -> None:
    freshness_data = {
        "observations": [
            {"code": "future_temporal_value", "entity_id": "e-1", "field": "dateModified"},
            {"code": "future_temporal_value", "entity_id": "e-2", "field": "dateModified"},
        ]
    }
    res1 = build_findings("https://example.com", freshness=freshness_data)
    res2 = build_findings("https://example.com", freshness=freshness_data)
    ids1 = [f["finding_id"] for f in res1["findings"]]
    ids2 = [f["finding_id"] for f in res2["findings"]]
    assert ids1 == ["freshness-001", "freshness-002"]
    assert ids1 == ids2


# 21. Deterministic ordering
def test_deterministic_ordering() -> None:
    jsonld_data = {"summary": {"invalid_blocks": 1, "valid_blocks": 0}, "errors": []}
    freshness_data = {
        "observations": [{"code": "future_temporal_value", "entity_id": "e-1", "field": "date"}]
    }
    res = build_findings("https://example.com", jsonld=jsonld_data, freshness=freshness_data)
    # High severity (malformed_jsonld) comes before Medium severity (future_temporal_value)
    assert res["findings"][0]["severity"] == "high"
    assert res["findings"][1]["severity"] == "medium"


# 22. Severity ranking
def test_severity_ranking() -> None:
    conflict_data = {
        "conflicts": [
            {
                "conflict_id": "c-1",
                "conflict_type": "temporal",
                "field": "datePublished",
                "result": "conflict",
                "confidence": "high",
            }
        ]
    }
    jsonld_data = {"summary": {"invalid_blocks": 1, "valid_blocks": 0}, "errors": []}
    res = build_findings("https://example.com", jsonld=jsonld_data, conflicts=conflict_data)
    sevs = [f["severity"] for f in res["findings"]]
    assert sevs == ["high", "medium"]


# 23. Confidence filtering
def test_confidence_filtering() -> None:
    conflict_data = {
        "conflicts": [
            {
                "conflict_id": "c-hi",
                "conflict_type": "temporal",
                "field": "dateModified",
                "result": "conflict",
                "confidence": "high",
            },
            {
                "conflict_id": "c-med",
                "conflict_type": "temporal",
                "field": "datePublished",
                "result": "conflict",
                "confidence": "medium",
            },
        ]
    }
    # Filter with minimum_confidence = 'high'
    res = build_findings("https://example.com", conflicts=conflict_data, options={"minimum_confidence": "high"})
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["confidence"] == "high"


# 24. Evidence preservation
def test_evidence_preservation() -> None:
    freshness_data = {
        "observations": [
            {
                "code": "future_temporal_value",
                "entity_id": "e-ev",
                "field": "dateCreated",
                "date": "2035-01-01T00:00:00Z",
                "reference_time": "2026-09-02T00:00:00Z",
            }
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    f = res["findings"][0]
    assert "date" in f["evidence"]
    assert f["evidence"]["date"] == "2035-01-01T00:00:00Z"


# 25. No network access
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_access(mock_urlopen: Any, mock_socket: Any) -> None:
    res = build_findings("https://example.com", options={"require_jsonld": True})
    assert res["summary"]["total_findings"] == 1
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()


# 26. No severity missing from generated findings
def test_no_severity_missing() -> None:
    freshness_data = {
        "observations": [
            {"code": "invalid_temporal_value", "entity_id": "e-sev", "field": "dateModified"}
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    for f in res["findings"]:
        assert f["severity"] in ("critical", "high", "medium", "low", "info")


# 27. JSON serialization
def test_json_serialization() -> None:
    freshness_data = {
        "observations": [
            {"code": "future_temporal_value", "entity_id": "e-json", "field": "datePublished"}
        ]
    }
    res = build_findings("https://example.com", freshness=freshness_data)
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["summary"]["total_findings"] == 1


# 28. Testimonials check: Do NOT emit CORROB-TESTIMONIAL-001 if page contains no testimonials
def test_28_corrob_testimonial_not_emitted_when_no_testimonials_on_page() -> None:
    from freshness_finding_builder import build_findings_llm
    # HTML with standard content, zero testimonials
    html_no_reviews = "<html><body><h1>Cattle Feed</h1><p>We supply nutritious cattle feed.</p></body></html>"
    res = build_findings_llm(
        target_url="https://example.com",
        corroboration={"sources": [], "candidate_urls": []},
        raw_html=html_no_reviews,
    )
    assert res is not None
    testimonial_findings = [f for f in res["findings"] if f["id"] == "CORROB-TESTIMONIAL-001"]
    assert len(testimonial_findings) == 0


# 29. Testimonials check: Emit CORROB-TESTIMONIAL-001 when testimonials are found and uncorroborated
def test_29_corrob_testimonial_emitted_when_testimonials_found_and_uncorroborated() -> None:
    from freshness_finding_builder import build_findings_llm
    # HTML with real customer testimonials and attribution
    html_with_reviews = """<html><body>
        <div class="customer-testimonials">
            <blockquote>
                <p>Outstanding cattle feed quality, milk yield increased by 20%!</p>
                <cite>Rajesh Patel, Dairy Farmer</cite>
            </blockquote>
        </div>
    </body></html>"""
    res = build_findings_llm(
        target_url="https://example.com",
        corroboration={"sources": [], "candidate_urls": []},
        raw_html=html_with_reviews,
    )
    assert res is not None
    testimonial_findings = [f for f in res["findings"] if f["id"] == "CORROB-TESTIMONIAL-001"]
    assert len(testimonial_findings) == 1
    tf = testimonial_findings[0]
    assert "blockquote" in tf["evidence"]
    assert "customer testimonials" in tf["evidence"].lower()

