# Brand AI Readiness Audit (`brand-ai-readiness-audit`)
### Adobe University Hackathon Round 3 — Agent Skill Marketplace Submission

An enterprise-grade, multi-skill website auditing marketplace conforming strictly to the **Agent Skills specification** (`agentskills.io`). Composed of **5 specialized domain audit skills** coordinated by a canonical, evidence-gating **Master Audit Orchestrator** located under `skills/`.

---

## 1. Marketplace Purpose & Scope

The `brand-ai-readiness-audit` marketplace performs autonomous, non-destructive, read-only technical audits of arbitrary, unseen websites to identify:
1. **AI & Off-Site Discoverability Failures**: Missing XML sitemaps, robots.txt crawler barriers, unlinked Schema.org JSON-LD knowledge graphs, client-side rendering/hydration dead ends, and uncorroborated entity facts that prevent search crawlers, AI assistants, and LLM retrieval pipelines from finding and correctly synthesizing brand knowledge.
2. **On-Site Engagement & Conversion Friction**: Missing or buried Calls-to-Action (CTAs), confusing heading hierarchies, cluttered navigation menus, intrusive immediate overlays, difficult readability (Flesch Reading Ease), and mobile viewport layout overflows.

---

## 2. Skill Catalog & Responsibilities

The marketplace is cleanly organized under the top-level `skills/` directory:

| Skill Directory | Spec (`SKILL.md`) | Core Responsibility |
|:----------------|:------------------|:---------------------|
| `skills/audit-orchestrator` | **Entrypoint Skill** (MIT) | Dispatches audit pipelines, normalizes domain evidence into canonical schema, enforces evidence tiers, gates severity ratings, deduplicates assets, and synthesizes final `output.json`. |
| `skills/crawl-render-audit` | Domain Skill 1 (MIT) | Inspects HTTP response headers, robots.txt directives, XML sitemaps, raw HTML, and Chromium-rendered DOM to detect preloader hydration blockers and anchor-only navigation. |
| `skills/freshness-corroboration-audit` | Domain Skill 2 (MIT) | Analyzes Schema.org JSON-LD structured data, entity relationships (`sameAs`), temporal publication/modification recency against UTC clock, and flags uncorroborated claims. |
| `skills/engagement-audit` | Domain Skill 3 (MIT) | Assesses above-the-fold hero experience (at 1366x768), primary/secondary CTA clarity, navigation cognitive load, intrusive blocking popups, and readability metrics. |
| `skills/multimodal-audit` | Domain Skill 4 (MIT) | Evaluates AI and machine extractability of visual assets: missing/suspicious alt text, OCR text embedded in raster graphics, and infographic data structures for automated LLM ingestion. *(Distinct from human WCAG compliance).* |
| `skills/visual-accessibility-audit` | Domain Skill 5 (MIT) | Evaluates WCAG 2.1/2.2 AA conformance for human visitors (contrast ratios, keyboard focus tab stops, ARIA landmarks) and multi-viewport responsive rendering (1440x900, 1024x768, 390x844). *(Distinct from machine computer vision).* |

---

## 3. Designated Entrypoint & Marketplace Manifest

The marketplace declares exactly **one designated entrypoint** in `marketplace.json` conforming to the agentskills.io catalog standard:

```json
{
  "name": "brand-ai-readiness-audit",
  "version": "1.0.0",
  "skills": [
    { "id": "audit-orchestrator", "path": "skills/audit-orchestrator", "entrypoint": true },
    { "id": "crawl-render-audit", "path": "skills/crawl-render-audit" },
    { "id": "freshness-corroboration-audit", "path": "skills/freshness-corroboration-audit" },
    { "id": "engagement-audit", "path": "skills/engagement-audit" },
    { "id": "multimodal-audit", "path": "skills/multimodal-audit" },
    { "id": "visual-accessibility-audit", "path": "skills/visual-accessibility-audit" }
  ]
}
```

The master execution entrypoint script is [`run_master_audit.py`](run_master_audit.py), which invokes the orchestration logic defined in `skills/audit-orchestrator`.

---

## 4. Architecture & Skill Composition Flow

