"""
Unit tests for the independent entity analyzer module.

Tests all 29 audit requirements without external network access or LLM inference.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from entity_analyzer import analyze_entities


# 1. Organization with complete identity signals
def test_org_with_complete_identity_signals() -> None:
    entities = [
        {
            "types": ["Organization"],
            "id": "https://example.com/#org",
            "name": "Adobe Inc.",
            "url": "https://www.adobe.com/",
            "sameAs": ["https://www.wikidata.org/wiki/Q11463"],
            "identifier": "ADBE",
        }
    ]
    res = analyze_entities(entities)
    assert res["summary"]["entity_count"] == 1
    assert res["summary"]["strong_identity_count"] == 1
    ent = res["entities"][0]
    assert ent["identity_strength"] == "strong"
    assert ent["external_verification"] == "not_checked"
    assert ent["identity_signals"]["has_type"] is True
    assert ent["identity_signals"]["has_id"] is True
    assert ent["identity_signals"]["has_name"] is True
    assert ent["identity_signals"]["has_url"] is True
    assert ent["identity_signals"]["has_sameAs"] is True
    assert ent["identity_signals"]["has_identifier"] is True


# 2. Person with @id
def test_person_with_id() -> None:
    entities = [
        {
            "type": "Person",
            "id": "https://example.com/team#jane",
            "name": "Jane Doe",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["identity_strength"] in ("moderate", "strong")
    assert ent["id_type"] == "url"
    assert ent["name"] == "Jane Doe"


# 3. Product with identifier
def test_product_with_identifier() -> None:
    entities = [
        {
            "type": "Product",
            "name": "Creative Suite Pro",
            "identifier": "SKU-9948",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["identifier"] == "SKU-9948"
    assert ent["identity_signals"]["has_identifier"] is True


# 4. Entity without @id
def test_entity_without_id() -> None:
    entities = [
        {
            "type": "WebSite",
            "name": "Example Site",
            "url": "https://example.com/",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["has_id"] is False or ent["id"] is None
    assert ent["identity_signals"]["has_id"] is False
    assert ent["internal_id"] == "entity-0"


# 5. Entity without name
def test_entity_without_name() -> None:
    entities = [
        {
            "type": "WebPage",
            "id": "https://example.com/page",
            "url": "https://example.com/page",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["name"] is None
    assert ent["identity_signals"]["has_name"] is False


# 6. Entity without URL
def test_entity_without_url() -> None:
    entities = [
        {
            "type": "LocalBusiness",
            "id": "https://example.com/#store",
            "name": "Corner Shop",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["url"] is None
    assert ent["identity_signals"]["has_url"] is False


# 7. Entity without sameAs
def test_entity_without_sameas() -> None:
    entities = [
        {
            "type": "Brand",
            "name": "Apex",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["identity_signals"]["has_sameAs"] is False
    assert ent["sameAs"] == []


# 8. sameAs extraction
def test_sameas_extraction() -> None:
    entities = [
        {
            "type": "Organization",
            "name": "Org",
            "sameAs": [
                "https://www.linkedin.com/company/adobe",
                "https://twitter.com/adobe",
            ],
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert len(ent["sameAs"]) == 2
    assert ent["sameAs"][0]["valid_url_format"] is True


# 9. Invalid sameAs URL
def test_invalid_sameas_url() -> None:
    entities = [
        {
            "type": "Organization",
            "name": "Org",
            "sameAs": ["not_a_valid_url", "ftp://unsupported.com"],
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["sameAs"][0]["valid_url_format"] is False
    assert ent["sameAs"][0]["status"] == "invalid_format"
    assert ent["sameAs"][1]["valid_url_format"] is False


# 10. Duplicate sameAs
def test_duplicate_sameas() -> None:
    entities = [
        {
            "type": "Organization",
            "name": "Org",
            "sameAs": [
                "https://example.com/profile",
                "https://example.com/profile",
            ],
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert "https://example.com/profile" in ent["duplicate_sameAs"]
    assert ent["sameAs"][1]["status"] == "duplicate"


# 11. Multiple types
def test_multiple_types() -> None:
    entities = [
        {
            "types": ["NewsArticle", "TechArticle", "Article"],
            "name": "AI Benchmark",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert len(ent["types"]) == 3
    assert "NewsArticle" in ent["types"]


# 12. Duplicate entity IDs
def test_duplicate_entity_ids() -> None:
    entities = [
        {"id": "https://example.com/#corp", "name": "Corp Alpha"},
        {"id": "https://example.com/#corp", "name": "Corp Beta"},
    ]
    res = analyze_entities(entities)
    dups = res["duplicate_entity_ids"]
    assert len(dups) == 1
    assert dups[0]["id"] == "https://example.com/#corp"
    assert dups[0]["definitions"] == 2


# 13. Duplicate entity definitions with different values
def test_duplicate_entity_definitions_with_different_values() -> None:
    entities = [
        {"id": "https://example.com/#org", "name": "Example Corp"},
        {"id": "https://example.com/#org", "name": "Example Corporation"},
    ]
    res = analyze_entities(entities)
    obs = res["duplicate_entity_ids"][0]["observed_values"]
    assert "Example Corp" in obs["name"]
    assert "Example Corporation" in obs["name"]


# 14. Nested identity data
def test_nested_identity_data() -> None:
    entities = [
        {
            "@id": "https://example.com/#article",
            "@type": "Article",
            "name": "Deep Dive",
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert ent["id"] == "https://example.com/#article"
    assert ent["types"] == ["Article"]


# 15. Identifier as string
def test_identifier_as_string() -> None:
    entities = [{"name": "Item", "identifier": "ABC-123"}]
    res = analyze_entities(entities)
    assert res["entities"][0]["identifier"] == "ABC-123"


# 16. Identifier as PropertyValue
def test_identifier_as_property_value() -> None:
    entities = [
        {
            "name": "Part",
            "identifier": {
                "@type": "PropertyValue",
                "propertyID": "modelNumber",
                "value": "MDL-9000",
            },
        }
    ]
    res = analyze_entities(entities)
    assert "modelNumber:MDL-9000" in res["entities"][0]["identifier"]


# 17. Relationship mapping
def test_relationship_mapping() -> None:
    entities = [
        {"id": "https://example.com/#post", "name": "Post"},
        {"id": "https://example.com/#author", "name": "Author"},
    ]
    relationships = [
        {
            "property": "author",
            "source_id": "https://example.com/#post",
            "target_id": "https://example.com/#author",
        }
    ]
    res = analyze_entities(entities, relationships)
    mapped = res["relationships"][0]
    assert mapped["target_resolved"] is True
    # Verify author role attached to target entity
    author_ent = next(e for e in res["entities"] if e["id"] == "https://example.com/#author")
    assert "author" in author_ent["roles"]


# 18. Unresolved relationship
def test_unresolved_relationship() -> None:
    entities = [{"id": "https://example.com/#post", "name": "Post"}]
    relationships = [
        {
            "property": "publisher",
            "source_id": "https://example.com/#post",
            "target_id": "https://external.org/#unknown_publisher",
        }
    ]
    res = analyze_entities(entities, relationships)
    assert res["relationships"][0]["target_resolved"] is False


# 19. Empty entity list
def test_empty_entity_list() -> None:
    res = analyze_entities([])
    assert res["status"] == "success"
    assert res["summary"]["entity_count"] == 0
    assert res["entities"] == []


# 20. Malformed entity
def test_malformed_entity() -> None:
    entities = ["not_a_dictionary", {"name": "Valid"}]  # type: ignore
    res = analyze_entities(entities)
    assert res["status"] == "success"
    assert len(res["entities"]) == 2
    assert "field_errors" in res["entities"][0]


# 21. Unexpected field types
def test_unexpected_field_types() -> None:
    entities = [
        {
            "name": {"invalid": "object"},
            "url": 12345,
            "sameAs": 999,
        }
    ]
    res = analyze_entities(entities)
    ent = res["entities"][0]
    assert "field_errors" in ent
    assert ent["name"] is None
    assert ent["url"] is None


# 22. Max entity limit
def test_max_entity_limit() -> None:
    entities = [{"name": f"Entity {i}"} for i in range(20)]
    res = analyze_entities(entities, options={"max_entities": 5})
    assert len(res["entities"]) == 5
    assert res["truncated"] is True
    assert res["truncation_reason"] == "max_entities_exceeded"


# 23. Max sameAs limit
def test_max_sameas_limit() -> None:
    urls = [f"https://example.com/{i}" for i in range(20)]
    entities = [{"name": "SocialOrg", "sameAs": urls}]
    res = analyze_entities(entities, options={"max_sameAs_per_entity": 5})
    ent = res["entities"][0]
    assert len(ent["sameAs"]) == 5


# 24. Deterministic internal IDs
def test_deterministic_internal_ids() -> None:
    entities = [{"name": "First"}, {"name": "Second"}]
    res1 = analyze_entities(entities)
    res2 = analyze_entities(entities)
    ids1 = [e["internal_id"] for e in res1["entities"]]
    ids2 = [e["internal_id"] for e in res2["entities"]]
    assert ids1 == ["entity-0", "entity-1"]
    assert ids1 == ids2


# 25. Deterministic identity strength
def test_deterministic_identity_strength() -> None:
    entities = [
        {"types": ["Organization"], "name": "Acme", "id": "https://example.com/#acme", "url": "https://example.com"},
        {"types": ["Thing"]},
    ]
    res1 = analyze_entities(entities)
    res2 = analyze_entities(entities)
    assert res1["entities"][0]["identity_strength"] == res2["entities"][0]["identity_strength"]
    assert res1["entities"][1]["identity_strength"] == res2["entities"][1]["identity_strength"]


# 26. No network calls
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_calls(mock_urlopen: Any, mock_socket: Any) -> None:
    entities = [
        {
            "name": "Org",
            "url": "https://example.com",
            "sameAs": ["https://linkedin.com/corp"],
        }
    ]
    res = analyze_entities(entities)
    assert res["status"] == "success"
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()


# 27. No freshness calculation
def test_no_freshness_calculation() -> None:
    entities = [
        {
            "name": "Doc",
            "datePublished": "2024-01-01",
            "dateModified": "2025-01-01",
        }
    ]
    res = analyze_entities(entities)
    serialized = json.dumps(res)
    assert '"stale"' not in serialized
    assert '"age_days"' not in serialized
    assert '"freshness"' not in serialized


# 28. No severity generation
def test_no_severity_generation() -> None:
    entities = [{"type": "WeakThing"}]
    res = analyze_entities(entities)
    serialized = json.dumps(res)
    assert '"severity"' not in serialized
    assert '"critical"' not in serialized
    assert '"high"' not in serialized


# 29. JSON serialization
def test_json_serialization() -> None:
    entities = [
        {
            "types": ["Person"],
            "name": "Alice",
            "id": "https://example.com/#alice",
            "sameAs": ["https://example.org/alice"],
        }
    ]
    res = analyze_entities(entities)
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["component"] == "entity_analyzer"
    assert unpacked["status"] == "success"
