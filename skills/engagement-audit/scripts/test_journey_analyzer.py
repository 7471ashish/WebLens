"""
Unit test suite for journey_analyzer.py using pytest.
Tests all 30 user journey and friction scenarios deterministically.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import PageInputData
from journey_analyzer import (
    JourneyAnalyzer,
    NormalizedJourney,
    NormalizedJourneyStep,
    analyze_journey,
    normalize_journey_evidence,
)


def test_1_valid_simple_journey_passes():
    """1. Test clean two-step journey with goal completed passes with score 100."""
    data = {
        "url": "https://example.com",
        "journey": {
            "name": "signup_flow",
            "goal": "Create a new user account",
            "goal_reached": True,
            "steps": [
                {"id": "step-1", "label": "Landing Page", "action": "Sign Up", "target": "step-2", "status": "completed"},
                {"id": "step-2", "label": "Confirmation", "action": "Go to Dashboard", "status": "completed", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.status == "passed"
    assert res.score == 100
    assert len(res.findings) == 0


def test_2_journey_with_clear_goal():
    """2. Test multi-step journey with explicit goal produces 0 goal clarity findings."""
    data = {
        "url": "https://example.com",
        "journey": {
            "name": "checkout_flow",
            "goal": "Purchase subscription",
            "steps": [
                {"id": "s1", "action": "Next", "target": "s2"},
                {"id": "s2", "action": "Finish", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    goal_f = [f for f in res.findings if f.id == "ENG-JOURNEY-001"]
    assert len(goal_f) == 0


def test_3_missing_journey_returns_insufficient_evidence():
    """3. Test missing journey field returns status 'insufficient_evidence'."""
    res = analyze_journey({"url": "https://example.com"})
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_4_missing_goal_on_multistep_journey():
    """4. Test multi-step journey without goal generates ENG-JOURNEY-001."""
    data = {
        "url": "https://example.com",
        "journey": {
            "name": "nameless_flow",
            "steps": [
                {"id": "s1", "action": "Next", "target": "s2"},
                {"id": "s2", "action": "Finish", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    goal_f = [f for f in res.findings if f.id == "ENG-JOURNEY-001"]
    assert len(goal_f) == 1
    assert goal_f[0].severity == "low"


def test_5_empty_steps_returns_insufficient_evidence():
    """5. Test empty steps list returns status 'insufficient_evidence'."""
    data = {"url": "https://example.com", "journey": {"steps": []}}
    res = analyze_journey(data)
    assert res.status == "insufficient_evidence"


def test_6_single_step_journey_valid():
    """6. Test single step journey analyzed without error."""
    data = {
        "url": "https://example.com",
        "journey": {
            "name": "quick_action",
            "goal": "Download Brochure",
            "steps": [{"id": "s1", "action": "Download Now", "is_terminal": True}],
        },
    }

    res = analyze_journey(data)
    assert res.status == "passed"


def test_7_excessive_steps_with_expected_steps():
    """7. Test actual steps exceeding expected_steps generates ENG-JOURNEY-002."""
    data = {
        "url": "https://example.com",
        "journey": {
            "name": "registration",
            "goal": "Register",
            "expected_steps": 2,
            "steps": [
                {"id": "s1", "target": "s2"},
                {"id": "s2", "target": "s3"},
                {"id": "s3", "target": "s4"},
                {"id": "s4", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    exc_f = [f for f in res.findings if f.id == "ENG-JOURNEY-002"]
    assert len(exc_f) == 1
    assert exc_f[0].severity == "low"


def test_8_unclear_next_action_on_intermediate_step():
    """8. Test intermediate step without action and target generates ENG-JOURNEY-003."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Subscribe",
            "steps": [
                {"id": "s1"},  # missing action and target
                {"id": "s2", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    act_f = [f for f in res.findings if f.id == "ENG-JOURNEY-003"]
    assert len(act_f) == 1
    assert act_f[0].severity == "medium"


def test_9_explicit_dead_end():
    """9. Test step marked dead_end=True generates ENG-JOURNEY-004 (high severity)."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Checkout",
            "steps": [
                {"id": "s1", "target": "s2"},
                {"id": "s2", "dead_end": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.status == "failed"
    dead_f = [f for f in res.findings if f.id == "ENG-JOURNEY-004"]
    assert len(dead_f) == 1
    assert dead_f[0].severity == "high"


def test_10_broken_transition_invalid_target():
    """10. Test target referencing non-existent step generates ENG-JOURNEY-005."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Login",
            "steps": [
                {"id": "s1", "action": "Submit", "target": "non-existent-step-999"},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.status == "failed"
    brk_f = [f for f in res.findings if f.id == "ENG-JOURNEY-005"]
    assert len(brk_f) == 1
    assert brk_f[0].severity == "high"


def test_11_transition_status_failed():
    """11. Test transition with status='failed' generates ENG-JOURNEY-005."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Payment",
            "steps": [
                {"id": "s1", "status": "failed", "target": "s2"},
                {"id": "s2", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    brk_f = [f for f in res.findings if f.id == "ENG-JOURNEY-005"]
    assert len(brk_f) == 1


def test_12_forced_journey_restart():
    """12. Test step with restart_required=True generates ENG-JOURNEY-006 (high severity)."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Form Submission",
            "steps": [
                {"id": "s1", "target": "s2"},
                {"id": "s2", "restart_required": True, "target": "s1"},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.status == "failed"
    rst_f = [f for f in res.findings if f.id == "ENG-JOURNEY-006"]
    assert len(rst_f) == 1
    assert rst_f[0].severity == "high"


def test_13_repeated_input_fields():
    """13. Test identical field requested across multiple steps generates ENG-JOURNEY-007."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Profile Setup",
            "steps": [
                {"id": "s1", "fields": ["email", "full_name"], "target": "s2"},
                {"id": "s2", "fields": ["email", "phone_number"], "target": "s3"},
                {"id": "s3", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    rep_f = [f for f in res.findings if f.id == "ENG-JOURNEY-007"]
    assert len(rep_f) == 1
    assert rep_f[0].severity == "low"


def test_14_error_without_recovery():
    """14. Test error without recovery_action generates ENG-JOURNEY-008."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Upload",
            "steps": [
                {"id": "s1", "error": "Server 500: Upload failed", "recovery_action": None},
            ],
        },
    }

    res = analyze_journey(data)
    err_f = [f for f in res.findings if f.id == "ENG-JOURNEY-008"]
    assert len(err_f) == 1
    assert err_f[0].severity == "high"


def test_15_error_with_recovery_valid():
    """15. Test error with recovery_action does not trigger unrecoverable error finding."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Upload",
            "steps": [
                {"id": "s1", "error": "Timeout", "recovery_action": "Retry Upload", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    err_f = [f for f in res.findings if f.id == "ENG-JOURNEY-008"]
    assert len(err_f) == 0


def test_16_missing_confirmation_state():
    """16. Test multi-step journey ending with goal_reached=False generates ENG-JOURNEY-009."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Account Verification",
            "goal_reached": False,
            "steps": [
                {"id": "s1", "target": "s2"},
                {"id": "s2", "status": "pending"},
            ],
        },
    }

    res = analyze_journey(data)
    conf_f = [f for f in res.findings if f.id == "ENG-JOURNEY-009"]
    assert len(conf_f) == 1
    assert conf_f[0].severity == "medium"


