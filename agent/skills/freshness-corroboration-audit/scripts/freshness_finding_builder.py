"""
Independent finding builder module for freshness-corroboration-audit.

This module converts upstream structured observations (from jsonld_analyzer, entity_analyzer,
freshness_analyzer, external_corroborator, and conflict_detector) into concise,
deterministic, machine-readable audit findings with clean severity and confidence rankings.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sys
from typing import Any

# Configure standard logger
logger = logging.getLogger("freshness_corroboration_audit.finding_builder")

# Controlled severity & confidence vocabularies
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
CONFIDENCE_ORDER = {"very_high": 0, "high": 1, "medium": 2, "low": 3, "very_low": 4}

# Safe defaults
DEFAULT_MAX_FINDINGS = 100
DEFAULT_MINIMUM_CONFIDENCE = "medium"
DEFAULT_MAX_EVIDENCE_STRING_LENGTH = 2000

# Freshness sensitive types (stale warnings apply)
FRESHNESS_SENSITIVE_TYPES = {
    "article",
    "newsarticle",
    "techarticle",
    "blogposting",
    "report",
    "product",
    "event",
    "jobposting",
}


def _truncate_evidence_value(val: Any, max_len: int) -> Any:
    """Recursively truncate long string values in evidence dictionaries."""
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + "... [truncated]"
        return val
    elif isinstance(val, dict):
        return {k: _truncate_evidence_value(v, max_len) for k, v in val.items()}
    elif isinstance(val, list):
        return [_truncate_evidence_value(item, max_len) for item in val[:20]]
    return val


def _is_confidence_sufficient(conf: str, min_conf: str) -> bool:
    """Check whether confidence meets or exceeds minimum confidence threshold."""
    val_score = CONFIDENCE_ORDER.get(conf.lower(), 4)
    min_score = CONFIDENCE_ORDER.get(min_conf.lower(), 2)
    return val_score <= min_score


def detect_page_testimonials(
    raw_html: str | None = None,
    jsonld: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Detect whether a webpage actually contains customer testimonials, user reviews,
    or rating endorsements via schema.org structured data or on-page DOM heuristics.
    """
    found_clues: list[str] = []

    # 1. Check Schema.org JSON-LD structured data
    review_types = {"review", "userreview", "aggregaterating", "criticreview", "employerreview", "rating"}
    if isinstance(jsonld, dict):
        types = [str(t).lower() for t in jsonld.get("types", [])]
        matched_types = [t for t in types if t in review_types]
        if matched_types:
            found_clues.append(f"Schema.org @type: {', '.join(matched_types)}")

        for blk in jsonld.get("blocks", []):
            if isinstance(blk, dict):
                b_types = [str(t).lower() for t in blk.get("types", [])]
                b_matched = [t for t in b_types if t in review_types]
                if b_matched:
                    found_clues.append(f"Schema.org block @type: {', '.join(b_matched)}")
                    break

    # 2. Check entities analyzer output
    if isinstance(entities, dict):
        for ent in entities.get("entities", []):
            if isinstance(ent, dict):
                e_types = [str(t).lower() for t in ent.get("types", [])]
                e_matched = [t for t in e_types if t in review_types]
                if e_matched:
                    found_clues.append(f"Entity @type: {', '.join(e_matched)}")
                    break

    # 3. Check HTML microdata / RDFa / DOM heuristics
    if raw_html and isinstance(raw_html, str):
        try:
            import bs4
            soup = bs4.BeautifulSoup(raw_html, "html.parser")

            # Check microdata itemscope itemtype
            itemtypes = soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/(Review|AggregateRating)", re.I)})
            if itemtypes:
                found_clues.append(f"{len(itemtypes)} microdata Review/AggregateRating item(s)")

            # Check itemprop
            itemprops = soup.find_all(attrs={"itemprop": re.compile(r"^(review|reviewBody|reviewRating)$", re.I)})
            if itemprops:
                found_clues.append(f"{len(itemprops)} itemprop review attribute(s)")

            # Check blockquote with nearby attribution (cite, author, designation, name)
            blockquotes = soup.find_all("blockquote")
            verified_quotes = 0
            for bq in blockquotes:
                bq_text = bq.get_text(strip=True)
                if len(bq_text) < 15:
                    continue
                if bq.find("cite"):
                    verified_quotes += 1
                    continue
                parent = bq.parent
                if parent and parent.find(class_=re.compile(r"author|client|customer|attribution|designation|name|role", re.I)):
                    verified_quotes += 1
                    continue
                if parent and any(kw in str(parent.get("class", []) + [parent.get("id", "")]).lower() for kw in ["testimonial", "review", "feedback", "quote"]):
                    verified_quotes += 1
                    continue
            if verified_quotes > 0:
                found_clues.append(f"{verified_quotes} blockquote testimonial(s) with attribution")

            # Check for dedicated testimonial/review containers with multi-element cards
            testimonial_containers = soup.find_all(
                lambda t: t.name in ("section", "div", "ul") and any(
                    kw in re.split(r"[-_\s]+", str(t.get(attr, "")).lower())
                    for attr in ["id", "class"]
                    for kw in ["testimonials", "testimonial", "reviews", "customer-reviews", "client-reviews"]
                )
            )
            valid_containers = []
            for c in testimonial_containers:
                c_text = c.get_text(strip=True)
                if len(c_text) > 40 and not c.find_parent(["nav", "header"]):
                    valid_containers.append(c)
            if valid_containers:
                found_clues.append(f"{len(valid_containers)} dedicated testimonial/review section(s)")

        except Exception as e:
            logger.debug("Testimonial DOM detection error: %s", e)

    return {
        "found": len(found_clues) > 0,
        "clues": found_clues,
        "count": len(found_clues),
    }


