/**
 * WebLens Side Panel Controller (Cross-Browser Chrome & Firefox)
 * Handles active tab synchronization, parallel audit scheduling (one browser-tab-like
 * strip per audit job), live progress updates, finding card rendering, evidence-tier
 * segregation, and filtering.
 */

const browserApi = typeof browser !== "undefined" ? browser : chrome;
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
const backendUrlInput = document.getElementById("backend-url-input");
const resetBackendBtn = document.getElementById("reset-backend-btn");
const testConnectionBtn = document.getElementById("test-connection-btn");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const connectionStatus = document.getElementById("connection-status");

async function getActiveBackendUrl() {
  try {
    const { backend_url } = await chrome.storage.local.get(["backend_url"]);
    if (backend_url && backend_url.trim()) {
      return backend_url.trim().replace(/\/+$/, "");
    }
  } catch (err) {
    console.warn("[WebLens] Failed to read backend_url from storage:", err);
  }
  return DEFAULT_BACKEND_URL;
}

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

// Progress Preflight Elements
const progressPreflight = document.getElementById("progress-preflight");
const progressPreflightScore = document.getElementById("progress-preflight-score");
const progressPreflightChips = document.getElementById("progress-preflight-chips");

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
const exportJsonBtn = document.getElementById("export-json-btn");
const exportMdBtn = document.getElementById("export-md-btn");
const resultPreflight = document.getElementById("result-preflight");
const resultPreflightScore = document.getElementById("result-preflight-score");
const resultPreflightChips = document.getElementById("result-preflight-chips");
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
browserApi.runtime.onMessage.addListener((message) => {
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
  if (resetBackendBtn) {
    resetBackendBtn.addEventListener("click", () => {
      if (backendUrlInput) backendUrlInput.value = DEFAULT_BACKEND_URL;
    });
  }
  testConnectionBtn.addEventListener("click", testBackendConnection);
  saveSettingsBtn.addEventListener("click", saveSettings);
  syncTabBtn.addEventListener("click", syncActiveTabUrl);
  startAuditBtn.addEventListener("click", initiateAudit);
  retryAuditBtn.addEventListener("click", retryAudit);
  terminateAuditBtn.addEventListener("click", terminateAudit);
  if (exportJsonBtn) {
    exportJsonBtn.addEventListener("click", () => {
      const job = getActiveJob();
      if (job) downloadJSONReport(job);
    });
  }
  if (exportMdBtn) {
    exportMdBtn.addEventListener("click", () => {
      const job = getActiveJob();
      if (job) downloadMarkdownReport(job);
    });
  }

  // Keep the target URL synced to whatever the browser's address bar shows.
  // This never needs to "lock" any more -- starting a new audit opens its own
  // tab, so it can't collide with audits already running.
  browserApi.tabs.onActivated.addListener(syncActiveTabUrl);
  browserApi.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
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
  const { groq_api_key } = await browserApi.storage.local.get(["groq_api_key"]);
  groqKeyInput.value = groq_api_key || "";
  if (backendUrlInput) {
    backendUrlInput.value = backend_url || DEFAULT_BACKEND_URL;
  }
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
  const rawTarget = backendUrlInput ? backendUrlInput.value.trim() : "";
  const targetUrl = (rawTarget || DEFAULT_BACKEND_URL).replace(/\/+$/, "");

  connectionStatus.className = "status-badge";
  connectionStatus.textContent = `Connecting to ${targetUrl}...`;
  connectionStatus.classList.remove("hidden");

  try {
    const resp = await fetch(`${targetUrl}/health`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    connectionStatus.className = "status-badge success";
    connectionStatus.textContent = `Connected! Backend ready (Playwright: ${data.playwright_ready ? "Ready" : "Offline fallback"}, Slots: ${data.max_concurrent_jobs})`;
  } catch (err) {
    connectionStatus.className = "status-badge error";
    connectionStatus.textContent = `Connection failed to ${targetUrl}: ${err.message}. Ensure backend is running.`;
  }
}

/**
 * Groq key is stored in browserApi.storage.local and picked back up automatically
 * on every future audit request -- see submitAuditJob(), which reads it fresh
 * from storage each time rather than keeping it only in memory.
 */
async function saveSettings() {
  const key = groqKeyInput.value.trim();
  const rawBackend = backendUrlInput ? backendUrlInput.value.trim() : "";
  const normalizedBackend = rawBackend ? rawBackend.replace(/\/+$/, "") : DEFAULT_BACKEND_URL;

  await browserApi.storage.local.set({
    groq_api_key: key,
    backend_url: normalizedBackend,
  });

  await checkConfiguredKey();
  closeSettingsPanel();
}


async function checkConfiguredKey() {
  const { groq_api_key } = await browserApi.storage.local.get(["groq_api_key"]);
  if (!groq_api_key || !groq_api_key.trim()) {
    missingKeyBanner.classList.remove("hidden");
  } else {
    missingKeyBanner.classList.add("hidden");
  }
}

async function syncActiveTabUrl() {
  try {
    const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
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
  await browserApi.storage.local.set({
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
  const stored = await browserApi.storage.local.get([
    "weblens_jobs",
    "weblens_active_tab_id",
  ]);

  jobs = Array.isArray(stored.weblens_jobs) ? stored.weblens_jobs : [];
  activeTabId = stored.weblens_active_tab_id || (jobs.length ? jobs[jobs.length - 1].id : null);

  const activeBackend = await getActiveBackendUrl();

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
    preflight: null,
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
  job.preflight = null;
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
  const { groq_api_key } = await browserApi.storage.local.get(["groq_api_key"]);
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

  const activeBackend = await getActiveBackendUrl();

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

function updateProgressDisplay(progress, currentJob = null) {
  const job = currentJob || getActiveJob();
  progressStageTag.textContent = (progress.stage || "Running").toUpperCase();
  const pct = Math.max(5, Math.min(100, progress.percent || 10));
  progressPercent.textContent = `${pct}%`;
  progressFill.style.width = `${pct}%`;
  progressMessage.textContent = progress.message || "Processing audit modules...";

  const preflight = (job && job.preflight) || progress.preflight;
  if (preflight && preflight.scanned && progressPreflight) {
    renderPreflightBox(preflight, progressPreflight, progressPreflightScore, progressPreflightChips);
  } else if (progressPreflight) {
    progressPreflight.classList.add("hidden");
  }
}

/** Applies a status/progress/result update to one job, then repaints it if visible. */
async function applyJobUpdate(job, data) {
  job.status = data.status;
  if (data.preflight || data.progress?.preflight) {
    job.preflight = data.preflight || data.progress?.preflight;
  }
  if (data.progress) {
    // Clamp so a later stage reporting a lower percent (or a stray/late SSE
    // event) can never make the loading bar visually move backward.
    const incoming = Math.max(5, Math.min(100, data.progress.percent ?? 10));
    job.maxPercent = Math.max(job.maxPercent || 0, incoming);
    job.progress = { ...data.progress, percent: job.maxPercent };
  }
  if (data.status === "done") {
    job.result = data.result;
    if (data.result?.preflight && !job.preflight) {
      job.preflight = data.result.preflight;
    }
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
      browserApi.runtime.sendMessage({
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

  // Pre-Flight Security Box in Results
  const job = getActiveJob();
  const preflight = (job && job.preflight) || result.preflight;
  if (preflight && preflight.scanned && resultPreflight) {
    renderPreflightBox(preflight, resultPreflight, resultPreflightScore, resultPreflightChips);
  } else if (resultPreflight) {
    resultPreflight.classList.add("hidden");
  }

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

    // Build scope/coverage context strip
    const scopeVal = finding.scope || null;
    const affectedPages = finding.affected_pages;
    const pagesExamined = finding.pages_examined;
    const affectedRatio = finding.affected_ratio;
    const affectedUrls = Array.isArray(finding.affected_urls) ? finding.affected_urls : [];

    let coverageChips = "";
    if (scopeVal) {
      const scopeClass = scopeVal === "site-wide" ? "scope-sitewide" : scopeVal === "majority" ? "scope-majority" : "scope-single";
      coverageChips += `<span class="coverage-chip ${scopeClass}">${escapeHtml(scopeVal)}</span>`;
    }
    if (affectedPages !== undefined && pagesExamined !== undefined) {
      coverageChips += `<span class="coverage-chip scope-pages">${affectedPages}/${pagesExamined} pages</span>`;
    }
    if (affectedRatio !== undefined) {
      coverageChips += `<span class="coverage-chip scope-ratio">${Math.round(affectedRatio * 100)}% affected</span>`;
    }

    const coverageStrip = coverageChips
      ? `<div class="finding-coverage">${coverageChips}</div>`
      : "";

    const urlListHtml = affectedUrls.length
      ? `<div class="affected-urls">
           <span class="affected-urls-label">Affected URLs</span>
           <ul class="affected-url-list">
             ${affectedUrls.map(u => `<li><a href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a></li>`).join("")}
           </ul>
         </div>`
      : "";

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
        ${coverageStrip}
        ${evidenceHtml}
        ${urlListHtml}
        <div class="action-box">
          <div class="action-title">Suggested Action (${escapeHtml(actionPriority)})</div>
          <div class="action-text">${escapeHtml(actionSummary)}</div>
        </div>
        <div class="finding-actions-row">
          <button class="card-action-btn copy-prompt-btn" title="Copy ready-to-paste AI remediation prompt">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span>Copy AI Fix Prompt</span>
          </button>
          <button class="card-action-btn locate-dom-btn" title="Highlight element on active page">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="22" y1="12" x2="18" y2="12"></line>
              <line x1="6" y1="12" x2="2" y2="12"></line>
              <line x1="12" y1="6" x2="12" y2="2"></line>
              <line x1="12" y1="22" x2="12" y2="18"></line>
            </svg>
            <span>Locate on Page</span>
          </button>
        </div>
      </div>
    `;

    card.querySelector(".finding-header").addEventListener("click", () => {
      card.classList.toggle("open");
    });

    const copyBtn = card.querySelector(".copy-prompt-btn");
    copyBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyAiFixPrompt(finding, copyBtn);
    });

    const locateBtn = card.querySelector(".locate-dom-btn");
    locateBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      locateFindingOnPage(finding, locateBtn);
    });

    findingsContainer.appendChild(card);
  });
}

/**
 * Parses the actual evidence string format produced by the backend:
 *
 *   "Audited N representative pages; K/N pages exhibit this issue (/a, /b, ...).
 *    Sample telemetry: URL 'https://...' (page_type): <Python dict or list>"
 *
 * Also handles the legacy "(source:..., field:..., value:..., details:...)"
 * tail format and falls back to plain text for anything else.
 */
/**
 * Character-by-character lexer that converts Python dict/list literals
 * (single quotes, True/False/None) into valid JSON objects or arrays.
 * Handles escaped characters, apostrophes in natural English sentences,
 * booleans, null, numbers, and nested structures without crashing.
 */
function pyToJson(str) {
  if (!str || typeof str !== "string") return null;
  const s = str.trim();
  if (!s.startsWith("{") && !s.startsWith("[")) return null;

  let out = "";
  let i = 0;
  const len = s.length;

  while (i < len) {
    const ch = s[i];

    if (ch === "'" || ch === '"') {
      const quote = ch;
      i++;
      let strVal = "";
      while (i < len) {
        if (s[i] === "\\") {
          if (i + 1 < len) {
            strVal += s[i + 1];
            i += 2;
          } else {
            i++;
          }
        } else if (s[i] === quote) {
          i++;
          break;
        } else {
          strVal += s[i];
          i++;
        }
      }
      out += JSON.stringify(strVal);
    } else {
      if (s.startsWith("True", i) && !/[a-zA-Z0-9_]/.test(s[i + 4] || "")) {
        out += "true";
        i += 4;
      } else if (s.startsWith("False", i) && !/[a-zA-Z0-9_]/.test(s[i + 5] || "")) {
        out += "false";
        i += 5;
      } else if (s.startsWith("None", i) && !/[a-zA-Z0-9_]/.test(s[i + 4] || "")) {
        out += "null";
        i += 4;
      } else {
        out += ch;
        i++;
      }
    }
  }

  try {
    return JSON.parse(out);
  } catch (err) {
    return null;
  }
}

/** Parses a Python-style list literal (single quotes, True/False/None). */
function tryParsePyList(str) {
  const res = pyToJson(str);
  return Array.isArray(res) ? res : null;
}

/** Parses a Python-style dict literal (single quotes, True/False/None) as JSON. */
function tryParsePyDict(str) {
  const res = pyToJson(str);
  return res && typeof res === "object" && !Array.isArray(res) ? res : null;
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
      if (Array.isArray(v)) {
        const itemChips = v.map((item) => `<span class="stat-chip-subval">${escapeHtml(String(item))}</span>`).join(" ");
        return `<span class="stat-chip">${keyHtml}<span class="stat-chip-val">${itemChips}</span></span>`;
      }
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

/** Formats parsed objects or arrays into human-friendly cards, chips, or editorial quote boxes. */
function formatParsedData(parsed) {
  if (parsed === null || parsed === undefined) return "";
  if (Array.isArray(parsed)) {
    if (parsed.length === 0) return `<span class="evidence-empty-list">[ ]</span>`;
    const items = parsed.map((item) => {
      if (typeof item === "string" && (item.startsWith("{") || item.startsWith("["))) {
        const sub = pyToJson(item);
        if (sub) return formatParsedData(sub);
      }
      if (item && typeof item === "object") {
        const pairs = Object.entries(item).filter(([, v]) => v !== null && v !== undefined && v !== "");
        return pairs.length ? chipGroup(pairs) : `<span class="evidence-empty-list">{ }</span>`;
      }
      return `<span class="stat-chip"><span class="stat-chip-val">${escapeHtml(String(item))}</span></span>`;
    }).join("");
    return `<div class="stat-chip-group">${items}</div>`;
  }
  if (typeof parsed === "object") {
    // If it has long prose fields like qualitative_critique, separate them into editorial quote boxes
    const proseKeys = ["qualitative_critique", "critique", "notes", "description"];
    const pairs = [];
    let proseHtml = "";
    for (const [k, v] of Object.entries(parsed)) {
      if (v === null || v === undefined) continue;
      if (proseKeys.includes(k) && typeof v === "string") {
        proseHtml += `<div class="evidence-critique-box"><span class="critique-label">${escapeHtml(k)}:</span> <span class="critique-text">${escapeHtml(v)}</span></div>`;
      } else {
        pairs.push([k, v]);
      }
    }
    let html = "";
    if (pairs.length) html += chipGroup(pairs);
    if (proseHtml) html += proseHtml;
    return html;
  }
  return `<span class="stat-chip"><span class="stat-chip-val">${escapeHtml(String(parsed))}</span></span>`;
}

function highlightTextUrls(html) {
  return html.replace(
    /(https?:\/\/[^\s"'<>()]+)/g,
    '<a class="tel-url inline" href="$1" target="_blank" rel="noopener">$1</a>'
  );
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

/**
 * Parses all backend evidence string patterns:
 *  1. Legacy "(source:..., field:..., value:..., details:...)"
 *  2. Direct URL block "URL '<url>' (<page_type>): <data>"
 *  3. Multi-page rollup with "Sample telemetry:" prefix (lists & newline entries)
 *  4. Parenthetical telemetry "(key: val; key: val)" with nav items
 *  5. Linkified text fallback
 */
function renderEvidence(evidence) {
  if (evidence === null || evidence === undefined || evidence === "") {
    return `<p class="evidence-text">No evidence recorded.</p>`;
  }

  if (typeof evidence !== "string") {
    return formatParsedData(evidence);
  }

  // 1. Legacy format: (source: ..., field: ..., value: {...}, details: ...)
  const legacyMatch = evidence.match(
    /^(.*?)\s*\(source:\s*([^,]+),\s*field:\s*([^,]+),\s*value:\s*(\{.*\})\s*,\s*details:\s*(.*)\)\s*$/s
  );
  if (legacyMatch) {
    const [, text, source, field, value, details] = legacyMatch;
    let html = "";
    if (text.trim()) html += `<p class="evidence-text">${escapeHtml(text.trim())}</p>`;
    html += `<div class="evidence-meta">`;
    html += metaRow("Source", escapeHtml(source.trim()));
    html += metaRow("Field", escapeHtml(field.trim()));
    const parsedValue = pyToJson(value.trim());
    html += metaStackRow("Value", parsedValue ? formatParsedData(parsedValue) : escapeHtml(value.trim()));
    const detailPairs = splitTopLevel(details.trim(), ",").map((part) => {
      const idx = part.indexOf(":");
      return idx === -1 ? [null, part] : [part.slice(0, idx).trim(), part.slice(idx + 1).trim()];
    });
    html += metaStackRow("Details", chipGroup(detailPairs));
    html += `</div>`;
    return html;
  }

  // 2. Direct URL + Data format (without 'Sample telemetry:' prefix)
  // Example: URL 'https://...' (homepage): <dict or list or text>
  const directUrlMatch = evidence.match(/^URL\s+'([^']+)'\s+\(([^)]+)\)\s*:\s*([\s\S]*)$/);
  if (directUrlMatch) {
    const [, url, pageType, rawData] = directUrlMatch;
    const trimmedData = rawData.trim();
    const parsed = pyToJson(trimmedData);
    const dataHtml = parsed ? formatParsedData(parsed) : `<p class="evidence-text">${escapeHtml(trimmedData)}</p>`;
    return `
      <div class="telemetry-block standalone">
        <div class="telemetry-block-url">
          <span class="tel-page-type">${escapeHtml(pageType)}</span>
          <a class="tel-url" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>
        </div>
        <div class="telemetry-block-data">${dataHtml}</div>
      </div>`;
  }

  // 3. Multi-page rollup with 'Sample telemetry:' prefix
  if (evidence.includes("Sample telemetry:")) {
    const telemetryIdx = evidence.indexOf("Sample telemetry:");
    const prosePart = evidence.slice(0, telemetryIdx).trim();
    const telemetryPart = evidence.slice(telemetryIdx + "Sample telemetry:".length).trim();

    let html = "";
    if (prosePart) html += `<p class="evidence-text">${escapeHtml(prosePart)}</p>`;

    // Check if telemetryPart is a list of items or strings
    const parsedList = pyToJson(telemetryPart);
    if (Array.isArray(parsedList)) {
      const listItems = parsedList.map((item) => {
        if (typeof item === "string") {
          // Check if item string itself is a URL '...' (page_type): ...
          const itemUrlMatch = item.match(/^URL\s+'([^']+)'\s+\(([^)]+)\)\s*:\s*([\s\S]*)$/);
          if (itemUrlMatch) {
            const [, url, pageType, rawData] = itemUrlMatch;
            const parsedData = pyToJson(rawData.trim());
            return `
              <div class="telemetry-block">
                <div class="telemetry-block-url">
                  <span class="tel-page-type">${escapeHtml(pageType)}</span>
                  <a class="tel-url" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>
                </div>
                <div class="telemetry-block-data">${formatParsedData(parsedData || rawData)}</div>
              </div>`;
          }
          return `<div class="telemetry-bullet-item"><span class="bullet-dot">•</span> <span>${escapeHtml(item)}</span></div>`;
        }
        return `<div class="telemetry-bullet-item">${formatParsedData(item)}</div>`;
      }).join("");

      html += `<div class="telemetry-section"><span class="telemetry-label">Sample Telemetry</span><div class="telemetry-list">${listItems}</div></div>`;
      return html;
    }

    // Split on newlines if multiple URL '...' entries
    const entries = telemetryPart.split(/\n(?=URL\s+')/);
    const blocks = entries.map((entry) => {
      const urlMatch = entry.match(/^URL\s+'([^']+)'\s+\(([^)]+)\)\s*:\s*([\s\S]*)$/);
      if (!urlMatch) {
        return `<p class="evidence-text">${escapeHtml(entry.trim())}</p>`;
      }
      const [, url, pageType, rawData] = urlMatch;
      const parsed = pyToJson(rawData.trim());
      return `
        <div class="telemetry-block">
          <div class="telemetry-block-url">
            <span class="tel-page-type">${escapeHtml(pageType)}</span>
            <a class="tel-url" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>
          </div>
          <div class="telemetry-block-data">${formatParsedData(parsed || rawData.trim())}</div>
        </div>`;
    });
    html += `<div class="telemetry-section"><span class="telemetry-label">Sample Telemetry</span>${blocks.join("")}</div>`;
    return html;
  }

  // 4. Parenthetical telemetry format: Text... (key: val; key: val)
  // Example: Primary navigation menu contains 20 items... (nav_item_count: 20; items: [...])
  const parenMatch = evidence.match(/^(.*?)\s*\((([a-zA-Z0-9_]+:\s*[^;)]+)(;\s*[a-zA-Z0-9_]+:\s*[^;)]+)*)\)\s*$/s);
  if (parenMatch) {
    const [, prose, telemetryContent] = parenMatch;
    let html = "";
    if (prose.trim()) html += `<p class="evidence-text">${escapeHtml(prose.trim())}</p>`;

    const parts = splitTopLevel(telemetryContent.trim(), ";");
    const pairs = [];
    for (const part of parts) {
      const cIdx = part.indexOf(":");
      if (cIdx !== -1) {
        const k = part.slice(0, cIdx).trim();
        const vRaw = part.slice(cIdx + 1).trim();
        const vParsed = pyToJson(vRaw);
        pairs.push([k, vParsed !== null ? vParsed : vRaw]);
      }
    }
    if (pairs.length) {
      const chips = pairs.map(([k, v]) => {
        if (Array.isArray(v)) {
          // If items is a list of nav items or objects
          const listHtml = v.map((item) => {
            if (typeof item === "string" && item.startsWith("{")) {
              const subObj = pyToJson(item);
              if (subObj) {
                const label = subObj.label || subObj.text || subObj.name || "";
                const href = subObj.href || "";
                if (href) {
                  return `<a class="nav-chip-link" href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label || href)}</a>`;
                }
                return `<span class="nav-chip">${escapeHtml(label)}</span>`;
              }
            }
            return `<span class="nav-chip">${escapeHtml(String(item))}</span>`;
          }).join(" ");
          return `<div class="evidence-meta-row evidence-meta-stack"><span class="meta-key">${escapeHtml(k)}</span><div class="nav-chip-group">${listHtml}</div></div>`;
        }
        return metaRow(escapeHtml(k), escapeHtml(String(v)));
      }).join("");

      html += `<div class="evidence-meta">${chips}</div>`;
      return html;
    }
  }

  // 5. Fallback: Clean formatted text with highlighted URLs
  return `<p class="evidence-text">${highlightTextUrls(escapeHtml(evidence))}</p>`;
}

async function getOrCreateInstallId() {
  let { install_id } = await browserApi.storage.local.get(["install_id"]);
  if (!install_id) {
    install_id = "inst_" + Math.random().toString(36).substring(2, 12);
    await browserApi.storage.local.set({ install_id });
  }
  return install_id;
}

/**
 * Renders the Pre-Flight Security & Header box with security score badge and chips.
 */
function renderPreflightBox(preflight, boxEl, scoreEl, chipsEl) {
  if (!preflight || !preflight.scanned || !boxEl) {
    if (boxEl) boxEl.classList.add("hidden");
    return;
  }
  boxEl.classList.remove("hidden");

  // Score badge
  const score = preflight.security_score ?? 0;
  if (scoreEl) {
    scoreEl.textContent = `Score: ${score}/100`;
    scoreEl.className = "preflight-score-badge " + (score >= 70 ? "high" : score >= 40 ? "med" : "low");
  }

  // Chips
  if (chipsEl) {
    chipsEl.innerHTML = "";

    // 1. HSTS
    const hstsChip = document.createElement("span");
    hstsChip.className = `preflight-chip ${preflight.hsts ? "pass" : "fail"}`;
    hstsChip.textContent = preflight.hsts ? "✓ HSTS" : "✗ No HSTS";
    chipsEl.appendChild(hstsChip);

    // 2. CSP
    const cspChip = document.createElement("span");
    cspChip.className = `preflight-chip ${preflight.csp ? "pass" : "fail"}`;
    cspChip.textContent = preflight.csp ? "✓ CSP" : "✗ No CSP";
    chipsEl.appendChild(cspChip);

    // 3. X-Frame-Options
    const xfoChip = document.createElement("span");
    xfoChip.className = `preflight-chip ${preflight.x_frame_options ? "pass" : "fail"}`;
    xfoChip.textContent = preflight.x_frame_options ? `✓ XFO: ${preflight.x_frame_options}` : "✗ No XFO";
    chipsEl.appendChild(xfoChip);

    // 4. X-Content-Type-Options
    const xctoChip = document.createElement("span");
    xctoChip.className = `preflight-chip ${preflight.x_content_type_options ? "pass" : "fail"}`;
    xctoChip.textContent = preflight.x_content_type_options ? "✓ nosniff" : "✗ No nosniff";
    chipsEl.appendChild(xctoChip);

    // 5. Protocol
    const protoChip = document.createElement("span");
    protoChip.className = "preflight-chip info";
    protoChip.textContent = preflight.http_version ? String(preflight.http_version).toUpperCase() : "HTTP/1.1";
    chipsEl.appendChild(protoChip);

    // 6. Cache / ETag
    if (preflight.cache_control) {
      const cacheChip = document.createElement("span");
      cacheChip.className = "preflight-chip info";
      const cc = preflight.cache_control.length > 22 ? preflight.cache_control.slice(0, 20) + "..." : preflight.cache_control;
      cacheChip.textContent = `Cache: ${cc}`;
      chipsEl.appendChild(cacheChip);
    } else if (preflight.etag) {
      const etagChip = document.createElement("span");
      etagChip.className = "preflight-chip info";
      etagChip.textContent = "✓ ETag";
      chipsEl.appendChild(etagChip);
    }

    // 7. Server
    if (preflight.server) {
      const srvChip = document.createElement("span");
      srvChip.className = "preflight-chip info";
      srvChip.textContent = `Server: ${preflight.server}`;
      chipsEl.appendChild(srvChip);
    }
  }
}

/**
 * Downloads canonical output.json for the completed audit.
 */
function downloadJSONReport(job) {
  if (!job || !job.result) {
    alert("No completed audit results to export for this tab.");
    return;
  }
  const host = hostnameOf(job.url) || "site";
  const dateTag = new Date().toISOString().slice(0, 10);
  const jsonStr = JSON.stringify(job.result, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = `weblens-audit-${host}-${dateTag}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(downloadUrl);
}

/**
 * Generates and downloads an Executive Markdown (.md) summary report.
 */
function downloadMarkdownReport(job) {
  if (!job || !job.result) {
    alert("No completed audit results to export for this tab.");
    return;
  }
  const result = job.result;
  const host = hostnameOf(job.url) || "site";
  const dateStr = result.audited_at ? new Date(result.audited_at).toLocaleString() : new Date().toLocaleString();
  const summary = result.summary || {};
  const coverage = result.coverage || {};
  const meta = result.meta || {};
  const preflight = job.preflight || result.preflight || null;

  let md = `# WebLens Technical Health & Readiness Audit Report\n\n`;
  md += `**Target Website:** [${job.url}](${job.url})\n`;
  md += `**Audit Timestamp:** ${dateStr}\n`;
  md += `**Runtime:** ${meta.runtime_seconds ? meta.runtime_seconds.toFixed(1) + "s" : "N/A"} | **Pages Crawled:** ${meta.pages_crawled || 1} | **Skills Evaluated:** ${coverage.skills_ok || 0}/${coverage.skills_run || 0}\n\n`;

  // Pre-flight section
  if (preflight && preflight.scanned) {
    md += `## ⚡ Pre-Flight Security & Headers\n\n`;
    md += `| Check | Status | Details |\n`;
    md += `| :--- | :--- | :--- |\n`;
    md += `| **Security Score** | **${preflight.security_score || 0}/100** | Initial fast evaluation |\n`;
    md += `| **HTTP Version** | \`${preflight.http_version || "HTTP/1.1"}\` | Response protocol |\n`;
    md += `| **HSTS** | ${preflight.hsts ? "✅ Enabled" : "❌ Missing"} | Strict-Transport-Security |\n`;
    md += `| **CSP** | ${preflight.csp ? "✅ Configured" : "❌ Missing"} | Content-Security-Policy |\n`;
    md += `| **X-Frame-Options** | ${preflight.x_frame_options ? "✅ " + preflight.x_frame_options : "❌ Missing"} | Clickjacking defense |\n`;
    md += `| **Cache-Control** | \`${preflight.cache_control || "None"}\` | Browser caching |\n`;
    md += `| **ETag** | ${preflight.etag ? "✅ Present" : "❌ Missing"} | Entity tag validation |\n`;
    if (preflight.server) md += `| **Server** | \`${preflight.server}\` | Web server software |\n`;
    md += `\n`;
  }

  // Summary Metrics Table
  md += `## 📊 Executive Summary\n\n`;
  md += `| Critical | High | Medium | Low | Total Findings |\n`;
  md += `| :---: | :---: | :---: | :---: | :---: |\n`;
  md += `| **${summary.critical || 0}** | **${summary.high || 0}** | **${summary.medium || 0}** | **${summary.low || 0}** | **${summary.total_findings || 0}** |\n\n`;

  // Findings Breakdown
  md += `## 🔍 Findings & Remediation Plan\n\n`;
  const findings = result.findings || [];
  if (findings.length === 0) {
    md += `*No technical defects detected. Site is operating within recommended parameters.*\n\n`;
  } else {
    const sevOrder = { critical: 1, high: 2, medium: 3, low: 4 };
    const sorted = [...findings].sort((a, b) => {
      const sa = sevOrder[(a.severity || "").toLowerCase()] || 5;
      const sb = sevOrder[(b.severity || "").toLowerCase()] || 5;
      return sa - sb;
    });

    sorted.forEach((f, idx) => {
      const sev = (f.severity || "LOW").toUpperCase();
      const tier = (f.evidence_tier || "heuristic").toUpperCase();
      md += `### ${idx + 1}. [${sev}] ${f.title || "Audit Finding"} (${f.id || "GEN"})\n\n`;
      md += `- **Evidence Tier:** \`${tier}\`\n`;
      if (f.evidence) {
        const evStr = typeof f.evidence === "string" ? f.evidence : JSON.stringify(f.evidence, null, 2);
        md += `- **Evidence:**\n  \`\`\`\n  ${evStr}\n  \`\`\`\n`;
      }
      if (f.suggested_action) {
        md += `- **Remediation Action (${f.suggested_action.priority || "P2"}):** ${f.suggested_action.summary || "Inspect and resolve."}\n`;
      }
      md += `\n---\n\n`;
    });
  }

  md += `\n*Generated by WebLens AI Technical Auditor on ${dateStr}*\n`;

  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = `weblens-audit-${host}-${new Date().toISOString().slice(0, 10)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(downloadUrl);
}

/**
 * Copies an engineered AI fix prompt ready to paste into Cursor, Copilot, or ChatGPT.
 */
async function copyAiFixPrompt(finding, btn) {
  const job = getActiveJob();
  const url = job?.url || targetUrlInput.value || "Active Website";
  const sev = (finding.severity || "medium").toUpperCase();
  const tier = (finding.evidence_tier || "heuristic").toUpperCase();
  const actionSummary = finding.suggested_action?.summary || "Inspect and resolve.";
  const actionPriority = finding.suggested_action?.priority || sev;

  let evidenceStr = "";
  if (typeof finding.evidence === "string") {
    evidenceStr = finding.evidence;
  } else if (finding.evidence) {
    try {
      evidenceStr = JSON.stringify(finding.evidence, null, 2);
    } catch {
      evidenceStr = String(finding.evidence);
    }
  } else {
    evidenceStr = "No diagnostic evidence recorded.";
  }

  const promptText = `### WebLens Defect Remediation: [${finding.id || "DEFECT"}] ${finding.title || "Audit Finding"}
- **Target URL:** ${url}
- **Severity:** ${sev}
- **Evidence Tier:** ${tier}
- **Action Priority:** ${actionPriority}

#### Defect Evidence & Technical Context:
\`\`\`
${evidenceStr}
\`\`\`

#### Recommended Remediation:
${actionSummary}

#### Task for AI Assistant:
Please analyze the defect, evidence, and remediation suggestion above. Provide the exact code fix, configuration change, or CSS/HTML patch required to fix this issue according to modern web best practices. Explain the root cause and provide verification instructions.`;

  try {
    await navigator.clipboard.writeText(promptText);
    btn.classList.add("copied");
    const originalHtml = btn.innerHTML;
    btn.innerHTML = `<span>✓ Copied Prompt!</span>`;
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.innerHTML = originalHtml;
    }, 2000);
  } catch (err) {
    console.error("[WebLens] Clipboard write failed, falling back to textarea:", err);
    const ta = document.createElement("textarea");
    ta.value = promptText;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    btn.classList.add("copied");
    const originalHtml = btn.innerHTML;
    btn.innerHTML = `<span>✓ Copied Prompt!</span>`;
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.innerHTML = originalHtml;
    }, 2000);
  }
}

/**
 * Highlights offending element or shows guidance banner directly on the active webpage.
 */
async function locateFindingOnPage(finding, btn) {
  try {
    btn.classList.add("locating");
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      alert("No active tab found. Please switch to the browser tab you wish to inspect.");
      return;
    }

    if (!tab.url || (!tab.url.startsWith("http://") && !tab.url.startsWith("https://"))) {
      alert("Element location only operates on active HTTP or HTTPS web pages.");
      return;
    }

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: inPageHighlightFunction,
      args: [{
        id: finding.id || "GEN",
        title: finding.title || "",
        severity: finding.severity || "low",
        evidence: typeof finding.evidence === "string" ? finding.evidence : JSON.stringify(finding.evidence),
        action: finding.suggested_action?.summary || ""
      }]
    });

    const res = results && results[0] ? results[0].result : null;
    const originalHtml = btn.innerHTML;
    if (res && res.matched) {
      btn.innerHTML = `<span>✓ Located &lt;${res.tag}&gt;</span>`;
    } else {
      btn.innerHTML = `<span>Not on DOM</span>`;
    }

    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.classList.remove("locating");
    }, 2500);

  } catch (err) {
    console.warn("[WebLens] locateFindingOnPage failed:", err);
    alert(`Cannot inspect DOM: ${err.message || err}`);
  } finally {
    btn.classList.remove("locating");
  }
}

