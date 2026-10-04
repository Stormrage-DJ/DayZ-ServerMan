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
function reportHostFailure(result) {
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
    if (!result || !result.success) return reportHostFailure(result);
    const profileContext = await window.ServerManProfileContext.initialize();
    if (!profileContext.success) return reportHostFailure(profileContext);
    // Mark the host ready and adopt the new session with a fresh event cursor.
    shellState.hostReady = true;
    shellState.sessionId = result.value.operation_session_id;
    shellState.cursor = 0;
    document.body.dataset.shellReady = "true";
    window.ServerManUi.setHostStatus("Application host connected", "", "host");
    window.ServerManUi.clearHostStatus("workspace");
    // Rebuild the operation bar from the snapshot; the page stays visible below it.
    const operations = Array.isArray(result.value.operations) ? result.value.operations : [];
    window.ServerManOperationBar.reset(operations);
    const active = [...operations].reverse().find((operation) =>
      ["QUEUED", "RUNNING", "CANCELLING"].includes(operation.state));
    // Preserve unsaved edits, otherwise reopen the section and let it follow the newest active operation.
    const section = window.ServerManSections.get(shellState.section);
    if (section.keepsUnsavedEdits && window.ServerManTransitions.hasUnsavedChanges()) {
      window.ServerManUi.setHostStatus("Unsaved edits preserved", "is-warning", "workspace");
    } else {
      if (active) section.trackOperation?.(active);
      window.ServerManSections.open(shellState.section, result.value);
    }
    schedulePoll();
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) reportHostFailure(null);
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
    if (!result.success) return reportHostFailure(result);
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
      if (!operation.success) return reportHostFailure(operation);
      // Update the bar and the busy state first, then let the visible section consume the event.
      window.ServerManOperationBar.sync(operation.value);
      window.ServerManSections.operationFinished(shellState.section, operation.value);
      // Announce a result only after the page had the chance to announce it itself.
      window.ServerManOperationBar.settle(operation.value);
    }
    // Refresh externally changed server state while Overview is visible.
    if (shellState.section === "overview") await window.ServerManOverviewStatus.refresh();
    // Keep the log view current while it is open.
    if (shellState.section === "logs") window.ServerManLogs.refresh(false);
    // Keep the update state current while Mods is visible.
    if (shellState.section === "mods") await window.ServerManUpdateStatus.poll();
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
  const { title, description } = window.ServerManSections.get(section);
  window.ServerManUi.clearHostStatus("workspace");
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-description").textContent = description;
  closeDrawer();
  // Open the workspace for the new section once the host is ready.
  if (shellState.hostReady) window.ServerManSections.open(section);
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

// Wire the one-time navigation, drawer, and lifecycle handlers.
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
  if (shellState.hostReady) window.ServerManSections.profileChanged(shellState.section);
});
// Load the first snapshot when the application host becomes available.
window.addEventListener("pywebviewready", loadSnapshot, { once: true });
