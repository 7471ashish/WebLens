"""
Unit tests for the independent freshness analyzer module.

Tests all 23 requirements without external network access or LLM inference.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from freshness_analyzer import analyze_freshness


# 1. dateCreated parsing
def test_date_created_parsing() -> None:
    entities = [
        {
            "id": "item-1",
            "dateCreated": "2024-05-10",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2024-06-01T00:00:00Z"})
    ent = res["entities"][0]
    t = ent["temporal"]["dateCreated"]
    assert t["parse_status"] == "valid"
    assert t["normalized"] == "2024-05-10T00:00:00Z"


# 2. datePublished parsing
def test_date_published_parsing() -> None:
    entities = [
        {
            "id": "item-2",
            "datePublished": "2025-01-15T10:30:00",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2025-02-01T00:00:00Z"})
    t = res["entities"][0]["temporal"]["datePublished"]
    assert t["parse_status"] == "valid"
    assert t["normalized"] == "2025-01-15T10:30:00Z"


# 3. dateModified parsing
def test_date_modified_parsing() -> None:
    entities = [
        {
            "id": "item-3",
            "dateModified": "2025-08-20T14:00:00Z",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2025-09-01T00:00:00Z"})
    t = res["entities"][0]["temporal"]["dateModified"]
    assert t["parse_status"] == "valid"
    assert t["normalized"] == "2025-08-20T14:00:00Z"


# 4. ISO 8601 Z timestamps
def test_iso_z_timestamps() -> None:
    entities = [{"id": "item-4", "datePublished": "2026-03-01T00:00:00Z"}]
    res = analyze_freshness(entities, options={"reference_time": "2026-04-01T00:00:00Z"})
    assert res["entities"][0]["temporal"]["datePublished"]["normalized"] == "2026-03-01T00:00:00Z"


# 5. ISO timestamps with offsets
def test_iso_timestamps_with_offsets() -> None:
    entities = [{"id": "item-5", "datePublished": "2026-03-01T12:00:00+05:00"}]
    res = analyze_freshness(entities, options={"reference_time": "2026-04-01T00:00:00Z"})
    # Converted to UTC
    assert res["entities"][0]["temporal"]["datePublished"]["normalized"] == "2026-03-01T07:00:00Z"


# 6. Malformed dates
def test_malformed_dates() -> None:
    entities = [{"id": "item-6", "dateModified": "not-a-valid-date"}]
    res = analyze_freshness(entities)
    t = res["entities"][0]["temporal"]["dateModified"]
    assert t["parse_status"] == "invalid"
    assert t["normalized"] is None
    assert res["summary"]["invalid_temporal_values"] == 1
    assert any(obs["code"] == "invalid_temporal_value" for obs in res["observations"])


# 7. Created-after-published detection
def test_created_after_published() -> None:
    entities = [
        {
            "id": "item-7",
            "dateCreated": "2026-05-01",
            "datePublished": "2026-04-01",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-06-01T00:00:00Z"})
    assert any(obs["code"] == "created_after_published" for obs in res["observations"])


# 8. Modified-before-published detection
def test_modified_before_published() -> None:
    entities = [
        {
            "id": "item-8",
            "datePublished": "2026-08-01",
            "dateModified": "2026-07-01",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-09-01T00:00:00Z"})
    assert any(obs["code"] == "modified_before_published" for obs in res["observations"])


# 9. Modified-before-created detection
def test_modified_before_created() -> None:
    entities = [
        {
            "id": "item-9",
            "dateCreated": "2026-05-01",
            "dateModified": "2026-04-01",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-06-01T00:00:00Z"})
    assert any(obs["code"] == "modified_before_created" for obs in res["observations"])


# 10. Future date detection
def test_future_date_detection() -> None:
    entities = [
        {
            "id": "item-10",
            "datePublished": "2028-01-01",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-09-02T00:00:00Z"})
    assert res["summary"]["future_dates"] == 1
    assert res["entities"][0]["freshness"]["status"] == "future_dated"
    assert any(obs["code"] == "future_temporal_value" for obs in res["observations"])


# 11. Missing temporal metadata
def test_missing_temporal_metadata() -> None:
    entities = [{"id": "item-11", "name": "No Dates"}]
    res = analyze_freshness(entities)
    assert res["summary"]["entities_without_temporal_metadata"] == 1
    assert res["entities"][0]["freshness"]["status"] == "unknown"
    assert res["entities"][0]["freshness"]["reason"] == "no_valid_temporal_metadata"


# 12. Freshness age calculation
def test_freshness_age_calculation() -> None:
    entities = [
        {
            "id": "item-12",
            "dateModified": "2026-08-01T00:00:00Z",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-08-31T00:00:00Z"})
    assert res["entities"][0]["freshness"]["age_days"] == 30
    assert res["entities"][0]["freshness"]["status"] == "fresh"


# 13. Stale threshold
def test_stale_threshold() -> None:
    entities = [
        {
            "id": "item-13",
            "dateModified": "2025-01-01T00:00:00Z",
        }
    ]
    # 500 days old -> exceeds 365 days default
    res = analyze_freshness(entities, options={"reference_time": "2026-05-16T00:00:00Z"})
    assert res["entities"][0]["freshness"]["status"] == "possibly_stale"
    assert res["summary"]["possibly_stale_entities"] == 1
    assert any(obs["code"] == "dateModified_old" for obs in res["observations"])


# 14. Very-stale threshold
def test_very_stale_threshold() -> None:
    entities = [
        {
            "id": "item-14",
            "dateModified": "2023-01-01T00:00:00Z",
        }
    ]
    # > 800 days old -> exceeds 730 days default
    res = analyze_freshness(entities, options={"reference_time": "2026-05-01T00:00:00Z"})
    assert res["entities"][0]["freshness"]["status"] == "very_stale"
    assert res["summary"]["very_stale_entities"] == 1
    assert any(obs["code"] == "dateModified_very_old" for obs in res["observations"])


# 15. Configurable thresholds
def test_configurable_thresholds() -> None:
    entities = [
        {
            "id": "item-15",
            "dateModified": "2026-08-01T00:00:00Z",
        }
    ]
    # 40 days old with stale threshold set to 30
    res = analyze_freshness(
        entities,
        options={
            "reference_time": "2026-09-10T00:00:00Z",
            "stale_after_days": 30,
        },
    )
    assert res["entities"][0]["freshness"]["status"] == "possibly_stale"


# 16. reference_time deterministic execution
def test_reference_time_deterministic() -> None:
    entities = [{"id": "item-16", "dateModified": "2025-01-01T00:00:00Z"}]
    res1 = analyze_freshness(entities, options={"reference_time": "2026-09-02T00:00:00Z"})
    res2 = analyze_freshness(entities, options={"reference_time": "2026-09-02T00:00:00Z"})
    assert res1["entities"][0]["freshness"]["age_days"] == res2["entities"][0]["freshness"]["age_days"]
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)


# 17. dateModified == datePublished
def test_date_modified_equals_date_published() -> None:
    entities = [
        {
            "id": "item-17",
            "datePublished": "2026-01-01",
            "dateModified": "2026-01-01",
        }
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-02-01T00:00:00Z"})
    assert "dateModified_equals_datePublished" in res["entities"][0]["freshness"]["signals"]
    # Ensure this equality is NOT logged as a chronological inconsistency
    assert not any(obs["code"] == "modified_before_published" for obs in res["observations"])


# 18. HTTP Last-Modified parsing
def test_http_last_modified_parsing() -> None:
    page_meta = {"headers": {"Last-Modified": "Wed, 01 Jan 2026 12:00:00 GMT"}}
    res = analyze_freshness([], page_metadata=page_meta)
    sig = res["http_temporal_signals"]["last_modified"]
    assert sig["parse_status"] == "valid"
    assert sig["normalized"] == "2026-01-01T12:00:00Z"


# 19. HTTP Last-Modified vs Schema.org dateModified separation
def test_http_last_modified_vs_schema_date_separation() -> None:
    entities = [
        {
            "id": "item-19",
            "dateModified": "2026-08-01T00:00:00Z",
        }
    ]
    page_meta = {"headers": {"last_modified": "Mon, 10 Aug 2026 00:00:00 GMT"}}
    res = analyze_freshness(entities, page_metadata=page_meta, options={"reference_time": "2026-09-01T00:00:00Z"})
    assert any(obs["code"] == "http_last_modified_differs_from_content_date" for obs in res["observations"])
    # Verify no severity or error label is generated
    serialized = json.dumps(res)
    assert '"severity"' not in serialized
    assert '"conflict"' not in serialized


# 20. Multiple entities analysis
def test_multiple_entities_analysis() -> None:
    entities = [
        {"id": "e1", "types": ["Article"], "datePublished": "2026-01-01"},
        {"id": "e2", "types": ["Product"], "dateModified": "2026-05-01"},
    ]
    res = analyze_freshness(entities, options={"reference_time": "2026-06-01T00:00:00Z"})
    assert res["summary"]["entities_analyzed"] == 2
    assert res["summary"]["entities_with_temporal_metadata"] == 2


# 21. Entity with no dates
def test_entity_with_no_dates() -> None:
    entities = [{"id": "e-empty", "types": ["Organization"], "name": "Corp"}]
    res = analyze_freshness(entities)
    assert res["summary"]["entities_without_temporal_metadata"] == 1
    assert res["entities"][0]["freshness"]["status"] == "unknown"


# 22. Deterministic output
def test_deterministic_output() -> None:
    entities = [
        {"id": "d1", "datePublished": "2025-10-01", "dateModified": "2026-01-01"},
        {"id": "d2", "name": "Item"},
    ]
    res1 = analyze_freshness(entities, options={"reference_time": "2026-09-02T00:00:00Z"})
    res2 = analyze_freshness(entities, options={"reference_time": "2026-09-02T00:00:00Z"})
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)


# 23. Max entities bound
def test_max_entities_bound() -> None:
    entities = [{"id": f"e-{i}", "datePublished": "2026-01-01"} for i in range(25)]
    res = analyze_freshness(entities, options={"max_entities": 10})
    assert res["summary"]["entities_analyzed"] == 10
    assert len(res["entities"]) == 10