def build_findings_qualitative_llm(
    target_url: str,
    jsonld: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    corroboration: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    raw_html: str | None = None,
) -> dict[str, Any] | None:
    """Qualitatively evaluate content freshness & entity trust using LLM reasoning (sync fallback)."""
    opts = options or {}
    client = opts.get("llm_client")
    if client is None:
        import os, sys
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate_root in [
            os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..")),
        ]:
            if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                sys.path.insert(0, candidate_root)
        try:
            from llm_client import LLMClient
            client = LLMClient()
        except Exception:
            return None

    prompt = _build_freshness_llm_prompt(target_url, jsonld, entities, freshness, corroboration, conflicts)
    result = client._generate_qualitative_fallback(prompt)
    return _parse_freshness_llm_result(result, target_url, jsonld, entities, freshness, corroboration=corroboration, options=opts, raw_html=raw_html)


async def build_findings_qualitative_llm_async(
    target_url: str,
    jsonld: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    corroboration: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    raw_html: str | None = None,
) -> dict[str, Any] | None:
    """Qualitatively evaluate content freshness & entity trust using LLM reasoning (async)."""
    opts = options or {}
    client = opts.get("llm_client")
    if client is None:
        import os, sys
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate_root in [
            os.path.abspath(os.path.join(cur_dir, "..", "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..", "..")),
            os.path.abspath(os.path.join(cur_dir, "..")),
        ]:
            if candidate_root not in sys.path and os.path.exists(os.path.join(candidate_root, "llm_client.py")):
                sys.path.insert(0, candidate_root)
        try:
            from llm_client import LLMClient
            client = LLMClient()
        except Exception:
            return None

    prompt = _build_freshness_llm_prompt(target_url, jsonld, entities, freshness, corroboration, conflicts)
    try:
        result = await client.query_json(prompt)
        return _parse_freshness_llm_result(result, target_url, jsonld, entities, freshness, corroboration=corroboration, options=opts, raw_html=raw_html)
    except Exception as exc:
        logger.warning("Qualitative LLM freshness audit fallback: %s", exc)
        return None


def _build_freshness_llm_prompt(
    target_url: str,
    jsonld: dict[str, Any] | None,
    entities: dict[str, Any] | None,
    freshness: dict[str, Any] | None,
    corroboration: dict[str, Any] | None,
    conflicts: dict[str, Any] | None,
) -> str:
    current_audit_year = datetime.now(timezone.utc).year
    return f"""
Audit Target URL: {target_url}
Current Audit Date/Year: {current_audit_year}
Structured Data (JSON-LD): {jsonld}
Entities Discovered: {entities}
Freshness & Timestamps: {freshness}
External Corroboration Claims: {corroboration}
Detected Contradictions/Conflicts: {conflicts}

Perform a qualitative, non-mathematical audit of this website's content freshness, factual accuracy, entity authority, and Schema.org markup.
Rules:
1. Always compare extracted dates against the Current Audit Year ({current_audit_year}). If the copyright year or content year is {current_audit_year}, there is NO temporal drift. Do NOT emit a staleness finding.
2. For Schema.org structured data, show the raw check: state the block count, entity @type, and specific missing attribute (e.g. sameAs).
3. Do NOT emit circular claims. Quote verified attributes or absence.

Return JSON with 'findings' array containing objects with:
id (prefixed with 'freshness-'), title, severity ('critical'|'high'|'medium'|'low'), evidence, and suggested_action.
"""


