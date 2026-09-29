// Owns the desktop shell lifecycle, section routing, and application-host event polling.
"use strict";

// Tracks the active section, event cursor, and host readiness for the shell.
const shellState = {
  section: "overview",
  sessionId: null,
  cursor: 0,
  hostReady: false,
  // Timer handle for the next queued host event poll.
  pollTimer: null,
};

// Mark the host unreachable and show the shared host error notice.
function renderHostError(result) {
  shellState.hostReady = false;
  window.ServerManUi.renderHostError(result);
}

// Load a fresh application snapshot and open the workspace section that requested it.
async function loadSnapshot() {
  // Bind this load to the active workspace so late host responses are ignored.
  const workspace = window.ServerManWorkspace.activate(shellState.section);
  try {
    const result = await window.pywebview.api.get_application_snapshot();
    if (!window.ServerManWorkspace.isActive(workspace)) return;
    if (!result || !result.success) return renderHostError(result);
    const profileContext = await window.ServerManProfileContext.initialize();
    if (!profileContext.success) return renderHostError(profileContext);
    // Mark the host ready and adopt the new session with a fresh event cursor.
    shellState.hostReady = true;
    shellState.sessionId = result.value.operation_session_id;
    shellState.cursor = 0;
    document.body.dataset.shellReady = "true";
    window.ServerManUi.setHostStatus("Application host connected", "", "host");
    window.ServerManUi.clearHostStatus("workspace");
    // Prefer the newest queued, running, or cancelling operation for the status view.
    const operations = Array.isArray(result.value.operations) ? result.value.operations : [];
    const active = [...operations].reverse().find((operation) =>
      ["QUEUED", "RUNNING", "CANCELLING"].includes(operation.state));
    // Preserve unsaved edits, otherwise show the active operation or reopen the section.
    if (["configuration", "profiles", "settings"].includes(shellState.section)
      && window.ServerManTransitions.hasUnsavedChanges()) {
      window.ServerManUi.setHostStatus("Unsaved edits preserved", "is-warning", "workspace");
    } else if (active) {
      window.ServerManUi.syncOperationStatus(active);
      if (shellState.section === "overview") window.ServerManOverview.track(active.operation_id);
      window.ServerManUi.renderOperation(active);
    }
    else if (shellState.section === "overview") window.ServerManOverview.open(result.value);
    else if (shellState.section === "profiles") window.ServerManProfiles.open(window.ServerManProfileContext.selectedId());
    else if (shellState.section === "configuration") window.ServerManConfiguration.open();
    else if (shellState.section === "tweaks") window.ServerManTweaks.open();
    else if (shellState.section === "backups") window.ServerManBackups.open();
    else if (shellState.section === "mods") window.ServerManMods.open();
    else if (shellState.section === "settings") window.ServerManSettings.open(result.value);
    else if (shellState.section === "logs") window.ServerManLogs.open();
    schedulePoll();
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) renderHostError(null);
  }
}

// Poll for host operation events and refresh only the visible workspace section.
async function pollEvents() {
  if (!shellState.hostReady || document.hidden) return;
  // Capture the current section so stale responses cannot touch a newer view.
  const workspace = window.ServerManWorkspace.capture(shellState.section);
  try {
    const result = await window.pywebview.api.read_operation_events(shellState.cursor, 100);
    if (!window.ServerManWorkspace.isActive(workspace)) {
      schedulePoll();
      return;
    }
    // Rebuild the whole snapshot when the host can no longer replay old events.
    if (!result.success && result.error && result.error.code === "EVENT_CURSOR_EXPIRED") {
      await loadSnapshot();
      return;
    }
    if (!result.success) return renderHostError(result);
    // Reload when the host session changed under the shell.
    if (result.value.session_id !== shellState.sessionId) {
      await loadSnapshot();
      return;
    }
    // Advance the cursor so the same events are not read twice.
    shellState.cursor = result.value.next_cursor;
    // Refresh each operation mentioned by the new events.
    const operationIds = [...new Set(result.value.events.map((event) => event.operation_id))];
    for (const operationId of operationIds) {
      const operation = await window.pywebview.api.get_operation(operationId);
      if (!window.ServerManWorkspace.isActive(workspace)) {
        schedulePoll();
        return;
      }
      if (!operation.success) return renderHostError(operation);
      window.ServerManUi.syncOperationStatus(operation.value);
      // Let the visible section consume the event before the shared fallback renders it.
      const handled = shellState.section === "configuration"
        && window.ServerManConfiguration.operationFinished(operation.value)
        || shellState.section === "backups"
          && (window.ServerManRestore.operationFinished(operation.value)
            || window.ServerManBackups.operationFinished(operation.value));
      const profileHandled = shellState.section === "profiles"
        && window.ServerManProfiles.operationFinished(operation.value);
      const migrationHandled = shellState.section === "settings"
        && window.ServerManMigration.operationFinished(operation.value);
      const settingsHandled = shellState.section === "settings"
        && window.ServerManSettings.operationFinished(operation.value);
      const modsHandled = shellState.section === "mods"
        && window.ServerManMods.operationFinished(operation.value);
      const tweaksHandled = shellState.section === "tweaks"
        && window.ServerManTweaks.operationFinished(operation.value);
      const overviewHandled = shellState.section === "overview"
        && window.ServerManOverview.operationFinished(operation.value);
      if (!handled && !profileHandled && !migrationHandled && !settingsHandled
        && !modsHandled && !tweaksHandled && !overviewHandled && !(shellState.section === "configuration"
          && window.ServerManTransitions.hasUnsavedChanges())) {
        window.ServerManUi.renderOperation(operation.value);
      }
    }
    // Keep the log view current while it is open.
    if (shellState.section === "logs") window.ServerManLogs.refresh(false);
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) {
      window.ServerManUi.setHostStatus("Application host interrupted", "is-error", "host");
    }
  }
  schedulePoll();
}

