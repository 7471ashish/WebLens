/**
 * WebLens Side Panel Controller
 * Handles active tab synchronization, audit scheduling, live progress updates,
 * finding card rendering, evidence-tier segregation, and filtering.
 */

const DEFAULT_BACKEND_URL = "http://localhost:8000";

// DOM Elements
const missingKeyBanner = document.getElementById("missing-key-banner");
const bannerConfigBtn = document.getElementById("banner-config-btn");
const openSettingsBtn = document.getElementById("open-settings-btn");
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
const testConnectionBtn = document.getElementById("test-connection-btn");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const connectionStatus = document.getElementById("connection-status");

// Progress Elements
const progressSection = document.getElementById("progress-section");
const progressStageTag = document.getElementById("progress-stage-tag");
const progressPercent = document.getElementById("progress-percent");
const progressTimer = document.getElementById("progress-timer");
const progressFill = document.getElementById("progress-fill");
const progressMessage = document.getElementById("progress-message");
const stopAuditBtn = document.getElementById("stop-audit-btn");

// Error Elements
const errorSection = document.getElementById("error-section");
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

// State
let currentFindings = [];
let activeSeverityFilter = "all";
let activeTierFilter = "all";
let activeSearchQuery = "";
let auditStartTime = null;
let timerInterval = null;
let activeEventSource = null;
let isAuditRunning = false;

// Initialize on Load
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await checkConfiguredKey();
  await restorePreviousSession();
  if (!isAuditRunning) {
    await syncActiveTabUrl();
  }
});

// Listen for updates from background service worker
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "JOB_UPDATED" && message.job) {
    handleJobUpdate(message.job);
  }
});

function setupEventListeners() {
  openSettingsBtn.addEventListener("click", openSettingsPanel);
  bannerConfigBtn.addEventListener("click", openSettingsPanel);
  closeSettingsBtn.addEventListener("click", closeSettingsPanel);
  togglePwdBtn.addEventListener("click", togglePasswordVisibility);
  testConnectionBtn.addEventListener("click", testBackendConnection);
  saveSettingsBtn.addEventListener("click", saveSettings);
  syncTabBtn.addEventListener("click", syncActiveTabUrl);
  startAuditBtn.addEventListener("click", initiateAudit);
  retryAuditBtn.addEventListener("click", initiateAudit);
  stopAuditBtn.addEventListener("click", stopAudit);

  // Keep the target URL synced to whatever the browser's address bar shows,
  // even while the side panel stays open across tab switches/navigations —
  // but never while an audit is actively running (see syncActiveTabUrl).
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

  // Cleanly close EventSource on side panel unload
  window.addEventListener("beforeunload", () => {
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
  });
}

async function openSettingsPanel() {
  const { groq_api_key, backend_url } = await chrome.storage.local.get([
    "groq_api_key",
    "backend_url",
  ]);
  groqKeyInput.value = groq_api_key || "";
  backendUrlInput.value = backend_url || DEFAULT_BACKEND_URL;
  connectionStatus.classList.add("hidden");
  settingsPanel.classList.remove("hidden");
}

function closeSettingsPanel() {
  settingsPanel.classList.add("hidden");
}

function togglePasswordVisibility() {
  const isPassword = groqKeyInput.type === "password";
  groqKeyInput.type = isPassword ? "text" : "password";
}

