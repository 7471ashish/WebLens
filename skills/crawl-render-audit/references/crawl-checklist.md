# Crawl & Render Audit Checklist

A comprehensive reference guide and verification checklist for the `crawl-render-audit` skill within the Agent Skills Marketplace.

---

## 1. Audit Scope

The `crawl-render-audit` skill deterministically evaluates whether automated systems, AI agents, and search crawlers can:

- Reach the target URL reliably over HTTP/HTTPS.
- Interpret server response codes and redirect chains.
- Determine crawling permissions and crawl delays via `robots.txt`.
- Discover and validate XML sitemaps and sitemap indexes.
- Access meaningful content, structure, and machine-readable data in the initial **raw HTML** server response.
- Access meaningful content, headings, links, and structured data after client-side **JavaScript rendering** in a real browser.
- Quantitatively measure the **content gap** between raw and rendered representations.
- Extract structured, machine-readable facts and metadata.

### Operating Principles
- **Strictly Read-Only:** The audit never executes mutations, submits forms, logs in, or alters website state.
- **Respectful & Safe:** Respects `robots.txt` directives, enforces timeout bounds, and applies SSRF protections against internal/private network addresses.
- **Evidence-Based:** Generates normalized findings supported by exact quantitative measurements without hallucinating missing data.

---

## 2. URL & HTTP Reachability

Verify that the target endpoint is reachable at the network and transport layers:

- [ ] **URL Validity:** Target URL conforms to RFC 3986 with standard syntax.
- [ ] **Allowed Schemes:** URL uses explicit `http://` or `https://` protocol (rejects `file://`, `javascript:`, `data:`).
- [ ] **SSRF & Network Safety:** Target does not resolve to `localhost`, `127.0.0.1`, `::1`, RFC 1918 private subnets, or link-local (`169.254.169.254`) addresses.
- [ ] **DNS Resolution:** Hostname resolves to valid public IP addresses.
- [ ] **TCP Connection:** Handshake establishes successfully within timeout limits (~15s).
- [ ] **TLS/SSL Handshake:** Certificate is valid, unexpired, matches domain, and negotiates securely.
- [ ] **HTTP Response Status:** Status code (2xx, 3xx, 4xx, 5xx) is accurately captured.
- [ ] **Final URL:** Final destination URL is recorded after any hops.
- [ ] **Response Headers:** Relevant headers (`Content-Type`, `X-Robots-Tag`, `Location`, `Server`) are recorded.
- [ ] **Latency / Response Time:** Total round-trip time is recorded with a monotonic clock.
- [ ] **Payload Bounding:** Initial raw HTML payload is captured up to a safe threshold (e.g., 5 MB limit).
- [ ] **Error Classification:** Transport failures (timeout, connection reset, DNS failure, SSL error) are recorded with their exact system cause rather than being conflated with HTTP status codes.

> **Important:** An HTTP `404 Not Found` or `500 Server Error` is an observed application-level status code, distinct from a transport-level network drop or timeout.

---

## 3. Redirects

Trace and evaluate all intermediate hops from the initial requested URL to the final destination:

- [ ] **Redirect Occurrence:** Identify whether one or more redirects take place.
- [ ] **Redirect Count:** Record the exact number of hops.
- [ ] **Status Codes:** Capture intermediate status codes (`301`, `302`, `303`, `307`, `308`).
- [ ] **Redirect Destinations:** Track intermediate target URLs step-by-step.
- [ ] **Final Destination:** Confirm the final landing URL.
- [ ] **Redirect Loops:** Detect cyclical redirect chains and terminate gracefully.
- [ ] **Origin Transitions:** Identify protocol upgrades (`http` $\to$ `https`), canonical subdomain changes, or cross-domain redirects.
- [ ] **Final URL Reachability:** Ensure the final landing URL returns an accessible 200 OK.