// Queue the next event poll after a short delay.
function schedulePoll() {
  window.clearTimeout(shellState.pollTimer);
  shellState.pollTimer = window.setTimeout(pollEvents, 1500);
}

// Activate a section, refresh navigation state, and open its workspace.
function commitSection(section) {
  window.ServerManWorkspace.activate(section);
  shellState.section = section;
  // Mark the selected navigation button as the current page.
  document.querySelectorAll(".nav-item").forEach((button) => {
    if (button.dataset.section === section) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  // Swap the page heading to the selected section and clear stale notices.
  const [title, description] = window.ServerManUi.sectionCopy[section];
  window.ServerManUi.clearHostStatus("workspace");
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-description").textContent = description;
  closeDrawer();
  // Open the workspace for the new section once the host is ready.
  if (shellState.hostReady && section === "overview") {
    window.ServerManOverview.open();
  } else if (shellState.hostReady && section === "configuration") {
    window.ServerManConfiguration.open();
  } else if (shellState.hostReady && section === "tweaks") {
    window.ServerManTweaks.open();
  } else if (shellState.hostReady && section === "profiles") {
    window.ServerManProfiles.open(window.ServerManProfileContext.selectedId());
  } else if (shellState.hostReady && section === "backups") {
    window.ServerManBackups.open();
  } else if (shellState.hostReady && section === "mods") {
    window.ServerManMods.open();
  } else if (shellState.hostReady && section === "settings") {
    window.ServerManSettings.open();
  } else if (shellState.hostReady && section === "logs") {
    window.ServerManLogs.open();
  }
}

// Switch sections through the unsaved-change guard.
function setSection(section) {
  if (section === shellState.section) return;
  // Let the guard confirm before any unsaved edits are discarded.
  window.ServerManTransitions.requestTransition(
    "Leaving this section will discard unsaved configuration edits.",
    () => commitSection(section),
  );
}

// Close the navigation drawer and reset its toggle state.
function closeDrawer() {
  document.body.classList.remove("drawer-open");
  document.getElementById("menu-button").setAttribute("aria-expanded", "false");
  document.getElementById("drawer-scrim").hidden = true;
}

// Wire the one-time navigation, drawer, cancellation, and lifecycle handlers.
function initializeInteractions() {
  // Route navigation clicks through the guarded section switch.
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setSection(button.dataset.section));
  });
  // Toggle the drawer and keep its toggle state in sync.
  document.getElementById("menu-button").addEventListener("click", () => {
    const open = !document.body.classList.contains("drawer-open");
    document.body.classList.toggle("drawer-open", open);
    document.getElementById("menu-button").setAttribute("aria-expanded", String(open));
    document.getElementById("drawer-scrim").hidden = !open;
  });
  document.getElementById("drawer-scrim").addEventListener("click", closeDrawer);
  // Handle cancellation requests from any operation card in the content area.
  document.getElementById("content-region").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-cancel-operation]");
    if (!button) return;
    const workspace = window.ServerManWorkspace.capture(shellState.section);
    button.disabled = true;
    const result = await window.pywebview.api.request_operation_cancellation(
      button.dataset.cancelOperation,
    );
    if (!window.ServerManWorkspace.isActive(workspace)) return;
    if (!result.success) renderHostError(result);
    else window.ServerManUi.renderOperation(result.value);
  });
  // Resume polling as soon as the window becomes visible again.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && shellState.hostReady) pollEvents();
  });
  // Discard stale workspace captures and warn when unsaved edits exist.
  window.addEventListener("beforeunload", (event) => {
    window.ServerManWorkspace.invalidate();
    if (!window.ServerManTransitions.hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

// Start the shell once the page structure is ready.
document.addEventListener("DOMContentLoaded", initializeInteractions, { once: true });
// Reopen the active section when the shared profile selection changes.
document.addEventListener("serverman:profile-change", () => {
  if (!shellState.hostReady) return;
  if (shellState.section === "overview") window.ServerManOverview.open();
  else if (shellState.section === "profiles") window.ServerManProfiles.open(window.ServerManProfileContext.selectedId());
  else if (shellState.section === "configuration") window.ServerManConfiguration.open();
  else if (shellState.section === "tweaks") window.ServerManTweaks.open();
  else if (shellState.section === "backups") window.ServerManBackups.open();
  else if (shellState.section === "mods") window.ServerManMods.open();
});
// Load the first snapshot when the application host becomes available.
window.addEventListener("pywebviewready", loadSnapshot, { once: true });