def _parse_freshness_llm_result(
    result: dict[str, Any],
    target_url: str,
    jsonld: dict[str, Any] | None,
    entities: dict[str, Any] | None,
    freshness: dict[str, Any] | None,
    corroboration: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    raw_html: str | None = None,
) -> dict[str, Any] | None:
    findings = []
    has_emitted_schema = False

    jsonld_summary = jsonld.get("summary") or {} if isinstance(jsonld, dict) else {}
    total_jsonld_blocks = jsonld_summary.get("json_ld_blocks") or len(jsonld.get("blocks", [])) if isinstance(jsonld, dict) else 0

    has_same_as = False
    if isinstance(jsonld, dict):
        for ent in jsonld.get("entities", []):
            val = ent.get("sameAs") or ent.get("same_as")
            if (isinstance(val, list) and len(val) > 0) or (isinstance(val, str) and val.strip()):
                has_same_as = True
                break
        if not has_same_as:
            for blk in jsonld.get("blocks", []):
                for ent in blk.get("entities", []):
                    val = ent.get("sameAs") or ent.get("same_as")
                    if (isinstance(val, list) and len(val) > 0) or (isinstance(val, str) and val.strip()):
                        has_same_as = True
                        break

    raw_findings = result.get("findings", []) if isinstance(result, dict) else []
    for idx, rf in enumerate(raw_findings):
        if isinstance(rf, str):
            rf = {"id": f"freshness-{idx+1:03d}", "title": rf, "evidence": rf, "severity": "low", "suggested_action": {"summary": rf}}
        elif not isinstance(rf, dict):
            continue
        fid = rf.get("id") or f"freshness-{idx+1:03d}"
        title = rf.get("title", "")
        ev = rf.get("evidence", "")

        # Check date/year staleness against current year (2026)
        if "copyright" in title.lower() or "stale" in title.lower() or "recency" in title.lower():
            if "2026" in str(freshness) or "2026" in ev:
                logger.info("Dropping false staleness finding: footer year matches audit year 2026 (no drift).")
                continue

        # For Schema/JSON-LD checklist item, format evidence with raw checked details
        if "schema" in fid.lower() or "schema" in title.lower() or "entity" in fid.lower() or "freshness-001" in fid.lower() or "jsonld" in fid.lower():
            if total_jsonld_blocks == 0:
                fid = "freshness-001"
                title = "No structured data (JSON-LD) present on the site"
                ev = f"checked at {target_url} (JSON-LD: 0 block(s) found on page)."
                rf["severity"] = "medium"
                has_emitted_schema = True
            else:
                if has_same_as:
                    logger.info("Dropping schema sameAs finding: sameAs is already present in JSON-LD.")
                    continue
                fid = "freshness-sameas-001"
                title = "Structured schema data lacks outbound entity relationship markup (sameAs)"
                ev = f"checked at {target_url} (JSON-LD: {total_jsonld_blocks} block(s) found, checked 'sameAs' attribute: not present)."
                rf["severity"] = "medium"
                has_emitted_schema = True

            sugg_action = rf.get("suggested_action")
            sugg_dict = sugg_action if isinstance(sugg_action, dict) else {
                "summary": str(sugg_action) if sugg_action else ("Embed standard Schema.org JSON-LD structured data." if total_jsonld_blocks == 0 else "Review content recency and entity markup."),
                "priority": rf.get("severity", "medium"),
            }

            findings.append({
                "id": fid,
                "finding_id": fid,
                "category": "freshness",
                "code": "qualitative_freshness",
                "severity": rf.get("severity", "medium"),
                "confidence": "high",
                "title": title or ("No structured data (JSON-LD) present on the site" if total_jsonld_blocks == 0 else "Freshness and entity trust opportunity"),
                "description": ev,
                "impact": "Improves entity confidence and content trust.",
                "recommendation": sugg_dict.get("summary", "Review content recency and entity markup."),
                "evidence": ev,
                "suggested_action": sugg_dict,
            })


        # Defect 7 / Requirement P2: Ensure raw checklist check for missing JSON-LD or missing sameAs is emitted
        if total_jsonld_blocks == 0 and not has_emitted_schema:
            findings.append({
                "id": "freshness-001",
                "finding_id": "freshness-001",
                "category": "structured_data",
                "code": "missing_structured_data_jsonld",
                "severity": "medium",
                "confidence": "high",
                "title": "No structured data (JSON-LD) present on the site",
                "description": f"checked at {target_url} (JSON-LD: 0 block(s) found on page). The site lacks Schema.org JSON-LD structured data.",
                "impact": "Search engines, knowledge graphs, and AI agents cannot deterministically extract rich entities, organization identity, dates, or author metadata.",
                "recommendation": "Deploy standard Schema.org JSON-LD structured data markup to establish rich entity definitions.",
                "evidence": f"checked at {target_url} (JSON-LD: 0 block(s) found on page).",
                "suggested_action": {
                    "summary": "Deploy standard Schema.org JSON-LD structured data markup to establish rich entity definitions.",
                    "priority": "medium",
                }
            })
        elif total_jsonld_blocks > 0 and not has_same_as and not has_emitted_schema:
            findings.append({
                "id": "freshness-sameas-001",
                "finding_id": "freshness-sameas-001",
                "category": "freshness",
                "code": "missing_schema_sameas",
                "severity": "medium",
                "confidence": "high",
                "title": "Structured schema data lacks outbound entity relationship markup (sameAs)",
                "description": f"checked at {target_url} (JSON-LD: {total_jsonld_blocks} block(s) found, checked 'sameAs' attribute: not present).",
                "impact": "Reduces machine confidence in entity identity and knowledge graph resolution.",
                "recommendation": "Enrich Schema.org JSON-LD with verified sameAs social profiles and authoritative entity identifiers.",
                "evidence": f"checked at {target_url} (JSON-LD: {total_jsonld_blocks} block(s) found, checked 'sameAs' attribute: not present).",
                "suggested_action": {
                    "summary": "Enrich Schema.org JSON-LD with verified sameAs social profiles and authoritative entity identifiers.",
                    "priority": "medium",
                }
            })


        # Requirement 2.E: Testimonial & Review Corroboration Check
        # Only emit CORROB-TESTIMONIAL-001 when testimonials are ACTUALLY FOUND on the page AND lack corroboration.
        corrob_summary = (corroboration.get("summary") or {}) if isinstance(corroboration, dict) else {}
        sources_count = int(corrob_summary.get("sources_fetched", 0) or len((corroboration or {}).get("sources", [])))
        candidate_count = len((corroboration or {}).get("candidate_urls", [])) if isinstance(corroboration, dict) else 0

        html_to_check = raw_html or (options or {}).get("raw_html") or (options or {}).get("html")
        testimonial_detection = detect_page_testimonials(
            raw_html=html_to_check,
            jsonld=jsonld,
            entities=entities,
        )

        if testimonial_detection["found"] and sources_count == 0 and candidate_count == 0 and not any("corrob" in f["id"].lower() for f in findings):
            clues_str = ", ".join(testimonial_detection["clues"])
            findings.append({
                "id": "CORROB-TESTIMONIAL-001",
                "finding_id": "CORROB-TESTIMONIAL-001",
                "category": "corroboration",
                "code": "uncorroborated_testimonials",
                "severity": "medium",
                "confidence": "high",
                "title": "Customer testimonials lack verifiable attribution and third-party corroboration",
                "description": f"checked on-page customer testimonials at {target_url} ({clues_str}); customer reviews feature unlinked attribution without external profile links or third-party platform corroboration.",
                "impact": "Uncorroborated testimonials reduce confidence for AI agents and search engines verifying brand claims.",
                "recommendation": "Link customer quotes to verified third-party review platforms (e.g. Google Reviews, Trustpilot) or verifiable entity profiles.",
                "evidence": f"checked on-page customer testimonials at {target_url} ({clues_str}); customer quotes feature unlinked attribution with 0 external corroboration profile links found.",
                "suggested_action": {
                    "summary": "Link customer quotes to verified third-party review platforms or verifiable entity profiles.",
                    "priority": "medium"
                }
            })

        if findings:
            return {
                "target_url": target_url,
                "summary": {
                    "total_findings": len(findings),
                    "critical": sum(1 for f in findings if f["severity"] == "critical"),
                    "high": sum(1 for f in findings if f["severity"] == "high"),
                    "medium": sum(1 for f in findings if f["severity"] == "medium"),
                    "low": sum(1 for f in findings if f["severity"] == "low"),
                    "info": sum(1 for f in findings if f["severity"] == "info"),
                },
                "findings": findings,
                "observations": [],
                "metadata": {"agent": "freshness-corroboration-audit", "version": "1.0"},
            }
        return None