### Reference Thresholds
- **0–2 Redirects:** Standard, acceptable configuration (e.g., canonical domain + HTTPS upgrade).
- **3–4 Redirects:** Potential concern; consumes unnecessary crawler budget and adds latency.
- **5+ Redirects:** Significant concern (High Severity); risk of crawler truncation or abandonment.

---

## 4. robots.txt

Locate, fetch, parse, and evaluate `robots.txt` according to RFC 9309 standards:

- [ ] **Location Determination:** Compute `robots.txt` URL at the host root (e.g., `https://example.com/robots.txt`).
- [ ] **Reachability:** Fetch `robots.txt` within timeout bounds.
- [ ] **HTTP Status:** Record HTTP status code.
- [ ] **Group Identification:** Parse declared `User-agent` record groups.
- [ ] **Crawler Matching:** Evaluate permissions for specific agents (`GPTBot`, `ClaudeBot`, `Googlebot`, `Bingbot`, `AgentAuditBot`) and fallback to wildcard `*`.
- [ ] **Rule Precedence:** Apply longest-match rule precedence for conflicting `Allow` and `Disallow` directives.
- [ ] **Wildcard & Anchor Matching:** Support path wildcards (`*`) and end-of-path anchors (`$`).
- [ ] **Target Permission:** Deterministically conclude whether the specific audit target URL is allowed or disallowed.
- [ ] **Sitemap Directives:** Extract all declared `Sitemap:` URLs.
- [ ] **Crawl-Delay:** Record declared `Crawl-delay` values where present.
- [ ] **Syntax Faults:** Record malformed lines or syntax errors without aborting.

> **Important:** An HTTP `404 Not Found` for `robots.txt` indicates that **all URLs are allowed by default**. Missing `robots.txt` is never a crawler failure.

---

## 5. Sitemap

Discover, fetch, decompress, and validate XML sitemaps:

### Discovery Hierarchy
1. **Robots.txt Directives:** Extract declared `Sitemap: <URL>` lines.
2. **Conventional Location:** Fallback to `/sitemap.xml`.
3. **Index Location:** Fallback to `/sitemap_index.xml`.

### Verification Checklist
- [ ] **URL Validation:** Validate syntax and scheme of discovered sitemap URLs.
- [ ] **Accessibility:** Confirm HTTP 200 OK with valid XML or gzip payload.
- [ ] **Gzip Support:** Automatically decompress `.xml.gz` or `application/x-gzip` streams.
- [ ] **XML Parsing:** Parse XML with namespace handling (`xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"`).
- [ ] **Root Node Identification:** Identify `<urlset>` (standard sitemap) vs. `<sitemapindex>` (index file).
- [ ] **Index Recursion:** Recursively resolve child sitemaps in index files with cycle detection.
- [ ] **Safety Bounds:** Impose strict recursion limits (e.g., max 10 sitemaps, max 10,000 URLs, 5 MB file size limit).
- [ ] **URL Extraction:** Extract `<loc>`, `<lastmod>`, `<changefreq>`, and `<priority>`.
- [ ] **Duplicate Detection:** Count duplicate `<loc>` entries within or across sitemaps.
- [ ] **Structural Validation:** Detect empty sitemaps, missing `<loc>` tags, or invalid XML roots.

> **Important:** Do NOT crawl every URL listed in a sitemap. Missing `sitemap.xml` is an informational observation, NOT an audit failure.

---

## 6. Raw HTML

Inspect the initial server response payload prior to client-side JavaScript execution:

