---
name: visual-accessibility-audit
description: Audits web visual rendering, multi-viewport layout consistency, WCAG 2.2 accessibility, color contrast ratios, and keyboard navigation.
license: MIT
allowed-tools:
  - playwright-render
  - screenshot-capture
  - wcag-contrast-evaluator
  - keyboard-nav-evaluator
---

# Visual & Accessibility Audit Skill (`visual-accessibility-audit`)

A specialized audit skill that audits webpage **visual layout integrity across multiple viewports and programmatic accessibility conformance against WCAG 2.2 Level AA standards**.

---

## When to Use
Use this skill when evaluating a live URL for responsive layout breakages, text truncation, WCAG 2.1/2.2 contrast compliance, focus visibility, keyboard navigation, and ARIA semantic correctness.

**Scope Distinction**: This skill focuses specifically on **human accessibility, WCAG 2.1/2.2 Level AA compliance, and responsive layout mechanics**—evaluating human-facing readability (text color contrast ratios), interactive accessibility (keyboard navigation tab stops, visible focus rings, ARIA semantic roles), and cross-viewport responsive layout stability (overflows, text clipping, and UI viewport scaling). It is explicitly distinct in scope from `multimodal-audit`, which governs machine and AI extractability of visual assets, OCR text extraction from raster graphics, and asset technical metadata.

---

## Inputs
- `target_url` (string, required): The target webpage URL to audit.
- `options` (dictionary, optional):
  - `timeout_seconds` (integer): Total audit execution budget (default: `10`).
  - `viewports` (list): Viewport dimensions to test (default: desktop, tablet, mobile).
  - `capture_screenshots` (boolean): Whether to retain viewport screenshots.
  - `use_llm` (boolean): Enable qualitative visual reasoning (default: `true`).

---

## High-Level Architecture & Procedure
1. **Headless Browser Execution**: Launches Playwright Chromium with strict 10s budgets and SSRF guardrails.
2. **Multi-Viewport Rendering**: Sequentially renders desktop (1440×900), tablet (1024×768), and mobile (390×844) viewports, capturing layout geometry and DOM snapshots.
   - *See [references/wcag-specifications.md](references/wcag-specifications.md) for viewport parameters and visual defect criteria.*
3. **Contrast & Color Evaluation**: Evaluates computed foreground and background colors against WCAG AA formulas ($4.5:1$ body, $3.0:1$ large/UI).
4. **Keyboard Navigation & ARIA Validation**: Verifies Tab navigation flow, focus indicators, form labeling, and ARIA landmarks.
5. **Tier-1 Evidence Normalization**: Produces findings with exact element selectors, computed values, and measured ratios. Unmeasured visual critiques are cleanly separated as suggestions.
   - *See [references/accessibility-rubric.md](references/accessibility-rubric.md) for data schemas, controlled vocabularies, and severity models.*

---

## Output Contract
Returns a structured JSON payload:
```json
{
  "skill": "visual-accessibility-audit",
  "status": "completed",
  "score": 85,
  "findings": [
    {
      "id": "VA-CONTRAST-001",
      "category": "color_contrast",
      "title": "Insufficient text color contrast ratio on secondary caption",
      "severity": "medium",
      "evidence": "checked at https://example.com/; selector: '.caption'; computed contrast ratio: 3.1:1; foreground: #888888; background: #ffffff; minimum required: 4.5:1.",
      "suggested_action": "Increase text darkness to meet 4.5:1 WCAG AA minimum.",
      "confidence": "high",
      "wcag_sc": "1.4.3"
    }
  ],
  "viewports_tested": ["desktop", "tablet", "mobile"]
}
```

---

## Safety & Security Guardrails
- **Read-Only / Non-Destructive**: Keyboard navigation is strictly observational (e.g. Tab/Shift+Tab focus traversal). Never triggers destructive actions or form submissions.
- **SSRF Hardening**: Rejects loopback, link-local, and RFC 1918 private IP addresses before initiating browser rendering.
