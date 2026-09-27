/**
 * WebLens Background Service (Manifest V3 - Cross-Browser Chrome & Firefox)
 * Manages Side Panel / Sidebar behavior and native desktop notifications.
 */

const browserApi = typeof browser !== "undefined" ? browser : chrome;

browserApi.runtime.onInstalled.addListener(() => {
  // Chrome: Set side panel to open on toolbar action click
  if (browserApi.sidePanel && browserApi.sidePanel.setPanelBehavior) {
    browserApi.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err) => {
      console.warn("[WebLens] Failed to set side panel behavior:", err);
    });
  }
  console.log("[WebLens] Extension installed (Cross-browser mode ready).");
});

// Firefox: Toggle sidebar on toolbar action click
if (browserApi.action && browserApi.action.onClicked) {
  browserApi.action.onClicked.addListener(async (tab) => {
    try {
      if (browserApi.sidebarAction && browserApi.sidebarAction.toggle) {
        await browserApi.sidebarAction.toggle();
      } else if (browserApi.sidePanel && browserApi.sidePanel.open && tab) {
        await browserApi.sidePanel.open({ tabId: tab.id });
      }
    } catch (err) {
      console.warn("[WebLens] Action click handler:", err);
    }
  });
}

// Listener for desktop notification requests from the UI
browserApi.runtime.onMessage.addListener((message) => {
  if (message.type === "NOTIFY_COMPLETION") {
    if (browserApi.notifications) {
      browserApi.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: message.title || "WebLens Audit Complete",
        message: (message.message || "").substring(0, 150),
        priority: 1,
      });
    }
  }
});