- [ ] **Payload Existence:** Response body contains non-empty HTML text.
- [ ] **DOM Parseability:** HTML parses without fatal parser exceptions.
- [ ] **Title Tag:** Document `<title>` tag exists with non-empty text.
- [ ] **Meta Description:** `<meta name="description" content="...">` exists (case-insensitive).
- [ ] **Canonical Link:** `<link rel="canonical" href="...">` is present, valid, and absolute.
- [ ] **Language Declaration:** `<html lang="...">` is declared.
- [ ] **Headings Hierarchy:** Counts and text for `<h1>` through `<h6>` are extracted.
- [ ] **Clean Text Content:** Visible body text (excluding `<script>`, `<style>`, `<noscript>`, `<template>`, `<svg>`, and comments) is measured for character and word counts.
- [ ] **Semantic Elements:** Counts for `<main>`, `<article>`, `<section>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, `<figure>`, `<figcaption>`.
- [ ] **Links:** Extracted `<a href="...">` classified as internal, external, relative, empty, or fragment-only.
- [ ] **Images:** HTML-level attributes (`src`, `alt`, `width`, `height`, `loading`) captured.
- [ ] **Structured Data:** Embedded `<script type="application/ld+json">` blocks parsed and schema `@type` values extracted.
- [ ] **Robots Directives:** `<meta name="robots" content="...">` and `X-Robots-Tag` headers extracted.
- [ ] **SPA Shell Heuristics:** Detect presence of `#root`, `#app`, `#__next`, `#app-root` combined with low raw text volume (<300 chars) and script tags.

> **Scope Note:** Deep image inspection (OCR, EXIF, vision models, visual branding) is handled by the dedicated image analysis subagent. Only HTML-level attributes are collected here.

---

## 7. Browser Rendering

Execute client-side JavaScript in headless Chromium via Playwright to observe post-hydration state:

- [ ] **Browser Launch & Context:** Clean, isolated browser context without cached cookies, stored profiles, or credentials.
- [ ] **Navigation Execution:** `page.goto(url, wait_until="networkidle", timeout=20000)`.
- [ ] **Fallback Strategy:** If `networkidle` times out on continuous polling/SSE, gracefully degrade to `domcontentloaded`.
- [ ] **Post-Load Stabilization:** Configurable stabilization delay (`wait_after_load_ms=1000`) for async DOM injection.
- [ ] **Rendered DOM Capture:** Full post-hydration HTML captured via `page.content()`.
- [ ] **Browser Visible Text:** Extract visible text via `document.body.innerText` (respecting `display: none`, `visibility: hidden`, and `hidden` attributes).
- [ ] **Main & Article Content:** Extract visible text inside `<main>` and `<article>`.
- [ ] **Rendered Metadata:** Document title, meta description, and canonical link evaluated post-hydration.
- [ ] **Rendered Headings & Links:** Headings and links extracted after dynamic script generation.
- [ ] **Rendered Structured Data:** JSON-LD schemas injected by client scripts extracted.
- [ ] **Runtime Error Trapping:** `page.on("pageerror")` captures unhandled JavaScript exceptions.
- [ ] **Console Monitoring:** `page.on("console")` records console errors and warnings.
- [ ] **Network Resource Failures:** `page.on("requestfailed")` logs failed script/asset requests.
- [ ] **Resource Cleanup:** Guaranteed `finally` block teardown for pages, contexts, and browser instances.

---

## 8. Raw vs. Rendered Comparison

Compare baseline raw HTML observations against post-hydration browser observations:

- [ ] **DOM Size Delta:** $\Delta_{\text{bytes}} = \text{DOM}_{\text{rendered}} - \text{HTML}_{\text{raw}}$ and percentage growth.
- [ ] **Visible Text Delta:** $\Delta_{\text{text}} = \text{Text}_{\text{rendered}} - \text{Text}_{\text{raw}}$.
- [ ] **Text Ratio:** $\text{Ratio} = \frac{\text{Text}_{\text{rendered}}}{\text{Text}_{\text{raw}}}$ (handling raw = 0 without division-by-zero).
- [ ] **Headings Growth:** Identify headings (`<h1>`–`<h6>`) that appear only after JavaScript execution.
- [ ] **Links Delta:** Quantitative count of links added post-hydration.
- [ ] **Structured Data Injection:** Schemas injected dynamically via client-side JavaScript.
- [ ] **Semantic Container Injection:** Discovery of `<main>` or `<article>` created only after render.
- [ ] **Dynamic Title / Metadata Mutation:** Changes in `<title>`, `<meta description>`, or canonical tags after navigation.
- [ ] **Client Shell Scenario:** High-confidence detection where raw HTML is an empty container while rendered DOM holds full content.
- [ ] **Empty Rendered Page:** Detection where the page remains empty/sparse even after full browser rendering.

