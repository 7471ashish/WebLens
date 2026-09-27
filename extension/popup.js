/**
 * WebLens Popup Launcher Controller (Cross-Browser Chrome & Firefox)
 */

document.getElementById("open-panel-btn").addEventListener("click", async () => {
  const browserApi = typeof browser !== "undefined" ? browser : chrome;
  try {
    if (browserApi.sidebarAction && browserApi.sidebarAction.open) {
      await browserApi.sidebarAction.open();
    } else if (browserApi.sidePanel && browserApi.sidePanel.open) {
      const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
      if (tab) await browserApi.sidePanel.open({ tabId: tab.id });
    }
  } catch (err) {
    console.warn("[WebLens] Failed to open panel/sidebar from popup:", err);
  }
  window.close();
});