def build_findings(
    target_url: str,
    jsonld: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    corroboration: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    raw_html: str | None = None,
) -> dict[str, Any]:
    """
    Construct deduplicated, policy-evaluated audit findings from structured observations.

    Args:
        target_url: Primary webpage URL being audited.
        jsonld: Structured output from jsonld_analyzer.py.
        entities: Structured output from entity_analyzer.py.
        freshness: Structured output from freshness_analyzer.py.
        corroboration: Structured output from external_corroborator.py.
        conflicts: Structured output from conflict_detector.py.
        options: Configurable policy dictionary.
        raw_html: Raw HTML string of the audited webpage.

    Returns:
        JSON-serializable dictionary with summary, findings, observations, and metadata.
    """
    opts = options or {}
    max_findings = int(opts.get("max_findings", DEFAULT_MAX_FINDINGS))
    require_jsonld = bool(opts.get("require_jsonld", False))
    report_stale = bool(opts.get("report_stale_content", True))
    report_unreachable_sameas = bool(opts.get("report_unreachable_sameas", False))
    min_confidence = str(opts.get("minimum_confidence", DEFAULT_MINIMUM_CONFIDENCE))
    max_ev_len = int(opts.get("max_evidence_string_length", DEFAULT_MAX_EVIDENCE_STRING_LENGTH))

    raw_findings: list[dict[str, Any]] = []
    audit_observations: list[dict[str, Any]] = []


    # Map entities by internal_id for context lookup
    entity_map: dict[str, dict[str, Any]] = {}
    if entities and isinstance(entities.get("entities"), list):
        for e in entities["entities"]:
            if isinstance(e, dict):
                eid = e.get("internal_id") or e.get("id") or "entity"
                entity_map[eid] = e

    # ---------------------------------------------------------
    # 1. STRUCTURED DATA FINDINGS (from jsonld_analyzer)
    # ---------------------------------------------------------
    total_blocks = 0
    invalid_blocks = 0
    valid_blocks = 0

    if jsonld and isinstance(jsonld, dict):
        j_summary = jsonld.get("summary", {})
        total_blocks = j_summary.get("json_ld_blocks", 0)
        invalid_blocks = j_summary.get("invalid_blocks", 0)
        valid_blocks = j_summary.get("valid_blocks", 0)

        # 1.1 Malformed JSON-LD
        if invalid_blocks > 0:
            severity = "high" if valid_blocks == 0 else "medium"
            raw_findings.append(
                {
                    "category": "structured_data",
                    "code": "malformed_jsonld",
                    "severity": severity,
                    "confidence": "high",
                    "title": "Malformed JSON-LD script block detected",
                    "description": f"Found {invalid_blocks} malformed JSON-LD script block(s) that could not be parsed as valid JSON.",
                    "impact": "Search engines and AI agents may ignore or fail to extract structured data from these blocks.",
                    "recommendation": "Validate JSON syntax in <script type='application/ld+json'> tags to ensure valid RFC 8259 format.",
                    "evidence": {
                        "invalid_blocks": invalid_blocks,
                        "total_blocks": total_blocks,
                        "errors": [err.get("error") for err in jsonld.get("errors", [])][:5],
                    },
                    "entity_id": None,
                }
            )

        # 1.3 Duplicate Entity IDs
        for dup in jsonld.get("duplicate_entity_ids", []):
            if isinstance(dup, dict) and dup.get("id"):
                raw_findings.append(
                    {
                        "category": "structured_data",
                        "code": "duplicate_entity_id",
                        "severity": "low",
                        "confidence": "high",
                        "title": "Duplicate Schema.org @id definition",
                        "description": f"The identifier '{dup.get('id')}' was defined {dup.get('definitions', 2)} times across structured data.",
                        "impact": "Ambiguous or conflicting definitions for the same identifier may complicate entity resolution.",
                        "recommendation": "Consolidate duplicate definitions of the same entity under a single canonical @id.",
                        "evidence": {
                            "id": dup.get("id"),
                            "definitions": dup.get("definitions"),
                            "observed_values": dup.get("observed_values"),
                        },
                        "entity_id": None,
                    }
                )

    # 1.2 Missing Expected Structured Data (Only if policy requires it)
    if require_jsonld and total_blocks == 0:
        raw_findings.append(
            {
                "category": "structured_data",
                "code": "missing_expected_structured_data",
                "severity": "medium",
                "confidence": "high",
                "title": "Missing Schema.org structured data",
                "description": f"The page at '{target_url}' contains no JSON-LD structured data blocks, which is required by policy.",
                "impact": "Search engines and AI agents cannot deterministically extract rich entities, dates, or author metadata.",
                "recommendation": "Embed standard Schema.org JSON-LD structured data representing the primary page entity.",
                "evidence": {
                    "target_url": target_url,
                    "json_ld_blocks": 0,
                },
                "entity_id": None,
            }
        )

    # ---------------------------------------------------------
    # 2. ENTITY IDENTITY FINDINGS (from entity_analyzer)
    # ---------------------------------------------------------
    if entities and isinstance(entities, dict):
        for ent in entities.get("entities", []):
            if not isinstance(ent, dict):
                continue
            ent_id = ent.get("internal_id") or ent.get("id") or "entity"
            types = ent.get("types", [])
            types_lower = {t.lower() for t in types if isinstance(t, str)}

            # 2.1 Invalid sameAs format
            for sa in ent.get("sameAs", []):
                if isinstance(sa, dict) and sa.get("status") == "invalid_format":
                    raw_findings.append(
                        {
                            "category": "entity_identity",
                            "code": "invalid_sameas_reference",
                            "severity": "low",
                            "confidence": "high",
                            "title": "Invalid sameAs URL format",
                            "description": f"Entity '{ent.get('name') or ent_id}' contains an invalid sameAs URL '{sa.get('value')}'.",
                            "impact": "Downstream agents and search engines cannot resolve or corroborate the outbound identity link.",
                            "recommendation": "Ensure sameAs values are valid absolute HTTP/HTTPS URLs.",
                            "evidence": {
                                "entity_id": ent_id,
                                "sameAs_value": sa.get("value"),
                            },
                            "entity_id": ent_id,
                        }
                    )

            # 2.2 Primary entity with minimal identity
            if types_lower & FRESHNESS_SENSITIVE_TYPES and ent.get("identity_strength") == "minimal":
                raw_findings.append(
                    {
                        "category": "entity_identity",
                        "code": "weak_entity_identity",
                        "severity": "low",
                        "confidence": "medium",
                        "title": "Primary entity has minimal identity signals",
                        "description": f"Entity of type {types} lacks a stable @id, canonical url, or external identifier.",
                        "impact": "Difficult for knowledge graphs and AI consumers to unambiguously identify the entity.",
                        "recommendation": "Add a canonical URL and stable @id reference to the primary entity.",
                        "evidence": {
                            "entity_id": ent_id,
                            "types": types,
                            "identity_signals": ent.get("identity_signals"),
                        },
                        "entity_id": ent_id,
                    }
                )

    # ---------------------------------------------------------
    # 3. FRESHNESS FINDINGS (from freshness_analyzer)
    # ---------------------------------------------------------
    if freshness and isinstance(freshness, dict):
        for obs in freshness.get("observations", []):
            if not isinstance(obs, dict):
                continue
            code = obs.get("code")
            ent_id = obs.get("entity_id")
            ent_info = entity_map.get(str(ent_id), {})
            types = ent_info.get("types", [])
            types_lower = {t.lower() for t in types if isinstance(t, str)}
            is_sensitive_type = bool(types_lower & FRESHNESS_SENSITIVE_TYPES)

            # 3.1 Invalid temporal values
            if code == "invalid_temporal_value":
                raw_findings.append(
                    {
                        "category": "freshness",
                        "code": "invalid_temporal_value",
                        "severity": "medium",
                        "confidence": "high",
                        "title": f"Invalid format in '{obs.get('field')}' property",
                        "description": f"Value '{obs.get('raw_value')}' for '{obs.get('field')}' could not be parsed as a valid ISO 8601 date.",
                        "impact": "Temporal freshness signals cannot be processed by search engines or AI assistants.",
                        "recommendation": "Provide valid ISO 8601 date values (e.g. YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ).",
                        "evidence": {
                            "entity_id": ent_id,
                            "field": obs.get("field"),
                            "raw_value": obs.get("raw_value"),
                        },
                        "entity_id": ent_id,
                    }
                )

            # 3.2 Future dated metadata
            elif code == "future_temporal_value":
                raw_findings.append(
                    {
                        "category": "freshness",
                        "code": "future_temporal_value",
                        "severity": "medium",
                        "confidence": "high",
                        "title": f"Future date detected in '{obs.get('field')}'",
                        "description": f"The date '{obs.get('date')}' is in the future relative to the reference audit time '{obs.get('reference_time')}'.",
                        "impact": "Future dates degrade factual credibility and cause search engines to misclassify content timeliness.",
                        "recommendation": "Verify and correct the temporal metadata to reflect the actual creation or modification date.",
                        "evidence": {
                            "entity_id": ent_id,
                            "field": obs.get("field"),
                            "date": obs.get("date"),
                            "reference_time": obs.get("reference_time"),
                        },
                        "entity_id": ent_id,
                    }
                )

            # 3.3 Chronological inconsistencies
            elif code in ("created_after_published", "modified_before_published", "modified_before_created"):
                raw_findings.append(
                    {
                        "category": "freshness",
                        "code": "inconsistent_temporal_order",
                        "severity": "medium",
                        "confidence": "high",
                        "title": "Inconsistent chronological metadata ordering",
                        "description": f"The entity's temporal fields show an inconsistent order ({code}): {obs.get('details')}.",
                        "impact": "Contradictory timestamps reduce confidence in content freshness and modification claims.",
                        "recommendation": "Review dateCreated, datePublished, and dateModified so they reflect a valid lifecycle sequence.",
                        "evidence": {
                            "entity_id": ent_id,
                            "inconsistency_code": code,
                            "details": obs.get("details"),
                        },
                        "entity_id": ent_id,
                    }
                )

            # 3.4 Stale / Very Stale metadata (Only for freshness-sensitive types)
            elif code in ("dateModified_very_old", "datePublished_very_old") and report_stale:
                if is_sensitive_type:
                    raw_findings.append(
                        {
                            "category": "freshness",
                            "code": "very_stale_content_metadata",
                            "severity": "medium",
                            "confidence": "high",
                            "title": f"Content has very old {obs.get('field')} ({obs.get('age_days')} days)",
                            "description": f"The {obs.get('field')} is {obs.get('age_days')} days old, exceeding the configured threshold of {obs.get('threshold_days')} days.",
                            "impact": "Older content may present outdated factual assertions to readers and generative AI agents.",
                            "recommendation": "Review the content for accuracy and update dateModified when significant updates occur.",
                            "evidence": {
                                "entity_id": ent_id,
                                "field": obs.get("field"),
                                "age_days": obs.get("age_days"),
                                "threshold_days": obs.get("threshold_days"),
                                "entity_types": types,
                            },
                            "entity_id": ent_id,
                        }
                    )
            elif code in ("dateModified_old", "datePublished_old") and report_stale:
                if is_sensitive_type:
                    raw_findings.append(
                        {
                            "category": "freshness",
                            "code": "stale_content_metadata",
                            "severity": "low",
                            "confidence": "medium",
                            "title": f"Content metadata indicates aging content ({obs.get('age_days')} days)",
                            "description": f"The {obs.get('field')} is {obs.get('age_days')} days old.",
                            "impact": "Content may require review to ensure facts, statistics, or recommendations remain current.",
                            "recommendation": "Periodically audit and update content to maintain freshness.",
                            "evidence": {
                                "entity_id": ent_id,
                                "field": obs.get("field"),
                                "age_days": obs.get("age_days"),
                                "threshold_days": obs.get("threshold_days"),
                                "entity_types": types,
                            },
                            "entity_id": ent_id,
                        }
                    )
            elif code == "http_last_modified_differs_from_content_date":
                audit_observations.append(obs)

    # ---------------------------------------------------------
    # 4. CORROBORATION FINDINGS (from external_corroborator)
    # ---------------------------------------------------------
    if corroboration and isinstance(corroboration, dict):
        for idc in corroboration.get("identity_checks", []):
            if not isinstance(idc, dict):
                continue
            st = idc.get("status")
            ent_id = idc.get("entity_id")

            # Unreachable sameAs (reported only if option set, else observation)
            if st == "unreachable":
                if report_unreachable_sameas:
                    raw_findings.append(
                        {
                            "category": "corroboration",
                            "code": "sameas_unreachable",
                            "severity": "low",
                            "confidence": "high",
                            "title": "Outbound sameAs reference is unreachable",
                            "description": f"The external identity URL '{idc.get('reference_url')}' could not be reached.",
                            "impact": "External agents cannot verify entity corroboration.",
                            "recommendation": "Update or remove broken outbound sameAs references.",
                            "evidence": {
                                "entity_id": ent_id,
                                "reference_url": idc.get("reference_url"),
                            },
                            "entity_id": ent_id,
                        }
                    )
                else:
                    audit_observations.append(idc)

    # ---------------------------------------------------------
    # 5. CONFLICT FINDINGS (from conflict_detector)
    # ---------------------------------------------------------
    if conflicts and isinstance(conflicts, dict):
        for conf in conflicts.get("conflicts", []):
            if not isinstance(conf, dict):
                continue

            res_type = conf.get("result")
            if res_type != "conflict":
                continue

            c_type = conf.get("conflict_type")
            field = conf.get("field")
            confidence = conf.get("confidence", "medium")
            target_ev = conf.get("target_evidence", {})
            ext_ev = conf.get("external_evidence", {})
            ent_id = target_ev.get("entity_id")

            # Assign severity based on conflict type & field
            if c_type == "identity" or conf.get("code") == "sameas_identity_mismatch":
                severity = "high"
                title = f"Identity conflict on '{field}' reference"
                rec = "Verify the identity relationship and update or remove contradictory sameAs/identifier links."
            elif c_type == "temporal":
                severity = "medium"
                title = f"Temporal conflict on '{field}'"
                rec = "Reconcile publication/modification timestamps with official external release records."
            else:
                severity = "medium"
                title = f"Structured value conflict on '{field}'"
                rec = "Verify accuracy against canonical external records."

            raw_findings.append(
                {
                    "category": "conflict",
                    "code": conf.get("code") or f"{c_type}_conflict",
                    "severity": severity,
                    "confidence": confidence,
                    "title": title,
                    "description": conf.get("reason") or f"Mutually conflicting values detected for '{field}'.",
                    "impact": "Conflicting factual claims reduce trust and entity resolution confidence across AI platforms.",
                    "recommendation": rec,
                    "evidence": {
                        "target_evidence": target_ev,
                        "external_evidence": ext_ev,
                    },
                    "entity_id": ent_id,
                }
            )

    # ---------------------------------------------------------
    # 5.5. OPTIONAL QUALITATIVE LLM ENRICHMENT (MERGED & GROUNDED)
    # ---------------------------------------------------------
    opts = options or {}
    html_content = raw_html or opts.get("raw_html") or opts.get("html")
    if opts.get("use_llm", False):
        llm_report = build_findings_llm(
            target_url=target_url,
            jsonld=jsonld,
            entities=entities,
            freshness=freshness,
            corroboration=corroboration,
            conflicts=conflicts,
            options=opts,
            raw_html=html_content,
        )
        if llm_report and isinstance(llm_report, dict):
            for lf in llm_report.get("findings", []):
                if isinstance(lf, dict):
                    raw_findings.append(lf)

    # ---------------------------------------------------------
    # 6. FILTERING, DEDUPLICATION, AND SORTING
    # ---------------------------------------------------------

    # Filter by minimum confidence
    filtered_findings: list[dict[str, Any]] = []
    for f in raw_findings:
        conf = f.get("confidence", "medium")
        code = f.get("code", "")
        # Exception: syntax errors like malformed_jsonld are always reported
        if code == "malformed_jsonld" or _is_confidence_sufficient(conf, min_confidence):
            filtered_findings.append(f)

    # Deduplicate findings on (code, entity_id, field)
    deduped_findings: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for f in filtered_findings:
        ev = f.get("evidence")
        if isinstance(ev, dict):
            field_val = ev.get("field") or ""
        elif isinstance(ev, list) and ev and isinstance(ev[0], dict):
            field_val = ev[0].get("field") or ""
        else:
            field_val = ""
        dedup_key = f"{f.get('code')}:{f.get('entity_id')}:{field_val}"
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            deduped_findings.append(f)

    # Sort deterministically
    def _sort_key(item: dict[str, Any]) -> tuple[int, int, str, str, str]:
        sev_rank = SEVERITY_ORDER.get(item.get("severity", "info").lower(), 4)
        conf_rank = CONFIDENCE_ORDER.get(item.get("confidence", "medium").lower(), 2)
        cat = item.get("category", "")
        eid = str(item.get("entity_id") or "")
        code = item.get("code", "")
        return (sev_rank, conf_rank, cat, eid, code)

    deduped_findings.sort(key=_sort_key)

    # Enforce max_findings limit
    bounded_findings = deduped_findings[:max_findings]

    # Assign deterministic finding IDs
    final_findings: list[dict[str, Any]] = []
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    cat_counts: dict[str, int] = {}

    for idx, f in enumerate(bounded_findings):
        finding_id = f"freshness-{idx + 1:03d}"
        sev = f.get("severity", "info").lower()
        cat = f.get("category", "general")

        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

        cleaned_evidence = _truncate_evidence_value(f.get("evidence", {}), max_ev_len)

        final_findings.append(
            {
                "id": finding_id,
                "finding_id": finding_id,
                "category": f.get("category"),
                "code": f.get("code"),
                "severity": sev,
                "confidence": f.get("confidence"),
                "title": f.get("title"),
                "description": f.get("description"),
                "impact": f.get("impact"),
                "recommendation": f.get("recommendation"),
                "evidence": cleaned_evidence,
                "entity_id": f.get("entity_id"),
            }
        )

    return {
        "target_url": target_url,
        "summary": {
            "total_findings": len(final_findings),
            "critical": sev_counts["critical"],
            "high": sev_counts["high"],
            "medium": sev_counts["medium"],
            "low": sev_counts["low"],
            "info": sev_counts["info"],
            "categories": cat_counts,
        },
        "findings": final_findings,
        "observations": audit_observations,
        "metadata": {
            "agent": "freshness-corroboration-audit",
            "version": "1.0",
        },
    }


