# External Corroboration & Conflict Detection Reference

Reference specifications for external claim verification, corroboration states, and factual contradiction detection in `freshness-corroboration-audit`.

---

## 1. External Corroboration Principles
This skill independently validates factual assertions without relying on peer subagents:
- Prioritizes official registries, government domains, Wikidata/Wikipedia, verified social handles, and primary documentation.
- Constrains external crawl volume: maximum 5 external sources, 10 selected claims, and strict 10s timeouts.

---

## 2. Corroboration States
Every evaluated claim maps to one of five mutually exclusive states:
1. `corroborated`: Direct factual agreement verified in an authoritative external source.
2. `partially_corroborated`: High-level claim confirmed, but specific sub-metrics or dates diverge slightly.
3. `not_verified`: No authoritative external source found within the query budget. Note: `not_verified != false`.
4. `conflicting`: Authoritative external source directly contradicts the website assertion.
5. `source_unavailable`: External target failed to respond (DNS, timeout, HTTP 5xx).

---

## 3. Conflict Detection
Compares factual signals between JSON-LD, metadata, DOM body text, and external references:
- Entity identity conflicts (divergent company or brand names)
- Numerical / pricing conflicts (e.g. on-page price vs official store)
- Temporal conflicts (divergent publication years or dates)
- Official URL conflicts (divergent canonical roots)