---

## 9. Machine Readability

Evaluate whether page content is organized for programmatic comprehension:

- [ ] **Meaningful Text:** Page provides sufficient non-boilerplate visible text (>500 characters).
- [ ] **Content Accessibility:** Important content is available directly in raw HTML when practical.
- [ ] **Heading Architecture:** Logical hierarchy starting with a single descriptive `<h1>`.
- [ ] **Navigability:** Internal links utilize standard `<a href="...">` tags rather than JavaScript `onclick` triggers.
- [ ] **Semantic Layout:** Primary content is contained within `<main>` or `<article>` landmark tags.
- [ ] **Schema Coverage:** Schema.org entities (`Organization`, `Product`, `Article`, `BreadcrumbList`) declared in JSON-LD.
- [ ] **Direct Indexability:** Search crawlers and automated LLM agents can extract facts without requiring a full browser rendering engine.

---

## 10. Metadata & Canonical

Evaluate identity, description, and canonicalization directives:

### Title
- [ ] Title tag exists in document `<head>`.
- [ ] Title contains descriptive text (not empty or default placeholder).
- [ ] Exactly one title tag is present (no duplicate tags).

### Meta Description
- [ ] Meta description tag is present with meaningful summary content.
- [ ] Raw and rendered descriptions are consistent.

### Canonical URL
- [ ] Canonical `<link rel="canonical" href="...">` exists.
- [ ] Canonical URL is a syntactically valid absolute URL.
- [ ] Exactly one canonical tag is defined.
- [ ] Canonical points to the expected canonical host and path.
- [ ] Raw and rendered canonical URLs do not conflict.

---

## 11. Evidence Requirements

Every finding generated by `finding_builder.py` must contain concrete, quantitative evidence:

```json
{
  "source": "dom_comparator",
  "field": "text_content",
  "value": {
    "raw_characters": 82,
    "rendered_characters": 3840,
    "ratio": 46.83
  },
  "details": "Raw HTML text: 82 characters, Rendered DOM text: 3,840 characters (46.8x increase)"
}
```

### Evidence Rules
- **Specific Numbers:** Include raw counts, rendered counts, status codes, and URLs.
- **Source Attributed:** Identify the generating module (`crawler`, `robots_checker`, `sitemap_checker`, `raw_html_analyzer`, `render_analyzer`, `dom_comparator`).
- **Zero Hallucinations:** Never construct assertions unsupported by direct observation data.

---

## 12. Severity Guidance

| Severity | Definition | Examples |
| :--- | :--- | :--- |
| **`CRITICAL`** | Issues that completely block automated crawlers from reaching or parsing the website. | DNS resolution failure; HTTP 503; target completely unreachable. |
| **`HIGH`** | Significant issues that substantially impair crawlability or machine comprehension. | Target blocked by `robots.txt`; HTTP 404/403; severe JS rendering dependency (raw text < 500 chars, rendered > 2500 chars); invalid canonical tag; redirect loops. |
| **`MEDIUM`** | Meaningful weaknesses that degrade performance, SEO, or indexing efficiency. | Moderate JS rendering dependency (2x–5x text ratio); 3–4 redirect hops; malformed sitemap XML; missing `<h1>`; conflicting raw vs. rendered canonical tags. |
| **`LOW`** | Minor optimization opportunities with low immediate indexing risk. | Missing `<meta name="description">`; multiple `<h1>` headings; unhandled JS console errors; excessive duplicate sitemap URLs; structured data injected only via JS. |
| **`INFO`** | Positive or neutral operational observations. | Sitemaps successfully discovered; valid JSON-LD schemas parsed; page successfully reached. |

