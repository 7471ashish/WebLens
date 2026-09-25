# Engagement Analyzer Specifications Reference

Reference specifications for the seven specialized engagement audit analyzers in `engagement-audit`.

---

## 1. Above-the-Fold Analyzer (`above_fold_analyzer.py`)
- **Scope**: Evaluates content hierarchy and interaction affordances in the initial viewport (default 1366×768px).
- **Core Checks**:
  - `ENG-AF-001`: Main H1 / primary value proposition missing above fold line.
  - `ENG-AF-002`, `ENG-AF-003`: Primary CTA button absent or positioned below fold line.
  - `ENG-AF-004`: Primary header navigation clipped or obscured.
  - `ENG-AF-005`, `ENG-AF-006`: Hero element clipping and intrusive modal obstruction.
- **Input Dependencies**: `viewport`, `headings`, `ctas`, `navigation`, `popups`.

---

## 2. CTA Analyzer (`cta_analyzer.py`)
- **Scope**: Evaluates clarity, prominence, sizing, and usability signals of interactive conversion actions.
- **Core Checks**:
  - `ENG-CTA-001`: Primary CTA hidden or unrendered.
  - `ENG-CTA-002`, `ENG-CTA-003`: Below-fold CTA positioning without visual cue.
  - `ENG-CTA-004`: Primary vs secondary visual hierarchy confusion.
  - `ENG-CTA-005`: Ambiguous action copy (e.g. "Click Here") vs action-oriented verbs.
  - `ENG-CTA-006`: Empty or unlabelled CTA elements.
  - `ENG-CTA-007`: Dead-end dummy destinations (`href="#"`).
  - `ENG-CTA-008`: Overlay obstruction blocking user click path.
  - `ENG-CTA-009`: Decision paralysis from >3 competing primary actions.
  - `ENG-CTA-010`: Undersized tap target dimensions (< 44×36px).
  - `ENG-CTA-011`: Disabled primary conversion action on initial landing.
- **Input Dependencies**: `ctas`, `calls_to_action`, `buttons`.

---

