"""
Independent entity identity analyzer module for freshness-corroboration-audit.

This module evaluates the identity signals (type, name, @id, url, identifier, sameAs,
relationships) provided by structured data on a webpage to classify identity strength
without performing external network verification or LLM inference.
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.parse
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.entity_analyzer")

# Safe defaults
DEFAULT_MAX_ENTITIES = 500
DEFAULT_MAX_SAMEAS_PER_ENTITY = 50
DEFAULT_MAX_RELATIONSHIPS = 1000

# Common Schema.org types for prioritization
PRIORITY_ENTITY_TYPES = {
    "organization",
    "corporation",
    "localbusiness",
    "person",
    "product",
    "brand",
}

SECONDARY_ENTITY_TYPES = {
    "article",
    "newsarticle",
    "techarticle",
    "blogposting",
    "webpage",
    "website",
    "event",
    "creativework",
}


def _is_valid_http_url(url_val: Any) -> bool:
    """Validate if value is a syntactically valid HTTP/HTTPS URL."""
    if not isinstance(url_val, str) or not url_val.strip():
        return False
    trimmed = url_val.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _classify_id_type(id_val: str) -> str:
    """Classify the syntactic form of an entity identifier."""
    trimmed = id_val.strip()
    if _is_valid_http_url(trimmed):
        return "url"
    elif trimmed.startswith("#"):
        return "fragment"
    elif ":" in trimmed or "-" in trimmed or "_" in trimmed or trimmed.isalnum():
        return "identifier"
    return "unknown"


def _extract_identifier_value(raw_identifier: Any) -> tuple[Any, bool]:
    """Extract and normalize identifier property."""
    if raw_identifier is None:
        return None, False
    if isinstance(raw_identifier, (str, int, float)):
        val_str = str(raw_identifier).strip()
        return val_str if val_str else None, bool(val_str)
    elif isinstance(raw_identifier, dict):
        # Handle PropertyValue object
        prop_val = raw_identifier.get("value")
        prop_id = raw_identifier.get("propertyID") or raw_identifier.get("name")
        if prop_val is not None:
            formatted = f"{prop_id}:{prop_val}" if prop_id else str(prop_val)
            return formatted, True
        return raw_identifier, True
    elif isinstance(raw_identifier, list):
        items = [str(x).strip() for x in raw_identifier if str(x).strip()]
        return items if items else None, bool(items)
    return None, False


def _calculate_identity_strength(
    has_type: bool,
    has_name: bool,
    has_id: bool,
    has_url: bool,
    has_identifier: bool,
    has_sameas: bool,
) -> str:
    """
    Calculate deterministic identity strength rating.

    Heuristic scoring:
        has_type: +1
        has_name: +1
        has_id: +2
        has_url: +1
        has_identifier: +1
        has_sameas: +1
        Total Max = 7

        6-7: strong
        4-5: moderate
        2-3: weak
        0-1: minimal
    """
    score = 0
    if has_type:
        score += 1
    if has_name:
        score += 1
    if has_id:
        score += 2
    if has_url:
        score += 1
    if has_identifier:
        score += 1
    if has_sameas:
        score += 1

    if score >= 6:
        return "strong"
    elif score >= 4:
        return "moderate"
    elif score >= 2:
        return "weak"
    return "minimal"


def analyze_entities(
    entities: list[dict[str, Any]] | None,
    relationships: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Analyze entity identity signals and calculate identity strength.

    Args:
        entities: List of entity dictionaries (from jsonld_analyzer or equivalent).
        relationships: Optional list of relationship links between entities.
        options: Optional configuration dictionary.
            - max_entities: Maximum entities to process (default: 500).
            - max_sameAs_per_entity: Maximum sameAs URLs per entity (default: 50).
            - max_relationships: Maximum relationships to map (default: 1000).

    Returns:
        Structured JSON-serializable dictionary with summary, entity evaluations,
        relationships, and duplicate ID observations.
    """
    opts = options or {}
    max_entities = int(opts.get("max_entities", DEFAULT_MAX_ENTITIES))
    max_sameas = int(opts.get("max_sameAs_per_entity", DEFAULT_MAX_SAMEAS_PER_ENTITY))
    max_rels = int(opts.get("max_relationships", DEFAULT_MAX_RELATIONSHIPS))

    # Guard: Null or empty input
    if not entities or not isinstance(entities, list):
        return {
            "component": "entity_analyzer",
            "status": "success",
            "summary": {
                "entity_count": 0,
                "strong_identity_count": 0,
                "moderate_identity_count": 0,
                "weak_identity_count": 0,
                "minimal_identity_count": 0,
                "entities_with_stable_id": 0,
                "entities_with_url": 0,
                "entities_with_sameAs": 0,
                "entities_with_identifier": 0,
            },
            "entities": [],
            "relationships": [],
            "duplicate_entity_ids": [],
            "errors": [],
        }

    # Step 1: Enforce bounds
    truncated = False
    truncation_reason: str | None = None
    if len(entities) > max_entities:
        logger.warning("Entity count (%d) exceeded max limit (%d), truncating.", len(entities), max_entities)
        entities = entities[:max_entities]
        truncated = True
        truncation_reason = "max_entities_exceeded"

    # Step 2: Track ID duplicates and map entities
    id_tracker: dict[str, list[int]] = {}  # id -> list of entity indices
    observed_values_by_id: dict[str, dict[str, list[Any]]] = {}

    analyzed_entities: list[dict[str, Any]] = []
    known_ids: set[str] = set()

    strong_count = 0
    moderate_count = 0
    weak_count = 0
    minimal_count = 0

    with_stable_id_count = 0
    with_url_count = 0
    with_sameas_count = 0
    with_identifier_count = 0

    for idx, raw_entity in enumerate(entities):
        field_errors: list[dict[str, str]] = []

        if not isinstance(raw_entity, dict):
            field_errors.append(
                {
                    "field": "entity",
                    "type": "unexpected_type",
                    "message": f"Expected dict at index {idx}, got {type(raw_entity).__name__}.",
                }
            )
            raw_entity = {}

        # 1. Deterministic Internal ID
        internal_id = f"entity-{idx}"

        # 2. Extract and normalize @type / types
        types_list: list[str] = []
        raw_types = raw_entity.get("types")
        if raw_types is None:
            raw_types = raw_entity.get("type") or raw_entity.get("@type")

        if isinstance(raw_types, str) and raw_types.strip():
            types_list = [raw_types.strip()]
        elif isinstance(raw_types, list):
            for t in raw_types:
                if isinstance(t, str) and t.strip() and t.strip() not in types_list:
                    types_list.append(t.strip())
        elif raw_types is not None:
            field_errors.append(
                {
                    "field": "type",
                    "type": "unexpected_type",
                    "message": f"Invalid type format: {type(raw_types).__name__}",
                }
            )

        has_type = len(types_list) > 0

        # 3. Extract and normalize @id / id
        raw_id = raw_entity.get("id")
        if raw_id is None:
            raw_id = raw_entity.get("@id")

        clean_id: str | None = None
        id_type = "none"
        has_id = False

        if isinstance(raw_id, str) and raw_id.strip():
            clean_id = raw_id.strip()
            has_id = True
            id_type = _classify_id_type(clean_id)
            with_stable_id_count += 1
            known_ids.add(clean_id)

            # Track duplicate entity IDs
            if clean_id not in id_tracker:
                id_tracker[clean_id] = []
                observed_values_by_id[clean_id] = {}
            id_tracker[clean_id].append(idx)
        elif raw_id is not None:
            field_errors.append(
                {
                    "field": "id",
                    "type": "unexpected_type",
                    "message": f"Invalid id format: {type(raw_id).__name__}",
                }
            )

        # Register internal ID in known IDs as well
        known_ids.add(internal_id)

        # 4. Extract and normalize name
        raw_name = raw_entity.get("name")
        clean_name: str | None = None
        has_name = False

        if isinstance(raw_name, (str, int, float)):
            name_str = str(raw_name).strip()
            if name_str:
                clean_name = name_str
                has_name = True
        elif raw_name is not None:
            field_errors.append(
                {
                    "field": "name",
                    "type": "unexpected_type",
                    "message": f"Invalid name format: {type(raw_name).__name__}",
                }
            )

        # Record observed values for duplicate detection
        if clean_id and clean_name:
            name_list = observed_values_by_id[clean_id].setdefault("name", [])
            if clean_name not in name_list:
                name_list.append(clean_name)

        # 5. Extract and normalize url
        raw_url = raw_entity.get("url")
        clean_url: str | None = None
        valid_url_format = False
        has_url = False

        if isinstance(raw_url, str) and raw_url.strip():
            clean_url = raw_url.strip()
            valid_url_format = _is_valid_http_url(clean_url)
            has_url = True
            with_url_count += 1
        elif raw_url is not None:
            field_errors.append(
                {
                    "field": "url",
                    "type": "unexpected_type",
                    "message": f"Invalid url format: {type(raw_url).__name__}",
                }
            )

        if clean_id and clean_url:
            url_list = observed_values_by_id[clean_id].setdefault("url", [])
            if clean_url not in url_list:
                url_list.append(clean_url)

        # 6. Extract and normalize identifier
        raw_identifier = raw_entity.get("identifier")
        clean_identifier, has_identifier = _extract_identifier_value(raw_identifier)
        if has_identifier:
            with_identifier_count += 1

        # 7. Extract and inspect sameAs references
        raw_sameas = raw_entity.get("sameAs")
        sameas_items: list[dict[str, Any]] = []
        duplicate_sameas_urls: list[str] = []
        seen_sameas: set[str] = set()

        if raw_sameas is not None:
            candidates: list[Any] = []
            if isinstance(raw_sameas, str):
                candidates = [raw_sameas]
            elif isinstance(raw_sameas, list):
                candidates = raw_sameas[:max_sameas]
            else:
                field_errors.append(
                    {
                        "field": "sameAs",
                        "type": "unexpected_type",
                        "message": f"Invalid sameAs format: {type(raw_sameas).__name__}",
                    }
                )

            for item in candidates:
                if isinstance(item, str) and item.strip():
                    url_val = item.strip()
                    is_valid_url = _is_valid_http_url(url_val)

                    if url_val in seen_sameas:
                        duplicate_sameas_urls.append(url_val)
                    else:
                        seen_sameas.add(url_val)

                    sameas_items.append(
                        {
                            "value": url_val,
                            "valid_url_format": is_valid_url,
                            "status": "duplicate" if url_val in duplicate_sameas_urls else ("valid_format" if is_valid_url else "invalid_format"),
                        }
                    )

        has_sameas = len(sameas_items) > 0
        if has_sameas:
            with_sameas_count += 1

        # 8. Calculate deterministic identity signals & strength
        identity_signals = {
            "has_type": has_type,
            "has_name": has_name,
            "has_id": has_id,
            "has_url": has_url,
            "has_identifier": has_identifier,
            "has_sameAs": has_sameas,
        }

        strength = _calculate_identity_strength(
            has_type=has_type,
            has_name=has_name,
            has_id=has_id,
            has_url=has_url,
            has_identifier=has_identifier,
            has_sameas=has_sameas,
        )

        if strength == "strong":
            strong_count += 1
        elif strength == "moderate":
            moderate_count += 1
        elif strength == "weak":
            weak_count += 1
        else:
            minimal_count += 1

        # 9. Evidence dictionary for downstream consumers
        evidence = {
            "type": types_list[0] if types_list else None,
            "name": clean_name,
            "id": clean_id,
            "url": clean_url,
            "sameAs_count": len(sameas_items),
        }

        entity_entry: dict[str, Any] = {
            "internal_id": internal_id,
            "types": types_list,
            "name": clean_name,
            "id": clean_id,
            "id_type": id_type,
            "url": clean_url,
            "valid_url_format": valid_url_format,
            "identifier": clean_identifier,
            "has_type": has_type,
            "has_name": has_name,
            "has_id": has_id,
            "has_url": has_url,
            "has_identifier": has_identifier,
            "has_sameAs": has_sameas,
            "identity_signals": identity_signals,
            "sameAs": sameas_items,
            "duplicate_sameAs": duplicate_sameas_urls,
            "roles": [],
            "identity_strength": strength,
            "external_verification": "not_checked",
            "evidence": evidence,
        }

        if field_errors:
            entity_entry["field_errors"] = field_errors

        analyzed_entities.append(entity_entry)

    # Step 3: Analyze Relationships & Map Roles
    mapped_relationships: list[dict[str, Any]] = []
    if relationships and isinstance(relationships, list):
        for rel in relationships[:max_rels]:
            if not isinstance(rel, dict):
                continue

            prop = rel.get("property")
            src = rel.get("source_id")
            tgt = rel.get("target_id")

            # Check if target maps to any known entity @id or internal_id
            target_resolved = bool(tgt and str(tgt).strip() in known_ids)

            # Assign roles back to target entity if resolved
            if target_resolved and prop and isinstance(prop, str):
                for ent in analyzed_entities:
                    if ent["id"] == tgt or ent["internal_id"] == tgt:
                        if prop not in ent["roles"]:
                            ent["roles"].append(prop)

            mapped_relationships.append(
                {
                    "property": prop,
                    "source_id": src,
                    "target_id": tgt,
                    "target_resolved": target_resolved,
                }
            )

    # Step 4: Build Duplicate Entity Observations
    duplicate_entities_obs: list[dict[str, Any]] = []
    for dup_id, indices in id_tracker.items():
        if len(indices) > 1:
            obs: dict[str, Any] = {
                "id": dup_id,
                "definitions": len(indices),
                "entity_indices": indices,
                "observed_values": observed_values_by_id.get(dup_id, {}),
            }
            duplicate_entities_obs.append(obs)

    result: dict[str, Any] = {
        "component": "entity_analyzer",
        "status": "success",
        "summary": {
            "entity_count": len(analyzed_entities),
            "strong_identity_count": strong_count,
            "moderate_identity_count": moderate_count,
            "weak_identity_count": weak_count,
            "minimal_identity_count": minimal_count,
            "entities_with_stable_id": with_stable_id_count,
            "entities_with_url": with_url_count,
            "entities_with_sameAs": with_sameas_count,
            "entities_with_identifier": with_identifier_count,
        },
        "entities": analyzed_entities,
        "relationships": mapped_relationships,
        "duplicate_entity_ids": duplicate_entities_obs,
        "errors": [],
    }

    if truncated:
        result["truncated"] = True
        result["truncation_reason"] = truncation_reason

    return result


def main() -> int:
    """Command-line interface to analyze a local entities JSON file."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write("Usage: python entity_analyzer.py <entities.json> [options_json]\n")
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
            data = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Error reading JSON file '{file_path}': {exc}\n")
        return 1

    entities = data.get("entities", []) if isinstance(data, dict) else data
    relationships = data.get("relationships", []) if isinstance(data, dict) else []

    result = analyze_entities(entities, relationships, options)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
