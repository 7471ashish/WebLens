# Schema & JSON-LD Rules Reference

Reference specifications for JSON-LD and Schema.org structured data extraction in `freshness-corroboration-audit`.

---

## 1. JSON-LD Extraction Specifications
Extract all JSON-LD blocks from retrieved HTML:
`<script type="application/ld+json">`

The analyzer supports:
- Single Schema.org objects
- Top-level arrays of schema objects
- Multiple independent JSON-LD blocks
- Nested schema objects and properties
- `@graph` flat arrays and nested `@graph` containers
- `@id` node references and internal IRI linking
- Explicit cross-entity relationships (e.g. `publisher`, `author`, `parentOrganization`)

### JSON-LD Example
```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://example.com/#organization",
      "name": "Example Corporation"
    },
    {
      "@type": "Article",
      "@id": "https://example.com/article#article",
      "headline": "Example Article",
      "author": {
        "@id": "https://example.com/#author"
      }
    }
  ]
}
```

### Observation Outputs
The extraction telemetry produces:
- `json_ld_blocks`: Total raw blocks discovered
- `valid_json_ld_blocks`: Count of syntactically valid JSON-LD blocks
- `invalid_json_ld_blocks`: Count of syntax or decoding failures
- `parsing_errors`: List of explicit syntax error messages
- `graph_count`: Total number of `@graph` constructs
- `entity_count`: Count of discrete Schema.org entities
- `types`: Distinct entity types identified
- `properties`: Available top-level and nested property keys

---

## 2. `@graph` Architecture & Container Traversal
`@graph` must be treated as a container of entities, never as an entity itself.

For each `@graph` container:
- Unpack each member node into the flat entity dictionary
- Preserve `@id` values for cross-referencing
- Resolve circular or reciprocal references safely
- Do not assume that higher entity counts imply higher semantic quality
