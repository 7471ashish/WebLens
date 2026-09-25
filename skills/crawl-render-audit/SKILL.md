---
name: crawl-render-audit
description: Audits website crawlability, HTTP reachability, robots.txt directives, sitemap discovery, raw HTML vs Playwright-rendered DOM gaps, and machine-readability for automated agents and search crawlers.
license: MIT
allowed-tools:
  - http-get
  - http-head
  - playwright-render
---

# Crawl & Render Audit (`crawl-render-audit`)

A specialized audit skill for the Agent Skill Marketplace that evaluates the **technical crawlability, HTTP accessibility, raw-vs-rendered DOM integrity, and machine-readability** of a target website.

```
                  ┌──────────────────────┐
                  │  AUDIT ORCHESTRATOR  │
                  └──────────┬───────────┘
                             │ invokes module
                             ▼
                  ┌──────────────────────┐
                  │  crawl-render-audit  │
                  └──────────┬───────────┘
                             │ returns
                             ▼
                  ┌──────────────────────┐
                  │ Structured Findings  │
                  └──────────┬───────────┘
                             │ consumed by
                             ▼
                  ┌──────────────────────┐
                  │  FINDINGS AGGREGATOR │
                  └──────────────────────┘
```

---

## 1. Scope & Boundaries

### In Scope
- **robots.txt Compliance:** Parsing directives (`User-agent`, `Disallow`, `Allow`, `Crawl-delay`, `Sitemap`), verifying crawl permissions for standard bot user agents and the target URL.
- **Sitemap Discovery & Reachability:** Discovering sitemaps via `robots.txt` or standard paths (`/sitemap.xml`, `/sitemap_index.xml`), checking reachability (200 OK), and detecting major discoverability blockers.
- **HTTP Accessibility & Redirects:** Measuring response status codes, detecting redirect chains/loops, handling canonical URL mismatches, and isolating page-level vs. site-wide outages.
- **Raw HTML Inspection:** Evaluating server-delivered HTML before client-side script execution (detecting empty application shells, missing core textual content, and absent initial metadata).
- **JavaScript & Rendered DOM Comparison:** Utilizing Playwright to render the page, capturing the post-hydration DOM, and calculating content disparities between raw HTML and client-rendered DOM.
- **Machine-Readable Content Extraction:** Verifying that core facts, headings, and body content exist in readable DOM text rather than inaccessible structures or obstructed client-only components.
- **Canonical & Metadata Signals:** Inspecting canonical link tags and HTTP headers for conflicting crawl signals.
- **Optional AI Signals:** Checking for optional `llms.txt` or `llms-full.txt` (informative only; absent file is never a failure).

### Out of Scope (Delegated to Other Skills)
- Freshness and temporal corroboration analysis.
- External entity knowledge graph corroboration.
- On-site user engagement, conversion funnel, or CTA analysis.
- Visual branding, UI styling, and aesthetic quality.
- WCAG accessibility compliance audits.
- Multimodal or image pixel semantic analysis.
- Modifying website files, forms, or database states.

---

## 2. Inputs & Allowed Tools

### Inputs
The orchestrator supplies an input payload:
```json
{
  "target_url": "https://example.com/target-page",
  "options": {
    "user_agent": "Mozilla/5.0 (compatible; AgentAuditBot/1.0)",
    "timeout_ms": 30000,
    "check_sitemap": true,
    "check_llms_txt": true
  }
}
```

### Allowed Tools & Execution Primitives
- **HTTP Client (Fetch / Curl / Requests):** For fetching raw responses, headers, `robots.txt`, `sitemap.xml`, and `llms.txt`.
- **Playwright Headless Browser:** For rendering JavaScript-heavy pages, awaiting network idle or hydration, and extracting the rendered DOM.
- **DOM & Text Parser:** For computing text lengths, tag structures, and raw-vs-rendered differential metrics.

---

## 3. Safety & Operational Constraints

1. **Strictly Read-Only:** Perform only `GET` and `HEAD` requests. Never submit forms, trigger state-changing mutations, invoke destructive API endpoints, or attempt authentication.
2. **Strict robots.txt Respect:** If `robots.txt` explicitly disallows automated indexing of the target URL for the bot or `*`, record the finding and halt deeper active crawling. Never bypass `Disallow` rules.
3. **Execution Time Budget:** The entire audit procedure must complete within **4 minutes** (typically under 45 seconds for a standard target).
4. **Rate Limiting & Politeness:** Do not hammer servers with concurrent burst requests. Apply sequential checks with reasonable request timeouts (default 15–30s).
5. **No Visual / CSS Assumptions:** Focus on machine-readable text and DOM elements rather than CSS layout engine nuances.

---

## 4. Deterministic 15-Step Audit Procedure

