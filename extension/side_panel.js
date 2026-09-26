/**
 * WebLens Side Panel Controller
 * Handles active tab synchronization, parallel audit scheduling (one browser-tab-like
 * strip per audit job), live progress updates, finding card rendering, evidence-tier
 * segregation, and filtering.
 */

const DEFAULT_BACKEND_URL = "http://localhost:8000";

// DOM Elements
const missingKeyBanner = document.getElementById("missing-key-banner");
const bannerConfigBtn = document.getElementById("banner-config-btn");
const openSettingsBtn = document.getElementById("open-settings-btn");
const settingsHint = document.getElementById("settings-hint");
const targetUrlInput = document.getElementById("target-url-input");
const syncTabBtn = document.getElementById("sync-tab-btn");
const startAuditBtn = document.getElementById("start-audit-btn");
const auditSpinner = document.getElementById("audit-spinner");
const btnText = document.getElementById("btn-text");

// Inline Settings Panel Elements (replaces the old separate options page)
const settingsPanel = document.getElementById("settings-panel");
const closeSettingsBtn = document.getElementById("close-settings-btn");
const groqKeyInput = document.getElementById("groq-key-input");
const togglePwdBtn = document.getElementById("toggle-pwd-btn");
const testConnectionBtn = document.getElementById("test-connection-btn");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const connectionStatus = document.getElementById("connection-status");

// Audit Tabs Bar
const tabsBar = document.getElementById("audit-tabs");

// Progress Elements
const progressSection = document.getElementById("progress-section");
const progressStageTag = document.getElementById("progress-stage-tag");
const progressPercent = document.getElementById("progress-percent");
const progressTimer = document.getElementById("progress-timer");
const progressFill = document.getElementById("progress-fill");
const progressMessage = document.getElementById("progress-message");
const terminateAuditBtn = document.getElementById("terminate-audit-btn");

// Skeleton
const resultsSkeleton = document.getElementById("results-skeleton");

// Error Elements
const errorSection = document.getElementById("error-section");
const errorBadge = document.getElementById("error-badge");
const errorMessage = document.getElementById("error-message");
const retryAuditBtn = document.getElementById("retry-audit-btn");

// Results Elements
const resultsSection = document.getElementById("results-section");
const resultSiteTitle = document.getElementById("result-site-title");
const resultTimestamp = document.getElementById("result-timestamp");
const resultRuntime = document.getElementById("result-runtime");
const countCritical = document.getElementById("count-critical");
const countHigh = document.getElementById("count-high");
const countMedium = document.getElementById("count-medium");
const countLow = document.getElementById("count-low");
const totalBadge = document.getElementById("total-badge");
const findingsContainer = document.getElementById("findings-container");

// Telemetry Elements
const telSkills = document.getElementById("tel-skills");
const telPages = document.getElementById("tel-pages");
const telTypes = document.getElementById("tel-types");

// Filters
const severityPills = document.querySelectorAll(".pill[data-severity]");
const tierFilter = document.getElementById("tier-filter");
const searchInput = document.getElementById("search-input");

// ---- State ----
// Each open/completed audit is a "job" rendered as its own tab, so several
// audits can run in parallel (one per website you visit and start).
let jobs = [];               // { id, jobId, url, label, status, progress, result, error, startedAt }
let activeTabId = null;      // which job's tab is currently displayed below the tab strip
const eventSources = {};     // tabId -> EventSource, one per in-flight job

let currentFindings = [];
let activeSeverityFilter = "all";
let activeTierFilter = "all";
let activeSearchQuery = "";

// Initialize on Load
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await checkConfiguredKey();
  await restoreSession();
  await syncActiveTabUrl();

  // Single shared ticker: only updates the timer text for whichever tab is
  // currently on screen, so background jobs don't need their own intervals.
  setInterval(() => {
    const job = getActiveJob();
    if (job && (job.status === "queued" || job.status === "running") && job.startedAt) {
      const elapsedSec = Math.floor((Date.now() - job.startedAt) / 1000);
      progressTimer.textContent = `${elapsedSec}s`;
    }
  }, 1000);
});

// Defensive: if a background message ever reports a job update, route it to
// the matching tab rather than assuming there's only one job.
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "JOB_UPDATED" && message.job) {
    const job = jobs.find((j) => j.jobId === (message.job.job_id || message.job.id));
    if (job) applyJobUpdate(job, message.job);
  }
});

