# Entity Identity & sameAs Verification Reference

Reference specifications for entity identity classification and `sameAs` linkage in `freshness-corroboration-audit`.

---

## 1. Entity Identity Analysis
Analyzes high-value entities defined in page structured data:
- `Organization`
- `Person`
- `Product`
- `Brand`
- `Article`
- `WebPage`
- `WebSite`
- `LocalBusiness`

### Evaluated Identity Fields
- `@id`: Machine-stable IRI identifier
- `@type`: Declared Schema.org type
- `name`: Human-readable entity title
- `url`: Canonical resource locator
- `identifier`: Disambiguation keys (e.g. DUNS, LEI, ISBN)
- `sameAs`: Authority profile linkages
- `publisher` / `author` / `creator`: Attribution relationships

### Identity Strength Classification
- **strong**: Stable canonical `@id`, valid outbound `sameAs` links to reputable platforms, and corroborated name/url.
- **moderate**: Valid name and URL with partial metadata, but lacking external authority disambiguation.
- **weak**: Generic name without `@id`, missing outbound authority relationships, or isolated entity fragments.

Never conclude that an entity is authentic merely because an `@id` or `name` exists.

---

## 2. `sameAs` Validation & Outbound Corroboration
Examines declared outbound authority references:
- **URL Validity**: Checks for absolute URLs with HTTP/HTTPS schemes.
- **Deduplication**: Filters duplicate target profiles.
- **Syntactic Integrity**: Flags malformed URLs, empty arrays (`"sameAs": []`), or circular self-references.
- **Outbound HTTP Verification**: Confirms target endpoint reachability where network validation is enabled.

### Handling Broken References
- A `sameAs` link returning HTTP 404/5xx must be reported as a `"broken external identity reference"`.
- Do not automatically conclude that the entity is fraudulent solely due to an external network failure.
