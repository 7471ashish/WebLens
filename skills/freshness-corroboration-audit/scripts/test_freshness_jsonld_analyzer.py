"""
Unit tests for the independent JSON-LD analyzer module.

Tests all 34 audit requirements without external network access or external agents.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from jsonld_analyzer import analyze_jsonld


# 1. No JSON-LD
def test_no_jsonld() -> None:
    html = "<html><head><title>No Scripts</title></head><body><h1>Hello</h1></body></html>"
    res = analyze_jsonld(html)
    assert res["status"] == "success"
    assert res["summary"]["json_ld_blocks"] == 0
    assert res["summary"]["entity_count"] == 0
    assert res["blocks"] == []
    assert res["entities"] == []


# 2. One valid JSON-LD object
def test_one_valid_jsonld_object() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Article",
      "@id": "https://example.com/article#article",
      "headline": "Example Article"
    }
    </script>
    </head></html>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["json_ld_blocks"] == 1
    assert res["summary"]["valid_blocks"] == 1
    assert res["summary"]["invalid_blocks"] == 0
    assert res["summary"]["schema_org_blocks"] == 1
    assert res["summary"]["entity_count"] == 1
    ent = res["entities"][0]
    assert ent["type"] == "Article"
    assert ent["id"] == "https://example.com/article#article"
    assert ent["key_properties"]["headline"] == "Example Article"


# 3. Multiple JSON-LD blocks
def test_multiple_jsonld_blocks() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Organization", "name": "Org A"}
    </script>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "WebSite", "name": "Site B"}
    </script>
    </head></html>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["json_ld_blocks"] == 2
    assert res["summary"]["valid_blocks"] == 2
    assert res["summary"]["entity_count"] == 2
    types = [e["type"] for e in res["entities"]]
    assert "Organization" in types
    assert "WebSite" in types


# 4. Malformed JSON-LD
def test_malformed_jsonld() -> None:
    html = """
    <script type="application/ld+json">
    { "@context": "https://schema.org", "@type": "Article", "headline": }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["json_ld_blocks"] == 1
    assert res["summary"]["valid_blocks"] == 0
    assert res["summary"]["invalid_blocks"] == 1
    assert res["blocks"][0]["valid"] is False
    assert res["blocks"][0]["parse_error"]["type"] == "json_decode_error"


# 5. Valid block + malformed block
def test_valid_and_malformed_blocks() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Person", "name": "Alice"}
    </script>
    <script type="application/ld+json">
    {"bad": "json", trailing, comma}
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["json_ld_blocks"] == 2
    assert res["summary"]["valid_blocks"] == 1
    assert res["summary"]["invalid_blocks"] == 1
    assert res["summary"]["entity_count"] == 1
    assert res["entities"][0]["name"] == "Alice"


# 6. JSON-LD array
def test_jsonld_array() -> None:
    html = """
    <script type="application/ld+json">
    [
      {"@context": "https://schema.org", "@type": "Article", "headline": "Post 1"},
      {"@context": "https://schema.org", "@type": "Article", "headline": "Post 2"}
    ]
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["valid_blocks"] == 1
    assert res["blocks"][0]["root_type"] == "array"
    assert res["summary"]["entity_count"] == 2


# 7. @graph container
def test_graph_container() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "Organization", "@id": "https://example.com/#org", "name": "Company"}
      ]
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["graph_count"] == 1
    assert res["blocks"][0]["graph_count"] == 1
    # @graph itself must NOT be counted as an entity
    assert res["summary"]["entity_count"] == 1
    assert res["entities"][0]["type"] == "Organization"


# 8. Multiple @graph entities
def test_multiple_graph_entities() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "Organization", "@id": "https://example.com/#org", "name": "Corp"},
        {"@type": "Person", "@id": "https://example.com/#author", "name": "Jane"},
        {"@type": "Article", "@id": "https://example.com/#post", "headline": "News"}
      ]
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["graph_count"] == 1
    assert res["summary"]["entity_count"] == 3
    names = [e["name"] for e in res["entities"] if e.get("name")]
    assert "Corp" in names
    assert "Jane" in names


# 9. Nested entity
def test_nested_entity() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Article",
      "@id": "https://example.com/#article",
      "headline": "Nested Test",
      "author": {
        "@type": "Person",
        "@id": "https://example.com/#author",
        "name": "Bob"
      }
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["entity_count"] == 2
    types = [e["type"] for e in res["entities"]]
    assert "Article" in types
    assert "Person" in types


# 10. @id extraction
def test_id_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Product",
      "@id": "https://example.com/products/gadget#item",
      "name": "Gadget"
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["entities"][0]["id"] == "https://example.com/products/gadget#item"


# 11. Multiple @type values
def test_multiple_type_values() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": ["NewsArticle", "TechArticle"],
      "headline": "Dual Type"
    }
    </script>
    """
    res = analyze_jsonld(html)
    ent = res["entities"][0]
    assert "NewsArticle" in ent["types"]
    assert "TechArticle" in ent["types"]