```
                              ┌────────────────────────┐
                              │    marketplace.json    │
                              └───────────┬────────────┘
                                          │
                                          ▼
                            ┌────────────────────────────┐
                            │ skills/audit-orchestrator  │ (Entrypoint Orchestrator)
                            └─────────────┬──────────────┘
                                          │
                                          ▼
                      ┌───────────────────────────────────────┐
                      │    Canonical Shared Evidence Model    │
                      │       (canonical_evidence.py)         │
                      └───────────────────┬───────────────────┘
                                          │
             ┌──────────────────┬─────────┴────────┬──────────────────┬──────────────────┐
             │                  │                  │                  │                  │
             ▼                  ▼                  ▼                  ▼                  ▼
    ┌──────────────────┐┌─────────────────┐┌────────────────┐┌────────────────┐┌─────────────────┐
    │crawl-render-audit││ freshness-corrob││engagement-audit││multimodal-audit││visual-accessib. │
    │  (HTTP / DOM)    ││ (Schema / Fact) ││ (UX / Read)    ││ (Images / OCR) ││ (WCAG / Responsive)│
    └────────┬─────────┘└────────┬────────┘└────────┬───────┘└────────┬───────┘└────────┬────────┘
             │                   │                  │                 │                 │
             └───────────────────┼──────────────────┼─────────────────┼─────────────────┘
                                 │ Machine-Readable Execution Envelopes
                                 ▼ (ok | partial | failed | skipped)
                     ┌────────────────────────────────────────┐
                     │ Evidence Gating, Normalization, Tiers  │
                     │  Compound Escalation & Deduplication   │
                     └───────────────────┬────────────────────┘
                                         │
                                         ▼
                                  ┌──────────────┐
                                  │ output.json  │ (Strict Canonical Schema)
                                  └──────────────┘
```

1. **Raw Observation**: `crawl-render-audit` fetches HTTP headers, raw HTML, robots directives, sitemaps, and launches headless Chromium to capture hydration differences.
2. **Canonical Context Construction**: The orchestrator transforms raw observations into a typed `CanonicalAuditContext` containing strictly observed data.
3. **Domain Evaluation**: Downstream skills (`engagement-audit`, `multimodal-audit`, `freshness-corroboration-audit`, `visual-accessibility-audit`) consume exact typed adapters from the shared canonical context.
4. **Machine-Readable Envelope**: Each skill returns an execution envelope (`status`: ok, partial, failed, skipped; `coverage`; `errors`; `findings`).
5. **Synthesis & Gating**: The orchestrator verifies referents, assigns explicit evidence tiers, deduplicates cross-skill asset findings, enforces compound risk escalation, calculates summary counts strictly from final findings, and emits `output.json`.

---

## 5. Bounded Multi-Page Website Auditing Architecture

Unlike single-page scrapers, the marketplace audits a **representative sample of a complete website** while strictly respecting the global 5-minute deadline:

```
                            Input Target URL
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
           robots.txt Evaluation           Sitemap Discovery
         (Strict RFC 9309 Stop)         (urlset & sitemap indexes)
                    │                             │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    Initial Homepage Crawl & Render
                     (HTML link graph extraction)
                                   │
                                   ▼
                Intelligent Page Candidate Prioritization
                  (page_discovery.py: scoring & diversity)
                                   │
                                   ▼
                Diverse Sample Selection (<= MAX_PAGES = 8)
    [homepage, pricing, product_service, about_company, contact, docs, blog]
                                   │
                                   ▼
           Bounded Per-Page Execution Loop (with Deadline Guard)
              (Crawl -> Raw HTML -> Render -> DOM Diff -> Findings)
                                   │
                                   ▼
            Multi-Page Finding Aggregation (audit_orchestrator.py)
        - Site-Wide Issues: "Audited 8 pages; 6/8 missing Schema JSON-LD"
        - Page-Specific Issues: "Isolated CTA contrast failure on /pricing"
```

### Key Multi-Page Capabilities:
1. **Intelligent Page Discovery**: Collects URL candidates from sitemaps, sitemap indexes (recursive extraction up to 2,000 URLs), internal anchor links (`<a href>`), and canonical tags.
2. **Page-Type Diversity Budgeting**: Categorizes URLs into semantic types (`homepage`, `pricing`, `product_service`, `about_company`, `contact`, `blog_content`, `docs`, `general`). Selects at least one page from each distinct category before picking duplicates, capped at `MAX_PAGES = 8`.
3. **Strict Robots.txt Compliance**: If `robots.txt` disallows crawler access to the target path, the crawler immediately halts deeper active crawling and emits a high-severity `CR-ROBOTS-DISALLOW` finding.
4. **Global Deadline Enforcement**: Tracks monotonic execution time (`global_timeout_seconds = 240.0`). If the budget runs low, it cleanly finishes current pages and emits findings without timing out.
5. **Multi-Page Finding Aggregation**: Identifies recurring structural issues across pages (e.g. 6/8 pages missing JSON-LD structured data or meta descriptions) and synthesizes them into site-wide findings with exact page ratios, while retaining isolated page-specific defects.
6. **Zero Synthetic Page Counts**: `meta.pages_crawled` reflects the actual, measured count of pages fetched and analyzed.