def test_17_successful_journey():
    """17. Test completed journey produces 0 confirmation findings."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Demo Signup",
            "goal_reached": True,
            "steps": [
                {"id": "s1", "target": "s2", "status": "completed"},
                {"id": "s2", "status": "completed", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.status == "passed"


def test_18_unavailable_required_action():
    """18. Test step with unavailable_action=True generates ENG-JOURNEY-010."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Subscription",
            "steps": [
                {"id": "s1", "unavailable_action": True},
            ],
        },
    }

    res = analyze_journey(data)
    unav_f = [f for f in res.findings if f.id == "ENG-JOURNEY-010"]
    assert len(unav_f) == 1
    assert unav_f[0].severity == "high"


def test_19_unexpected_redirect():
    """19. Test step with unexpected_redirect=True generates ENG-JOURNEY-011."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Download App",
            "steps": [
                {"id": "s1", "unexpected_redirect": True, "target": "s2"},
                {"id": "s2", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    redir_f = [f for f in res.findings if f.id == "ENG-JOURNEY-011"]
    assert len(redir_f) == 1
    assert redir_f[0].severity == "medium"


def test_20_backward_transition_valid():
    """20. Test intentional backward transition is not penalized as broken."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Edit Information",
            "steps": [
                {"id": "s1", "target": "s2"},
                {"id": "s2", "action": "Back to Step 1", "target": "s1", "is_intentional_backtrack": True},
            ],
        },
    }

    res = analyze_journey(data)
    brk_f = [f for f in res.findings if f.id == "ENG-JOURNEY-005"]
    assert len(brk_f) == 0


