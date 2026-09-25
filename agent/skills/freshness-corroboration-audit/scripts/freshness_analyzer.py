"""
Independent freshness and temporal metadata analyzer for freshness-corroboration-audit.

This module evaluates on-page temporal metadata (dateCreated, datePublished, dateModified)
and HTTP transport headers (Last-Modified) against configurable freshness heuristics and
chronological consistency rules without performing network calls, LLM inference, or severity scoring.
"""

from __future__ import annotations

import email.utils
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.freshness_analyzer")

# Default thresholds (in days)
DEFAULT_STALE_AFTER_DAYS = 365
DEFAULT_VERY_STALE_AFTER_DAYS = 730
DEFAULT_AGING_AFTER_DAYS = 180

# Bounds
DEFAULT_MAX_ENTITIES = 500
DEFAULT_MAX_TEMPORAL_VALUES_PER_ENTITY = 20

# Content type freshness relevance categories
HIGH_RELEVANCE_TYPES = {
    "article",
    "newsarticle",
    "techarticle",
    "blogposting",
    "report",
    "product",
    "event",
    "jobposting",
}

MEDIUM_RELEVANCE_TYPES = {
    "webpage",
    "dataset",
    "creativework",
}

# Supported temporal fields in Schema.org
TEMPORAL_FIELD_NAMES = ("dateCreated", "datePublished", "dateModified")