---

## 6. Canonical Evidence Model & Zero Synthetic Data

A foundational rule of this marketplace is that **no fake, fabricated, or synthetic evidence is ever emitted**:

- **No Synthetic Page Counts**: `pages_crawled`, `pages_discovered`, and `pages_successfully_audited` match actual network requests.
- **No Synthetic Coordinates**: Headings, buttons, and CTAs contain real bounding box coordinates (`x`, `y`, `width`, `height`) *only* when measured via Playwright. When browser rendering is unavailable, bounding boxes are `None`.
- **No Placeholder Image Dimensions**: Images report actual declared HTML attributes (`width`, `height`) or intrinsic dimensions when available. Unmeasured images report `None` — **never default 800x600 placeholders**.
- **No Assumed Responsiveness**: Viewport overflow is reported only when measured (`scrollWidth > clientWidth`). If mobile rendering was not executed, mobile checks report `insufficient_evidence` rather than false passes.

### Explicit Evidence Tiers

Every finding tracks an explicit evidence tier and confidence rating:
- **`tier_1`**: Directly observed and measured (HTTP status codes, computed contrast ratios e.g. `3.1:1`, viewport scroll dimensions, byte sizes, multi-page sample ratios e.g. `6/8 pages`, post-hydration character counts).
- **`tier_2`**: Parsed DOM structure and attributes (HTML tags, CSS selectors, missing alt attributes, Schema.org JSON-LD blocks, robots.txt directives).
- **`tier_3`**: Externally corroborated observations (external search verification, authoritative social profile linkage).
- **`tier_4`**: Inferred / heuristic assessments (Flesch reading ease scores, content density, qualitative UX structure).

> [!IMPORTANT]
> **Severity Gating Rule**: A finding is strictly forbidden from being rated `high` or `critical` based solely on Tier-4 heuristics or subjective opinions. Any finding without concrete Tier-1 or Tier-2 machine-checkable evidence is automatically downgraded to `low` or moved to `suggestions`.

---

## 7. Truthful Failure Propagation & Fallbacks

Subagent execution status is never masked:
- **`ok`**: The skill executed all intended checks successfully.
- **`partial`**: The skill completed, but certain checks were unavailable (e.g. headless browser unavailable, no images on page, external search rate-limited).
- **`failed`**: Core functionality failed (e.g. target host DNS unreachable, network timed out).
- **`skipped`**: Prerequisite inputs were not present (e.g. no HTML payload to audit).

The final report includes high-level coverage telemetry without breaking the required minimum schema:
```json
{
  "coverage": {
    "skills_run": 5,
    "skills_ok": 5,
    "skills_partial": 0,
    "skills_failed": 0
  },
  "meta": {
    "runtime_seconds": 28.96,
    "pages_discovered": 1,
    "pages_selected": 1,
    "pages_crawled": 1,
    "pages_successfully_audited": 1,
    "page_types_covered": [
      "homepage"
    ]
  }
}
```

---

## 8. LLM Connectivity, Diagnostics & Offline Resilience

The marketplace features dual-mode intelligence with automatic connection verification:
- **Live LLM Reasoning**: Seamlessly integrates with Groq, OpenRouter, OpenAI, Gemini, or local Ollama endpoints for nuanced, qualitative indexing insights.
- **Round-Trip Startup Ping**: On startup, `run_master_audit.py` initiates a non-blocking diagnostic ping via `llm_client.py` to determine actual live round-trip status.
- **Diagnostic Status Banner**:
  - `[LLM STATUS] LIVE (model: <model_name>, provider: <provider>)` — when API keys are configured and round-trip ping succeeds.
  - `[LLM STATUS] OFFLINE FALLBACK (reason: <reason>)` — when no API keys are provided or network/auth fails.
- **100% Offline Fallback**: In offline fallback mode, all 5 skills execute full-fidelity deterministic heuristic analysis and generate conformant audit findings with zero synthetic placeholders.

---

## 9. Installation & Requirements

### Prerequisites
- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- Chromium browser binaries (for Playwright client-side rendering)

### Setup & Environment Configuration
```bash
# 1. Clone repository
git clone https://github.com/neerajmittal5252-create/Machine_learning09.git
cd Machine_learning09

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install headless Chromium browser for Playwright
playwright install chromium

# 4. (Optional) Configure environment variables
# Copy .env.example to .env to provide optional LLM provider keys (Groq, OpenRouter, or OpenAI).
# Note: Real secrets must NEVER be committed to version control.
# The system runs 100% offline with full fidelity if .env is omitted.
cp .env.example .env
```

---

## 10. How to Run