function setupEventListeners() {
  // Gear icon toggles: first click opens, clicking it again (while open)
  // closes -- same animation either way.
  openSettingsBtn.addEventListener("click", toggleSettingsPanel);
  settingsHint.addEventListener("click", toggleSettingsPanel);
  bannerConfigBtn.addEventListener("click", () => {
    if (!isSettingsPanelOpen()) openSettingsPanel();
  });
  closeSettingsBtn.addEventListener("click", closeSettingsPanel);
  togglePwdBtn.addEventListener("click", togglePasswordVisibility);
  testConnectionBtn.addEventListener("click", testBackendConnection);
  saveSettingsBtn.addEventListener("click", saveSettings);
  syncTabBtn.addEventListener("click", syncActiveTabUrl);
  startAuditBtn.addEventListener("click", initiateAudit);
  retryAuditBtn.addEventListener("click", retryAudit);
  terminateAuditBtn.addEventListener("click", terminateAudit);

  // Keep the target URL synced to whatever the browser's address bar shows.
  // This never needs to "lock" any more -- starting a new audit opens its own
  // tab, so it can't collide with audits already running.
  chrome.tabs.onActivated.addListener(syncActiveTabUrl);
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.url && tab.active) {
      syncActiveTabUrl();
    }
  });

  // Severity Filter Pills
  severityPills.forEach((pill) => {
    pill.addEventListener("click", () => {
      severityPills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      activeSeverityFilter = pill.dataset.severity;
      applyFilters();
    });
  });

  // Tier Filter
  tierFilter.addEventListener("change", (e) => {
    activeTierFilter = e.target.value;
    applyFilters();
  });

  // Search Filter
  searchInput.addEventListener("input", (e) => {
    activeSearchQuery = e.target.value.toLowerCase().trim();
    applyFilters();
  });

  // Cleanly close every open EventSource on side panel unload
  window.addEventListener("beforeunload", () => {
    Object.values(eventSources).forEach((es) => es.close());
  });
}

function isSettingsPanelOpen() {
  return (
    !settingsPanel.classList.contains("hidden") &&
    !settingsPanel.classList.contains("panel-collapsed")
  );
}

/** Gear icon (and hint text) behavior: open if closed, close if open. */
async function toggleSettingsPanel() {
  if (isSettingsPanelOpen()) {
    closeSettingsPanel();
  } else {
    await openSettingsPanel();
  }
}

async function openSettingsPanel() {
  const { groq_api_key } = await chrome.storage.local.get(["groq_api_key"]);
  groqKeyInput.value = groq_api_key || "";
  connectionStatus.classList.add("hidden");

  popIcon(openSettingsBtn);

  // Reveal by growing outward from the gear icon's corner, mirroring the
  // reference "Register" button's expand-to-fill animation, instead of
  // just snapping the hidden class off.
  settingsPanel.classList.remove("hidden");
  settingsPanel.classList.add("panel-collapsed");
  // Force layout so the collapsed state is committed before we remove it --
  // otherwise both class changes get batched and no transition plays.
  void settingsPanel.offsetWidth;
  settingsPanel.classList.remove("panel-collapsed");
}

function closeSettingsPanel() {
  popIcon(closeSettingsBtn);

  // Mirror the open animation in reverse: shrink back down into the corner,
  // then pull it out of layout once the transition finishes (timing matches
  // the .settings-panel transform duration in the CSS).
  settingsPanel.classList.add("panel-collapsed");
  window.setTimeout(() => {
    if (settingsPanel.classList.contains("panel-collapsed")) {
      settingsPanel.classList.add("hidden");
    }
  }, 620);
}

/** Quick press-pop feedback on an icon button (gear / close-X). */
function popIcon(el) {
  el.classList.remove("icon-pop");
  void el.offsetWidth; // restart the animation if it's still running
  el.classList.add("icon-pop");
}

function togglePasswordVisibility() {
  const isPassword = groqKeyInput.type === "password";
  groqKeyInput.type = isPassword ? "text" : "password";
}

