# WebLens — Mozilla Firefox Extension Guide & AMO Submission Manual

This guide covers everything needed to test, package, and publish the **WebLens** extension to the **Mozilla Firefox Add-ons Store (AMO — addons.mozilla.org)**.

---

## 1. Quick Start & Build Packages

WebLens maintains a single, unified source codebase inside `extension/` and uses an automated build script to package target-specific builds for both Google Chrome and Mozilla Firefox.

### Building the Packages
Run either the Node.js or Python packager from the project root:

```bash
# Using Node.js:
node scripts/build_extension.js

# OR using Python:
python scripts/package_extensions.py
```

### Build Outputs
- **Unpacked Folders (for local development & debugging):**
  - Chrome: `dist/chrome/`
  - Firefox: `dist/firefox/`
- **Store-Ready Zip Archives:**
  - Chrome Web Store: `dist/weblens-chrome-v1.0.1.zip`
  - Mozilla Add-ons (AMO): `dist/weblens-firefox-v1.0.1.zip`

---

## 2. Local Testing in Firefox (Developer Mode)

Before publishing, verify the extension inside Firefox:

1. Open **Mozilla Firefox**.
2. In the URL address bar, enter:
   ```
   about:debugging#/runtime/this-firefox
   ```
3. Under **Temporary Extensions**, click **"Load Temporary Add-on..."**.
4. Browse to your WebLens directory and select:
   ```
   WebLens/dist/firefox/manifest.json
   ```
5. **Verify the Extension:**
   - The WebLens icon appears in the Firefox toolbar.
   - Click the icon or open the Firefox Sidebar (**Ctrl+B** / **Cmd+B** -> select **WebLens Auditor**).
   - Enter a target URL (e.g. `https://example.com`) or test with the local backend (`http://localhost:8000`).
   - Confirm that the pre-flight scan chips, progress bar, and finding cards render properly.

---

## 3. Step-by-Step AMO Store Submission Guide

Publishing on Mozilla Add-ons (AMO) is **100% free** (no developer registration fee).

### Step 1: Create a Mozilla Developer Account
1. Go to [addons.mozilla.org/developers](https://addons.mozilla.org/developers/).
2. Click **Sign in** (use your existing Firefox account or register a new one).
3. **Mandatory Security Requirement:** Enable **Two-Factor Authentication (2FA)** on your Mozilla account:
   - Go to your [Mozilla Account Settings](https://accounts.firefox.com/settings).
   - Under **Security**, enable **Two-step authentication**.
   - *Note: AMO will reject add-on submissions if 2FA is not enabled.*
4. Accept the **Mozilla Add-on Distribution Agreement**.

---

### Step 2: Submit a New Add-on
1. On the Developer Hub dashboard, click the blue **"Submit a New Add-on"** button.
2. **Distribution Option:**
   - Select **"On this site"** *(Recommended: Mozilla hosts the add-on, lists it in search, and handles automatic updates)*.
   - *(Optionally choose "On your own" if you prefer to self-host a signed `.xpi` file on your own website).*
3. **Upload Package:**
   - Click **Select a file...** and upload:
     ```
     WebLens/dist/weblens-firefox-v1.0.1.zip
     ```
   - Mozilla's automated validator will scan the package (~10–20 seconds).
   - Ensure the validator gives a green checkmark.

---

### Step 3: Source Code Disclosure (Important!)
Mozilla asks:
> *"Does your add-on use source code that requires a build tool or compilation (e.g., webpack, babel, minification)?"*

- **Select: "No"**
- **Why?** WebLens is written in clean, standard, human-readable vanilla JavaScript, HTML, and CSS without obfuscation, minification, or complex bundlers.
- Selecting **"No"** allows human reviewers to inspect the files directly, which dramatically accelerates review times (often approved in **under 24 hours**).

---

### Step 4: Store Listing Information

Fill out the metadata form:

| Field | Recommended Value |
| :--- | :--- |
| **Add-on Name** *(max 45 chars)* | `WebLens — AI Technical Health Auditor` |
| **Summary** *(max 250 chars)* | `Autonomous technical website audit: AI discoverability, WCAG accessibility, rendering, and engagement friction.` |
| **Categories** | `Web Development`, `Privacy & Security` |
| **Tags** | `audit`, `developer-tools`, `accessibility`, `seo`, `performance`, `wcag` |
| **Support Email** | Your support or developer contact email |
| **Homepage / Repo** | Your project website or GitHub repository |

#### Store Description (Markdown supported):
```markdown
WebLens is an autonomous technical website auditor that inspects websites across 5 critical dimensions:
1. AI Search Engine Discoverability & llms.txt Readiness
2. WCAG 2.1 AA Visual Accessibility & Contrast
3. Page-Level Engagement & Visual Friction
4. Crawl-Render Diagnostics & DOM Hydration
5. Freshness & Content Corroboration

Features:
- Sub-500ms Instant Pre-Flight Security & Header evaluation (HSTS, CSP, X-Frame-Options)
- Real-time Server-Sent Events (SSE) telemetry streaming
- Standardized findings with measured evidence tiers (Tier 1/2 vs Tier 4 heuristic)
- One-click ready-to-paste AI defect remediation prompts
- Native Firefox sidebar integration
```

#### Notes to Reviewer:
```text
WebLens connects to a hosted backend API (https://weblens-backend-i7n8.onrender.com) to execute website technical diagnostics and stream real-time progress via Server-Sent Events (SSE).

How to test:
1. Open the WebLens sidebar (or click the toolbar icon).
2. Click the gear icon to open Settings. The backend is preconfigured to:
   https://weblens-backend-i7n8.onrender.com
3. (Optional) Click "Test Connection" to confirm backend connectivity.
4. Navigate to any public webpage (e.g., https://example.com) and click "Audit This Page".
5. WebLens immediately runs an instant pre-flight security scan and displays live audit progress and findings in the sidebar.

All source code is vanilla JavaScript/HTML/CSS without minification or obfuscation.
```

---

### Step 5: Visual Assets
1. **Icon:** Upload `extension/icons/icon128.png` (or 64x64).
2. **Screenshots:** Upload 1–3 screenshots showing:
   - WebLens running inside the Firefox Sidebar.
   - Expanded finding card showing evidence telemetry and action items.
   - Pre-flight security score badge and header chips.

---

### Step 6: Review & Approval Timeline

- **Automated Validation:** Instant.
- **Human Code Review:** Typically takes **1 to 3 business days** (often same day for clean vanilla extensions).
- **Publication:** Once approved, your add-on will be publicly accessible at:
  `https://addons.mozilla.org/firefox/addon/weblens-auditor/`

---

## 4. Releasing Future Updates

When releasing a new version:

1. Increment the `"version"` field in:
   - `extension/manifest.firefox.json` (e.g. `1.0.1`)
   - `extension/manifest.chrome.json` (e.g. `1.0.1`)
2. Re-run the packager:
   ```bash
   node scripts/build_extension.js
   ```
3. In the [AMO Developer Hub](https://addons.mozilla.org/developers/):
   - Select **WebLens**.
   - Click **"Upload New Version"**.
   - Upload `dist/weblens-firefox-v1.0.1.zip`.
   - Updates are reviewed and pushed automatically to all installed Firefox browsers.
