"""
Unit tests for the top-level run_audit orchestrator.

Tests all 30 audit orchestration requirements without external network access or LLM inference.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freshness_run_audit import main, run_audit


MOCK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Adobe Creative Suite</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "@id": "https://example.com/item#1",
        "name": "Creative Suite Pro",
        "datePublished": "2025-01-01",
        "dateModified": "2025-06-01",
        "sameAs": ["https://example.org/profile"]
    }
    </script>
</head>
<body>
    <h1>Product</h1>
</body>
</html>
"""


def _mock_crawl_success(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "status": "success",
        "requested_url": url,
        "final_url": url,
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "last-modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        },
        "response_time_ms": 120,
        "html": MOCK_HTML,
    }


# 1. Successful complete pipeline
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_successful_complete_pipeline(mock_crawl: Any) -> None:
    res = run_audit("https://example.com/product", options={"check_external_corroboration": False})
    meta = res["audit_metadata"]
    assert meta["status"] == "completed"
    assert meta["stages"]["crawl"] == "completed"
    assert meta["stages"]["jsonld"] == "completed"
    assert meta["stages"]["entities"] == "completed"
    assert meta["stages"]["freshness"] == "completed"
    assert meta["stages"]["corroboration"] == "disabled"
    assert meta["stages"]["conflicts"] == "completed"
    assert meta["stages"]["findings"] == "completed"
    assert "findings" in res["findings"]


# 2. Invalid target URL
def test_invalid_target_url() -> None:
    res = run_audit("not_a_valid_url")
    assert res["audit_metadata"]["status"] == "failed"
    assert res["audit_metadata"]["stages"]["crawl"] == "failed"
    assert len(res["errors"]) == 1
    assert res["errors"][0]["stage"] == "validation"


# 3. Crawler failure
@patch("freshness_run_audit.crawl_page")
def test_crawler_failure(mock_crawl: Any) -> None:
    mock_crawl.return_value = {
        "status": "connection_error",
        "requested_url": "https://down.com",
        "error_type": "ConnectionRefusedError",
        "error_message": "Connection refused",
    }
    res = run_audit("https://down.com")
    assert res["audit_metadata"]["status"] == "failed"
    assert res["audit_metadata"]["stages"]["crawl"] == "failed"
    assert res["audit_metadata"]["stages"]["jsonld"] == "skipped"
    assert len(res["errors"]) == 1


# 4. Non-HTML response
@patch("freshness_run_audit.crawl_page")
def test_non_html_response(mock_crawl: Any) -> None:
    mock_crawl.return_value = {
        "status": "success",
        "requested_url": "https://example.com/binary.pdf",
        "final_url": "https://example.com/binary.pdf",
        "status_code": 200,
        "content_type": "application/pdf",
        "headers": {"content-type": "application/pdf"},
        "html": "",
    }
    res = run_audit("https://example.com/binary.pdf")
    assert res["audit_metadata"]["stages"]["jsonld"] == "not_applicable"


