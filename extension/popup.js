/**
 * WebLens Popup Launcher Controller
 */

document.getElementById("open-panel-btn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && chrome.sidePanel && chrome.sidePanel.open) {
    await chrome.sidePanel.open({ tabId: tab.id });
  }
  window.close();
});

document.getElementById("open-options-btn").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
  window.close();
});
