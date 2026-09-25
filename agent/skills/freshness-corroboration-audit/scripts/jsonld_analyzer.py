"""
Independent JSON-LD / Schema.org analyzer module for freshness-corroboration-audit.

This module parses HTML, locates <script type="application/ld+json"> elements,
and extracts structured data, entities, relationships, @graph containers, sameAs
links, and temporal signals without performing network requests, date math, or
severity evaluations.
"""

from __future__ import annotations

import html.parser
import json
import logging
import sys
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.jsonld_analyzer")

# Safe defaults
DEFAULT_MAX_JSONLD_BLOCK_BYTES = 1_000_000  # 1 MB per block
DEFAULT_MAX_ENTITIES = 500
DEFAULT_MAX_RELATIONSHIPS = 1000
DEFAULT_MAX_DEPTH = 20

# Known common Schema.org types for classification
RECOGNIZED_SCHEMA_TYPES = {
    "Article",
    "NewsArticle",
    "TechArticle",
    "BlogPosting",
    "Organization",
    "Corporation",
    "LocalBusiness",
    "Person",
    "Product",
    "Offer",
    "Brand",
    "WebPage",
    "WebSite",
    "AboutPage",
    "ContactPage",
    "Event",
    "BreadcrumbList",
    "CreativeWork",
    "SoftwareApplication",
}

# Key relationship properties between entities
RELATIONSHIP_PROPERTIES = {
    "author",
    "publisher",
    "creator",
    "brand",
    "manufacturer",
    "mainEntity",
    "mainEntityOfPage",
    "about",
    "mentions",
    "isPartOf",
    "item",
    "itemListElement",
}

# Key properties to extract onto entity records
KEY_PROPERTIES = {
    "name",
    "headline",
    "description",
    "url",
    "image",
    "author",
    "creator",
    "publisher",
    "brand",
    "manufacturer",
    "identifier",
    "sku",
    "gtin",
    "sameAs",
    "datePublished",
    "dateModified",
    "dateCreated",
    "mainEntity",
    "mainEntityOfPage",
    "about",
    "mentions",
    "isPartOf",
    "item",
    "itemListElement",
}


class _JSONLDExtractor(html.parser.HTMLParser):
    """HTML parser to extract all script tags with type application/ld+json."""

    def __init__(self) -> None:
        super().__init__()
        self.in_jsonld_script = False
        self.current_content: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            for attr_name, attr_val in attrs:
                if attr_name.lower() == "type" and attr_val:
                    # Clean type attribute: remove whitespace, split params like charset
                    cleaned_type = attr_val.split(";")[0].strip().lower()
                    if cleaned_type == "application/ld+json":
                        self.in_jsonld_script = True
                        self.current_content = []
                        break

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.in_jsonld_script:
            self.blocks.append("".join(self.current_content))
            self.in_jsonld_script = False
            self.current_content = []

    def close(self) -> None:
        super().close()
        if self.in_jsonld_script and self.current_content:
            self.blocks.append("".join(self.current_content))
            self.in_jsonld_script = False
            self.current_content = []

    def handle_data(self, data: str) -> None:
        if self.in_jsonld_script:
            self.current_content.append(data)


def _check_uses_schema_org(context: Any) -> bool:
    """Check if @context refers to Schema.org."""
    if isinstance(context, str):
        cleaned = context.strip().lower().rstrip("/")
        return cleaned in ("https://schema.org", "http://schema.org")
    elif isinstance(context, list):
        return any(_check_uses_schema_org(c) for c in context)
    elif isinstance(context, dict):
        # Look for vocab definition
        vocab = context.get("@vocab", "")
        if isinstance(vocab, str) and vocab.strip().lower().rstrip("/") in (
            "https://schema.org",
            "http://schema.org",
        ):
            return True
        return any(_check_uses_schema_org(v) for v in context.values() if isinstance(v, (str, list, dict)))
    return False


def _is_entity_candidate(obj: Any) -> bool:
    """Determine if a dict represents a Schema.org entity candidate."""
    if not isinstance(obj, dict) or not obj:
        return False
    return bool(
        obj.get("@type")
        or obj.get("@id")
        or obj.get("name")
        or obj.get("url")
    )