async function testBackendConnection() {
  const targetUrl = DEFAULT_BACKEND_URL;
  connectionStatus.className = "status-badge";
  connectionStatus.textContent = "Connecting to backend...";
  connectionStatus.classList.remove("hidden");

  try {
    const resp = await fetch(`${targetUrl}/health`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    connectionStatus.className = "status-badge success";
    connectionStatus.textContent = `Connected! Backend ready (Playwright: ${data.playwright_ready ? "Ready" : "Offline fallback"}, Slots: ${data.max_concurrent_jobs})`;
  } catch (err) {
    connectionStatus.className = "status-badge error";
    connectionStatus.textContent = `Connection failed: ${err.message}. Ensure backend is running.`;
  }
}

/**
 * Groq key is stored in chrome.storage.local and picked back up automatically
 * on every future audit request -- see submitAuditJob(), which reads it fresh
 * from storage each time rather than keeping it only in memory.
 */
async function saveSettings() {
  const key = groqKeyInput.value.trim();

  await chrome.storage.local.set({
    groq_api_key: key,
  });

  await checkConfiguredKey();
  closeSettingsPanel();
}

async function checkConfiguredKey() {
  const { groq_api_key } = await chrome.storage.local.get(["groq_api_key"]);
  if (!groq_api_key || !groq_api_key.trim()) {
    missingKeyBanner.classList.remove("hidden");
  } else {
    missingKeyBanner.classList.add("hidden");
  }
}

async function syncActiveTabUrl() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && (tab.url.startsWith("http://") || tab.url.startsWith("https://"))) {
      targetUrlInput.value = tab.url;
    }
  } catch (err) {
    console.warn("Unable to query active tab:", err);
  }
}

function getActiveJob() {
  return jobs.find((j) => j.id === activeTabId) || null;
}

function hostnameOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

async function persistState() {
  await chrome.storage.local.set({
    weblens_jobs: jobs,
    weblens_active_tab_id: activeTabId,
  });
}

/**
 * Restore whatever tabs existed from a previous session. Jobs that were still
 * queued/running get their SSE stream reconnected; finished/failed jobs stay
 * exactly as they were so the user can still review or close them.
 */
async function restoreSession() {
  const stored = await chrome.storage.local.get([
    "weblens_jobs",
    "weblens_active_tab_id",
  ]);

  jobs = Array.isArray(stored.weblens_jobs) ? stored.weblens_jobs : [];
  activeTabId = stored.weblens_active_tab_id || (jobs.length ? jobs[jobs.length - 1].id : null);

  const activeBackend = DEFAULT_BACKEND_URL;

  jobs.forEach((job) => {
    if ((job.status === "queued" || job.status === "running") && job.jobId) {
      connectSSE(job, activeBackend);
    }
  });

  renderTabs();
  renderActiveTabContent();
}

/**
 * Starts a brand-new audit in its own tab -- like opening a new browser tab --
 * so it runs alongside anything already in progress instead of replacing it.
 */
async function initiateAudit() {
  const url = targetUrlInput.value.trim();
  if (!url) {
    alert("Please provide a valid website URL to audit.");
    return;
  }

  const tabId = "tab_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
  const job = {
    id: tabId,
    jobId: null,
    url,
    label: hostnameOf(url),
    status: "queued",
    progress: null,
    maxPercent: 0,
    result: null,
    error: null,
    startedAt: Date.now(),
  };

  jobs.push(job);
  activeTabId = tabId;
  await persistState();
  renderTabs();
  renderActiveTabContent();

  await submitAuditJob(job);
}

/** Re-runs a finished/failed tab's audit in place, same tab, fresh job id. */
async function retryAudit() {
  const job = getActiveJob();
  if (!job) return;

  job.status = "queued";
  job.progress = null;
  job.maxPercent = 0;
  job.result = null;
  job.error = null;
  job.jobId = null;
  job.startedAt = Date.now();
  await persistState();
  renderTabs();
  renderActiveTabContent();

  await submitAuditJob(job);
}

