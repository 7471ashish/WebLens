# Temporal Signals & Freshness Reference

Reference specifications for date extraction, normalization, consistency checking, and content freshness decay in `freshness-corroboration-audit`.

---

## 1. Temporal Signals Extraction
Extracts machine and human-readable timestamps across multiple DOM layers:
- **JSON-LD**: `datePublished`, `dateModified`, `dateCreated`
- **OpenGraph & Meta Tags**: `article:published_time`, `article:modified_time`
- **HTML5 Elements**: `<time datetime="...">`
- **HTTP Transport Headers**: `Last-Modified`

Every temporal observation retains its provenance (`source: jsonld | opengraph | header`).

---

## 2. Date Normalization & Parsing
All dates are normalized into ISO-8601 UTC representation (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`).
- Non-parseable strings are explicitly flagged (`parseable: false`).
- Ambiguous dates (e.g. `03/04/2026`) must not be silently guessed without locale context.

---

## 3. Date Consistency & Invariant Validation
Verifies temporal ordering invariants:
$$\text{dateModified} \ge \text{datePublished}$$

If $\text{dateModified} < \text{datePublished}$, records a temporal contradiction finding (`conflict_type: temporal`).

---

## 4. Freshness Decay & Category Thresholds
Content age is calculated against the reference audit timestamp. Content freshness is evaluated relative to its content type:

| Content Type | Stale Threshold | Rationale |
| :--- | :---: | :--- |
| **news / current-events** | 30 days | Fast-moving real-time reporting |
| **product / pricing** | 180 days | Catalog and inventory lifecycle |
| **technical documentation** | 180 days | Software and API version velocity |
| **general content** | 365 days | Standard corporate and brand pages |
| **evergreen content** | 730 days | Historical or foundational articles |

### Missing Date Policy
- The absence of `dateModified` is **not** an automatic defect.
- If no reliable temporal signal exists, `freshness_status` is recorded as `not_determinable` without flagging false-positive staleness.
