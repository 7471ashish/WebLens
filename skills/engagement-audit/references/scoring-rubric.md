# Engagement Scoring & Finding Rubric Reference

Reference specifications for finding ID conventions, severity assignments, confidence scaling, and aggregate scoring in `engagement-audit`.

---

## 1. Finding ID Taxonomy
Finding IDs follow the canonical prefix format `ENG-{AREA}-{NNN}`:
- `ENG-AF-{NNN}`: Above-the-fold observations
- `ENG-CTA-{NNN}`: Call-to-action issues
- `ENG-NAV-{NNN}`: Navigation and wayfinding defects
- `ENG-POP-{NNN}`: Popup and interstitial intrusions
- `ENG-READ-{NNN}`: Readability and typographical clarity
- `ENG-RESP-{NNN}`: Responsive layout and viewport scaling
- `ENG-JOURNEY-{NNN}`: User journey and conversion flow

---

## 2. Severity Classification Guidelines

| Severity | Definition | Concrete Criteria |
| :--- | :--- | :--- |
| **critical** | Complete blocker | Primary CTA hidden/disabled on landing, dead-end conversion step, complete mobile page unresponsiveness. |
| **high** | Major friction | Primary CTA below fold, blocking modal covering >70% screen, broken navigation link, severe horizontal overflow. |
| **medium** | Notable usability gap | FRE < 50, missing heading structure, competing secondary buttons, menu depth > 4 levels. |
| **low** | Minor optimization | Slightly long paragraphs (>120 words), non-critical touch target slightly below 44px, minor whitespace imbalance. |
| **info** | Non-defect notice | Informational metrics, reading grade level notes, or confirmed baseline engagement observations. |

---

## 3. Confidence Scale
- **high (0.85 – 1.0)**: Direct geometric or DOM proof (e.g. bounding box coordinates, missing H1 element, `href="#"`).
- **medium (0.65 – 0.84)**: Heuristic calculation with calibrated thresholds (e.g. Flesch Reading Ease score on prose).
- **low (0.0 – 0.64)**: Ambiguous context or qualitative UX critique; auto-routed to `suggestions`.

---

## 4. Overall Engagement Score Formula
The overall composite score ($0 - 100$) is computed as a weighted sum across the seven analyzer categories:
$$\text{Score} = \sum_{i=1}^7 w_i \times S_i$$
Default weights:
- Above-the-fold ($w = 0.20$)
- Call-to-Action ($w = 0.20$)
- Navigation ($w = 0.15$)
- Responsive Layout ($w = 0.15$)
- User Journey ($w = 0.15$)
- Popups & Interstitials ($w = 0.10$)
- Readability ($w = 0.05$)
