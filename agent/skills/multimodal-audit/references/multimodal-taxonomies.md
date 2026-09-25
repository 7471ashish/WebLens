# Multimodal Taxonomies & Scoring Reference

Reference specifications for finding conventions, severity grading, confidence scoring, and category weights in `multimodal-audit`.

---

## 1. Finding Taxonomies & Prefixes

### Image Asset Quality (`MM-IMG-xxx`)
- `MM-IMG-001`: Broken image link / non-200 HTTP response.
- `MM-IMG-002`: Missing intrinsic width or height attributes (cumulative layout shift risk).
- `MM-IMG-003`: Excessive file payload size (> 1MB without progressive loading).
- `MM-IMG-004`: Legacy raster format (e.g. BMP/TIFF instead of modern WebP/AVIF).
- `MM-IMG-005`: Severe aspect ratio distortion (> 10% skew between intrinsic and CSS box).

### Alt-Text Accessibility Quality (`MM-ALT-xxx`)
- `MM-ALT-001`: Missing `alt` attribute on non-decorative image.
- `MM-ALT-002`: Generic placeholder text (e.g., "image", "photo", "untitled").
- `MM-ALT-003`: Raw filename used as alt text (e.g. "IMG_4920.jpg").
- `MM-ALT-004`: Redundant prefix phrasing ("picture of", "graphic showing").
- `MM-ALT-005`: Excessive keyword stuffing (> 150 characters without sentence structure).

### Image OCR & Embedded Text (`MM-OCR-xxx`)
- `MM-OCR-001`: Critical textual content embedded directly into image pixels without HTML/ARIA text equivalent.
- `MM-OCR-002`: Visual text contrast ratio in graphic element falls below WCAG 4.5:1.

### Image Technical Metadata (`MM-META-xxx`)
- `MM-META-001`: Above-the-fold hero image missing `loading="eager"` or `fetchpriority="high"`.
- `MM-META-002`: Below-the-fold secondary image missing `loading="lazy"`.
- `MM-META-003`: Missing responsive `srcset` / `sizes` on high-resolution display assets.

### Charts & Infographics (`MM-CHART-xxx`)
- `MM-CHART-001`: Complex data graphic lacking accessible data table fallback or long description.
- `MM-CHART-002`: Chart missing labeled coordinate axes or legend keys.

---

## 2. Severity & Confidence Models

| Severity | Criteria |
| :--- | :--- |
| **critical** | Broken primary hero graphic, complete accessibility failure on critical transaction visual. |
| **high** | Missing alt attribute on key informative graphic, text trapped in pixels without HTML equivalent. |
| **medium** | Legacy image format, excessive image weight (>1MB), below-fold lazy-loading omission. |
| **low** | Minor aspect ratio skew, slightly verbose alt text (>125 chars). |

**Confidence Scale**:
- `1.0`: Direct HTTP status (e.g. 404), explicit attribute absence (`alt is None`).
- `0.85 – 0.95`: Measured geometric skew, deterministic regex match on placeholder alt text.
- `0.50 – 0.70`: Semantic OCR ambiguity; routed to `suggestions`.

---

## 3. Category Weights in `MultimodalAudit`
The aggregate composite multimodal score ($0 - 100$) uses normalized weights:
- Image Quality (`image`): 25%
- Alt-Text Accessibility (`alt_text`): 30%
- Optical Character Recognition (`ocr`): 15%
- Technical Metadata (`image_metadata`): 15%
- Charts & Infographics (`chart_infographic`): 15%