/**
 * Self-contained DOM inspection and highlight script executed directly inside the active webpage.
 */
function inPageHighlightFunction(finding) {
  const oldOverlay = document.getElementById("weblens-highlight-overlay");
  if (oldOverlay) oldOverlay.remove();
  const oldBanner = document.getElementById("weblens-page-banner");
  if (oldBanner) oldBanner.remove();

  if (!document.getElementById("weblens-pulse-style")) {
    const style = document.createElement("style");
    style.id = "weblens-pulse-style";
    style.textContent = `
      @keyframes weblens-pulse-glow {
        0% { box-shadow: 0 0 0 3px rgba(217, 119, 87, 0.9), 0 0 16px rgba(217, 119, 87, 0.4); }
        50% { box-shadow: 0 0 0 9px rgba(217, 119, 87, 0.25), 0 0 24px rgba(217, 119, 87, 0.6); }
        100% { box-shadow: 0 0 0 3px rgba(217, 119, 87, 0.9), 0 0 16px rgba(217, 119, 87, 0.4); }
      }
    `;
    document.head.appendChild(style);
  }

  let targetEl = null;
  const evText = (typeof finding.evidence === "string" ? finding.evidence : JSON.stringify(finding.evidence || "")).toLowerCase();
  const titleText = (finding.title || "").toLowerCase();

  // Strategy 1: Explicit CSS selector in evidence
  const selectorMatch = evText.match(/selector:\s*['"]([^'"]+)['"]/i) || evText.match(/selector:\s*([^\s,;]+)/i);
  if (selectorMatch && selectorMatch[1]) {
    try {
      const candidate = document.querySelector(selectorMatch[1]);
      if (candidate) targetEl = candidate;
    } catch (e) { }
  }

  // Strategy 2: Image / Alt attribute issues
  if (!targetEl && (titleText.includes("image") || titleText.includes("alt") || evText.includes("img") || evText.includes("alt"))) {
    const badImgs = Array.from(document.querySelectorAll("img:not([alt]), img[alt='']"));
    if (badImgs.length > 0) {
      targetEl = badImgs[0];
    } else {
      const anyImg = document.querySelector("img");
      if (anyImg) targetEl = anyImg;
    }
  }

  // Strategy 3: Heading issues
  if (!targetEl && (titleText.includes("heading") || titleText.includes("h1") || evText.includes("h1"))) {
    const h1 = document.querySelector("h1");
    if (h1) targetEl = h1;
    else targetEl = document.querySelector("h2, h3, header");
  }

  // Strategy 4: Button / Interactive / CTA issues
  if (!targetEl && (titleText.includes("button") || titleText.includes("cta") || evText.includes("button") || evText.includes("click"))) {
    const btn = document.querySelector("button, [role='button'], input[type='submit'], a.btn, a.button");
    if (btn) targetEl = btn;
  }

  // Strategy 5: Link / Anchor issues
  if (!targetEl && (titleText.includes("link") || titleText.includes("anchor") || evText.includes("href"))) {
    const link = document.querySelector("a[href]");
    if (link) targetEl = link;
  }

  // Strategy 6: Form / Input issues
  if (!targetEl && (titleText.includes("input") || titleText.includes("label") || titleText.includes("form"))) {
    const input = document.querySelector("input:not([type='hidden']), select, textarea");
    if (input) targetEl = input;
  }

  // Strategy 7: Search by text snippet mentioned in evidence
  if (!targetEl) {
    const quotes = evText.match(/["']([^"']{4,40})["']/g);
    if (quotes) {
      for (const q of quotes) {
        const raw = q.slice(1, -1).trim();
        if (raw.length >= 4) {
          const allEls = Array.from(document.querySelectorAll("p, span, a, h1, h2, h3, div, button"));
          const matchEl = allEls.find((el) => el.children.length === 0 && el.innerText && el.innerText.toLowerCase().includes(raw.toLowerCase()));
          if (matchEl) {
            targetEl = matchEl;
            break;
          }
        }
      }
    }
  }

  if (targetEl) {
    targetEl.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });

    const rect = targetEl.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;

    const overlay = document.createElement("div");
    overlay.id = "weblens-highlight-overlay";
    overlay.style.position = "absolute";
    overlay.style.top = `${rect.top + scrollY - 4}px`;
    overlay.style.left = `${rect.left + scrollX - 4}px`;
    overlay.style.width = `${Math.max(24, rect.width + 8)}px`;
    overlay.style.height = `${Math.max(24, rect.height + 8)}px`;
    overlay.style.border = "3px solid #d97757";
    overlay.style.borderRadius = "6px";
    overlay.style.zIndex = "2147483640";
    overlay.style.pointerEvents = "auto";
    overlay.style.animation = "weblens-pulse-glow 1.4s ease-in-out infinite";
    overlay.style.cursor = "pointer";
    overlay.title = "WebLens: Click to dismiss highlight";

    const badge = document.createElement("div");
    badge.style.position = "absolute";
    badge.style.bottom = "calc(100% + 6px)";
    badge.style.left = "0";
    badge.style.background = "#1f1e1d";
    badge.style.color = "#ffffff";
    badge.style.padding = "4px 8px";
    badge.style.borderRadius = "5px";
    badge.style.fontSize = "11px";
    badge.style.fontWeight = "700";
    badge.style.fontFamily = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif";
    badge.style.whiteSpace = "nowrap";
    badge.style.boxShadow = "0 2px 8px rgba(0,0,0,0.3)";
    badge.style.display = "flex";
    badge.style.alignItems = "center";
    badge.style.gap = "6px";

    const safeTitle = (finding.title || "Audit Finding").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    badge.innerHTML = `<span style="background:#d97757; color:#fff; padding:1px 5px; border-radius:3px; font-size:10px;">${finding.id}</span> <span>${safeTitle.slice(0, 45)}</span> <span style="opacity:0.6; font-size:12px; margin-left:4px;">✕</span>`;
    overlay.appendChild(badge);

    overlay.addEventListener("click", () => overlay.remove());
    document.body.appendChild(overlay);

    setTimeout(() => {
      if (overlay.parentNode) overlay.remove();
    }, 8000);

    return { matched: true, tag: targetEl.tagName.toLowerCase() };
  }

  // Fallback: Page/Header level finding banner
  const banner = document.createElement("div");
  banner.id = "weblens-page-banner";
  banner.style.position = "fixed";
  banner.style.top = "18px";
  banner.style.left = "50%";
  banner.style.transform = "translateX(-50%)";
  banner.style.background = "#1f1e1d";
  banner.style.color = "#ffffff";
  banner.style.border = "1px solid #d97757";
  banner.style.borderRadius = "8px";
  banner.style.padding = "8px 16px";
  banner.style.fontSize = "12px";
  banner.style.fontFamily = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif";
  banner.style.zIndex = "2147483647";
  banner.style.boxShadow = "0 4px 16px rgba(0,0,0,0.35)";
  banner.style.cursor = "pointer";
  const safeTitle = (finding.title || "Audit Finding").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  banner.innerHTML = `<span style="color:#d97757; font-weight:700;">WebLens:</span> Page/Header Level Finding (<strong>${finding.id}</strong>: ${safeTitle.slice(0, 45)}) — No specific DOM element match.`;
  banner.addEventListener("click", () => banner.remove());
  document.body.appendChild(banner);
  setTimeout(() => {
    if (banner.parentNode) banner.remove();
  }, 4500);

  return { matched: false, reason: "Page-level issue" };
}