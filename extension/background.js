/**
 * WebLens Background Service Worker (Manifest V3)
 * Manages Side Panel behavior and native desktop notifications.
 * Polling has been completely eliminated in favor of Server-Sent Events (SSE).
 */

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err) => {
      console.warn("Failed to set side panel behavior:", err);
    });
  }
  console.log("WebLens extension installed (SSE Real-Time Stream Mode active).");
});

// Listener for desktop notification requests from the UI
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "NOTIFY_COMPLETION") {
    if (chrome.notifications) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: message.title || "WebLens Audit Complete",
        message: (message.message || "").substring(0, 150),
        priority: 1,
      });
    }
  }
});