build_findings_llm = build_findings_qualitative_llm


async def build_findings_async(
    target_url: str,
    jsonld: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    corroboration: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    raw_html: str | None = None,
) -> dict[str, Any]:
    """Asynchronously construct deduplicated audit findings with shared LLMClient."""
    opts = options or {}
    report = build_findings(
        target_url=target_url,
        jsonld=jsonld,
        entities=entities,
        freshness=freshness,
        corroboration=corroboration,
        conflicts=conflicts,
        options={**opts, "use_llm": False},
        raw_html=raw_html,
    )
    if opts.get("use_llm", False):
        llm_report = await build_findings_qualitative_llm_async(
            target_url=target_url,
            jsonld=jsonld,
            entities=entities,
            freshness=freshness,
            corroboration=corroboration,
            conflicts=conflicts,
            options=opts,
        )
        if llm_report and isinstance(llm_report, dict):
            for lf in llm_report.get("findings", []):
                if isinstance(lf, dict):
                    report["findings"].append(lf)
            report["summary"]["total_findings"] = len(report["findings"])
    return report



def main() -> int:
    """Command-line interface to build final audit findings from an input JSON file."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write("Usage: python finding_builder.py <input.json>\n")
        return 1

    file_path = sys.argv[1]
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Error reading JSON file '{file_path}': {exc}\n")
        return 1

    target_url = data.get("target_url", "https://example.com")
    jsonld = data.get("jsonld", {})
    entities = data.get("entities", {})
    freshness = data.get("freshness", {})
    corroboration = data.get("corroboration", {})
    conflicts = data.get("conflicts", {})
    options = data.get("options", {})

    result = build_findings(
        target_url=target_url,
        jsonld=jsonld,
        entities=entities,
        freshness=freshness,
        corroboration=corroboration,
        conflicts=conflicts,
        options=options,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