/** POSTs a job to the backend and, on success, opens its SSE stream. */
async function submitAuditJob(job) {
  const { groq_api_key } = await chrome.storage.local.get(["groq_api_key"]);
  const activeBackend = DEFAULT_BACKEND_URL;
  const activeKey = groq_api_key ? groq_api_key.trim() : "placeholder_key";

  startAuditBtn.disabled = true;
  try {
    const response = await fetch(`${activeBackend}/audits`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Install-ID": await getOrCreateInstallId(),
      },
      body: JSON.stringify({
        url: job.url,
        groq_api_key: activeKey,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Server returned HTTP ${response.status}`);
    }

    const { job_id } = await response.json();
    job.jobId = job_id;
    await persistState();
    connectSSE(job, activeBackend);
  } catch (err) {
    job.status = "failed";
    job.error = err.message || "Failed to contact WebLens backend.";
    await persistState();
    renderTabs();
    if (activeTabId === job.id) renderActiveTabContent();
  } finally {
    startAuditBtn.disabled = false;
  }
}

/**
 * Ask the backend to cancel the currently viewed tab's in-flight job, tear
 * down its SSE stream, and mark it terminated. Assumes a POST
 * `${backendUrl}/audits/{job_id}/cancel` endpoint -- adjust the path here if
 * your backend exposes cancellation differently. Only available while running
 * -- once a job is done/failed/terminated it's closed via the tab's "x" instead.
 */
async function terminateAudit() {
  const job = getActiveJob();
  if (!job || (job.status !== "queued" && job.status !== "running")) return;

  const activeBackend = DEFAULT_BACKEND_URL;

  const es = eventSources[job.id];
  if (es) {
    es.close();
    delete eventSources[job.id];
  }

  if (job.jobId) {
    try {
      await fetch(`${activeBackend}/audits/${encodeURIComponent(job.jobId)}/cancel`, {
        method: "POST",
      });
    } catch (err) {
      console.warn("[WebLens] Failed to notify backend of cancellation:", err);
    }
  }

  job.status = "terminated";
  job.error = "Audit terminated by user.";
  await persistState();
  renderTabs();
  renderActiveTabContent();
}

/** Only allowed once a tab is done/failed/terminated -- never mid-run. */
async function closeTab(tabId) {
  const job = jobs.find((j) => j.id === tabId);
  if (!job || job.status === "queued" || job.status === "running") return;

  const es = eventSources[tabId];
  if (es) {
    es.close();
    delete eventSources[tabId];
  }

  jobs = jobs.filter((j) => j.id !== tabId);

  if (activeTabId === tabId) {
    activeTabId = jobs.length ? jobs[jobs.length - 1].id : null;
  }

  await persistState();
  renderTabs();
  renderActiveTabContent();
}

async function switchToTab(tabId) {
  if (activeTabId === tabId) return;
  activeTabId = tabId;
  await persistState();
  renderTabs();
  renderActiveTabContent();
}

function statusDotClass(job) {
  if (job.status === "queued" || job.status === "running") return "dot-running";
  if (job.status === "done") return "dot-done";
  return "dot-failed"; // failed or terminated
}

function renderTabs() {
  tabsBar.innerHTML = "";

  if (jobs.length === 0) {
    tabsBar.classList.add("hidden");
    return;
  }
  tabsBar.classList.remove("hidden");

  jobs.forEach((job) => {
    const tab = document.createElement("div");
    tab.className = "audit-tab" + (job.id === activeTabId ? " active" : "");
    tab.title = job.url;

    const dot = document.createElement("span");
    dot.className = `tab-status-dot ${statusDotClass(job)}`;
    tab.appendChild(dot);

    const label = document.createElement("span");
    label.className = "tab-label";
    label.textContent = job.label;
    tab.appendChild(label);

    // The close "x" only ever shows once a job is finished (done, failed, or
    // terminated) -- never while it's still queued/running.
    const finished = job.status === "done" || job.status === "failed" || job.status === "terminated";
    if (finished) {
      const closeBtn = document.createElement("button");
      closeBtn.className = "tab-close-btn";
      closeBtn.title = "Close";
      closeBtn.textContent = "\u00d7";
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        closeTab(job.id);
      });
      tab.appendChild(closeBtn);
    }

    tab.addEventListener("click", () => switchToTab(job.id));
    tabsBar.appendChild(tab);
  });
}

/** Redraws progress/skeleton/error/results to match whichever tab is active. */
function renderActiveTabContent() {
  const job = getActiveJob();

  progressSection.classList.add("hidden");
  resultsSkeleton.classList.add("hidden");
  errorSection.classList.add("hidden");
  resultsSection.classList.add("hidden");

  if (!job) return;

  if (job.status === "queued" || job.status === "running") {
    progressSection.classList.remove("hidden");
    resultsSkeleton.classList.remove("hidden");
    updateProgressDisplay(job.progress || { stage: "Queued", percent: 5, message: "Waiting for available audit slot..." });
  } else if (job.status === "done" && job.result) {
    renderAuditResult(job.result);
  } else if (job.status === "failed" || job.status === "terminated") {
    errorBadge.textContent = job.status === "terminated" ? "Audit Terminated" : "Audit Failed";
    errorMessage.textContent = job.error || "Audit failed during execution.";
    errorSection.classList.remove("hidden");
  }
}

function updateProgressDisplay(progress) {
  progressStageTag.textContent = (progress.stage || "Running").toUpperCase();
  const pct = Math.max(5, Math.min(100, progress.percent || 10));
  progressPercent.textContent = `${pct}%`;
  progressFill.style.width = `${pct}%`;
  progressMessage.textContent = progress.message || "Processing audit modules...";
}

/** Applies a status/progress/result update to one job, then repaints it if visible. */
async function applyJobUpdate(job, data) {
  job.status = data.status;
  if (data.progress) {
    // Clamp so a later stage reporting a lower percent (or a stray/late SSE
    // event) can never make the loading bar visually move backward.
    const incoming = Math.max(5, Math.min(100, data.progress.percent ?? 10));
    job.maxPercent = Math.max(job.maxPercent || 0, incoming);
    job.progress = { ...data.progress, percent: job.maxPercent };
  }
  if (data.status === "done") {
    job.result = data.result;
    job.maxPercent = 100;
    if (job.progress) job.progress.percent = 100;
  }
  if (data.status === "failed") job.error = data.error || "Audit failed during execution.";

  await persistState();
  renderTabs();
  if (activeTabId === job.id) {
    renderActiveTabContent();
  }

  if (data.status === "done") {
    try {
      chrome.runtime.sendMessage({
        type: "NOTIFY_COMPLETION",
        title: "WebLens Audit Complete",
        message: `Completed audit for ${data.result?.site || job.label}. Total findings: ${data.result?.summary?.total_findings || 0}.`,
      });
    } catch {
      // Extension context may be closing
    }
  }
}

/**
 * Connect to backend via Server-Sent Events (SSE) for one job.
 * Only fires when major lifecycle events occur (stage changes, completion, or failure).
 */
function connectSSE(job, backendUrl) {
  const existing = eventSources[job.id];
  if (existing) {
    existing.close();
    delete eventSources[job.id];
  }

  const streamUrl = `${backendUrl}/audits/${encodeURIComponent(job.jobId)}/stream`;
  console.log(`[WebLens] Connecting to SSE stream: ${streamUrl}`);
  const es = new EventSource(streamUrl);
  eventSources[job.id] = es;

  es.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);
      console.log("[WebLens] Major event received via SSE:", data.status, data.progress?.stage, job.label);

      await applyJobUpdate(job, data);

      if (data.status === "done" || data.status === "failed") {
        es.close();
        if (eventSources[job.id] === es) delete eventSources[job.id];
      }
    } catch (parseErr) {
      console.error("[WebLens] Failed to parse SSE event data:", parseErr, event.data);
    }
  };

  es.onerror = async () => {
    console.warn(`[WebLens] SSE stream disconnected or closed for ${job.label}`);
    es.close();
    if (eventSources[job.id] === es) delete eventSources[job.id];

    // Safety fallback: query backend once to see if job completed while stream closed
    try {
      const resp = await fetch(`${backendUrl}/audits/${encodeURIComponent(job.jobId)}`);
      if (resp.ok) {
        const currentJob = await resp.json();
        await applyJobUpdate(job, currentJob);
      }
    } catch (fetchErr) {
      console.debug("[WebLens] Status check on SSE disconnect:", fetchErr);
    }
  };
}

function renderAuditResult(result) {
  resultsSection.classList.remove("hidden");

  // Summary Metrics
  const summary = result.summary || {};
  countCritical.textContent = summary.critical || 0;
  countHigh.textContent = summary.high || 0;
  countMedium.textContent = summary.medium || 0;
  countLow.textContent = summary.low || 0;
  totalBadge.textContent = summary.total_findings || 0;

  // Header Details
  resultSiteTitle.textContent = result.site || "Audited Site";
  if (result.audited_at) {
    try {
      const d = new Date(result.audited_at);
      resultTimestamp.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      resultTimestamp.textContent = "Recently";
    }
  }
  const runtime = result.meta?.runtime_seconds ? `${result.meta.runtime_seconds.toFixed(1)}s` : "N/A";
  resultRuntime.textContent = `Runtime: ${runtime}`;

  // Telemetry Footer
  const cov = result.coverage || {};
  telSkills.textContent = `${cov.skills_ok || 0}/${cov.skills_run || 0} Ok`;
  const pages = result.meta?.pages_crawled || 1;
  telPages.textContent = `${pages} page${pages > 1 ? "s" : ""}`;
  const types = result.meta?.page_types_covered || ["homepage"];
  telTypes.textContent = types.join(", ");

  // Reset filters back to defaults each time a different tab's results are shown
  activeSeverityFilter = "all";
  activeTierFilter = "all";
  activeSearchQuery = "";
  severityPills.forEach((p) => p.classList.toggle("active", p.dataset.severity === "all"));
  tierFilter.value = "all";
  searchInput.value = "";

  // Findings
  currentFindings = result.findings || [];
  applyFilters();
}

function applyFilters() {
  let filtered = currentFindings;

  // 1. Severity Filter
  if (activeSeverityFilter !== "all") {
    filtered = filtered.filter(
      (f) => (f.severity || "").toLowerCase() === activeSeverityFilter
    );
  }

  // 2. Evidence Tier Filter
  if (activeTierFilter === "measured") {
    filtered = filtered.filter((f) =>
      ["tier_1", "tier_2"].includes((f.evidence_tier || "").toLowerCase())
    );
  } else if (activeTierFilter === "heuristic") {
    filtered = filtered.filter(
      (f) => (f.evidence_tier || "").toLowerCase() === "tier_4"
    );
  }

  // 3. Search Query Filter
  if (activeSearchQuery) {
    filtered = filtered.filter((f) => {
      const title = (f.title || "").toLowerCase();
      const id = (f.id || "").toLowerCase();
      const evidence = typeof f.evidence === "string" ? f.evidence.toLowerCase() : JSON.stringify(f.evidence).toLowerCase();
      const action = f.suggested_action?.summary ? f.suggested_action.summary.toLowerCase() : "";
      return title.includes(activeSearchQuery) || id.includes(activeSearchQuery) || evidence.includes(activeSearchQuery) || action.includes(activeSearchQuery);
    });
  }

  renderFindingsList(filtered);
}

function renderFindingsList(findings) {
  findingsContainer.innerHTML = "";

  if (findings.length === 0) {
    findingsContainer.innerHTML = `
      <div style="padding: 24px; text-align: center; color: var(--text-secondary); background: var(--bg-secondary); border-radius: 8px;">
        No findings match current filters.
      </div>
    `;
    return;
  }

  findings.forEach((finding) => {
    const card = document.createElement("div");
    card.className = "finding-card";

    const sev = (finding.severity || "low").toLowerCase();
    const isTierMeasured = ["tier_1", "tier_2"].includes((finding.evidence_tier || "").toLowerCase());
    const tierLabel = isTierMeasured ? "Measured" : "Heuristic";
    const tierClass = isTierMeasured ? "measured" : "heuristic";

    const evidenceHtml = renderEvidence(finding.evidence);

    const actionSummary = finding.suggested_action?.summary || "No specific action recorded.";
    const actionPriority = finding.suggested_action?.priority || sev;

    card.innerHTML = `
      <div class="finding-header">
        <span class="finding-badge ${sev}">${sev}</span>
        <div class="finding-title-wrap">
          <div class="finding-title">${escapeHtml(finding.title || "Audit Finding")}</div>
          <div class="finding-tags">
            <span class="finding-id">${escapeHtml(finding.id || "GEN")}</span>
            <span class="tier-tag ${tierClass}">${tierLabel}</span>
          </div>
        </div>
        <svg class="chevron-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </div>
      <div class="finding-body">
        ${evidenceHtml}
        <div class="action-box">
          <div class="action-title">Suggested Action (${escapeHtml(actionPriority)})</div>
          <div class="action-text">${escapeHtml(actionSummary)}</div>
        </div>
      </div>
    `;

    card.querySelector(".finding-header").addEventListener("click", () => {
      card.classList.toggle("open");
    });

    findingsContainer.appendChild(card);
  });
}

/**
 * Evidence usually arrives as a plain sentence, but a lot of it is a human
 * sentence followed by a machine-generated
 * "(source: ..., field: ..., value: {...}, details: ...)" tail. Dumping that
 * whole thing as one monospace blob (old behavior) is hard to scan, so this
 * pulls the tail apart into labeled rows and turns the value/details data
 * into small stat chips instead.
 */
function renderEvidence(evidence) {
  if (evidence === null || evidence === undefined || evidence === "") {
    return `<p class="evidence-text">No evidence recorded.</p>`;
  }

  if (typeof evidence !== "string") {
    return renderMetaRows(objectToPairs(evidence));
  }

  const match = evidence.match(
    /^(.*?)\s*\(source:\s*([^,]+),\s*field:\s*([^,]+),\s*value:\s*(\{.*\})\s*,\s*details:\s*(.*)\)\s*$/s
  );

  if (!match) {
    return `<p class="evidence-text">${escapeHtml(evidence)}</p>`;
  }

  const [, text, source, field, value, details] = match;
  let html = "";
  if (text.trim()) {
    html += `<p class="evidence-text">${escapeHtml(text.trim())}</p>`;
  }

  html += `<div class="evidence-meta">`;
  html += metaRow("Source", escapeHtml(source.trim()));
  html += metaRow("Field", escapeHtml(field.trim()));

  const parsedValue = tryParsePyDict(value.trim());
  html += metaStackRow("Value", parsedValue ? chipGroup(objectToPairs(parsedValue)) : escapeHtml(value.trim()));

  const detailPairs = splitTopLevel(details.trim(), ",").map((part) => {
    const idx = part.indexOf(":");
    return idx === -1 ? [null, part] : [part.slice(0, idx).trim(), part.slice(idx + 1).trim()];
  });
  html += metaStackRow("Details", chipGroup(detailPairs));
  html += `</div>`;

  return html;
}

function metaRow(label, valueHtml) {
  return `<div class="evidence-meta-row"><span class="meta-key">${label}</span><span class="meta-val mono">${valueHtml}</span></div>`;
}

function metaStackRow(label, innerHtml) {
  return `<div class="evidence-meta-row evidence-meta-stack"><span class="meta-key">${label}</span>${innerHtml}</div>`;
}

function chipGroup(pairs) {
  const chips = pairs
    .map(([k, v]) => {
      const keyHtml = k ? `<span class="stat-chip-key">${escapeHtml(k)}</span>` : "";
      return `<span class="stat-chip">${keyHtml}<span class="stat-chip-val">${escapeHtml(String(v))}</span></span>`;
    })
    .join("");
  return `<div class="stat-chip-group">${chips}</div>`;
}

function renderMetaRows(pairs) {
  if (!pairs.length) return `<p class="evidence-text">No evidence recorded.</p>`;
  return `<div class="evidence-meta">${pairs
    .map(([k, v]) => metaRow(escapeHtml(k), `${escapeHtml(String(v))}`))
    .join("")}</div>`;
}

function objectToPairs(obj) {
  if (!obj || typeof obj !== "object") return [];
  return Object.entries(obj);
}

/** Parses a Python-style dict literal (single quotes, True/False/None) as JSON. */
function tryParsePyDict(str) {
  try {
    const jsonish = str
      .replace(/'/g, '"')
      .replace(/\bTrue\b/g, "true")
      .replace(/\bFalse\b/g, "false")
      .replace(/\bNone\b/g, "null");
    const parsed = JSON.parse(jsonish);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

/** Splits on a separator at brace/bracket depth 0, so nested {..}/[..] survive intact. */
function splitTopLevel(str, sep) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const ch of str) {
    if (ch === "{" || ch === "[") depth++;
    if (ch === "}" || ch === "]") depth--;
    if (ch === sep && depth === 0) {
      parts.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function getOrCreateInstallId() {
  let { install_id } = await chrome.storage.local.get(["install_id"]);
  if (!install_id) {
    install_id = "inst_" + Math.random().toString(36).substring(2, 12);
    await chrome.storage.local.set({ install_id });
  }
  return install_id;
}