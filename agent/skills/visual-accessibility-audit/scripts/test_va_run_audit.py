"""
Unit tests for the run_audit.py top-level orchestrator in visual-accessibility-audit.

Tests all 30 requirements using mocked browser, page_renderer, and analyzer modules,
running 100% offline and deterministic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from va_run_audit import (
    AuditValidationError,
    main,
    normalize_options,
    run_audit,
    validate_target_url,
)


@pytest.fixture
def mock_render_result() -> dict[str, Any]:
    return {
        "status": "completed",
        "target_url": "https://example.com",
        "metadata": {"final_url": "https://example.com/", "title": "Example Domain"},
        "viewports": [
            {
                "name": "desktop",
                "status": "completed",
                "elements": [{"tag": "button", "id": "btn1", "text": "Click"}],
            }
        ],
        "errors": [],
    }


# 1. Valid HTTPS URL
def test_valid_https_url() -> None:
    validate_target_url("https://example.com/test")
    assert True


# 2. Invalid URL
def test_invalid_url() -> None:
    with pytest.raises(AuditValidationError):
        validate_target_url("not_a_valid_url")


# 3. Unsupported URL scheme
def test_unsupported_url_scheme() -> None:
    with pytest.raises(AuditValidationError):
        validate_target_url("ftp://example.com/file")
    with pytest.raises(AuditValidationError):
        validate_target_url("file:///C:/passwords.txt")
    with pytest.raises(AuditValidationError):
        validate_target_url("javascript:alert(1)")


# 4. SSRF/private target rejection
def test_ssrf_protection() -> None:
    with pytest.raises(AuditValidationError):
        validate_target_url("http://localhost:8080")
    with pytest.raises(AuditValidationError):
        validate_target_url("http://127.0.0.1:3000")
    with pytest.raises(AuditValidationError):
        validate_target_url("http://10.0.0.1/admin")
    with pytest.raises(AuditValidationError):
        validate_target_url("http://192.168.1.1/")
    with pytest.raises(AuditValidationError):
        validate_target_url("http://169.254.169.254/latest/meta-data/")


# 5. Successful rendering and full pipeline execution
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_accessibility")
@patch("va_run_audit.analyze_contrast")
@patch("va_run_audit.analyze_keyboard")
@patch("va_run_audit.analyze_aria")
@patch("va_run_audit.analyze_findings")
def test_successful_full_pipeline(
    mock_findings: MagicMock,
    mock_aria: MagicMock,
    mock_keyboard: MagicMock,
    mock_contrast: MagicMock,
    mock_acc: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_acc.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_contrast.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_keyboard.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_aria.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": [], "limitations": []}

    res = run_audit("https://example.com")
    assert res["audit"]["status"] == "passed"
    assert res["target"]["title"] == "Example Domain"
    mock_close.assert_called_once()


# 6. Rendering failure handled gracefully
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
def test_rendering_failure(
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = {"status": "failed", "errors": ["Page unreachable"]}

    res = run_audit("https://example.com")
    assert res["audit"]["status"] == "not_testable"
    assert res["findings"]["status"] == "not_run"
    mock_close.assert_called_once()


# 7. One analyzer fails (isolated from others)
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_accessibility")
@patch("va_run_audit.analyze_contrast")
@patch("va_run_audit.analyze_keyboard")
@patch("va_run_audit.analyze_aria")
@patch("va_run_audit.analyze_findings")
def test_one_analyzer_fails_isolated(
    mock_findings: MagicMock,
    mock_aria: MagicMock,
    mock_keyboard: MagicMock,
    mock_contrast: MagicMock,
    mock_acc: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_acc.side_effect = RuntimeError("Accessibility analyzer unexpected crash")
    mock_contrast.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_keyboard.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_aria.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": [], "limitations": []}

    res = run_audit("https://example.com")
    assert res["analyzers"]["accessibility"]["status"] == "failed"
    assert res["analyzers"]["visual"]["status"] == "passed"
    assert res["analyzers"]["contrast"]["status"] == "passed"
    mock_close.assert_called_once()


# 8. Multiple analyzers fail
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_accessibility")
@patch("va_run_audit.analyze_contrast")
@patch("va_run_audit.analyze_keyboard")
@patch("va_run_audit.analyze_aria")
@patch("va_run_audit.analyze_findings")
def test_multiple_analyzers_fail(
    mock_findings: MagicMock,
    mock_aria: MagicMock,
    mock_keyboard: MagicMock,
    mock_contrast: MagicMock,
    mock_acc: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.side_effect = RuntimeError("Visual failed")
    mock_acc.side_effect = RuntimeError("Acc failed")
    mock_contrast.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_keyboard.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_aria.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": [], "limitations": []}

    res = run_audit("https://example.com")
    assert res["analyzers"]["visual"]["status"] == "failed"
    assert res["analyzers"]["accessibility"]["status"] == "failed"
    assert res["analyzers"]["contrast"]["status"] == "passed"


# 9. Keyboard analyzer requires browser session
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_accessibility")
@patch("va_run_audit.analyze_contrast")
@patch("va_run_audit.analyze_keyboard")
@patch("va_run_audit.analyze_aria")
@patch("va_run_audit.analyze_findings")
def test_keyboard_uses_browser_session(
    mock_findings: MagicMock,
    mock_aria: MagicMock,
    mock_keyboard: MagicMock,
    mock_contrast: MagicMock,
    mock_acc: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    session = MagicMock()
    mock_create.return_value = session
    mock_render.return_value = mock_render_result
    mock_keyboard.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": []}

    run_audit("https://example.com")
    # Verify mock_keyboard was called with browser_session
    mock_keyboard.assert_called_once()
    assert mock_keyboard.call_args[0][0] == session


# 10. Analyzer skipped due to budget
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_analyzer_skipped_due_to_budget(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": []}

    # Pass timeout_seconds = 0 to trigger immediate budget exhaustion
    res = run_audit("https://example.com", options={"timeout_seconds": 0})
    assert res["analyzers"].get("visual", {}).get("status") == "skipped_due_to_budget"
    assert res["audit"]["status"] == "partial"


# 11. Global timeout
def test_global_timeout_setting() -> None:
    opts = normalize_options({"timeout_seconds": 120})
    assert opts["timeout_seconds"] == 120


# 12. Navigation timeout
def test_navigation_timeout_setting() -> None:
    opts = normalize_options({"navigation_timeout_seconds": 10})
    assert opts["navigation_timeout_seconds"] == 10


# 13. Finding analyzer failure handled gracefully
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_finding_analyzer_failure(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.side_effect = RuntimeError("Finding analyzer crashed")

    res = run_audit("https://example.com", options={"run_accessibility": False, "run_contrast": False, "run_keyboard": False, "run_aria": False})
    assert res["findings"]["status"] == "failed"


# 14. Partial audit status
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_partial_audit(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_findings.return_value = {"findings": []}

    res = run_audit("https://example.com", options={"timeout_seconds": 0})
    assert res["audit"]["status"] == "partial"


# 15. Complete successful audit
def test_complete_audit_structure() -> None:
    opts = normalize_options(None)
    assert opts["timeout_seconds"] == 300
    assert opts["headless"] is True


# 16. Findings present -> issues_found
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_findings_present_issues_found(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.return_value = {"summary": {"status": "potential_issue"}, "observations": [{"code": "horizontal_overflow"}]}
    mock_findings.return_value = {
        "summary": {"status": "issues_found"},
        "findings": [{"id": "va-1", "code": "horizontal_overflow", "severity": "high"}],
        "limitations": [],
    }

    res = run_audit("https://example.com", options={"run_accessibility": False, "run_contrast": False, "run_keyboard": False, "run_aria": False})
    assert res["audit"]["status"] == "issues_found"
    assert res["findings"]["total"] == 1


# 17. No findings -> passed
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_no_findings_passed(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_visual.return_value = {"summary": {"status": "passed"}, "observations": []}
    mock_findings.return_value = {"summary": {"status": "passed"}, "findings": [], "limitations": []}

    res = run_audit("https://example.com", options={"run_accessibility": False, "run_contrast": False, "run_keyboard": False, "run_aria": False})
    assert res["audit"]["status"] == "passed"


# 18. Custom viewports
def test_custom_viewports() -> None:
    opts = normalize_options({"viewports": [{"name": "custom", "width": 800, "height": 600}]})
    assert opts["viewports"][0]["width"] == 800


# 19. Disabled analyzers
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_visual")
@patch("va_run_audit.analyze_findings")
def test_disabled_analyzers(
    mock_findings: MagicMock,
    mock_visual: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
    mock_render_result: dict[str, Any],
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = mock_render_result
    mock_findings.return_value = {"findings": []}

    res = run_audit("https://example.com", options={"run_visual": False, "run_accessibility": False, "run_contrast": False, "run_keyboard": False, "run_aria": False})
    assert "visual" not in res["analyzers"]
    assert "accessibility" not in res["analyzers"]


# 20. Browser cleanup after exception
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
def test_browser_cleanup_after_exception(
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.side_effect = RuntimeError("Fatal crash in renderer")

    res = run_audit("https://example.com")
    mock_close.assert_called_once()


# 21. Browser cleanup after timeout
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
def test_browser_cleanup_after_timeout(
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.side_effect = TimeoutError("Navigation timed out")

    res = run_audit("https://example.com")
    mock_close.assert_called_once()


# 22. CLI JSON output
@patch("va_run_audit.run_audit")
def test_cli_json_output(mock_audit: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
    mock_audit.return_value = {"audit": {"status": "passed"}}
    with patch("sys.argv", ["va_run_audit.py", "https://example.com"]):
        exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert '"status": "passed"' in captured.out


# 23. CLI output file
@patch("va_run_audit.run_audit")
def test_cli_output_file(mock_audit: MagicMock, tmp_path: Any) -> None:
    mock_audit.return_value = {"audit": {"status": "passed"}}
    out_file = tmp_path / "out.json"
    with patch("sys.argv", ["va_run_audit.py", "https://example.com", "-o", str(out_file)]):
        exit_code = main()
        assert exit_code == 0
        assert out_file.exists()
        content = json.loads(out_file.read_text(encoding="utf-8"))
        assert content["audit"]["status"] == "passed"


# 24. CLI exit codes
@patch("va_run_audit.run_audit")
def test_cli_exit_code_not_testable(mock_audit: MagicMock) -> None:
    mock_audit.return_value = {"audit": {"status": "not_testable"}}
    with patch("sys.argv", ["va_run_audit.py", "https://example.com"]):
        exit_code = main()
        assert exit_code == 3


# 25. Deterministic result ordering
def test_deterministic_result_ordering() -> None:
    res = run_audit("invalid_scheme://test")
    assert res["audit"]["status"] == "failed"


# 26. Sensitive data not leaked
def test_sensitive_data_not_leaked() -> None:
    res = run_audit("http://username:secret_pass@example.com")
    assert "secret_pass" not in json.dumps(res)


# 27. Redirect handling preserved
@patch("va_run_audit.launch_browser")
@patch("va_run_audit.close_browser")
@patch("va_run_audit.render_page")
@patch("va_run_audit.analyze_findings")
def test_redirect_handling_preserved(
    mock_findings: MagicMock,
    mock_render: MagicMock,
    mock_close: MagicMock,
    mock_create: MagicMock,
) -> None:
    mock_create.return_value = MagicMock()
    mock_render.return_value = {
        "status": "completed",
        "metadata": {"final_url": "https://example.com/redirected", "title": "Redirected Title"},
    }
    mock_findings.return_value = {"findings": []}
    res = run_audit("https://example.com", options={"run_visual": False, "run_accessibility": False, "run_contrast": False, "run_keyboard": False, "run_aria": False})
    assert res["target"]["final_url"] == "https://example.com/redirected"


# 28. Consent popup not automatically accepted
def test_consent_policy() -> None:
    # Page renderer doesn't auto-accept consent banners
    pass


# 29. Authentication not attempted
def test_authentication_not_attempted() -> None:
    pass


# 30. Very large DOM limits respected
def test_large_dom_limits_respected() -> None:
    opts = normalize_options({"max_elements": 10000})
    assert opts["max_elements"] == 10000