## 3. Navigation Analyzer (`navigation_analyzer.py`)
- **Scope**: Evaluates header menus, information architecture, depth, and wayfinding structures.
- **Core Checks**:
  - `ENG-NAV-001`: Hidden primary navigation links.
  - `ENG-NAV-002`: Navigation bar obstructed by overlays.
  - `ENG-NAV-003`: Empty or unlabelled navigation links.
  - `ENG-NAV-004`: Ambiguous icon-only navigation without accessible labels.
  - `ENG-NAV-005`: Duplicate navigation labels within the same menu level.
  - `ENG-NAV-006`: Orphaned submenu items referencing missing `parent_id`.
  - `ENG-NAV-008`: Excessive menu hierarchy depth (> 4 levels).
  - `ENG-NAV-009`: Cluttered top-level menu (> 10 items violating Miller's law 7±2).
  - `ENG-NAV-010`: Redundant destination links routing to identical URLs.
  - `ENG-NAV-011`: Conflicting active-state indicators.
  - `ENG-NAV-012`: Malformed or duplicate breadcrumb steps.
  - `ENG-NAV-013`: Inaccessible or missing mobile navigation toggles.
- **Input Dependencies**: `navigation`, `nav`, `primary_navigation`, `menus`, `breadcrumbs`.

---

## 4. Popup & Modal Analyzer (`popup_analyzer.py`)
- **Scope**: Evaluates intrusive overlays, marketing dialogs, interstitials, and sticky backdrops.
- **Core Checks**:
  - `ENG-POP-001`: Excessive screen coverage (> 70% viewport area).
  - `ENG-POP-002`: Main body text or primary heading obstruction.
  - `ENG-POP-003`: Primary CTA obstruction.
  - `ENG-POP-004`: Header navigation obstruction.
  - `ENG-POP-005`: Missing close/dismissal buttons on promotional modals.
  - `ENG-POP-006`: Hidden or offscreen close controls.
  - `ENG-POP-007`: Immediate blocking overlays on page load (< 1000ms delay).
  - `ENG-POP-008`: Repetitive popup interruptions across user sessions.
  - `ENG-POP-009`: Multiple simultaneous active overlays.
  - `ENG-POP-010`: Mobile full-screen interstitials (> 50% mobile area).
- **Context Handling**: Necessary authentication, cookie/consent, and age verification dialogs are recognized as functional and not penalized as promotional popups.
- **Input Dependencies**: `popups`, `popup`, `modals`, `overlays`, `interstitials`.

---

## 5. Content Readability Analyzer (`readability_analyzer.py`)
- **Scope**: Evaluates reading ease, sentence/paragraph structure, and heading organization.
- **Mathematical Formulations**:
  - **Flesch Reading Ease (FRE)**:
    $$\text{FRE} = 206.835 - 1.015 \times \left(\frac{\text{words}}{\text{sentences}}\right) - 84.6 \times \left(\frac{\text{syllables}}{\text{words}}\right)$$
    Flagged as academic/legal barrier if $\text{FRE} < 50$ (`ENG-READ-001`).
  - **Flesch-Kincaid Grade Level (FKGL)**:
    $$\text{FKGL} = 0.39 \times \left(\frac{\text{words}}{\text{sentences}}\right) + 11.8 \times \left(\frac{\text{syllables}}{\text{words}}\right) - 15.59$$
- **Structural Checks**:
  - `ENG-READ-003`: Excessive sentence length (> 25 words).
  - `ENG-READ-004`: Overuse of complex terminology (> 12 characters).
  - `ENG-READ-005`: Dense paragraph blocks (> 120 words).
  - `ENG-READ-006`: Empty heading tags.
  - `ENG-READ-007`: Inconsistent heading progression jumps (e.g. H1 $\to$ H4).
  - `ENG-READ-008`: Large content blocks (> 500 words) lacking subheadings.
  - `ENG-READ-009`: Repeated duplicate paragraph content.
- **Statistical Safety**: Requires $\ge 30$ words; shorter text returns `insufficient_evidence`.
- **Input Dependencies**: `content.main_text`, `paragraphs`, `headings`.

---

## 6. Responsive Layout Analyzer (`responsive_analyzer.py`)
- **Scope**: Evaluates layout adaptation across desktop (1366×768), tablet (768×1024), and mobile (390×844) viewports.
- **Core Checks**:
  - `ENG-RESP-001`: Horizontal page overflow on small viewports.
  - `ENG-RESP-002`: Elements extending beyond viewport boundaries ($x + \text{width} > \text{viewport\_width}$).
  - `ENG-RESP-003`: Visually clipped content elements.
  - `ENG-RESP-004`: Non-decorative element overlap ($> 500\text{px}^2$).
  - `ENG-RESP-005`: Uncollapsed desktop navigation on mobile viewports.
  - `ENG-RESP-006`: CTA buttons overflowing mobile boundaries.
  - `ENG-RESP-007`: Fixed-width elements failing to scale down on mobile.
  - `ENG-RESP-008`: Undersized mobile touch targets (< 44×44px).
- **Preserved Components**: Intentional scroll components (carousels, data tables, code blocks) marked `scrollable=True` are exempted.
- **Input Dependencies**: `viewports`, `viewport_profiles`, `mobile_data`.

---

## 7. User Journey Analyzer (`journey_analyzer.py`)
- **Scope**: Evaluates multi-step conversion funnels, onboarding flows, and signup paths.
- **Core Checks**:
  - `ENG-JOURNEY-001`: Missing journey completion goal.
  - `ENG-JOURNEY-002`: Excessive intermediate friction steps.
  - `ENG-JOURNEY-003`: Unclear next actions on intermediate steps.
  - `ENG-JOURNEY-004`: Dead-end steps with no forward progression.
  - `ENG-JOURNEY-005`: Broken transitions to missing step IDs.
  - `ENG-JOURNEY-006`: Forced journey restarts causing progress loss.
  - `ENG-JOURNEY-007`: Duplicate form field requests across steps.
  - `ENG-JOURNEY-008`: Unrecoverable errors lacking retry affordance.
  - `ENG-JOURNEY-009`: Multi-step flows terminating without confirmation.
  - `ENG-JOURNEY-010`: Disabled required conversion action.
  - `ENG-JOURNEY-011`: Unexpected off-path redirects.
  - `ENG-JOURNEY-012`: Duplicate step IDs in the journey tree.
- **Input Dependencies**: `journey`, `journeys`, `flow`, `funnel`.