Execute the following sequential workflow:

### Step 1: Validate Input URL
- Parse `target_url` for protocol (`http://` or `https://`), valid domain, and standard URI formatting.
- Normalize URL (e.g., ensure scheme exists). If invalid, emit a Critical `CR-001` error finding and terminate.

### Step 2: Fetch & Inspect `robots.txt`
- Issue a `GET` request to `<origin>/robots.txt`.
- Record HTTP status (e.g., `200`, `404`, `403`).
- If `200 OK`, extract:
  - `Disallow` and `Allow` rules for `*` and AI/search user-agents (`GPTBot`, `ClaudeBot`, `Googlebot`, `Bingbot`).
  - `Sitemap:` directives.
  - `Crawl-delay:` directives.

### Step 3: Determine Crawl Permission
- Evaluate whether `target_url` path matches any `Disallow` rule without an overriding `Allow`.
- If blocked by `robots.txt`:
  - Flag high/critical finding depending on whether the block was intended or accidental.
  - Set `crawl_status: "blocked_by_robots_txt"`.

### Step 4: Discover & Inspect Sitemaps
- Identify sitemap URL(s) from `robots.txt` or fallback to `<origin>/sitemap.xml`.
- Issue a `HEAD` or `GET` request to verify availability.
- Verify that the sitemap is valid XML and returns HTTP 200.
- Note if sitemap is missing or unreachable (assign Low or Medium severity depending on site scale; do not treat missing sitemap as site failure).

### Step 5: Fetch Raw HTTP Target Page
- Perform an initial direct HTTP `GET` request to `target_url` following redirects (limit max 5 redirects).
- Capture response headers, status code, content-type, and timing.

### Step 6: Record HTTP Status & Redirect Behavior
- Detect status code:
  - `200 OK`: Standard success.
  - `301` / `302` / `307` / `308`: Check redirect destination. Flag redirect chains (>2 hops) or redirect loops.
  - `4xx` (e.g., 404, 403, 410): Flag as Critical/High reachability issue.
  - `5xx` (e.g., 500, 502, 503): Flag as Critical server-side failure.
- Distinguish whether failures are page-specific (404) or host-wide (503/DNS failure).

### Step 7: Capture Raw HTML Representation
- Extract raw response body text.
- Measure:
  - Raw HTML byte size.
  - Raw text character count (text within `<body>` excluding `<script>`, `<style>`, `<noscript>`).
  - Existence of core elements: `<title>`, `<meta name="description">`, `<h1>`, `<main>`, `<article>`, `<p>`.
- Check if raw HTML is merely an empty single-page application (SPA) shell (e.g., `<div id="root"></div>` with no textual body).

### Step 8: Launch Playwright Rendering (When Needed)
- If the page contains scripts, hydration targets, or is suspected of client rendering:
  - Launch headless browser via Playwright.
  - Navigate to `target_url` with timeout (e.g., 30s).
  - Wait for `domcontentloaded` and network idle / element stabilization.

### Step 9: Capture Rendered DOM Representation
- Extract the fully rendered DOM using `page.content()`.
- Extract visible readable text content via DOM traversal or `document.body.innerText`.
- Measure rendered text character count and word count.

### Step 10: Compare Raw vs. Rendered Content
- Calculate content differential:
  $$\text{Render Gap Ratio} = \frac{\text{Rendered Text Length} - \text{Raw Text Length}}{\max(\text{Rendered Text Length}, 1)}$$
- Check if core factual information (headings, pricing, product specs, article copy) exists in the raw response or requires JavaScript execution.
- If raw HTML contains $< 15\%$ of the rendered text content, document a client-rendering dependency gap.

### Step 11: Identify Machine-Readability & Structure Issues
- Inspect rendered DOM for:
  - Essential heading hierarchy (`<h1>` presence and structure).
  - Text hidden in binary formats (e.g., text baked into canvas, non-text SVG elements, or images without `alt` attributes).
  - Unstructured layout traps that prevent standard automated scrapers from parsing the main content block (`<main>`, `<article>`).

### Step 12: Gather Objective Evidence
- Compile exact metrics for every finding:
  - HTTP status codes and redirect URLs.
  - Raw text length vs. rendered text length numbers.
  - Snippets of raw HTML vs. rendered DOM.
  - Exact `robots.txt` matching line if blocked.

### Step 13: Assign Calibrated Severity
- Apply the **Severity Guidance Matrix** (Section 5).
- Ensure strict false-positive controls (no critical severity for optional features).

### Step 14: Generate Actionable Suggested Actions
- Provide clear, developer-actionable recommendations (e.g., Server-Side Rendering (SSR), Static Site Generation (SSG), pre-rendering, sitemap correction, canonical alignment).
- Assign an implementation priority (`critical`, `high`, `medium`, `low`).

