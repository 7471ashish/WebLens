/**
 * WebLens Options / Settings Controller
 */

const DEFAULT_BACKEND_URL = "http://localhost:8000";

const groqKeyInput = document.getElementById("groq-key-input");
const togglePwdBtn = document.getElementById("toggle-pwd-btn");
const backendUrlInput = document.getElementById("backend-url-input");
const testConnectionBtn = document.getElementById("test-connection-btn");
const saveBtn = document.getElementById("save-btn");
const connectionStatus = document.getElementById("connection-status");
const toast = document.getElementById("toast");

document.addEventListener("DOMContentLoaded", async () => {
  // Load saved preferences
  const { groq_api_key, backend_url } = await chrome.storage.local.get([
    "groq_api_key",
    "backend_url",
  ]);

  if (groq_api_key) {
    groqKeyInput.value = groq_api_key;
  }
  backendUrlInput.value = backend_url || DEFAULT_BACKEND_URL;

  // Toggle Password Masking
  togglePwdBtn.addEventListener("click", () => {
    const isPassword = groqKeyInput.type === "password";
    groqKeyInput.type = isPassword ? "text" : "password";
  });

  // Test Backend Connection
  testConnectionBtn.addEventListener("click", async () => {
    const targetUrl = (backendUrlInput.value.trim() || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
    connectionStatus.className = "status-badge";
    connectionStatus.textContent = "Connecting to backend...";
    connectionStatus.classList.remove("hidden");

    try {
      const resp = await fetch(`${targetUrl}/health`, { method: "GET" });
      if (resp.ok) {
        const data = await resp.json();
        connectionStatus.className = "status-badge success";
        connectionStatus.textContent = `Connected! Backend ready (Playwright: ${data.playwright_ready ? "Ready" : "Offline fallback"}, Slots: ${data.max_concurrent_jobs})`;
      } else {
        throw new Error(`HTTP ${resp.status}`);
      }
    } catch (err) {
      connectionStatus.className = "status-badge error";
      connectionStatus.textContent = `Connection failed: ${err.message}. Ensure backend is running.`;
    }
  });

  // Save Settings
  saveBtn.addEventListener("click", async () => {
    const key = groqKeyInput.value.trim();
    const url = (backendUrlInput.value.trim() || DEFAULT_BACKEND_URL).replace(/\/+$/, "");

    await chrome.storage.local.set({
      groq_api_key: key,
      backend_url: url,
    });

    showToast("Settings saved successfully!");
  });
});

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => {
    toast.classList.remove("show");
  }, 2500);
}