---

## 13. False-Positive Rules

Mandatory constraints to prevent inaccurate findings:

1. **JavaScript is Not Broken by Default:** Modern sites use client-side hydration. Only flag rendering dependencies when meaningful content is absent from the initial raw payload.
2. **Missing Robots.txt $\neq$ Failure:** An HTTP 404 for `robots.txt` means the site is open for crawling.
3. **Missing Sitemap $\neq$ Critical Failure:** A missing sitemap is an optimization note, never an error.
4. **Missing Optional Metadata $\neq$ Critical:** Absence of OpenGraph or description tags is low severity.
5. **Tool Timeout $\neq$ Site Failure:** A Playwright timeout during `networkidle` triggers graceful fallback to `domcontentloaded` with `medium` confidence.
6. **Missing Data $\neq$ Zero:** Distinguish between missing fields (`None`) and explicit zero counts (`0`).
7. **No Direct HTML Diffs:** Compare structured metrics (text, headings, links, schemas) rather than raw HTML string diffs.
8. **Deduplication:** Merge overlapping observations into a single representative finding.

---

## 14. Safety & Resource Bounds

| Parameter | Recommended Bound | Purpose |
| :--- | :--- | :--- |
| **HTTP Request Timeout** | 15 seconds | Prevents hung socket connections. |
| **Browser Navigation Timeout** | 20 seconds | Bounds long-running client scripts. |
| **Max Payload Size** | 5,000,000 bytes (5 MB) | Guards against memory exhaustion from large downloads. |
| **Max Sitemaps Parsed** | 10 sitemaps | Bounds index recursion. |
| **Max Sitemap URLs Extracted** | 10,000 URLs | Prevents unbounded list parsing. |
| **SSRF Filtering** | Enabled | Blocks access to private IP subnets and cloud metadata endpoints. |
| **Browser Contexts** | Non-persistent, isolated | Never uses stored cookies, credentials, or user profiles. |

---

## 15. Final Checklist

### Connectivity & HTTP
- [ ] Target URL syntax and scheme validated.
- [ ] SSRF check passed.
- [ ] HTTP status code recorded.
- [ ] Redirect chain traced and hop count evaluated.

### Crawlability & Sitemaps
- [ ] `robots.txt` fetched and parsed.
- [ ] Target URL crawl permission evaluated.
- [ ] Sitemaps discovered via `robots.txt` and root paths.
- [ ] Sitemap XML parsed, decompressed, and validated.

### Raw HTML Analysis
- [ ] Raw HTML captured and bounded.
- [ ] Text character count and word count measured.
- [ ] Headings (`<h1>`–`<h6>`) counted and extracted.
- [ ] Links and HTML-level image attributes extracted.
- [ ] Embedded JSON-LD structured data parsed.
- [ ] Meta robots and `X-Robots-Tag` extracted.

### Browser Rendering & Hydration
- [ ] Headless Chromium rendering executed.
- [ ] Post-hydration visible text measured (`innerText`).
- [ ] Runtime JavaScript exceptions and console errors captured.
- [ ] Failed network resources recorded.

### Differential Gap Analysis
- [ ] Text growth ratio and delta calculated safely without division-by-zero.
- [ ] Headings, links, and structured data additions identified.
- [ ] Client-rendered SPA shell vs. static SSR state classified.

### Finding Generation
- [ ] Normalized findings created with stable IDs (`HTTP-404`, `ROBOTS-DISALLOWED`, `RENDER-GAP-HIGH`).
- [ ] Concrete quantitative evidence attached to each finding.
- [ ] Evidence-based severity assigned (`critical`, `high`, `medium`, `low`, `info`).
- [ ] Actionable remediation recommendations provided.
- [ ] Duplicate findings merged.
- [ ] Output formatted as clean, deterministic JSON.