### Execute Audit on Any Website
Run the master entrypoint with any target HTTP/HTTPS URL:

```bash
python run_master_audit.py https://example.com/
```

### Command-Line Arguments
```bash
python run_master_audit.py <TARGET_URL>
```

The unified audit findings are written directly to [`output.json`](output.json).

---

## 11. Required Report Output Schema

The final report conforms strictly to the challenge schema:

```json
{
  "site": "dhartiputracattlefeed.netlify.app",
  "audited_at": "2026-09-04T13:21:36Z",
  "summary": {
    "total_findings": 10,
    "critical": 0,
    "high": 2,
    "medium": 4,
    "low": 4
  },
  "findings": [
    {
      "id": "CR-HYDRATE-001",
      "title": "Client-side rendering dependency with viewport-blocking preloader (div#preloader.fixed.inset-0.z-[100].bg-white.flex.flex-col.items-center.justify-center)",
      "severity": "high",
      "evidence": "Initial HTML payload contains a viewport-blocking preloader overlay (div#preloader.fixed.inset-0.z-[100].bg-white.flex.flex-col.items-center.justify-center), creating client-side rendering dependency. Crawlers or assistants lacking interactive JavaScript execution risk indexing loading states. (Initial raw HTML contains viewport-blocking preloader overlay (div#preloader.fixed.inset-0.z-[100].bg-white.flex.flex-col.items-center.justify-center). Rendered DOM contains 4138 visible text characters post-hydration.)",
      "suggested_action": {
        "summary": "Ensure critical text and product information are server-rendered or accessible without client-side JavaScript execution.",
        "priority": "high"
      },
      "confidence": 0.9,
      "evidence_tier": "tier_1"
    }
  ],
  "coverage": {
    "skills_run": 5,
    "skills_ok": 5,
    "skills_partial": 0,
    "skills_failed": 0
  },
  "meta": {
    "runtime_seconds": 28.96,
    "pages_discovered": 1,
    "pages_selected": 1,
    "pages_crawled": 1,
    "pages_successfully_audited": 1,
    "page_types_covered": [
      "homepage"
    ]
  }
}
```

---

## 12. Testing & Validation

### Validate Submission Package Integrity
Run the deterministic submission check script to verify marketplace manifest, skill specs, zero secret exposure, and package size constraints:

```bash
python scripts/check_submission.py
```

### Run Full Test Suite
The repository features an exhaustive test suite covering individual analyzers, cross-skill imports, multi-page selection, offline resilience, and master integration scenarios:

```bash
python -m pytest -q
```

### Run Multi-Page and Master Integration Suites
```bash
python -m pytest test_multipage_audit.py test_master_integration.py test_network_resilience.py test_final_output_schema.py -v
```

Tests:
- **Multi-Page Discovery**: URL normalization, asset filtering, candidate scoring, diversity budgeting (`homepage`, `pricing`, `product`, `about`, `contact`, `blog`, `docs`).
- **Sitemap & Index Recursion**: Parsing `<urlset>` and `<sitemapindex>` hierarchies up to 2,000 URLs.
- **Finding Aggregation**: Multi-page defect synthesis (`X/Y pages`) with single-page isolated defect preservation.
- **Scenario A–L**: Complete end-to-end audit, browser fallback, network timeout, HTML degradation, robots.txt strict stop, redirect chains, zero-defect clean sites, and summary count invariants.

---

## 13. Runtime Expectations & Benchmarks

Audits run deterministically and finish in **10 to 30 seconds** on real-world websites (well under the 300s budget):

| Target Website | Elapsed Time | Pages Crawled | Findings | Execution Status |
|:---------------|:-------------|:--------------|:---------|:-----------------|
| `https://dhartiputracattlefeed.netlify.app/` | **28.96s** | 1 page | 10 findings | OK |
| `https://example.com/` | **6.08s** | 1 page | 4 findings | OK |
| `https://adobe.com/` | **24.50s** | 8 pages | 2 findings | OK |

---

## 13. Safety & Known Boundaries

1. **Bounded Multi-Page Budget**: The intelligent crawler audits up to `MAX_PAGES = 8` diverse representative routes and enforces a `global_timeout_seconds = 240.0` deadline to guarantee predictable execution.
2. **Authenticated / CAPTCHA Pages**: Sites behind Cloudflare Turnstile, Cloudflare Ray ID challenges, or login forms are not bypassed; they are safely reported with `HTTP 403 Forbidden` / `HTTP 503` findings.
3. **External Corroboration API Budget**: In environments without active LLM provider tokens, the corroboration and qualitative analyzers operate with full fidelity using deterministic heuristics (`confidence: 0.5-0.7`).
