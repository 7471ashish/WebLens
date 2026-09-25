"""
Unit tests for the finding_analyzer.py module in visual-accessibility-audit.

Tests all 28 requirements using deterministic synthetic analyzer results,
running 100% offline without live internet access.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from typing import Any
import pytest

from finding_analyzer import analyze_findings, assign_finding_severity, normalize_observation


def _make_sample_observation(
    code: str = "low_text_contrast",
    category: str = "contrast",
    status: str = "potential_issue",
    confidence: str = "high",
    element: dict[str, Any] | None = None,
    viewport: str = "desktop",
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "status": status,
        "confidence": confidence,
        "description": f"Description for {code}",
        "element": element or {"tag": "button", "id": "btn-1", "viewport": viewport},
        "viewport": viewport,
        "evidence": {"ratio": 2.5},
        "wcag": ["1.4.3"],
    }


# 1. One valid potential_issue becomes one finding
def test_valid_potential_issue_becomes_finding() -> None:
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [_make_sample_observation()],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["code"] == "low_text_contrast"
    assert res["findings"][0]["status"] == "open"


# 2. Passed observation creates no finding
def test_passed_observation_creates_no_finding() -> None:
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [_make_sample_observation(status="passed")],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 0


# 3. Not-testable observation does not become a failure
def test_not_testable_not_failure() -> None:
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [_make_sample_observation(status="not_testable")],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 0


# 4. Skipped observation does not become a failure
def test_skipped_observation_not_failure() -> None:
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [_make_sample_observation(status="skipped_due_to_budget")],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 0


# 5. Manual-review observation is preserved correctly
def test_manual_review_preserved() -> None:
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [_make_sample_observation(status="needs_manual_review")],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["manual_review"] == 1
    assert res["findings"][0]["status"] == "needs_manual_review"
    assert res["findings"][0]["manual_review"] is True


# 6. Duplicate observations are merged
def test_duplicate_observations_merged() -> None:
    obs = _make_sample_observation()
    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [obs, obs],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 1
    assert res["findings"][0]["occurrences"] == 2


# 7. Same issue across desktop/tablet/mobile is grouped
def test_same_issue_across_viewports_grouped() -> None:
    el = {"tag": "button", "id": "btn-1"}
    obs_desktop = _make_sample_observation(element=el, viewport="desktop")
    obs_mobile = _make_sample_observation(element=el, viewport="mobile")

    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [obs_desktop, obs_mobile],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 1
    f = res["findings"][0]
    assert "desktop" in f["viewports"]
    assert "mobile" in f["viewports"]
    assert f["occurrences"] == 2


# 8. Different viewport-specific issues remain separate
def test_different_viewport_issues_separate() -> None:
    obs1 = _make_sample_observation(element={"tag": "button", "id": "btn-1"}, viewport="desktop")
    obs2 = _make_sample_observation(element={"tag": "button", "id": "btn-2"}, viewport="mobile")

    analyzer_res = {
        "contrast": {
            "status": "completed",
            "observations": [obs1, obs2],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 2


# 9. Different issue types on the same element remain separate
def test_different_issue_types_on_same_element_separate() -> None:
    el = {"tag": "button", "id": "btn-1"}
    obs1 = _make_sample_observation(code="low_text_contrast", category="contrast", element=el)
    obs2 = _make_sample_observation(code="missing_focus_indicator", category="keyboard", element=el)

    analyzer_res = {
        "contrast": {"observations": [obs1]},
        "keyboard": {"observations": [obs2]},
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 2


# 10. Cross-analyzer duplicate findings are merged
def test_cross_analyzer_duplicates_merged() -> None:
    el = {"tag": "button", "id": "btn-save"}
    obs_acc = _make_sample_observation(code="button_without_name", category="accessibility", element=el)
    obs_aria = _make_sample_observation(code="missing_required_aria_name", category="accessibility", element=el)

    analyzer_res = {
        "accessibility": {"observations": [obs_acc]},
        "aria": {"observations": [obs_aria]},
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 1
    f = res["findings"][0]
    assert "accessibility" in f["source"]
    assert "aria" in f["source"]


# 11. Unrelated cross-analyzer findings remain separate
def test_unrelated_cross_analyzer_findings_separate() -> None:
    obs1 = _make_sample_observation(code="low_text_contrast", category="contrast")
    obs2 = _make_sample_observation(code="missing_image_alt", category="accessibility")

    analyzer_res = {
        "contrast": {"observations": [obs1]},
        "accessibility": {"observations": [obs2]},
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 2


# 12. Severity assignment
def test_severity_assignment() -> None:
    assert assign_finding_severity("keyboard_trap", "keyboard") == "critical"
    assert assign_finding_severity("missing_focus_indicator", "keyboard") == "high"
    assert assign_finding_severity("low_text_contrast", "contrast") == "medium"
    assert assign_finding_severity("low_non_text_contrast", "contrast") == "low"


# 13. Confidence preservation
def test_confidence_preservation() -> None:
    obs = _make_sample_observation(confidence="medium")
    res = analyze_findings({"contrast": {"observations": [obs]}})
    assert res["findings"][0]["confidence"] == "medium"


# 14. WCAG mapping preservation
def test_wcag_mapping_preservation() -> None:
    obs = _make_sample_observation()
    res = analyze_findings({"contrast": {"observations": [obs]}})
    wcag = res["findings"][0]["wcag"]
    assert any(w["criterion"] == "1.4.3" for w in wcag)


# 15. Evidence preservation
def test_evidence_preservation() -> None:
    obs = _make_sample_observation()
    res = analyze_findings({"contrast": {"observations": [obs]}})
    assert len(res["findings"][0]["evidence"]) == 1
    assert res["findings"][0]["evidence"][0]["ratio"] == 2.5


# 16. Evidence truncation
def test_evidence_truncation() -> None:
    # 30 observations of the same issue
    observations = [_make_sample_observation() for _ in range(30)]
    res = analyze_findings({"contrast": {"observations": observations}})
    assert res["findings"][0]["occurrences"] == 30
    assert len(res["findings"][0]["evidence"]) <= 20


# 17. Stable deterministic finding IDs
def test_deterministic_finding_ids() -> None:
    obs = _make_sample_observation()
    res1 = analyze_findings({"contrast": {"observations": [obs]}})
    res2 = analyze_findings({"contrast": {"observations": [obs]}})
    assert res1["findings"][0]["id"] == res2["findings"][0]["id"]


# 18. Stable deterministic ordering (critical before medium)
def test_deterministic_ordering() -> None:
    obs_med = _make_sample_observation(code="low_text_contrast", category="contrast")
    obs_crit = _make_sample_observation(code="keyboard_trap", category="keyboard")

    res = analyze_findings({"contrast": {"observations": [obs_med]}, "keyboard": {"observations": [obs_crit]}})
    assert res["findings"][0]["severity"] == "critical"
    assert res["findings"][1]["severity"] == "medium"


# 19. Missing analyzer result handled safely
def test_missing_analyzer_result() -> None:
    res = analyze_findings({"visual": None, "contrast": {}})
    assert res["coverage"]["visual"] == "not_run"


# 20. Analyzer execution error handled safely
def test_analyzer_execution_error_handled() -> None:
    analyzer_res = {
        "contrast": {
            "status": "failed",
            "errors": ["Internal color parser exception"],
            "observations": [],
        }
    }
    res = analyze_findings(analyzer_res)
    assert len(res["errors"]) == 1
    assert res["summary"]["total_findings"] == 0


# 21. Malformed observation handled safely
def test_malformed_observation_handled() -> None:
    analyzer_res = {
        "contrast": {
            "observations": [{"bad_key": None}],
        }
    }
    res = analyze_findings(analyzer_res)
    assert res["summary"]["total_findings"] == 1


# 22. Large observation set bounded
def test_large_observation_set_bounded() -> None:
    observations = [
        _make_sample_observation(element={"tag": "p", "id": f"p-{i}"}) for i in range(100)
    ]
    res = analyze_findings({"contrast": {"observations": observations}}, options={"max_findings": 10})
    assert len(res["findings"]) <= 10


# 23. Multiple affected elements counted
def test_multiple_affected_elements_counted() -> None:
    el = {"tag": "button", "id": "b1"}
    observations = [_make_sample_observation(element=el) for _ in range(5)]
    res = analyze_findings({"contrast": {"observations": observations}})
    assert res["findings"][0]["occurrences"] == 5


# 24. Manual-review aggregation
def test_manual_review_aggregation() -> None:
    obs = _make_sample_observation(status="needs_manual_review")
    res = analyze_findings({"contrast": {"observations": [obs]}})
    assert res["summary"]["manual_review"] == 1


# 25. Coverage and limitations reporting
def test_coverage_and_limitations_reporting() -> None:
    res = analyze_findings({"keyboard": {"status": "completed"}})
    assert res["coverage"]["keyboard"] == "completed"
    assert res["coverage"]["contrast"] == "not_run"


# 26. Sensitive data filtering
def test_sensitive_data_filtering() -> None:
    obs = _make_sample_observation()
    obs["evidence"] = {"password": "secret_password_123", "ratio": 3.0}
    res = analyze_findings({"contrast": {"observations": [obs]}})
    ev = res["findings"][0]["evidence"][0]
    assert ev["password"] == "[REDACTED]"
    assert ev["ratio"] == 3.0


# 27. Unknown analyzer/category handling
def test_unknown_analyzer_handling() -> None:
    obs = _make_sample_observation()
    res = analyze_findings({"future_custom_analyzer": {"observations": [obs]}})
    assert res["summary"]["total_findings"] == 0  # Expected analyzers filter


# 28. Same input produces byte-equivalent JSON
def test_byte_equivalent_json_serialization() -> None:
    analyzer_res = {
        "contrast": {
            "observations": [_make_sample_observation()],
        }
    }
    res1 = analyze_findings(analyzer_res)
    res2 = analyze_findings(analyzer_res)
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)
