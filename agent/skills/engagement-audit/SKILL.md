---
name: engagement-audit
description: Audits user experience, attention hierarchy, CTA prominence, navigation clarity, popup obstruction, readability, responsive layout, and multi-step journey friction.
license: MIT
allowed-tools:
  - dom-traversal
  - viewport-render
  - layout-analyzer
---

# Engagement Audit Skill (`engagement-audit`)

A specialized audit skill that evaluates the **user experience (UX), above-the-fold attention hierarchy, call-to-action (CTA) prominence, navigation structure, intrusive popups, typography readability, responsive viewports, and conversion funnels** of a webpage.

---

## When to Use
Use this skill when auditing landing pages, marketing funnels, or e-commerce experiences for cognitive load, visual friction, and conversion velocity.

---

## Inputs
Consumes a pre-extracted, structured dictionary representing DOM elements, viewport coordinates, and text blocks:
- `url` (string, required): Page canonical URL.
- `viewport` (dict): Dimensions for desktop fold calculations (default: `1366×768`).
- `headings` (list): Heading tags with hierarchy levels and bounding boxes.
- `ctas` (list): Buttons and action links with bounding boxes, text, and role tags.
- `navigation` (dict): Primary header navigation links and breadcrumbs.
- `content` (dict): Main page text, paragraph splits, and language declaration.
- `forms` (list): Form definitions, inputs, and submission buttons.
- `popups` (list): Active dialogs, modals, and sticky overlays.
- `viewports` (list): Responsive layout evaluations across desktop, tablet, and mobile.
- `journey` (dict): Multi-step user path and conversion goal state.

---

## High-Level Architecture & Procedure
The orchestrator dispatches evidence across seven focused analyzers:

```
Structured Page Input
  ├──► above_fold_analyzer.py   (H1 & primary action placement)
  ├──► cta_analyzer.py          (Dominance, sizing, copy, dead ends)
  ├──► navigation_analyzer.py   (Menu depth, breadcrumbs, hierarchy)
  ├──► popup_analyzer.py        (Intrusive modals, screen coverage)
  ├──► readability_analyzer.py  (Flesch Reading Ease, grade level)
  ├──► responsive_analyzer.py   (Horizontal overflow, mobile clipping)
  └──► journey_analyzer.py      (Funnel friction, restart traps)
             │
             ▼
     engagement_audit.py (Aggregation, scoring & output)
```

- *See [references/analyzer-specs.md](references/analyzer-specs.md) for full individual analyzer checks, formulas, and edge-case handling.*
- *See [references/scoring-rubric.md](references/scoring-rubric.md) for finding ID conventions, severity tiers, confidence scales, and score weighting.*

---

## Output Contract
Returns a unified dictionary with composite scoring and categorized findings:
```json
{
  "skill": "engagement-audit",
  "score": 92,
  "status": "warning",
  "findings": [
    {
      "id": "ENG-CTA-001",
      "category": "call_to_action",
      "title": "Primary call-to-action hidden below the initial viewport fold",
      "severity": "high",
      "evidence": "Primary button 'Sign Up Free' located at y=840px (viewport height: 768px).",
      "suggested_action": {
        "summary": "Position primary conversion button above the 768px fold line.",
        "priority": "high"
      },
      "confidence": 0.95
    }
  ],
  "analyzer_reports": { ... }
}
```

---

## Operational Guardrails
- **Read-Only**: Analyzes static evidence trees without executing client mutations.
- **Strict Evidence Standards**: All severity ratings require geometric coordinates, bounding box metrics, or exact word counts.
- **Qualitative Gating**: Subjective copy critiques are automatically routed to suggestions.
