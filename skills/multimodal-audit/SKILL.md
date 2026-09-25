---
name: multimodal-audit
description: Audits web visual assets, image quality, accessibility alt-text, embedded OCR text, technical metadata, and chart readability.
license: MIT
allowed-tools:
  - http-get
  - image-metadata-reader
  - ocr-analyzer
---

# Multimodal Audit Skill (`multimodal-audit`)

A specialized audit skill that evaluates the **visual asset health, alt-text accessibility, embedded pixel text (OCR), performance metadata, and chart clarity** across webpage media.

---

## When to Use
Use this skill when auditing visual media for machine discoverability, screen-reader accessibility, Core Web Vitals image delivery, and brand visual fidelity.

**Scope Distinction**: This skill focuses specifically on the **machine and AI extractability of visual content**—evaluating alt text for LLM/bot consumption, OCR extraction of text trapped in raster graphics, structured chart/infographic data extraction, and technical image delivery metadata. It is explicitly distinct in scope from `visual-accessibility-audit`, which governs human accessibility, WCAG Level AA compliance, and multi-viewport responsive layout mechanics.

---

## Inputs
Consumes pre-extracted structured metadata describing visual assets:
- `url` (string, required): Page canonical URL.
- `page_context` (dict): Title, primary headings, and document language.
- `images` (list): Image objects with URLs, dimensions, mime-types, bounding boxes, and alt attributes.
- `images_checked` (bool): Explicit audit coverage boolean.
- `charts` (list): Complex graphic/chart descriptors.
- `charts_checked` (bool): Explicit chart coverage boolean.

*See [references/evidence-contract.md](references/evidence-contract.md) for full evidence schemas and 3-state evidence semantics.*

---

## High-Level Architecture & Procedure
The primary auditor delegates asset verification across five specialized analyzers:

```
Multimodal Input Evidence
  ├──► ImageAnalyzer               (Format, payload size, aspect ratio)
  ├──► AltTextAnalyzer             (Presence, placeholders, conciseness)
  ├──► ImageOCRAnalyzer            (Text trapped in pixels without HTML)
  ├──► ImageMetadataAnalyzer       (Lazy-loading, srcset, priority hints)
  └──► ChartInfographicAnalyzer    (Data tables, legends, axis labeling)
             │
             ▼
      MultimodalAudit (Composite score 0-100 & findings)
```

- *See [references/multimodal-taxonomies.md](references/multimodal-taxonomies.md) for finding codes (`MM-IMG`, `MM-ALT`, `MM-OCR`, `MM-META`, `MM-CHART`), severity tiers, confidence scores, and weight distributions.*

---

## Output Contract
Returns normalized findings and category breakdowns:
```json
{
  "skill": "multimodal-audit",
  "score": 88,
  "status": "warning",
  "findings": [
    {
      "id": "MM-ALT-img-006",
      "category": "accessibility",
      "title": "Image is missing an alt attribute (img-006)",
      "severity": "medium",
      "evidence": "Image asset 'img-006' has no alt attribute defined. (url: https://example.com/logo.jpeg; role: unspecified)",
      "suggested_action": {
        "summary": "Add an alt attribute with a concise factual description.",
        "priority": "medium"
      },
      "confidence": 0.96
    }
  ],
  "categories": {
    "image": {"score": 95, "status": "passed"},
    "alt_text": {"score": 75, "status": "warning"}
  }
}
```

---

## Operational Guardrails
- **Read-Only**: Fetches asset headers and image payloads without creating client state.
- **Deduplication**: Reused layout or branding graphics sharing identical source URLs are recognized as layout assets and suppressed from false-positive duplication findings.
