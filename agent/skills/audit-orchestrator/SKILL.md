---
name: brand-ai-readiness-audit
description: Audit a website for AI-discoverability and on-site-engagement problems — crawlability, JS-render gaps, missing/invalid structured data, facts locked in non-text, stale or uncorroborated facts, entity ambiguity, weak on-site orientation / no context retention — and produce a report of findings plus prioritized, actionable fixes. Use when diagnosing why a brand is missing or misrepresented in AI assistants, or why visitors who arrive don't engage.
license: MIT
allowed-tools:
  - module-composition
  - schema-validator
  - report-synthesizer
---

# Brand AI-Readiness Audit (discoverability + engagement)

A unified, multi-agent audit marketplace entrypoint that coordinates specialized subagents to diagnose why a brand is missing or misrepresented in AI assistants, or why visitors who arrive don't engage.

---

## When to use
- Use when diagnosing why a brand is missing or misrepresented in AI assistants (SearchGPT, Perplexity, Gemini, Claude).
- Use when investigating why human visitors who arrive on a site bounce or fail to engage.
- Use as the primary coordinator for comprehensive website audits, competitive benchmark analyses, and production deployment quality gates.

---

## Inputs (e.g. a URL / domain)
Consumes high-level target and scope configuration:
- `url` / `target_url` (string, required): Fully qualified HTTP/HTTPS URL or domain of target webpage.
- `options` (dict, optional):
  - `timeout_seconds` (integer): Maximum execution budget per audit module (default: 30s).
  - `allowed_domains` (list of strings): Scoped subdomains for crawl boundaries.
  - `max_pages` (integer): Maximum pages to discover and audit across the target site (default: 8).
  - `use_llm` (boolean): Whether to enable qualitative LLM enrichment (default: true with offline fallback).

---

## Procedure (numbered, deterministic steps)
1. **Parallel Domain Dispatch**: Dispatches asynchronous audit tasks across all five narrowly-scoped audit skills (`crawl-render-audit`, `freshness-corroboration-audit`, `engagement-audit`, `multimodal-audit`, `visual-accessibility-audit`).
2. **Finding Normalization**: Translates raw module observations into the canonical finding schema:
   - `id`: Globally unique, per-asset identifier (e.g., `MM-ALT-img-006`).
   - `title`: Concise summary of the objective defect.
   - `severity`: Ranked as `critical`, `high`, `medium`, or `low`.
   - `evidence`: Verifiable facts (element references, quoted attributes, URLs + status codes, measured metrics).
   - `suggested_action`: Prescriptive remediation summary and priority.
   - `confidence`: Standardized float (0.0 to 1.0).
3. **Evidence Verification & Gating**:
   - Disallows circular/self-referential claims.
   - Enforces Tier-1 evidence gating: "high" severity is strictly forbidden without verifiable facts.
   - Checks contrast claims against computed ratios, selectors, and color values.
4. **Hard-Gate Defect vs. Suggestion Split**:
   - Verifiable defects remain in `findings` with severity rankings.
   - Subjective UX/copy opinions and unmeasured qualitative claims are auto-moved to `suggestions` (no severity score, labeled `type: suggestion`).
5. **Compounding Severity Escalation**:
   - Detects compounding discoverability failures (e.g. 404 sitemap + 0 JSON-LD blocks + anchor-only navigation) and escalates compound risks to `high`.
6. **Cross-Page Deduplication & Serialization**:
   - Merges multiple observations targeting the same underlying asset into a single unified finding.
   - Computes summary counts strictly from the verified defects array.
   - Serializes canonical JSON output to `output.json`.

---

## Output (an audit report against a fixed schema: findings + suggested actions)
A JSON object adhering strictly to the specification:
```json
{
  "site": "example.com",
  "audited_at": "2026-09-03T12:00:00Z",
  "summary": {
    "total_findings": 6,
    "critical": 0,
    "high": 1,
    "medium": 3,
    "low": 2
  },
  "findings": [
    {
      "id": "MM-ALT-001",
      "title": "Missing alt attribute on informative image",
      "severity": "medium",
      "category": "multimodal_accessibility",
      "evidence": "Element <img class=\"hero-img\" src=\"/assets/hero.png\"> lacks an alt attribute.",
      "suggested_action": {
        "summary": "Add descriptive alt text to the hero image summarizing its visual content.",
        "priority": "medium"
      },
      "confidence": 1.0
    }
  ],
  "suggestions": [
    {
      "id": "SUGG-001",
      "type": "suggestion",
      "title": "Enhance Above-The-Fold Value Proposition",
      "evidence": "Hero section text is concise but does not prominently highlight the primary product differentiator.",
      "suggested_action": {
        "summary": "Consider testing a clearer secondary sub-headline directly beneath the main hero title.",
        "priority": "low"
      }
    }
  ]
}
```