def _normalize_types(type_val: Any) -> list[str]:
    """Normalize @type value into a list of strings."""
    if isinstance(type_val, str) and type_val.strip():
        return [type_val.strip()]
    elif isinstance(type_val, list):
        types: list[str] = []
        for t in type_val:
            if isinstance(t, str) and t.strip() and t.strip() not in types:
                types.append(t.strip())
        return types
    return []


def _normalize_sameas(sameas_val: Any) -> list[str]:
    """Normalize sameAs value into a list of string URLs."""
    if isinstance(sameas_val, str) and sameas_val.strip():
        return [sameas_val.strip()]
    elif isinstance(sameas_val, list):
        results: list[str] = []
        for item in sameas_val:
            if isinstance(item, str) and item.strip() and item.strip() not in results:
                results.append(item.strip())
        return results
    return []


def analyze_jsonld(
    html: str | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Analyze JSON-LD and Schema.org structured data in an HTML document.

    Args:
        html: Raw HTML string containing markup and scripts.
        options: Optional configuration dictionary.
            - max_jsonld_block_bytes: Max byte length of a single script block (default: 1,000,000).
            - max_entities: Max entities to extract across document (default: 500).
            - max_relationships: Max relationships to discover (default: 1000).
            - max_depth: Max object traversal recursion depth (default: 20).

    Returns:
        Structured JSON-serializable dictionary with summary, blocks, entities,
        relationships, and temporal signals.
    """
    opts = options or {}
    max_block_bytes = int(opts.get("max_jsonld_block_bytes", DEFAULT_MAX_JSONLD_BLOCK_BYTES))
    max_entities = int(opts.get("max_entities", DEFAULT_MAX_ENTITIES))
    max_relationships = int(opts.get("max_relationships", DEFAULT_MAX_RELATIONSHIPS))
    max_depth = int(opts.get("max_depth", DEFAULT_MAX_DEPTH))

    # Guard: Null or empty HTML
    if not html or not isinstance(html, str) or not html.strip():
        return {
            "component": "jsonld_analyzer",
            "status": "success",
            "summary": {
                "json_ld_blocks": 0,
                "valid_blocks": 0,
                "invalid_blocks": 0,
                "schema_org_blocks": 0,
                "graph_count": 0,
                "entity_count": 0,
            },
            "blocks": [],
            "entities": [],
            "relationships": [],
            "temporal_signals": [],
            "duplicate_entity_ids": [],
            "errors": [],
        }

    # Step 1: Extract JSON-LD script blocks using HTMLParser
    extractor = _JSONLDExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception as exc:
        logger.warning("HTMLParser encountered exception while extracting scripts: %s", exc)
        try:
            extractor.close()
        except Exception:
            pass

    raw_blocks = extractor.blocks
    total_blocks_found = len(raw_blocks)
    logger.info("Discovered %d JSON-LD block(s)", total_blocks_found)

    blocks_result: list[dict[str, Any]] = []
    all_entities: list[dict[str, Any]] = []
    all_relationships: list[dict[str, Any]] = []
    all_temporal_signals: list[dict[str, Any]] = []

    seen_ids: dict[str, int] = {}  # id -> occurrence count
    duplicate_ids: list[str] = []

    valid_block_count = 0
    invalid_block_count = 0
    schema_org_blocks_count = 0
    total_graph_count = 0

    entity_counter = 0

    # Step 2: Parse and inspect each block
    for block_idx, raw_content in enumerate(raw_blocks):
        trimmed = raw_content.strip()
        block_byte_len = len(trimmed.encode("utf-8", errors="replace"))

        # Check block size limit
        if block_byte_len > max_block_bytes:
            invalid_block_count += 1
            logger.warning(
                "JSON-LD block %d exceeds size limit (%d bytes > %d bytes)",
                block_idx,
                block_byte_len,
                max_block_bytes,
            )
            blocks_result.append(
                {
                    "index": block_idx,
                    "valid": False,
                    "root_type": "unknown",
                    "uses_schema_org": False,
                    "context": None,
                    "graph_count": 0,
                    "entity_count": 0,
                    "entities": [],
                    "relationships": [],
                    "properties": [],
                    "parse_error": {
                        "type": "size_limit_exceeded",
                        "message": f"JSON-LD block size {block_byte_len} bytes exceeds limit of {max_block_bytes} bytes.",
                    },
                }
            )
            continue

        # Parse JSON
        try:
            parsed_json = json.loads(trimmed)
            valid_block_count += 1
        except json.JSONDecodeError as jde:
            invalid_block_count += 1
            logger.warning("Malformed JSON in block %d: %s", block_idx, jde)
            blocks_result.append(
                {
                    "index": block_idx,
                    "valid": False,
                    "root_type": "malformed",
                    "uses_schema_org": False,
                    "context": None,
                    "graph_count": 0,
                    "entity_count": 0,
                    "entities": [],
                    "relationships": [],
                    "properties": [],
                    "parse_error": {
                        "type": "json_decode_error",
                        "message": f"Line {jde.lineno}, column {jde.colno}: {jde.msg}",
                    },
                }
            )
            continue

        # Determine root structure and context
        root_type = "object"
        context_val: Any = None
        if isinstance(parsed_json, list):
            root_type = "array"
        elif isinstance(parsed_json, dict):
            context_val = parsed_json.get("@context")
            if "@graph" in parsed_json:
                root_type = "graph_container"

        uses_schema_org = _check_uses_schema_org(context_val)
        if uses_schema_org:
            schema_org_blocks_count += 1

        block_graph_count = 0
        block_entities: list[dict[str, Any]] = []
        block_relationships: list[dict[str, Any]] = []

        # Traverse and extract entities recursively with depth and budget guards
        def _traverse(node: Any, location_path: str, depth: int, parent_entity_id: str | None = None, parent_prop: str | None = None) -> None:
            nonlocal entity_counter, block_graph_count

            if depth > max_depth:
                logger.debug("Max depth reached at %s", location_path)
                return

            if len(all_entities) >= max_entities:
                return

            if isinstance(node, dict):
                # Check for @graph container
                if "@graph" in node:
                    block_graph_count += 1
                    graph_content = node["@graph"]
                    if isinstance(graph_content, list):
                        for idx, item in enumerate(graph_content):
                            _traverse(item, f"{location_path}@graph[{idx}]", depth + 1)
                    elif isinstance(graph_content, dict):
                        _traverse(graph_content, f"{location_path}@graph", depth + 1)

                # Check if this node is an @id reference only: {"@id": "..."}
                if list(node.keys()) == ["@id"]:
                    target_ref = node["@id"]
                    if parent_entity_id and parent_prop and len(all_relationships) < max_relationships:
                        rel = {
                            "property": parent_prop,
                            "source_id": parent_entity_id,
                            "target_id": target_ref,
                            "source_location": location_path,
                        }
                        block_relationships.append(rel)
                        all_relationships.append(rel)
                    return

                # Check if this node qualifies as an entity candidate
                if _is_entity_candidate(node):
                    entity_idx = entity_counter
                    entity_counter += 1

                    raw_id = node.get("@id")
                    clean_id = str(raw_id).strip() if raw_id is not None else None

                    # Track duplicate entity IDs
                    if clean_id:
                        seen_ids[clean_id] = seen_ids.get(clean_id, 0) + 1
                        if seen_ids[clean_id] == 2 and clean_id not in duplicate_ids:
                            duplicate_ids.append(clean_id)

                    types_list = _normalize_types(node.get("@type"))
                    sameas_list = _normalize_sameas(node.get("sameAs"))

                    # Deterministic internal identifier
                    internal_id = clean_id if clean_id else f"ent_b{block_idx}_{entity_idx}"

                    # Collect property inventory
                    property_inventory = sorted(list(node.keys()))

                    # Extract temporal signals
                    for tf in ("datePublished", "dateModified", "dateCreated"):
                        val = node.get(tf)
                        if val is not None:
                            all_temporal_signals.append(
                                {
                                    "field": tf,
                                    "value": str(val).strip(),
                                    "entity_id": internal_id,
                                    "block_index": block_idx,
                                }
                            )

                    # Extract key property values
                    extracted_props: dict[str, Any] = {}
                    for prop in KEY_PROPERTIES:
                        if prop in node and prop not in ("sameAs", "datePublished", "dateModified", "dateCreated"):
                            p_val = node[prop]
                            if isinstance(p_val, (str, int, float, bool)):
                                extracted_props[prop] = p_val
                            elif isinstance(p_val, list) and all(isinstance(x, (str, int, float, bool)) for x in p_val):
                                extracted_props[prop] = p_val

                    entity_record = {
                        "entity_index": entity_idx,
                        "internal_id": internal_id,
                        "id": clean_id,
                        "type": types_list[0] if len(types_list) == 1 else (types_list if types_list else None),
                        "types": types_list,
                        "name": str(node["name"]).strip() if "name" in node and isinstance(node["name"], (str, int, float)) else None,
                        "url": str(node["url"]).strip() if "url" in node and isinstance(node["url"], str) else None,
                        "sameAs": sameas_list,
                        "properties": property_inventory,
                        "key_properties": extracted_props,
                        "source": {
                            "block_index": block_idx,
                            "location": location_path if location_path else "root",
                        },
                    }

                    block_entities.append(entity_record)
                    all_entities.append(entity_record)

                    # If this entity was nested inside another entity via a relationship property
                    if parent_entity_id and parent_prop and len(all_relationships) < max_relationships:
                        rel = {
                            "property": parent_prop,
                            "source_id": parent_entity_id,
                            "target_id": internal_id,
                            "source_location": location_path,
                        }
                        block_relationships.append(rel)
                        all_relationships.append(rel)

                    current_entity_id = internal_id
                else:
                    current_entity_id = parent_entity_id

                # Traverse child properties
                for prop_name, prop_val in node.items():
                    if prop_name == "@graph":
                        continue  # already handled above
                    child_path = f"{location_path}.{prop_name}" if location_path else prop_name
                    _traverse(prop_val, child_path, depth + 1, current_entity_id, prop_name)

            elif isinstance(node, list):
                for idx, item in enumerate(node):
                    child_path = f"{location_path}[{idx}]"
                    _traverse(item, child_path, depth + 1, parent_entity_id, parent_prop)

        # Launch traversal from root
        _traverse(parsed_json, "", 0)
        total_graph_count += block_graph_count

        block_props = sorted(list(parsed_json.keys())) if isinstance(parsed_json, dict) else []

        blocks_result.append(
            {
                "index": block_idx,
                "valid": True,
                "root_type": root_type,
                "uses_schema_org": uses_schema_org,
                "context": context_val,
                "graph_count": block_graph_count,
                "entity_count": len(block_entities),
                "entities": block_entities,
                "relationships": block_relationships,
                "properties": block_props,
                "parse_error": None,
            }
        )

    return {
        "component": "jsonld_analyzer",
        "status": "success",
        "summary": {
            "json_ld_blocks": total_blocks_found,
            "valid_blocks": valid_block_count,
            "invalid_blocks": invalid_block_count,
            "schema_org_blocks": schema_org_blocks_count,
            "graph_count": total_graph_count,
            "entity_count": len(all_entities),
        },
        "blocks": blocks_result,
        "entities": all_entities,
        "relationships": all_relationships,
        "temporal_signals": all_temporal_signals,
        "duplicate_entity_ids": duplicate_ids,
        "errors": [],
    }


def main() -> int:
    """Command-line interface to analyze a local HTML file."""
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python jsonld_analyzer.py <file.html> [options_json]\n")
        return 1

    file_path = sys.argv[1]
    options: dict[str, Any] = {}

    if len(sys.argv) >= 3:
        try:
            options = json.loads(sys.argv[2])
        except json.JSONDecodeError as err:
            sys.stderr.write(f"Invalid options JSON: {err}\n")
            return 1

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            html_content = f.read()
    except Exception as exc:
        sys.stderr.write(f"Error reading file '{file_path}': {exc}\n")
        return 1

    result = analyze_jsonld(html_content, options)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