# 5. JSON-LD disabled
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_jsonld_disabled(mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_jsonld": False})
    assert res["audit_metadata"]["stages"]["jsonld"] == "disabled"
    assert res["structured_data"]["status"] == "disabled"


# 6. Entity analysis disabled
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_entity_analysis_disabled(mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_entities": False})
    assert res["audit_metadata"]["stages"]["entities"] == "disabled"
    assert res["entities"]["status"] == "disabled"


# 7. Freshness analysis disabled
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_freshness_analysis_disabled(mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_freshness": False})
    assert res["audit_metadata"]["stages"]["freshness"] == "disabled"
    assert res["temporal"]["status"] == "disabled"


# 8. External corroboration disabled
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_external_corroboration_disabled(mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_external_corroboration": False})
    assert res["audit_metadata"]["stages"]["corroboration"] == "disabled"
    assert res["corroboration"]["status"] == "disabled"


# 9. Conflict detection disabled
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_conflict_detection_disabled(mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_conflicts": False})
    assert res["audit_metadata"]["stages"]["conflicts"] == "disabled"
    assert res["conflicts"]["status"] == "disabled"


# 10. Partial JSON-LD failure
@patch("freshness_run_audit.crawl_page")
def test_partial_jsonld_failure(mock_crawl: Any) -> None:
    bad_html = "<html><script type='application/ld+json'>{bad json}</script></html>"
    mock_crawl.return_value = {
        "status": "success",
        "requested_url": "https://example.com/bad",
        "final_url": "https://example.com/bad",
        "status_code": 200,
        "content_type": "text/html",
        "headers": {},
        "html": bad_html,
    }
    res = run_audit("https://example.com/bad")
    assert res["audit_metadata"]["stages"]["jsonld"] == "completed"
    # Finding builder reports malformed JSON-LD
    codes = [f["code"] for f in res["findings"]["findings"]]
    assert "malformed_jsonld" in codes


# 11. Entity analyzer failure handled gracefully
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.analyze_entities", side_effect=ValueError("Entity crash"))
def test_entity_analyzer_failure(mock_ent: Any, mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    assert res["audit_metadata"]["stages"]["entities"] == "failed"
    assert any(err["stage"] == "entities" for err in res["errors"])
    # Audit still proceeds to completion
    assert res["audit_metadata"]["status"] == "partial_failure"


# 12. Freshness analyzer failure handled gracefully
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.analyze_freshness", side_effect=RuntimeError("Freshness crash"))
def test_freshness_analyzer_failure(mock_fresh: Any, mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    assert res["audit_metadata"]["stages"]["freshness"] == "failed"
    assert any(err["stage"] == "freshness" for err in res["errors"])


# 13. External corroborator failure handled gracefully
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.corroborate", side_effect=TimeoutError("Corroborator timeout"))
def test_external_corroborator_failure(mock_corrob: Any, mock_crawl: Any) -> None:
    res = run_audit("https://example.com", options={"check_external_corroboration": True})
    assert res["audit_metadata"]["stages"]["corroboration"] == "failed"
    assert any(err["stage"] == "corroboration" for err in res["errors"])


# 14. Conflict detector failure handled gracefully
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.detect_conflicts", side_effect=KeyError("Missing key"))
def test_conflict_detector_failure(mock_conf: Any, mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    assert res["audit_metadata"]["stages"]["conflicts"] == "failed"
    assert any(err["stage"] == "conflicts" for err in res["errors"])


# 15. Finding builder failure handled gracefully
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.build_findings", side_effect=Exception("Finding build crash"))
def test_finding_builder_failure(mock_fb: Any, mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    assert res["audit_metadata"]["stages"]["findings"] == "failed"
    assert res["findings"]["findings"] == []


# 16. Total budget exhaustion
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("time.monotonic")
def test_total_budget_exhaustion(mock_time: Any, mock_crawl: Any) -> None:
    # Jump time by 400s (budget is 300s)
    mock_time.side_effect = [0.0, 1.0, 2.0, 400.0, 400.0, 400.0, 400.0, 400.0]
    res = run_audit("https://example.com", options={"total_budget_ms": 300_000})
    assert res["audit_metadata"]["budget_exhausted"] is True


# 17. Correct propagation of options
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.build_findings")
def test_propagation_of_options(mock_fb: Any, mock_crawl: Any) -> None:
    mock_fb.return_value = {"summary": {"total_findings": 0}, "findings": []}
    custom_opts = {"max_findings": 42, "require_jsonld": True}
    run_audit("https://example.com", options=custom_opts)
    # Verify max_findings is passed to build_findings
    call_opts = mock_fb.call_args[1].get("options", {})
    assert call_opts.get("max_findings") == 42
    assert call_opts.get("require_jsonld") is True


# 18. Correct propagation of entities and relationships
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.analyze_entities")
def test_propagation_of_entities(mock_ent: Any, mock_crawl: Any) -> None:
    mock_ent.return_value = {"entities": [{"id": "p1"}]}
    run_audit("https://example.com")
    assert mock_ent.called
    passed_entities = mock_ent.call_args[0][0]
    assert len(passed_entities) == 1
    assert passed_entities[0]["id"] == "https://example.com/item#1"


# 19. Correct propagation of page metadata
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.analyze_freshness")
def test_propagation_of_page_metadata(mock_fresh: Any, mock_crawl: Any) -> None:
    mock_fresh.return_value = {"observations": []}
    run_audit("https://example.com")
    assert mock_fresh.called
    page_meta = mock_fresh.call_args[0][2]
    assert "last_modified" in page_meta
    assert "Wed, 01 Jan 2025" in str(page_meta["last_modified"])


# 20. No cross-agent imports
def test_no_cross_agent_imports() -> None:
    import inspect
    import freshness_run_audit as runner_mod

    source = inspect.getsource(runner_mod)
    assert "crawl-render-audit" not in source
    assert "crawl_render_audit" not in source


# 21. Deterministic output shape
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_deterministic_output_shape(mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    assert "audit_metadata" in res
    assert "crawl" in res
    assert "structured_data" in res
    assert "entities" in res
    assert "temporal" in res
    assert "corroboration" in res
    assert "conflicts" in res
    assert "findings" in res
    assert "errors" in res


# 22. JSON serialization
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_json_serialization(mock_crawl: Any) -> None:
    res = run_audit("https://example.com")
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["audit_metadata"]["skill"] == "freshness-corroboration-audit"


# 23. CLI outputs valid JSON
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_cli_outputs_valid_json(mock_crawl: Any, monkeypatch: Any) -> None:
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["freshness_run_audit.py", "https://example.com"])
    monkeypatch.setattr(sys, "stdout", captured_stdout)
    monkeypatch.setattr(sys, "stderr", captured_stderr)

    ret = main()
    assert ret == 0
    raw_out = captured_stdout.getvalue().strip()
    data = json.loads(raw_out)
    assert data["audit_metadata"]["target_url"] == "https://example.com"


# 24. Logs do not contaminate stdout
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_logs_do_not_contaminate_stdout(mock_crawl: Any, monkeypatch: Any) -> None:
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["freshness_run_audit.py", "https://example.com", "--verbose"])
    monkeypatch.setattr(sys, "stdout", captured_stdout)
    monkeypatch.setattr(sys, "stderr", captured_stderr)

    ret = main()
    assert ret == 0
    # stdout should parse cleanly as JSON without leading/trailing log text
    parsed = json.loads(captured_stdout.getvalue())
    assert isinstance(parsed, dict)


# 25. Findings do not cause nonzero CLI exit
@patch("freshness_run_audit.crawl_page")
def test_findings_do_not_cause_nonzero_cli_exit(mock_crawl: Any, monkeypatch: Any) -> None:
    # Return HTML with malformed JSON-LD which creates findings
    mock_crawl.return_value = {
        "status": "success",
        "requested_url": "https://example.com",
        "final_url": "https://example.com",
        "status_code": 200,
        "content_type": "text/html",
        "headers": {},
        "html": "<html><script type='application/ld+json'>{bad}</script></html>",
    }
    captured_stdout = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["freshness_run_audit.py", "https://example.com"])
    monkeypatch.setattr(sys, "stdout", captured_stdout)

    ret = main()
    assert ret == 0


# 26. Max findings option passed to finding_builder
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.build_findings")
def test_max_findings_passed(mock_fb: Any, mock_crawl: Any) -> None:
    mock_fb.return_value = {"summary": {}, "findings": []}
    run_audit("https://example.com", options={"max_findings": 15})
    opts = mock_fb.call_args[1].get("options", {})
    assert opts.get("max_findings") == 15


# 27. External source/claim limits passed to corroborator
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.corroborate")
def test_corroborator_limits_passed(mock_corrob: Any, mock_crawl: Any) -> None:
    mock_corrob.return_value = {"sources": [], "identity_checks": [], "claim_checks": []}
    run_audit(
        "https://example.com",
        options={"max_external_sources": 2, "max_claims": 4, "check_external_corroboration": True},
    )
    opts = mock_corrob.call_args[0][3]
    assert opts.get("max_external_sources") == 2
    assert opts.get("max_claims") == 4


# 28. Reference time is propagated
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
@patch("freshness_run_audit.analyze_freshness")
def test_reference_time_propagated(mock_fresh: Any, mock_crawl: Any) -> None:
    mock_fresh.return_value = {"observations": []}
    run_audit("https://example.com", options={"reference_time": "2026-09-02T00:00:00Z"})
    passed_opts = mock_fresh.call_args[0][3]
    assert passed_opts.get("reference_time") == "2026-09-02T00:00:00Z"


# 29. Stage status correctly identifies disabled/skipped/failed stages
def test_stage_status_identification() -> None:
    res = run_audit(
        "https://example.com",
        options={
            "check_jsonld": False,
            "check_entities": False,
            "check_freshness": False,
            "check_external_corroboration": False,
            "check_conflicts": False,
        },
    )
    stages = res["audit_metadata"]["stages"]
    assert stages["jsonld"] == "disabled"
    assert stages["entities"] == "disabled"
    assert stages["freshness"] == "disabled"
    assert stages["corroboration"] == "disabled"
    assert stages["conflicts"] == "disabled"


# 30. No network is performed by run_audit.py itself
@patch("socket.socket")
@patch("urllib.request.urlopen")
@patch("freshness_run_audit.crawl_page", side_effect=_mock_crawl_success)
def test_no_network_by_run_audit(mock_crawl: Any, mock_urlopen: Any, mock_socket: Any) -> None:
    run_audit("https://example.com", options={"check_external_corroboration": False})
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()