async function testBackendConnection() {
  const targetUrl = (backendUrlInput.value.trim() || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
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

async function saveSettings() {
  const key = groqKeyInput.value.trim();
  const url = (backendUrlInput.value.trim() || DEFAULT_BACKEND_URL).replace(/\/+$/, "");

  await chrome.storage.local.set({
    groq_api_key: key,
    backend_url: url,
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
  if (isAuditRunning) return; // Never move the target URL while an audit is in flight.
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && (tab.url.startsWith("http://") || tab.url.startsWith("https://"))) {
      targetUrlInput.value = tab.url;
    }
  } catch (err) {
    console.warn("Unable to query active tab:", err);
  }
}

async function restorePreviousSession() {
  const stored = await chrome.storage.local.get([
    "active_job_id",
    "active_job_status",
    "active_job_progress",
    "active_job_result",
    "active_job_error",
    "active_job_url",
  ]);

  if (stored.active_job_status === "running" || stored.active_job_status === "queued") {
    // A job is genuinely still in flight — keep watching it, and keep the
    // URL pinned to whatever it's auditing rather than the active tab.
    if (stored.active_job_url) {
      targetUrlInput.value = stored.active_job_url;
    }
    showRunningState();
    if (stored.active_job_progress) {
      updateProgressDisplay(stored.active_job_progress);
    }
    if (stored.active_job_id) {
      const { backend_url } = await chrome.storage.local.get(["backend_url"]);
      const activeBackend = (backend_url || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
      connectSSE(stored.active_job_id, activeBackend);
    }
  } else if (stored.active_job_status === "done" || stored.active_job_status === "failed") {
    // Don't surface results/errors from a previous audit — start fresh each
    // time the panel opens. Clear the stale job state so it doesn't reappear.
    await chrome.storage.local.set({
      active_job_id: null,
      active_job_status: null,
      active_job_progress: null,
      active_job_result: null,
      active_job_error: null,
    });
  }
}

async function initiateAudit() {
  const url = targetUrlInput.value.trim();
  if (!url) {
    alert("Please provide a valid website URL to audit.");
    return;
  }

  const { groq_api_key, backend_url } = await chrome.storage.local.get([
    "groq_api_key",
    "backend_url",
  ]);

  const activeBackend = (backend_url || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
  const activeKey = groq_api_key ? groq_api_key.trim() : "placeholder_key";

  // Hide previous errors/results, start loading
  errorSection.classList.add("hidden");
  resultsSection.classList.add("hidden");
  showRunningState();

  try {
    const response = await fetch(`${activeBackend}/audits`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Install-ID": await getOrCreateInstallId(),
      },
      body: JSON.stringify({
        url: url,
        groq_api_key: activeKey,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Server returned HTTP ${response.status}`);
    }

    const { job_id } = await response.json();

    // Store in storage
    await chrome.storage.local.set({
      active_job_id: job_id,
      active_job_status: "queued",
      active_job_url: url,
      active_job_result: null,
      active_job_error: null,
    });

    // Establish Server-Sent Events (SSE) stream for major lifecycle milestones
    connectSSE(job_id, activeBackend);

  } catch (err) {
    showError(err.message || "Failed to contact WebLens backend.");
  }
}

/**
 * Ask the backend to cancel the in-flight job, tear down the local SSE
 * stream, and reset the panel to idle. Assumes a POST
 * `${backendUrl}/audits/{job_id}/cancel` endpoint -- adjust the path here if
 * your backend exposes cancellation differently.
 */
async function stopAudit() {
  const { active_job_id, backend_url } = await chrome.storage.local.get([
    "active_job_id",
    "backend_url",
  ]);
  const activeBackend = (backend_url || DEFAULT_BACKEND_URL).replace(/\/+$/, "");

  if (activeEventSource) {
    activeEventSource.close();
    activeEventSource = null;
  }

  if (active_job_id) {
    try {
      await fetch(`${activeBackend}/audits/${encodeURIComponent(active_job_id)}/cancel`, {
        method: "POST",
      });
    } catch (err) {
      console.warn("[WebLens] Failed to notify backend of cancellation:", err);
    }
  }

  await chrome.storage.local.set({
    active_job_id: null,
    active_job_status: null,
    active_job_progress: null,
    active_job_result: null,
    active_job_error: null,
  });

  stopTimer();
  hideRunningState();
}

function handleJobUpdate(job) {
  if (job.status === "queued" || job.status === "running") {
    showRunningState();
    if (job.progress) {
      updateProgressDisplay(job.progress);
    }
  } else if (job.status === "done" && job.result) {
    stopTimer();
    hideRunningState();
    renderAuditResult(job.result);
  } else if (job.status === "failed") {
    stopTimer();
    hideRunningState();
    showError(job.error || "Audit failed during execution.");
  }
}

/**
 * Connect to backend via Server-Sent Events (SSE).
 * Only fires when major lifecycle events occur (stage changes, completion, or failure).
 */
function connectSSE(jobId, backendUrl) {
  if (activeEventSource) {
    activeEventSource.close();
    activeEventSource = null;
  }

  const streamUrl = `${backendUrl}/audits/${encodeURIComponent(jobId)}/stream`;
  console.log(`[WebLens] Connecting to SSE stream: ${streamUrl}`);
  const es = new EventSource(streamUrl);
  activeEventSource = es;

  es.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);
      console.log("[WebLens] Major event received via SSE:", data.status, data.progress?.stage);

      handleJobUpdate(data);

      await chrome.storage.local.set({
        active_job_status: data.status,
        active_job_progress: data.progress,
      });

      if (data.status === "done") {
        if (data.result) {
          await chrome.storage.local.set({
            active_job_result: data.result,
          });
        }
        es.close();
        if (activeEventSource === es) {
          activeEventSource = null;
        }

        // Trigger native desktop notification
        try {
          chrome.runtime.sendMessage({
            type: "NOTIFY_COMPLETION",
            title: "WebLens Audit Complete",
            message: `Completed audit for ${data.result?.site || "target site"}. Total findings: ${data.result?.summary?.total_findings || 0}.`,
          });
        } catch {
          // Extension context may be closing
        }
      } else if (data.status === "failed") {
        await chrome.storage.local.set({
          active_job_error: data.error || "Audit failed during execution.",
        });
        es.close();
        if (activeEventSource === es) {
          activeEventSource = null;
        }
      }
    } catch (parseErr) {
      console.error("[WebLens] Failed to parse SSE event data:", parseErr, event.data);
    }
  };

  es.onerror = async (err) => {
    console.warn("[WebLens] SSE stream disconnected or closed:", err);
    es.close();
    if (activeEventSource === es) {
      activeEventSource = null;
    }

    // Safety fallback: query backend once to see if job completed while stream closed
    try {
      const resp = await fetch(`${backendUrl}/audits/${encodeURIComponent(jobId)}`);
      if (resp.ok) {
        const currentJob = await resp.json();
        handleJobUpdate(currentJob);
        if (currentJob.status === "done" || currentJob.status === "failed") {
          await chrome.storage.local.set({
            active_job_status: currentJob.status,
            active_job_result: currentJob.result,
            active_job_error: currentJob.error,
          });
        }
      }
    } catch (fetchErr) {
      console.debug("[WebLens] Status check on SSE disconnect:", fetchErr);
    }
  };
}

function showRunningState() {
  isAuditRunning = true;
  startAuditBtn.disabled = true;
  syncTabBtn.disabled = true;
  auditSpinner.classList.remove("hidden");
  btnText.textContent = "Auditing...";
  progressSection.classList.remove("hidden");

  if (!timerInterval) {
    auditStartTime = Date.now();
    timerInterval = setInterval(() => {
      const elapsedSec = Math.floor((Date.now() - auditStartTime) / 1000);
      progressTimer.textContent = `${elapsedSec}s`;
    }, 1000);
  }
}

function hideRunningState() {
  isAuditRunning = false;
  startAuditBtn.disabled = false;
  syncTabBtn.disabled = false;
  auditSpinner.classList.add("hidden");
  btnText.textContent = "Audit This Page";
  progressSection.classList.add("hidden");
  stopTimer();
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

function updateProgressDisplay(progress) {
  progressStageTag.textContent = (progress.stage || "Running").toUpperCase();
  const pct = Math.max(5, Math.min(100, progress.percent || 10));
  progressPercent.textContent = `${pct}%`;
  progressFill.style.width = `${pct}%`;
  progressMessage.textContent = progress.message || "Processing audit modules...";
}

function showError(msg) {
  hideRunningState();
  errorSection.classList.remove("hidden");
  errorMessage.textContent = msg;
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

    let evidenceStr = "";
    if (typeof finding.evidence === "string") {
      evidenceStr = finding.evidence;
    } else {
      evidenceStr = JSON.stringify(finding.evidence, null, 2);
    }

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
        <div class="finding-evidence">${escapeHtml(evidenceStr)}</div>
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