def test_21_multiple_branches():
    """21. Test branching multi-step journey analyzed cleanly."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Select Plan",
            "steps": [
                {"id": "plan-select", "target": "billing-monthly"},
                {"id": "billing-monthly", "target": "checkout"},
                {"id": "billing-annual", "target": "checkout"},
                {"id": "checkout", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.metrics["steps_analyzed"] == 4


def test_22_duplicate_step_ids():
    """22. Test duplicate step IDs generates ENG-JOURNEY-012."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Test",
            "steps": [
                {"id": "step-dup", "target": "step-2"},
                {"id": "step-dup", "target": "step-3"},
                {"id": "step-2", "is_terminal": True},
                {"id": "step-3", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    dup_f = [f for f in res.findings if f.id == "ENG-JOURNEY-012"]
    assert len(dup_f) == 1
    assert dup_f[0].severity == "low"


def test_23_malformed_step_data_handled_safely():
    """23. Test None, numbers, and corrupt objects in steps list."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Test",
            "steps": [
                None,
                "corrupt string",
                12345,
                {"id": "s1", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert res.metrics["steps_analyzed"] == 1


def test_24_deterministic_output():
    """24. Test identical findings across repeated runs."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Test",
            "steps": [{"id": "s1", "dead_end": True}],
        },
    }

    res1 = analyze_journey(data)
    res2 = analyze_journey(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_25_score_clamping():
    """25. Test score is integer bounded between 0 and 100."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Test",
            "steps": [
                {"id": "s1", "dead_end": True, "unavailable_action": True, "restart_required": True},
            ],
        },
    }

    res = analyze_journey(data)
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100
    assert res.score < 50


def test_26_insufficient_evidence_on_none():
    """26. Test None input returns status 'insufficient_evidence'."""
    res = analyze_journey(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_27_critical_blocking_journey_failure():
    """27. Test failed transition on terminal step generates critical severity."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Finalize Payment",
            "goal_reached": False,
            "steps": [
                {"id": "checkout", "status": "failed"},
            ],
        },
    }

    res = analyze_journey(data)
    crit_f = [f for f in res.findings if f.severity == "critical"]
    assert len(crit_f) == 1


def test_28_legitimate_terminal_state_not_flagged():
    """28. Test terminal confirmation step is not falsely flagged as dead end."""
    data = {
        "url": "https://example.com",
        "journey": {
            "goal": "Order Placement",
            "goal_reached": True,
            "steps": [
                {"id": "step-review", "target": "step-confirm"},
                {"id": "step-confirm", "type": "confirmation", "is_terminal": True},
            ],
        },
    }

    res = analyze_journey(data)
    dead_f = [f for f in res.findings if f.id == "ENG-JOURNEY-004"]
    assert len(dead_f) == 0


def test_29_dictionary_alias_flow():
    """29. Test dictionary key alias 'flow'."""
    data = {
        "url": "https://example.com",
        "flow": {
            "name": "onboarding",
            "goal": "Complete Setup",
            "steps": [{"id": "step-1", "is_terminal": True}],
        },
    }

    res = analyze_journey(data)
    assert res.metrics["steps_analyzed"] == 1


def test_30_missing_optional_fields_handled_safely():
    """30. Test minimal dictionary with missing optional fields."""
    data = {
        "url": "https://example.com",
        "journey": {
            "steps": [{"id": "s1"}],
        },
    }

    res = analyze_journey(data)
    assert isinstance(res.score, int)
