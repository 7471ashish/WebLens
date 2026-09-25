# Multimodal Input Evidence Contract Reference

Reference specifications for input evidence data contracts and 3-state evidence semantics in `multimodal-audit`.

---

## 1. Input Evidence Schema
The orchestrator supplies a structured JSON evidence dictionary to `MultimodalAudit.audit()`:

```json
{
  "url": "https://example.com/products",
  "page_context": {
    "title": "Industrial Equipment Catalog",
    "heading": "Heavy Machinery & Solutions",
    "language": "en"
  },
  "images": [
    {
      "id": "img-001",
      "url": "https://example.com/assets/loader.webp",
      "filename": "loader.webp",
      "alt_text": "Heavy industrial front loader digging in quarry",
      "alt_attribute_present": true,
      "decorative": false,
      "role": "img",
      "width": 1200,
      "height": 800,
      "mime_type": "image/webp",
      "format": "webp",
      "visible": true,
      "position": {"x": 100, "y": 250, "width": 600, "height": 400}
    }
  ],
  "images_checked": true,
  "charts": [],
  "charts_checked": true
}
```

---

## 2. 3-State Evidence Semantics
Analyzers must strictly differentiate between missing audit evidence and verified absence:

| State | Evidence Manifestation | Expected Behavior | Finding Generated? |
| :--- | :--- | :--- | :---: |
| **`UNKNOWN`** | Field is `None`, missing key, or `checked=False` | Emit `status="insufficient_evidence"`, score `None`, zero deductions. | **NO** |
| **`ABSENT`** | Field is `[]` or `count=0` with `checked=True` | Category evaluated clean (e.g. 0 charts present on page). | **NO** |
| **`AVAILABLE`**| Field populated with list of assets | Execute full deterministic validation rules. | If defects found |

Never penalize a website for unmeasured or uncollected dimensions.
