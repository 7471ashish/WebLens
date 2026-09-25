# Accessibility Finding Rubric Reference

Reference specifications for finding data models, controlled vocabularies, severity rankings, and confidence scales in `visual-accessibility-audit`.

---

## 1. Finding Data Model
Each visual accessibility finding is structured as:
```json
{
  "id": "VA-CONTRAST-001",
  "category": "color_contrast",
  "title": "Insufficient text color contrast ratio on secondary caption",
  "severity": "medium",
  "affected_url": "https://example.com/",
  "evidence": "checked at https://example.com/; selector: '.testimonial-caption'; computed contrast ratio: 3.1:1; foreground: #888888; background: #ffffff; minimum required: 4.5:1.",
  "suggested_action": "Adjust foreground color to #595959 or darker to meet the 4.5:1 WCAG AA threshold.",
  "confidence": "high",
  "viewport": "desktop",
  "wcag_sc": "1.4.3"
}
```

---

## 2. Controlled Vocabularies

### Severity Levels
- **critical**: Blocker preventing navigation or comprehension (e.g. keyboard trap, completely invisible active focus state, screen-reader blocking modal).
- **high**: Direct WCAG AA violation on primary content (e.g. missing form labels on purchase form, body text contrast ratio $< 3.0:1$, horizontal layout break on mobile).
- **medium**: Contrast ratio between $3.0:1$ and $4.5:1$ on secondary text, skipped heading levels, touch targets below recommended dimensions.
- **low**: Missing landmark region, minor focus styling inconsistency.
- **info**: Informational audit notice or positive accessibility verification.

### Confidence Levels
- **high**: Measured with deterministic computed CSS styles, bounding boxes, or automated headless browser telemetry.
- **medium**: Heuristic inference requiring human confirmation.
- **low**: Unmeasured visual opinion; automatically routed to `suggestions`.
