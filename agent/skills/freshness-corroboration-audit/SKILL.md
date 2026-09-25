---
name: freshness-corroboration-audit
description: Audits the freshness, structured factual representation, entity identity, external corroboration, and consistency of important information presented on a webpage.
license: MIT
allowed-tools:
  - http-get
  - jsonld-parser
  - search-api
---

# Freshness & Corroboration Audit (`freshness-corroboration-audit`)

A specialized, self-contained audit skill that evaluates the **semantic freshness, structured JSON-LD data, entity identity strength, external factual corroboration, and temporal consistency** of a webpage.

---

## When to Use
Use this skill when auditing a webpage for machine discoverability, brand authority, Schema.org factual consistency, and factual accuracy. It operates completely independently, performing its own HTTP retrieval and bounded corroboration lookups.

---

## Inputs
- `target_url` (string, required): The fully qualified HTTP/HTTPS URL to audit.
- `options` (dictionary, optional):
  - `timeout_ms` (integer): HTTP crawl and request timeout in milliseconds (default: `10000`).
  - `max_external_sources` (integer): Maximum external corroboration sources to query (default: `5`).
  - `max_claims` (integer): Maximum factual claims to corroborate (default: `10`).
  - `freshness_thresholds` (dict): Overrides for domain freshness decay days.
  - `use_llm` (boolean): Enable qualitative semantic corroboration (default: `true`).

---

## High-Level Procedure
1. **Independent Page Retrieval**: Fetches the HTML payload directly via `crawler.py` with custom User-Agent headers, redirect tracking, and strict 10s timeout budgets.
2. **Structured Schema Analysis**: Extracts and validates all `<script type="application/ld+json">` blocks, flattening `@graph` containers and verifying syntactic validity.
   - *See [references/schema-rules.md](references/schema-rules.md) for full JSON-LD and `@graph` rules.*
3. **Entity Identity Resolution**: Classifies canonical entities (`Organization`, `Person`, `Product`) and verifies stable `@id` attributes and outbound `sameAs` authority links.
   - *See [references/entity-identity.md](references/entity-identity.md) for entity classification and `sameAs` linkage rules.*
4. **Temporal & Freshness Evaluation**: Extracts timestamps (`datePublished`, `dateModified`, OpenGraph, HTTP `Last-Modified`), validates temporal ordering ($\text{dateModified} \ge \text{datePublished}$), and evaluates freshness relative to content type.
   - *See [references/temporal-freshness.md](references/temporal-freshness.md) for date parsing, consistency checks, and decay thresholds.*
5. **Bounded External Corroboration**: Checks key entity assertions and testimonials against authoritative external sources (e.g., official domains, registries).
   - *See [references/corroboration.md](references/corroboration.md) for claim selection, source limits, and conflict detection.*
6. **Defect Generation & False-Positive Controls**: Builds normalized findings with concrete Tier-1 evidence.
   - *See [references/severity-rubric.md](references/severity-rubric.md) for severity grading, confidence levels, and false-positive gates.*

---

## Output Contract
The skill returns a structured dictionary:
```json
{
  "skill": "freshness-corroboration-audit",
  "audit_metadata": {
    "audited_url": "https://example.com/",
    "http_status": 200,
    "retrieval_status": "success",
    "audit_duration_ms": 1420
  },
  "structured_data": {
    "json_ld_blocks": 1,
    "valid_json_ld_blocks": 1,
    "invalid_json_ld_blocks": 0,
    "graph_count": 1,
    "entity_count": 2,
    "types": ["Organization", "WebSite"]
  },
  "entities": [ ... ],
  "temporal_signals": { ... },
  "freshness": { ... },
  "corroboration": {
    "claims_checked": 2,
    "corroborated": 2,
    "conflicting": 0
  },
  "conflicts": [],
  "findings": [
    {
      "id": "freshness-001",
      "category": "entity_identity",
      "title": "Structured schema data lacks outbound entity relationship markup",
      "severity": "medium",
      "affected_url": "https://example.com/",
      "evidence": "checked at https://example.com/ (JSON-LD: 1 block(s) found, checked 'sameAs' attribute: not present).",
      "suggested_action": "Enrich Schema.org JSON-LD with verified sameAs social profiles and authoritative entity identifiers.",
      "confidence": "high"
    }
  ]
}
```

---

## Safety & Security Guardrails
- **Strictly Read-Only**: Performs only safe HTTP GET/HEAD requests. Never submits forms or modifies state.
- **SSRF Prevention**: Blocks private/loopback IP address ranges (`127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.0.0/16`).
- **Clean State**: If no defects are found, `findings` is returned as an empty array `[]`.
