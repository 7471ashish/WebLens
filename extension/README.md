# WebLens Chrome Extension (Manifest V3)

Autonomous, single-click technical website audits right from your browser Side Panel.

---

## Features
- **Chrome Side Panel API**: Runs alongside your active browsing session so you can inspect pages without popup dismissals.
- **Active Tab Auto-Sync**: Prefills the target URL from the current active tab.
- **Real-Time Progress**: Live animated progress bar, stage indicators (Crawling, Evaluating Skills, Gating Evidence), and runtime counters.
- **Evidence-Tier Separation**:
  - **Measured Evidence (`Tier 1` & `Tier 2`)**: Direct DOM measurements, computed contrast ratios, viewport scroll metrics, and exact HTTP codes.
  - **Heuristic Assessments (`Tier 4`)**: Qualitative UX structure, content density, and readability heuristics.
- **Collapsible Finding Cards**: Expandable details with exact DOM selectors, code evidence, and suggested remedial actions with priority tags.
- **Severity Summary Strip**: Color-coded scorecards for Critical, High, Medium, and Low findings with instant interactive filtering.
- **Resilient Polling**: Driven by `chrome.alarms` and backed by `chrome.storage.local` to survive service worker sleep cycles.
- **Native Desktop Notifications**: Alerts you when audits finish even if the browser panel is closed.

---

## Installation & Setup

1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked** and select the `WebLens/extension` folder.
4. The WebLens icon will appear in your Chrome toolbar.
5. Click the WebLens icon (or right-click -> Options) to configure your **Groq Cloud API Key**.
6. Navigate to any website, click the WebLens extension to open the Side Panel, and click **Audit This Page**!