# 12. @context extraction
def test_context_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Event",
      "name": "Webinar"
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["blocks"][0]["context"] == "https://schema.org"


# 13. Schema.org detection
def test_schema_org_detection() -> None:
    html_schema = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "WebPage"}
    </script>
    """
    res1 = analyze_jsonld(html_schema)
    assert res1["blocks"][0]["uses_schema_org"] is True

    html_other = """
    <script type="application/ld+json">
    {"@context": "https://www.w3.org/ns/activitystreams", "@type": "Person", "name": "Sam"}
    </script>
    """
    res2 = analyze_jsonld(html_other)
    assert res2["blocks"][0]["uses_schema_org"] is False


# 14. sameAs extraction
def test_sameas_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "Corp",
      "sameAs": [
        "https://www.wikidata.org/wiki/Q12345",
        "https://twitter.com/corp"
      ]
    }
    </script>
    """
    res = analyze_jsonld(html)
    ent = res["entities"][0]
    assert len(ent["sameAs"]) == 2
    assert "https://www.wikidata.org/wiki/Q12345" in ent["sameAs"]


# 15. datePublished extraction
def test_date_published_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Article", "datePublished": "2026-03-15"}
    </script>
    """
    res = analyze_jsonld(html)
    signals = res["temporal_signals"]
    assert any(s["field"] == "datePublished" and s["value"] == "2026-03-15" for s in signals)


# 16. dateModified extraction
def test_date_modified_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Article", "dateModified": "2026-08-01T12:00:00Z"}
    </script>
    """
    res = analyze_jsonld(html)
    signals = res["temporal_signals"]
    assert any(s["field"] == "dateModified" and s["value"] == "2026-08-01T12:00:00Z" for s in signals)


# 17. dateCreated extraction
def test_date_created_extraction() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Article", "dateCreated": "2025-12-01"}
    </script>
    """
    res = analyze_jsonld(html)
    signals = res["temporal_signals"]
    assert any(s["field"] == "dateCreated" and s["value"] == "2025-12-01" for s in signals)


# 18. Relationships
def test_relationships() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Article",
      "@id": "https://example.com/#article",
      "author": {
        "@type": "Person",
        "@id": "https://example.com/#author",
        "name": "Charlie"
      }
    }
    </script>
    """
    res = analyze_jsonld(html)
    rels = res["relationships"]
    assert len(rels) >= 1
    assert rels[0]["property"] == "author"
    assert rels[0]["source_id"] == "https://example.com/#article"
    assert rels[0]["target_id"] == "https://example.com/#author"


# 19. @id references
def test_id_references() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Article",
      "@id": "https://example.com/#article",
      "author": {
        "@id": "https://example.com/#person"
      }
    }
    </script>
    """
    res = analyze_jsonld(html)
    rels = res["relationships"]
    assert len(rels) == 1
    assert rels[0]["property"] == "author"
    assert rels[0]["target_id"] == "https://example.com/#person"


# 20. Duplicate @id detection
def test_duplicate_id_detection() -> None:
    html = """
    <script type="application/ld+json">
    [
      {"@type": "Organization", "@id": "https://example.com/#dup", "name": "Version 1"},
      {"@type": "Organization", "@id": "https://example.com/#dup", "name": "Version 2"}
    ]
    </script>
    """
    res = analyze_jsonld(html)
    assert "https://example.com/#dup" in res["duplicate_entity_ids"]


# 21. Entity deduplication / counting
def test_entity_deduplication() -> None:
    html = """
    <script type="application/ld+json">
    [
      {"@type": "Brand", "@id": "https://example.com/#brand1", "name": "Brand A"},
      {"@type": "Brand", "@id": "https://example.com/#brand2", "name": "Brand B"}
    ]
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["entity_count"] == 2
    assert len(res["duplicate_entity_ids"]) == 0


