# Severity Rubric & False-Positive Controls Reference

Reference specifications for finding severity grading, confidence scoring, evidence standards, and false-positive gates in `freshness-corroboration-audit`.

---

## 1. Finding Categories
Findings are strictly categorized under:
- `structured_data`: JSON-LD syntax, schema completeness, `@graph` issues
- `entity_identity`: Identity stability, `@id` integrity, `sameAs` linkage
- `freshness`: Temporal staleness relative to content domain
- `corroboration`: Uncorroborated testimonial or credential claims
- `conflict`: Direct factual contradictions
- `metadata`: HTTP transport, headers, or document-level metadata

---

## 2. Severity Level Rubric

| Severity | Definition & Requirements | Examples |
| :--- | :--- | :--- |
| **critical** | Exceptionally severe factual breakdown destroying AI comprehension. Requires multi-source proof. | Entity identity hijacking, verified fraudulent business identity. |
| **high** | Major verifiable contradiction or missing critical entity structure. | Severe conflict with authoritative registry, broken primary canonical identity. |
| **medium** | Notable freshness decay or uncorroborated public claims. | Stale product documentation (>180d), testimonials lacking attribution, missing `sameAs`. |
| **low** | Minor schema omissions or missing optional attributes. | Missing optional JSON-LD fields, unparseable secondary date string. |
| **info** | Informational observations or structural notices. | Clean schema validation notice, verified outbound identity confirmation. |

---

## 3. Confidence Scale
- **high (0.85 – 1.0)**: Direct, deterministic proof (e.g. HTTP 404, schema syntax exception).
- **medium (0.65 – 0.84)**: Strong signal with minor heuristics (e.g. content staleness estimate).
- **low (0.0 – 0.64)**: Ambiguous evidence; should generally be suppressed or downgraded to informational suggestions.

---

## 4. False-Positive Controls
1. Missing JSON-LD is **not** automatically critical.
2. Missing `@graph` container is **not** a defect.
3. Missing `sameAs` links is a medium defect at most, never high/critical.
4. Missing `dateModified` does not imply stale content.
5. Lack of external corroboration does not imply the website statement is false (`not_verified != false`).
6. A single disagreement does not prove the website is incorrect without corroborating evidence.
7. External network timeouts must never be converted into factual conflict findings.