### Step 15: Return Structured Payload
- Assemble findings and metadata into the standard JSON contract and return to the orchestrator.

---

## 5. Severity & False-Positive Control

### Severity Matrix

| Severity | Definition | Examples |
|---|---|---|
| `critical` | Automated systems cannot reach the site, page is blocked, or page is completely blank/inoperable to crawlers. | HTTP 5xx on target page, `robots.txt` disallows all crawlers on main content, DNS/SSL connection failure, blank page in both raw and rendered DOM. |
| `high` | Core content or discoverability is severely impaired for non-JS crawlers or automated agents. | Pure client-rendered SPA where 90%+ of primary text/facts are absent in raw HTML; broken redirect loops; conflicting noindex tags. |
| `medium` | Non-blocking crawlability or discoverability issue that degrades machine extraction efficiency. | Unreachable sitemap XML; redirect chain with 3+ hops; missing canonical tag on duplicate parameter URLs; heavy JS dependency for secondary content. |
| `low` | Minor optimization opportunity or non-critical discoverability enhancement. | Missing `llms.txt`; sitemap missing `lastmod` timestamps; minor difference between raw and rendered meta descriptions. |

### False-Positive Control Rules
1. **JavaScript is not an automatic failure:** Modern crawlers can execute JS. Only flag client-side rendering as `high` or `medium` when critical facts/content are completely inaccessible in the initial HTTP response and require long/fragile hydration cycles.
2. **Missing `llms.txt` is NOT a finding:** `llms.txt` is an optional AI-era convenience. Its absence must never trigger a finding or decrease the score. If present, report it in `audit_metadata` as a positive note.
3. **Missing sitemap is never `critical`:** A missing sitemap on a small or well-linked site is at most `low` or `medium`.
4. **Evidence must be quantitative:**
   - ❌ **Bad:** *"JavaScript might hurt your SEO or bot readability."*
   - ✅ **Good:** *"The initial raw HTML response contains only an empty container (`<div id="app"></div>`) with 142 characters of text, whereas the Playwright-rendered DOM contains 5,230 characters of product specification text. Automated agents that do not execute client-side scripts receive no product information."*

---

## 6. Output Contract

The skill must return a structured JSON object strictly conforming to this schema:

```json
{
  "skill": "crawl-render-audit",
  "audit_metadata": {
    "audited_url": "https://example.com/products/sample-item",
    "final_url": "https://example.com/products/sample-item",
    "http_status": 200,
    "crawl_status": "accessible",
    "robots_txt": {
      "found": true,
      "url": "https://example.com/robots.txt",
      "allowed": true,
      "matching_rule": "Allow: /products/"
    },
    "sitemap": {
      "found": true,
      "url": "https://example.com/sitemap.xml",
      "status": 200
    },
    "llms_txt": {
      "found": false,
      "url": "https://example.com/llms.txt"
    },
    "rendering_metrics": {
      "raw_html_bytes": 14200,
      "raw_text_characters": 310,
      "rendered_text_characters": 4850,
      "render_gap_ratio": 0.936,
      "is_spa_shell": true,
      "hydration_duration_ms": 1240
    }
  },
  "findings": [
    {
      "id": "CR-001",
      "title": "Primary Content Absent from Raw HTML Response (Client-Side Rendering Dependency)",
      "severity": "high",
      "evidence": "The initial server response contains 310 characters of visible text consisting mostly of navigation shell elements. After Playwright browser execution, 4,850 characters of product description and technical specifications were populated. The render gap ratio is 93.6%, meaning non-JavaScript HTTP agents cannot extract the page's factual data.",
      "suggested_action": {
        "summary": "Implement Server-Side Rendering (SSR) or Static Site Generation (SSG) for public product detail pages so that core textual facts are present in the initial HTML payload.",
        "priority": "high"
      }
    }
  ]
}
```

### Clean State Guarantee
When no issues are detected during the audit:
- Return `"findings": []`
- Populate complete `audit_metadata` with all observed signals.

---

## 7. Operational Reference

### Quick Heuristics Checklist
- **SPA Shell Detection:** Raw HTML body has $< 500$ characters of text AND contains `<div id="root"></div>` or `<div id="__next"></div>` with empty child nodes.
- **Redirect Chain Limit:** $\ge 3$ sequential 3xx redirects should trigger a `medium` finding.
- **Canonical Consistency:** If `link[rel="canonical"]` points to a different domain or a 404 URL, emit a `high` finding.
- **Robots Block:** If `robots.txt` contains `Disallow: /` matching the target bot, return `crawl_status: "blocked_by_robots_txt"` and a `critical` or `high` finding.