def _parse_iso_datetime(val_str: str) -> datetime | None:
    """Parse ISO 8601 date string, returning a UTC-aware datetime or None."""
    trimmed = val_str.strip()
    if not trimmed:
        return None

    # Reject ambiguous slash-formatted natural dates like "01/02/2025"
    if "/" in trimmed:
        return None

    # Date-only: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", trimmed):
        try:
            dt = datetime.strptime(trimmed, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # ISO format with T or space
    try:
        dt = datetime.fromisoformat(trimmed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    return None


def _parse_http_date(val_str: str) -> datetime | None:
    """Parse RFC 1123 / RFC 2822 HTTP date string (e.g. 'Wed, 01 Jan 2026 12:00:00 GMT')."""
    trimmed = val_str.strip()
    if not trimmed:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(trimmed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return _parse_iso_datetime(trimmed)


def _format_normalized(dt: datetime | None) -> str | None:
    """Format datetime as UTC ISO-8601 string: YYYY-MM-DDTHH:MM:SSZ."""
    if dt is None:
        return None
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_temporal_value(raw_val: Any) -> tuple[str | None, datetime | None, str]:
    """
    Parse and validate a temporal value.

    Returns:
        (normalized_str, parsed_datetime_utc, parse_status)
    """
    if raw_val is None:
        return None, None, "missing"

    raw_str = str(raw_val).strip()
    if not raw_str:
        return None, None, "invalid"

    dt = _parse_iso_datetime(raw_str)
    if dt is not None:
        return _format_normalized(dt), dt, "valid"

    return None, None, "invalid"


def _determine_freshness_relevance(types: list[str]) -> str:
    """Determine freshness sensitivity based on entity types."""
    lower_types = {t.lower() for t in types if isinstance(t, str)}
    if lower_types & HIGH_RELEVANCE_TYPES:
        return "high"
    elif lower_types & MEDIUM_RELEVANCE_TYPES:
        return "medium"
    return "low" if types else "unknown"


def analyze_freshness(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]] | None = None,
    page_metadata: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Analyze temporal signals, freshness, and chronological consistency.

    Args:
        entities: List of entity dictionaries from jsonld_analyzer / entity_analyzer.
        relationships: Optional entity relationship links (accepted for pipeline symmetry).
        page_metadata: Optional HTTP transport metadata from crawler.py (e.g. headers).
        options: Optional configuration dictionary.
            - reference_time: ISO-8601 reference timestamp (default: current UTC).
            - stale_after_days: Days before content is possibly stale (default: 365).
            - very_stale_after_days: Days before content is very stale (default: 730).
            - aging_after_days: Days before content is aging (default: 180).
            - max_entities: Limit entities analyzed (default: 500).

    Returns:
        Structured JSON-serializable dictionary with summary, analyzed entities,
        HTTP temporal signals, and chronological observations.
    """
    opts = options or {}
    stale_threshold = int(opts.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
    very_stale_threshold = int(opts.get("very_stale_after_days", DEFAULT_VERY_STALE_AFTER_DAYS))
    aging_threshold = int(opts.get("aging_after_days", DEFAULT_AGING_AFTER_DAYS))
    max_entities = int(opts.get("max_entities", DEFAULT_MAX_ENTITIES))

    # Reference time normalization
    raw_ref_time = opts.get("reference_time")
    if raw_ref_time and isinstance(raw_ref_time, str):
        ref_dt = _parse_iso_datetime(raw_ref_time) or datetime.now(timezone.utc)
    else:
        ref_dt = datetime.now(timezone.utc)

    ref_time_str = _format_normalized(ref_dt) or "1970-01-01T00:00:00Z"

    # Summary metrics
    entities_analyzed_count = 0
    with_temporal_count = 0
    without_temporal_count = 0
    valid_temporal_count = 0
    invalid_temporal_count = 0
    future_dates_count = 0
    possibly_stale_count = 0
    very_stale_count = 0

    analyzed_entities: list[dict[str, Any]] = []
    global_observations: list[dict[str, Any]] = []

    # Step 1: Analyze Entities
    safe_entities = entities[:max_entities] if isinstance(entities, list) else []

    for idx, ent in enumerate(safe_entities):
        if not isinstance(ent, dict):
            continue

        entities_analyzed_count += 1
        entity_id = ent.get("internal_id") or ent.get("id") or f"entity-{idx}"
        types = ent.get("types") or ([ent.get("type")] if ent.get("type") else [])
        name = ent.get("name")

        # Extract temporal fields from top-level or key_properties
        key_props = ent.get("key_properties", {}) if isinstance(ent.get("key_properties"), dict) else {}
        temporal_analysis: dict[str, Any] = {}
        entity_consistency_issues: list[dict[str, Any]] = []

        field_dts: dict[str, datetime | None] = {
            "dateCreated": None,
            "datePublished": None,
            "dateModified": None,
        }

        has_any_temporal = False

        for field in TEMPORAL_FIELD_NAMES:
            raw_val = ent.get(field)
            if raw_val is None and isinstance(key_props, dict):
                raw_val = key_props.get(field)

            if raw_val is not None:
                has_any_temporal = True
                norm_str, dt, status = _parse_temporal_value(raw_val)

                if status == "valid":
                    valid_temporal_count += 1
                    field_dts[field] = dt
                else:
                    invalid_temporal_count += 1
                    obs_invalid = {
                        "code": "invalid_temporal_value",
                        "entity_id": entity_id,
                        "field": field,
                        "raw_value": str(raw_val),
                    }
                    entity_consistency_issues.append(obs_invalid)
                    global_observations.append(obs_invalid)

                temporal_analysis[field] = {
                    "field": field,
                    "raw": str(raw_val),
                    "normalized": norm_str,
                    "parse_status": status,
                }

        if has_any_temporal:
            with_temporal_count += 1
        else:
            without_temporal_count += 1

        # Check future dates
        for field, dt in field_dts.items():
            if dt is not None and dt > ref_dt:
                future_dates_count += 1
                obs_future = {
                    "code": "future_temporal_value",
                    "entity_id": entity_id,
                    "field": field,
                    "date": _format_normalized(dt),
                    "reference_time": ref_time_str,
                }
                entity_consistency_issues.append(obs_future)
                global_observations.append(obs_future)

        # Check chronological consistency
        dt_created = field_dts["dateCreated"]
        dt_published = field_dts["datePublished"]
        dt_modified = field_dts["dateModified"]

        if dt_created and dt_published and dt_created > dt_published:
            obs = {
                "code": "created_after_published",
                "entity_id": entity_id,
                "fields": ["dateCreated", "datePublished"],
                "details": {
                    "dateCreated": _format_normalized(dt_created),
                    "datePublished": _format_normalized(dt_published),
                },
            }
            entity_consistency_issues.append(obs)
            global_observations.append(obs)

        if dt_published and dt_modified and dt_modified < dt_published:
            obs = {
                "code": "modified_before_published",
                "entity_id": entity_id,
                "fields": ["datePublished", "dateModified"],
                "details": {
                    "datePublished": _format_normalized(dt_published),
                    "dateModified": _format_normalized(dt_modified),
                },
            }
            entity_consistency_issues.append(obs)
            global_observations.append(obs)

        if dt_created and dt_modified and dt_modified < dt_created:
            obs = {
                "code": "modified_before_created",
                "entity_id": entity_id,
                "fields": ["dateCreated", "dateModified"],
                "details": {
                    "dateCreated": _format_normalized(dt_created),
                    "dateModified": _format_normalized(dt_modified),
                },
            }
            entity_consistency_issues.append(obs)
            global_observations.append(obs)

        # Evaluate freshness heuristic
        freshness_signals: list[str] = []
        target_ref_type: str | None = None
        target_ref_dt: datetime | None = None

        if dt_modified is not None:
            target_ref_type = "dateModified"
            target_ref_dt = dt_modified
        elif dt_published is not None:
            target_ref_type = "datePublished"
            target_ref_dt = dt_published
        elif dt_created is not None:
            target_ref_type = "dateCreated"
            target_ref_dt = dt_created

        freshness_status = "unknown"
        age_days: int | None = None
        freshness_relevance = _determine_freshness_relevance(types)

        if target_ref_dt is not None:
            if target_ref_dt > ref_dt:
                freshness_status = "future_dated"
            else:
                age_days = max(0, (ref_dt.date() - target_ref_dt.date()).days)

                if age_days >= very_stale_threshold:
                    freshness_status = "very_stale"
                    very_stale_count += 1
                    signal_name = f"{target_ref_type}_very_old"
                    freshness_signals.append(signal_name)
                    global_observations.append(
                        {
                            "code": signal_name,
                            "entity_id": entity_id,
                            "field": target_ref_type,
                            "age_days": age_days,
                            "threshold_days": very_stale_threshold,
                        }
                    )
                elif age_days >= stale_threshold:
                    freshness_status = "possibly_stale"
                    possibly_stale_count += 1
                    signal_name = f"{target_ref_type}_old"
                    freshness_signals.append(signal_name)
                    global_observations.append(
                        {
                            "code": signal_name,
                            "entity_id": entity_id,
                            "field": target_ref_type,
                            "age_days": age_days,
                            "threshold_days": stale_threshold,
                        }
                    )
                elif age_days >= aging_threshold:
                    freshness_status = "aging"
                else:
                    freshness_status = "fresh"
        elif has_any_temporal:
            # Had temporal fields, but none parsed as valid
            freshness_status = "invalid_date"

        # Observation for dateModified == datePublished
        if dt_modified and dt_published and dt_modified == dt_published:
            freshness_signals.append("dateModified_equals_datePublished")

        freshness_obj: dict[str, Any] = {
            "status": freshness_status,
            "reference_date": _format_normalized(target_ref_dt),
            "reference_date_type": target_ref_type,
            "age_days": age_days,
            "freshness_relevance": freshness_relevance,
            "signals": freshness_signals,
        }
        if freshness_status == "unknown":
            freshness_obj["reason"] = "no_valid_temporal_metadata"

        analyzed_entities.append(
            {
                "entity_id": entity_id,
                "types": types,
                "name": name,
                "temporal": temporal_analysis,
                "consistency": {
                    "issues": entity_consistency_issues,
                },
                "freshness": freshness_obj,
            }
        )

    # Step 2: Analyze HTTP Temporal Metadata (Last-Modified)
    http_signals: dict[str, Any] = {}
    if page_metadata and isinstance(page_metadata, dict):
        raw_headers = page_metadata.get("headers")
        headers = raw_headers if isinstance(raw_headers, dict) else page_metadata

        raw_last_mod = headers.get("last_modified") or headers.get("Last-Modified")
        if raw_last_mod and isinstance(raw_last_mod, str):
            parsed_last_mod = _parse_http_date(raw_last_mod)
            norm_last_mod = _format_normalized(parsed_last_mod)

            http_signals["last_modified"] = {
                "raw": raw_last_mod,
                "normalized": norm_last_mod,
                "parse_status": "valid" if parsed_last_mod else "invalid",
            }

            if parsed_last_mod:
                global_observations.append(
                    {
                        "code": "http_last_modified_available",
                        "normalized": norm_last_mod,
                    }
                )

                # Check difference against primary content date
                for a_ent in analyzed_entities:
                    dt_mod_obj = a_ent["temporal"].get("dateModified")
                    if dt_mod_obj and dt_mod_obj.get("parse_status") == "valid":
                        if dt_mod_obj.get("normalized") != norm_last_mod:
                            global_observations.append(
                                {
                                    "code": "http_last_modified_differs_from_content_date",
                                    "entity_id": a_ent["entity_id"],
                                    "http_last_modified": norm_last_mod,
                                    "schema_date_modified": dt_mod_obj.get("normalized"),
                                }
                            )

    return {
        "component": "freshness_analyzer",
        "status": "success",
        "summary": {
            "entities_analyzed": entities_analyzed_count,
            "entities_with_temporal_metadata": with_temporal_count,
            "entities_without_temporal_metadata": without_temporal_count,
            "valid_temporal_values": valid_temporal_count,
            "invalid_temporal_values": invalid_temporal_count,
            "future_dates": future_dates_count,
            "possibly_stale_entities": possibly_stale_count,
            "very_stale_entities": very_stale_count,
        },
        "entities": analyzed_entities,
        "http_temporal_signals": http_signals,
        "observations": global_observations,
    }


def main() -> int:
    """Command-line interface to analyze temporal data in an input JSON file."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write("Usage: python freshness_analyzer.py <input.json>\n")
        return 1

    file_path = sys.argv[1]
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Error reading JSON file '{file_path}': {exc}\n")
        return 1

    entities = data.get("entities", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    relationships = data.get("relationships", []) if isinstance(data, dict) else []
    page_metadata = data.get("page_metadata", {}) if isinstance(data, dict) else {}
    options = data.get("options", {}) if isinstance(data, dict) else {}

    result = analyze_freshness(entities, relationships, page_metadata, options)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