# 22. Missing @type
def test_missing_type() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@id": "https://example.com/#untyped",
      "name": "Untyped Entity"
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["summary"]["entity_count"] == 1
    ent = res["entities"][0]
    assert ent["name"] == "Untyped Entity"
    assert ent["type"] is None


# 23. Unknown @type preserved
def test_unknown_type_preserved() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "FutureCustomSemanticType",
      "name": "Future Item"
    }
    </script>
    """
    res = analyze_jsonld(html)
    assert res["entities"][0]["type"] == "FutureCustomSemanticType"


# 24. Malformed HTML handled gracefully
def test_malformed_html() -> None:
    html = "<div unclosed><script type='application/ld+json'>{\"@type\": \"WebSite\"}</script></b></unclosed>"
    res = analyze_jsonld(html)
    assert res["status"] == "success"
    assert res["summary"]["json_ld_blocks"] == 1
    assert res["entities"][0]["type"] == "WebSite"


# 25. Empty HTML
def test_empty_html() -> None:
    res = analyze_jsonld("")
    assert res["status"] == "success"
    assert res["summary"]["json_ld_blocks"] == 0
    assert res["entities"] == []


# 26. None HTML
def test_none_html() -> None:
    res = analyze_jsonld(None)
    assert res["status"] == "success"
    assert res["summary"]["json_ld_blocks"] == 0
    assert res["blocks"] == []


# 27. Oversized JSON-LD block protection
def test_oversized_jsonld_block() -> None:
    large_headline = "A" * 2000
    html = f"""
    <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Article", "headline": "{large_headline}"}}
    </script>
    """
    res = analyze_jsonld(html, options={"max_jsonld_block_bytes": 500})
    assert res["summary"]["invalid_blocks"] == 1
    assert res["blocks"][0]["parse_error"]["type"] == "size_limit_exceeded"
    assert res["summary"]["entity_count"] == 0


# 28. Maximum traversal depth protection
def test_maximum_traversal_depth() -> None:
    # Construct 25 levels deep nesting
    curr = {"@type": "DeepEntity", "name": "Bottom"}
    for i in range(25):
        curr = {"@type": "Level", "sub": curr}
    html = f'<script type="application/ld+json">{json.dumps(curr)}</script>'

    res = analyze_jsonld(html, options={"max_depth": 5})
    assert res["status"] == "success"
    # Traverser should cut off beyond depth 5 without infinite loop or crash
    assert len(res["entities"]) <= 6


# 29. Deterministic entity IDs
def test_deterministic_entity_ids() -> None:
    html = """
    <script type="application/ld+json">
    {"@type": "Article", "headline": "Post 1"}
    </script>
    <script type="application/ld+json">
    {"@type": "Article", "headline": "Post 2"}
    </script>
    """
    res1 = analyze_jsonld(html)
    res2 = analyze_jsonld(html)
    ids1 = [e["internal_id"] for e in res1["entities"]]
    ids2 = [e["internal_id"] for e in res2["entities"]]
    assert ids1 == ids2


# 30. Deterministic output
def test_deterministic_output() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Organization", "name": "Test Org", "url": "https://test.org"}
    </script>
    """
    res1 = analyze_jsonld(html)
    res2 = analyze_jsonld(html)
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)


# 31. JSON serializability
def test_json_serializability() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Widget"}
    </script>
    """
    res = analyze_jsonld(html)
    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    unpacked = json.loads(serialized)
    assert unpacked["component"] == "jsonld_analyzer"


# 32. No network calls
@patch("socket.socket")
@patch("urllib.request.urlopen")
def test_no_network_calls(mock_urlopen: Any, mock_socket: Any) -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Article", "@id": "https://example.com/network-test"}
    </script>
    """
    res = analyze_jsonld(html)
    assert res["status"] == "success"
    mock_urlopen.assert_not_called()
    mock_socket.assert_not_called()


# 33. No severity generation
def test_no_severity_generation() -> None:
    # Malformed block should have parse_error, but no severity fields
    html = '<script type="application/ld+json">{ broken json }</script>'
    res = analyze_jsonld(html)
    serialized = json.dumps(res)
    assert '"severity"' not in serialized
    assert '"critical"' not in serialized
    assert '"high"' not in serialized


# 34. No external verification
def test_no_external_verification() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Person",
      "name": "Jane",
      "sameAs": ["https://unverified.example.org/profile"]
    }
    </script>
    """
    res = analyze_jsonld(html)
    ent = res["entities"][0]
    # URL is extracted, but not verified
    assert "https://unverified.example.org/profile" in ent["sameAs"]
    assert "verified" not in ent
