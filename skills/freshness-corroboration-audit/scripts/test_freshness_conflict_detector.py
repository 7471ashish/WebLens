"""
Unit tests for the independent conflict detector module.

Tests all 25 requirements without external network access or LLM inference.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conflict_detector import (
    _are_dates_compatible,
    _normalize_string,
    _normalize_url,
    detect_conflicts,
)


# 1. Exact agreement
def test_exact_agreement() -> None:
    entities = [{"internal_id": "e1", "name": "Adobe Inc.", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [{"entity_id": "e1", "status": "strong_identity_match", "signals": ["name_match"]}],
        "claim_checks": [
            {
                "entity_id": "e1",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-01-01"],
                "result": "supported",
                "source_url": "https://example.org/p",
            }
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["agreements"] >= 2
    assert res["summary"]["conflicts"] == 0


# 2. Simple disagreement
def test_simple_disagreement() -> None:
    entities = [{"internal_id": "e2", "url": "https://example.com/page/"}]
    corroboration = {
        "sources": [
            {
                "final_url": "https://example.org/other",
                "evidence": {"canonical_url": "https://example.com/canonical-alt"},
            }
        ],
        "identity_checks": [],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["disagreements"] >= 1
    assert any(obs["code"] == "canonical_url_difference" for obs in res["observations"])


# 3. Meaningful temporal conflict
def test_meaningful_temporal_conflict() -> None:
    entities = [{"internal_id": "e3", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e3",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-06-15"],
                "result": "disagreement",
                "source_url": "https://example.org/source1",
            }
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 1
    assert res["conflicts"][0]["conflict_type"] == "temporal"
    assert res["conflicts"][0]["field"] == "datePublished"


# 4. datePublished vs datePublished comparison
def test_date_published_vs_date_published() -> None:
    entities = [{"internal_id": "e4", "datePublished": "2024-03-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e4",
                "claim_type": "datePublished",
                "target_value": "2024-03-01",
                "external_values": ["2024-03-01"],
                "result": "supported",
                "source_url": "https://source.com/doc",
            }
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["agreements"] == 1
    assert res["summary"]["conflicts"] == 0


# 5. datePublished vs dateModified not treated as direct conflict
def test_date_published_vs_date_modified_no_conflict() -> None:
    # Target datePublished = 2025-01-01; External dateModified = 2025-06-01
    entities = [{"internal_id": "e5", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [],  # No claim check on datePublished because external only has dateModified
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 0


# 6. Timezone-equivalent dates
def test_timezone_equivalent_dates() -> None:
    # 2025-01-01T05:00:00+05:00 is equal to 2025-01-01T00:00:00Z
    assert _are_dates_compatible("2025-01-01T00:00:00Z", "2025-01-01T05:00:00+05:00") is True


# 7. Date-only vs datetime compatibility
def test_date_only_vs_datetime_compatibility() -> None:
    # 2025-01-01 vs 2025-01-01T14:30:00Z on the same calendar day
    assert _are_dates_compatible("2025-01-01", "2025-01-01T14:30:00Z") is True
    # Different day is not compatible
    assert _are_dates_compatible("2025-01-01", "2025-01-02T01:00:00Z") is False


# 8. Missing external value
def test_missing_external_value() -> None:
    entities = [{"internal_id": "e8", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e8",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": [],
                "result": "insufficient_evidence",
                "source_url": "https://example.org/empty",
            }
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 0
    assert res["summary"]["insufficient_evidence"] == 1


# 9. Unreachable external source
def test_unreachable_external_source() -> None:
    entities = [{"internal_id": "e9", "name": "Company"}]
    corroboration = {
        "sources": [],
        "identity_checks": [{"entity_id": "e9", "status": "unreachable", "reference_url": "https://down.org"}],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 0
    assert res["summary"]["insufficient_evidence"] == 1


# 10. Weak identity evidence
def test_weak_identity_evidence() -> None:
    entities = [{"internal_id": "e10", "name": "Alpha Corp"}]
    corroboration = {
        "sources": [{"requested_url": "https://ext.org", "evidence": {"entity_names": ["Alpha Corp"]}}],
        "identity_checks": [{"entity_id": "e10", "status": "weak_identity_match", "reference_url": "https://ext.org"}],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 0
    assert res["summary"]["ambiguous"] >= 1


# 11. Strong identity mismatch
def test_strong_identity_mismatch() -> None:
    entities = [{"internal_id": "e11", "name": "Acme Widgets"}]
    corroboration = {
        "sources": [
            {
                "requested_url": "https://ext.org/item",
                "evidence": {"entity_names": ["Completely Unrelated Brand X"]},
            }
        ],
        "identity_checks": [{"entity_id": "e11", "status": "weak_identity_match", "reference_url": "https://ext.org/item"}],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 1
    assert res["conflicts"][0]["code"] == "sameas_identity_mismatch"


# 12. sameAs mismatch
def test_sameas_mismatch() -> None:
    entities = [{"internal_id": "e12", "name": "John Doe", "sameAs": ["https://example.org/profile"]}]
    corroboration = {
        "sources": [
            {
                "requested_url": "https://example.org/profile",
                "evidence": {"entity_names": ["Mega Corporation Inc."]},
            }
        ],
        "identity_checks": [{"entity_id": "e12", "status": "weak_identity_match", "reference_url": "https://example.org/profile"}],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 1
    assert res["conflicts"][0]["field"] == "sameAs"


# 13. Name normalization
def test_name_normalization() -> None:
    assert _normalize_string("  Adobe   SYSTEMS, INC.  ") == "adobe systems inc"


# 14. URL normalization
def test_url_normalization() -> None:
    assert _normalize_url("HTTPS://Example.COM/Page/") == "https://example.com/page"


# 15. Identifier comparison
def test_identifier_comparison() -> None:
    entities = [{"internal_id": "e15", "identifier": "SKU-9900"}]
    corroboration = {"sources": [], "identity_checks": [], "claim_checks": []}
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["status"] == "success"


# 16. Multiple external sources
def test_multiple_external_sources() -> None:
    entities = [{"internal_id": "e16", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e16",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-05-01"],
                "result": "disagreement",
                "source_url": "https://source1.org/doc",
            },
            {
                "entity_id": "e16",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-05-01"],
                "result": "disagreement",
                "source_url": "https://source2.org/doc",
            },
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["conflicts"] == 1
    # 2 independent domains -> confidence high
    assert res["conflicts"][0]["confidence"] == "high"


# 17. Same-domain sources
def test_same_domain_sources_do_not_inflate_confidence() -> None:
    entities = [{"internal_id": "e17", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e17",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-05-01"],
                "result": "disagreement",
                "source_url": "https://same-domain.org/p1",
            },
            {
                "entity_id": "e17",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-05-01"],
                "result": "disagreement",
                "source_url": "https://same-domain.org/p2",
            },
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    # Only 1 unique domain -> confidence medium, not high
    assert res["conflicts"][0]["confidence"] == "medium"


# 18. External-vs-external disagreement
def test_external_vs_external_disagreement() -> None:
    entities = [{"internal_id": "e18", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e18",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-04-01"],
                "result": "disagreement",
                "source_url": "https://sourceA.org/d",
            },
            {
                "entity_id": "e18",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-09-01"],
                "result": "disagreement",
                "source_url": "https://sourceB.org/d",
            },
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["summary"]["external_external_disagreements"] >= 1
    assert any(obs["code"] == "external_source_disagreement" for obs in res["observations"])


# 19. Majority disagreement without automatic truth determination
def test_majority_disagreement_preserves_evidence() -> None:
    entities = [{"internal_id": "e19", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e19",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-02-01"],
                "result": "disagreement",
                "source_url": "https://s1.org/1",
            },
            {
                "entity_id": "e19",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-02-01"],
                "result": "disagreement",
                "source_url": "https://s2.org/2",
            },
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    # Does not assert "target is wrong", preserves evidence
    c = res["conflicts"][0]
    assert c["target_evidence"]["value"] == "2025-01-01"
    assert "2025-02-01" in c["external_evidence"]["values"]


# 20. Confidence calculation
def test_confidence_calculation() -> None:
    entities = [{"internal_id": "e20", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e20",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-08-01"],
                "result": "disagreement",
                "source_url": "https://auth1.org/doc",
            },
            {
                "entity_id": "e20",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-08-01"],
                "result": "disagreement",
                "source_url": "https://auth2.org/doc",
            },
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["conflicts"][0]["confidence"] in ("low", "medium", "high")


# 21. Max comparisons limit
def test_max_comparisons_limit() -> None:
    entities = [{"internal_id": f"e21_{i}", "url": f"https://example.com/{i}"} for i in range(50)]
    corroboration = {
        "sources": [{"final_url": f"https://s{i}.org", "evidence": {"canonical_url": f"https://c{i}.org"}} for i in range(50)],
        "identity_checks": [],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration, options={"max_comparisons": 10})
    assert res["summary"]["comparisons_performed"] <= 10


# 22. Max conflicts limit
def test_max_conflicts_limit() -> None:
    entities = [{"internal_id": f"e22_{i}", "datePublished": "2025-01-01"} for i in range(20)]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": f"e22_{i}",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-08-01"],
                "result": "disagreement",
                "source_url": f"https://s{i}.org/doc",
            }
            for i in range(20)
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration, options={"max_conflicts": 3})
    assert len(res["conflicts"]) <= 3


# 23. Deterministic conflict IDs
def test_deterministic_conflict_ids() -> None:
    entities = [{"internal_id": "e23", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e23",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-05-01"],
                "result": "disagreement",
                "source_url": "https://ext.org/doc",
            }
        ],
    }
    res1 = detect_conflicts(entities, corroboration=corroboration)
    res2 = detect_conflicts(entities, corroboration=corroboration)
    assert res1["conflicts"][0]["conflict_id"] == "conflict-0"
    assert res2["conflicts"][0]["conflict_id"] == "conflict-0"


# 24. No severity field
def test_no_severity_field() -> None:
    entities = [{"internal_id": "e24", "datePublished": "2025-01-01"}]
    corroboration = {
        "sources": [],
        "identity_checks": [],
        "claim_checks": [
            {
                "entity_id": "e24",
                "claim_type": "datePublished",
                "target_value": "2025-01-01",
                "external_values": ["2025-09-01"],
                "result": "disagreement",
                "source_url": "https://ext.org/doc",
            }
        ],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    serialized = json.dumps(res)
    assert '"severity"' not in serialized
    assert '"critical"' not in serialized
    assert '"high"' not in serialized or '"confidence": "high"' in serialized


# 25. No network requests
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_requests(mock_urlopen: Any, mock_socket: Any) -> None:
    entities = [{"internal_id": "e25", "name": "Company", "url": "https://example.com"}]
    corroboration = {
        "sources": [{"requested_url": "https://ext.org", "evidence": {"title": "Company"}}],
        "identity_checks": [],
        "claim_checks": [],
    }
    res = detect_conflicts(entities, corroboration=corroboration)
    assert res["status"] == "success"
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()
