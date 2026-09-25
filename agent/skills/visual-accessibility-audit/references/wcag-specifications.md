# Visual & WCAG 2.2 Accessibility Specifications Reference

Reference specifications for visual layout integrity and WCAG 2.2 accessibility conformance checks in `visual-accessibility-audit`.

---

## 1. Multi-Viewport Testing Matrix
Evaluates pages across three deterministic rendering viewports via Playwright Chromium:
- **Desktop**: 1440 × 900 px (`viewport_label: desktop`)
- **Tablet**: 1024 × 768 px (`viewport_label: tablet`)
- **Mobile**: 390 × 844 px (`viewport_label: mobile`)

---

## 2. Measurable Visual Defect Categories
- **Text Truncation & Line Clamping**: Text elements truncated with ellipses (`text-overflow: ellipsis`) or clipped by overflow containers without accessible tooltips or expanders.
- **Horizontal Overflow**: Root or container elements forcing horizontal scrolling on mobile/tablet viewports ($x + \text{width} > \text{viewport\_width}$).
- **Content Overlap**: Text or interactive elements overlapping other non-decorative elements by $\ge 200\text{px}^2$.
- **Responsive Regressions**: UI components present and functional on desktop but clipped or un-navigable on mobile.

---

## 3. WCAG 2.2 Accessibility Conformance Checks

### 3.1 Color Contrast (WCAG 1.4.3 & 1.4.11)
Calculates the relative luminance $L$ according to the standard formula:
$$L = 0.2126 \times R + 0.7152 \times G + 0.0722 \times B$$
Where color channels $C \in \{R, G, B\}$ are linear:
$$C = \begin{cases} \frac{C_{\text{sRGB}}}{12.92} & C_{\text{sRGB}} \le 0.04045 \\ \left(\frac{C_{\text{sRGB}} + 0.055}{1.055}\right)^{2.4} & C_{\text{sRGB}} > 0.04045 \end{cases}$$
Contrast ratio:
$$\text{Ratio} = \frac{L_1 + 0.05}{L_2 + 0.05} \quad (L_1 > L_2)$$
- **Body Text**: Must satisfy $\ge 4.5:1$ (AA)
- **Large Text** ($\ge 18\text{pt}$ or $\ge 14\text{pt}$ bold): Must satisfy $\ge 3.0:1$
- **UI Components & Form Borders**: Must satisfy $\ge 3.0:1$ (WCAG 1.4.11)

### 3.2 Document & Semantic Structure
- HTML `lang` attribute present and valid (e.g. `<html lang="en">`).
- Exactly one primary `<h1>` element per document.
- Proper heading hierarchy without skipping levels (e.g. H2 $\to$ H4).
- Landmark regions present (`header`, `nav`, `main`, `footer`).

### 3.3 Interactive Controls & Forms
- All form `<input>` elements must have programmatic label association (`<label for="...">` or `aria-label`).
- Touch target sizes must meet $\ge 24\times24\text{px}$ minimum and $\ge 44\times44\text{px}$ recommended.

### 3.4 Keyboard Navigation & Focus (WCAG 2.1.1, 2.4.7, 2.1.2)
- All interactive elements must be reachable via `Tab` sequence.
- Visible focus indicator present on active elements (focus ring not removed via `outline: none`).
- No keyboard traps: focus must be able to leave any sub-component.

### 3.5 ARIA Validation
- ARIA roles, states, and properties must be valid WAI-ARIA tokens.
- Custom interactive widgets must expose appropriate ARIA roles and expanded/selected states.
