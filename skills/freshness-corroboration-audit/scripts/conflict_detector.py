"""
Independent conflict detection module for freshness-corroboration-audit.

This module compares target webpage facts/identity/temporal observations against
bounded external evidence (from external_corroborator.py) to determine whether differences
constitute meaningful, evidenced conflicts without assigning severities or making network calls.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.conflict_detector")

# Safe default options
DEFAULT_MAX_CONFLICTS = 100
DEFAULT_MAX_COMPARISONS = 1000
DEFAULT_MAX_SOURCES_PER_CLAIM = 10

# Attributes allowed for direct structured comparison
ALLOWED_ATTRIBUTE_FIELDS = {
    "name",
    "url",
    "identifier",
    "sku",
    "brand",
    "manufacturer",
    "publisher",
    "author",
    "creator",
}

TEMPORAL_FIELDS = {"datePublished", "dateModified", "dateCreated"}


def _normalize_string(val: Any) -> str:
    """Normalize string: collapse whitespace, remove punctuation, and lower-case."""
    if val is None:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", str(val).lower())
    return " ".join(cleaned.split())


def _normalize_url(url: str | None) -> str:
    """Normalize URL by lowering scheme/host, path and trimming trailing slashes."""
    if not url or not isinstance(url, str):
        return ""
    trimmed = url.strip()
    try:
        parsed = urllib.parse.urlparse(trimmed)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/").lower()
        normalized = urllib.parse.urlunparse((scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))
        return normalized
    except Exception:
        return trimmed.rstrip("/").lower()


def _parse_to_utc_timestamp(date_str: str | None) -> datetime | None:
    """Parse date string into UTC datetime if possible."""
    if not date_str or not isinstance(date_str, str):
        return None
    trimmed = date_str.strip()
    if not trimmed or "/" in trimmed:
        return None

    # Date only: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", trimmed):
        try:
            dt = datetime.strptime(trimmed, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    try:
        dt = datetime.fromisoformat(trimmed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _are_dates_compatible(target_val: str, ext_val: str) -> bool:
    """
    Check if two date/timestamp values are semantically equal or compatible.
    Handles UTC timezone offsets and date-only vs datetime precision.
    """
    dt_target = _parse_to_utc_timestamp(target_val)
    dt_ext = _parse_to_utc_timestamp(ext_val)

    if dt_target is not None and dt_ext is not None:
        # If one is date-only (00:00:00) and the other has time precision on the exact same calendar day
        is_target_date_only = len(target_val.strip()) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_val.strip())
        is_ext_date_only = len(ext_val.strip()) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ext_val.strip())

        if is_target_date_only or is_ext_date_only:
            return dt_target.date() == dt_ext.date()

        # Both have time precision: exact match in UTC
        return dt_target == dt_ext

    # Fallback to string prefix (e.g. YYYY-MM-DD)
    t_clean = str(target_val).strip()[:10]
    e_clean = str(ext_val).strip()[:10]
    return bool(t_clean and e_clean and t_clean == e_clean)


def _extract_domain(url: str) -> str:
    """Extract domain from URL for independence tracking."""
    try:
        parsed = urllib.parse.urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return "unknown_domain"


def detect_conflicts(
    entities: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    corroboration: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compare target observations against external evidence to detect meaningful conflicts.

    Args:
        entities: List of target entities from entity_analyzer/jsonld_analyzer.
        freshness: Optional freshness analysis output from freshness_analyzer.
        corroboration: Optional external corroboration output from external_corroborator.
        options: Optional configuration dictionary.
            - max_conflicts: Maximum conflicts to return (default: 100).
            - max_comparisons: Maximum pairwise comparisons (default: 1000).
            - max_sources_per_claim: Max external sources per claim (default: 10).

    Returns:
        Structured JSON-serializable dictionary with summary, conflicts, and observations.
    """
    opts = options or {}
    max_conflicts = int(opts.get("max_conflicts", DEFAULT_MAX_CONFLICTS))
    max_comparisons = int(opts.get("max_comparisons", DEFAULT_MAX_COMPARISONS))
    max_sources_per_claim = int(opts.get("max_sources_per_claim", DEFAULT_MAX_SOURCES_PER_CLAIM))

    comparisons_count = 0
    agreements_count = 0
    disagreements_count = 0
    conflicts_count = 0
    ambiguous_count = 0
    insufficient_evidence_count = 0
    target_external_conflicts_count = 0
    external_external_disagreements_count = 0

    conflicts_list: list[dict[str, Any]] = []
    observations_list: list[dict[str, Any]] = []

    corrob_data = corroboration or {}
    external_sources = corrob_data.get("sources", [])
    identity_checks = corrob_data.get("identity_checks", [])
    claim_checks = corrob_data.get("claim_checks", [])

    # Index sources by URL for quick metadata lookup
    source_by_url: dict[str, dict[str, Any]] = {}
    for src in external_sources:
        if isinstance(src, dict) and src.get("requested_url"):
            source_by_url[src["requested_url"]] = src
        if isinstance(src, dict) and src.get("final_url"):
            source_by_url[src["final_url"]] = src

    # Step 1: Analyze Identity & sameAs Conflicts
    for id_check in identity_checks:
        if comparisons_count >= max_comparisons or len(conflicts_list) >= max_conflicts:
            break

        comparisons_count += 1
        status = id_check.get("status")
        ent_id = id_check.get("entity_id", "unknown_entity")
        ref_url = id_check.get("reference_url", "")
        signals = id_check.get("signals", [])

        # 1.1 Unreachable or insufficient evidence: NO conflict
        if status in ("unreachable", "invalid_reference", "reachable_no_identity_evidence"):
            insufficient_evidence_count += 1
            observations_list.append(
                {
                    "code": "insufficient_external_evidence",
                    "entity_id": ent_id,
                    "reference_url": ref_url,
                    "status": status,
                    "result": "insufficient_evidence",
                }
            )
            continue

        # 1.2 Strong or Probable Identity Match: Agreement
        if status in ("strong_identity_match", "probable_identity_match"):
            agreements_count += 1
            continue

        # 1.3 Weak or Mismatch Identity
        if status == "weak_identity_match":
            # Check if external source clearly belongs to a different named entity
            src_info = source_by_url.get(ref_url, {})
            ext_names = src_info.get("evidence", {}).get("entity_names", [])

            # Locate matching target entity
            target_ent = next((e for e in entities if (e.get("internal_id") == ent_id or e.get("id") == ent_id)), None)
            target_name = target_ent.get("name") if target_ent else None

            if ext_names and target_name:
                norm_target = _normalize_string(target_name)
                norm_exts = [_normalize_string(n) for n in ext_names]

                # If external has well-formed names that clearly don't match target
                if norm_target and not any(norm_target in ne or ne in norm_target for ne in norm_exts):
                    conflicts_count += 1
                    target_external_conflicts_count += 1
                    conflict_id = f"conflict-{len(conflicts_list)}"
                    conflicts_list.append(
                        {
                            "conflict_id": conflict_id,
                            "conflict_type": "identity",
                            "code": "sameas_identity_mismatch",
                            "comparison_scope": "target_vs_external",
                            "field": "sameAs",
                            "result": "conflict",
                            "confidence": "high" if "name_match" not in signals else "medium",
                            "target_evidence": {
                                "entity_id": ent_id,
                                "field": "name",
                                "value": target_name,
                                "sameAs": ref_url,
                            },
                            "external_evidence": {
                                "source_url": ref_url,
                                "field": "entity_names",
                                "value": ext_names,
                            },
                            "reason": f"Target entity '{target_name}' referenced sameAs '{ref_url}', but external source identifies '{ext_names[0]}'.",
                        }
                    )
                else:
                    ambiguous_count += 1
            else:
                ambiguous_count += 1

    # Step 2: Analyze Temporal & Claim Checks from External Corroborator
    claims_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for cc in claim_checks:
        ent_id = cc.get("entity_id", "")
        claim_type = cc.get("claim_type", "")
        key = (ent_id, claim_type)
        claims_by_key.setdefault(key, []).append(cc)

    for (ent_id, claim_field), c_list in claims_by_key.items():
        if comparisons_count >= max_comparisons or len(conflicts_list) >= max_conflicts:
            break

        # Group external evidence across sources
        target_val = c_list[0].get("target_value")
        if not target_val:
            continue

        # Track unique domains for independence
        domains_seen: set[str] = set()
        supported_domains: set[str] = set()
        disagreed_domains: set[str] = set()
        disagreed_values: list[dict[str, Any]] = []

        for cc in c_list[:max_sources_per_claim]:
            comparisons_count += 1
            res_val = cc.get("result")
            src_url = cc.get("source_url", "")
            ext_vals = cc.get("external_values", [])
            dom = _extract_domain(src_url)
            domains_seen.add(dom)

            if res_val == "supported":
                agreements_count += 1
                supported_domains.add(dom)
            elif res_val == "disagreement":
                disagreements_count += 1
                disagreed_domains.add(dom)
                disagreed_values.append(
                    {
                        "source_url": src_url,
                        "domain": dom,
                        "values": ext_vals,
                    }
                )
            elif res_val == "insufficient_evidence":
                insufficient_evidence_count += 1

        # Check for meaningful target vs external temporal conflict
        if disagreed_values:
            # Check calendar compatibility
            has_genuine_conflict = False
            for dv in disagreed_values:
                for ev in dv["values"]:
                    if not _are_dates_compatible(str(target_val), str(ev)):
                        has_genuine_conflict = True
                        break

            if has_genuine_conflict:
                conflicts_count += 1
                target_external_conflicts_count += 1
                conflict_id = f"conflict-{len(conflicts_list)}"

                # Calculate confidence based on independent domain diversity
                num_independent_domains = len(disagreed_domains)
                if num_independent_domains >= 2:
                    confidence = "high"
                elif num_independent_domains == 1:
                    confidence = "medium"
                else:
                    confidence = "low"

                conflicts_list.append(
                    {
                        "conflict_id": conflict_id,
                        "conflict_type": "temporal" if claim_field in TEMPORAL_FIELDS else "attribute",
                        "comparison_scope": "target_vs_external",
                        "field": claim_field,
                        "result": "conflict",
                        "confidence": confidence,
                        "target_evidence": {
                            "entity_id": ent_id,
                            "field": claim_field,
                            "value": target_val,
                        },
                        "external_evidence": {
                            "sources": [dv["source_url"] for dv in disagreed_values],
                            "unique_domains": list(disagreed_domains),
                            "values": [v for dv in disagreed_values for v in dv["values"]],
                        },
                        "reason": f"Target value '{target_val}' for '{claim_field}' conflicts with external evidence from {num_independent_domains} independent domain(s).",
                    }
                )

        # Step 3: Check External-vs-External Disagreements among multiple sources
        if len(disagreed_values) > 1:
            ext_vals_all = [v for dv in disagreed_values for v in dv["values"]]
            unique_ext_vals = list(dict.fromkeys(ext_vals_all))
            if len(unique_ext_vals) > 1:
                # Multiple external sources disagree with each other!
                external_external_disagreements_count += 1
                disagreements_count += 1
                observations_list.append(
                    {
                        "code": "external_source_disagreement",
                        "conflict_type": "external_source_disagreement",
                        "comparison_scope": "external_vs_external",
                        "field": claim_field,
                        "result": "disagreement",
                        "unique_domains": list(domains_seen),
                        "observed_external_values": unique_ext_vals,
                        "reason": f"External corroborating sources report differing values for '{claim_field}': {unique_ext_vals}.",
                    }
                )

    # Step 4: Whitelisted Attribute Comparisons directly across entities and source evidence
    for ent in entities:
        if comparisons_count >= max_comparisons or len(conflicts_list) >= max_conflicts:
            break

        ent_id = ent.get("internal_id") or ent.get("id") or "entity"
        for field in ALLOWED_ATTRIBUTE_FIELDS:
            if field in ent and ent[field] is not None:
                target_val = ent[field]

                # Check if an external source claims this entity identifier or URL
                for src in external_sources:
                    if comparisons_count >= max_comparisons or len(conflicts_list) >= max_conflicts:
                        break

                    ev_dict = src.get("evidence", {})
                    src_url = src.get("final_url") or src.get("requested_url", "")

                    # Example: Target URL vs external canonical URL
                    if field == "url" and ev_dict.get("canonical_url"):
                        comparisons_count += 1
                        t_norm = _normalize_url(str(target_val))
                        e_norm = _normalize_url(str(ev_dict["canonical_url"]))
                        if t_norm and e_norm:
                            if t_norm == e_norm:
                                agreements_count += 1
                            else:
                                # Canonical URL difference is recorded as disagreement, not high conflict
                                disagreements_count += 1
                                observations_list.append(
                                    {
                                        "code": "canonical_url_difference",
                                        "entity_id": ent_id,
                                        "target_url": target_val,
                                        "external_canonical": ev_dict["canonical_url"],
                                        "source_url": src_url,
                                        "result": "disagreement",
                                    }
                                )

    return {
        "component": "conflict_detector",
        "status": "success",
        "summary": {
            "comparisons_performed": comparisons_count,
            "agreements": agreements_count,
            "disagreements": disagreements_count,
            "conflicts": conflicts_count,
            "ambiguous": ambiguous_count,
            "insufficient_evidence": insufficient_evidence_count,
            "target_external_conflicts": target_external_conflicts_count,
            "external_external_disagreements": external_external_disagreements_count,
        },
        "conflicts": conflicts_list,
        "observations": observations_list,
        "errors": [],
    }


def main() -> int:
    """Command-line interface to evaluate conflicts from an input JSON file."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write("Usage: python conflict_detector.py <input.json>\n")
        return 1

    file_path = sys.argv[1]
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Error reading JSON file '{file_path}': {exc}\n")
        return 1

    entities = data.get("entities", [])
    freshness = data.get("freshness", {})
    corroboration = data.get("corroboration", {})
    options = data.get("options", {})

    result = detect_conflicts(entities, freshness, corroboration, options)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
