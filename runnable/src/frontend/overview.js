// Overview workspace: notices, the server panel, and the cards for the profile selected in the sidebar.
"use strict";

// Tracks the last snapshot, profiles, server status, and pending lifecycle operation.
const overviewState = {
  generation: 0,
  snapshot: null,
  profiles: [],
  status: null,
  selectedProfileId: null,
  pendingOperationId: null,
};

// Create one interface element through the shared element helper.
function overviewNode(tag, className = "", text = "") {
  return window.ServerManUi.element(tag, className, text);
}

// Return the profile that matches the overview selection, or null.
function selectedOverviewProfile() {
  return overviewState.profiles.find(
    (profile) => profile.profile_id === overviewState.selectedProfileId,
  ) || null;
}

// Build a notice panel with a title and message.
function overviewNotice(title, message, kind = "warning") {
  const notice = overviewNode("section", `notice notice-${kind}`);
  notice.append(overviewNode("h2", "", title), overviewNode("p", "", message));
  return notice;
}

// Build the buttons of a notice; each opens a section through the guarded section switch.
function overviewNoticeActions(targets) {
  const row = overviewNode("div", "action-row");
  targets.forEach(([label, section]) => {
    const button = overviewNode("button", "button", label); button.type = "button";
    // The identifier lets a redraw give the focus back to the same button.
    button.id = `overview-notice-${section}`;
    button.addEventListener("click", () => setSection(section));
    row.append(button);
  });
  return row;
}

// Collect what the server panel and the notice depend on: state, profile, setup, and a recovery block.
function overviewContext() {
  const settings = overviewState.snapshot.settings;
  return {
    status: overviewState.status,
    profile: selectedOverviewProfile(),
    configured: Boolean(settings.dayz_root && settings.dayz_executable),
    blocked: Boolean(overviewState.snapshot.mutation_block),
  };
}

// Build the one notice of the page, in the order recovery, setup, first profile, another profile running.
function renderOverviewNotice(context) {
  if (context.blocked) {
    return overviewNotice("Recovery required",
      window.ServerManOperationMessages.recoveryNotice(overviewState.snapshot.mutation_block,
        overviewState.snapshot.mutation_block_owner), "recovery");
  }
  if (!context.configured) {
    const notice = overviewNotice("Complete application setup",
      "Configure the DayZ installation and executable in Settings before starting a server.");
    notice.append(overviewNoticeActions([["Open Settings", "settings"]]));
    return notice;
  }
  if (!context.profile) {
    const notice = overviewNotice("Create a server profile",
      "At least one profile is required before DayZ can be started.");
    // Both ways to the first profile stay one press away.
    notice.append(overviewNoticeActions([
      ["Create profile", "profiles"], ["Restore profile from backup\u2026", "backups"],
    ]));
    return notice;
  }
  // A stop or restart acts with the selected profile, so the page says which profile runs.
  const other = window.ServerManServerState.otherRunningProfile();
  if (!other) return null;
  const notice = overviewNotice(`The running server was started with ${other.display_name}`,
    `Select ${other.display_name} in the sidebar before you stop or restart it.`);
  notice.lastElementChild.id = "overview-running-notice";
  return notice;
}

// Give the focus back to the control that held it before a redraw; a control that is now off leaves it on
// the panel heading, so the focus stays in the panel (QF-032).
function restoreOverviewFocus(focusedId) {
  if (!focusedId) return;
  const control = document.getElementById(focusedId);
  const target = control && !control.disabled ? control : document.getElementById("overview-server-title");
  target?.focus({ preventScroll: true });
}

// Draw the notice and the server panel; the cards below them keep their state.
function renderOverviewTop() {
  const top = document.getElementById("overview-top");
  if (!top) return;
  const context = overviewContext();
  // Keep the process details open and the focus where it was across a redraw that the operator did not ask for.
  const open = document.getElementById("overview-process")?.open === true;
  const active = document.activeElement;
  const focusedId = active && top.contains(active) ? active.id : "";
  const notice = renderOverviewNotice(context);
  top.replaceChildren(...(notice ? [notice] : []));
  top.append(window.ServerManOverviewServer.render(context));
  document.getElementById("overview-process").open = open;
  restoreOverviewFocus(focusedId);
}

// Build the overview workspace for the selected profile and server state.
function renderOverview() {
  const region = document.getElementById("content-region");
  const profile = selectedOverviewProfile();
  const top = overviewNode("div", "overview-top"); top.id = "overview-top";
  // Three cards follow the server panel: updates, the newest backup, and the daily schedule.
  const cards = overviewNode("div", "overview-cards");
  cards.append(window.ServerManOverviewCards.updates(), window.ServerManOverviewCards.backup(profile),
    window.ServerManOverviewSchedule.create(profile));
  region.replaceChildren(top, cards);
  renderOverviewTop();
  region.setAttribute("aria-busy", "false");
}

// Confirm and submit the requested lifecycle action for the selected profile.
async function confirmLifecycleAction(action) {
  const profile = selectedOverviewProfile();
  // The lock is checked again at the press: the running profile may have changed since the page was drawn.
  if (action !== "start" && window.ServerManServerState.lockReason()) return;
  const backupAfterStop = window.ServerManOverviewBackup.enabled(profile);
  // Stop when the confirmation dialog is declined.
  if (!await window.ServerManLifecycleDialog.confirm(action, backupAfterStop)) return;
  const settingsRevision = overviewState.snapshot.settings.revision;
  let result;
  // Submit the revision-bound request for the chosen action.
  if (action === "start") {
    result = await window.pywebview.api.start_server(
      profile.profile_id, profile.revision, settingsRevision,
    );
  } else if (action === "restart") {
    result = await window.pywebview.api.restart_server(
      profile.profile_id, profile.revision, settingsRevision, backupAfterStop,
    );
  } else {
    result = await window.pywebview.api.stop_server(
      profile.profile_id, profile.revision, settingsRevision, backupAfterStop,
    );
  }
  if (!result.success) return window.ServerManUi.renderHostError(result);
  // Track the queued operation and hand its first record to the operation bar; the page stays.
  overviewState.pendingOperationId = result.value.operation_id;
  const operation = await window.pywebview.api.get_operation(result.value.operation_id);
  if (!operation.success) return window.ServerManUi.renderHostError(operation);
  window.ServerManOperationBar?.sync(operation.value);
}

// Load the overview state and render it for the selected profile.
async function openOverview(snapshot = null) {
  const generation = ++overviewState.generation;
  const workspace = window.ServerManWorkspace.capture("overview");
  // While this load runs, a state change event must not redraw the page from the earlier data.
  overviewState.status = null;
  window.ServerManUi.renderLoading("Loading server state", "Reconciling DayZ and manager state.");
  try {
    // Load the snapshot, profiles, and server status together.
    const [snapshotResult, profilesResult, statusResult] = await Promise.all([
      snapshot ? Promise.resolve({ success: true, value: snapshot })
        : window.pywebview.api.get_application_snapshot(),
      window.pywebview.api.list_profiles(),
      // The one read of this page goes through the shared state, so the sidebar sees it too.
      window.ServerManServerState.read(),
    ]);
    // Ignore the batch when the workspace or generation moved on.
    if (generation !== overviewState.generation
      || !window.ServerManWorkspace.isActive(workspace)) return;
    const failed = [snapshotResult, profilesResult, statusResult].find((item) => !item.success);
    if (failed) return window.ServerManUi.renderHostError(failed);
    overviewState.snapshot = snapshotResult.value;
    overviewState.profiles = profilesResult.value;
    overviewState.status = statusResult.value;
    // Prefer the remembered profile and fall back to the first available one.
    const remembered = window.ServerManProfileContext.selectedId();
    overviewState.selectedProfileId = overviewState.profiles.some(
      (item) => item.profile_id === remembered,
    ) ? remembered : overviewState.profiles[0]?.profile_id || null;
    renderOverview();
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) window.ServerManUi.renderHostError(null);
  }
}

// React to tracked lifecycle and scheduled server operations, and to a finished backup.
function overviewOperationFinished(operation) {
  const tracked = operation.operation_id === overviewState.pendingOperationId;
  // A backup that ended elsewhere changes only the "Last backup" card; the event is not consumed here.
  if (!tracked && operation.kind === "CREATE_BACKUP"
      && !["QUEUED", "RUNNING", "CANCELLING"].includes(operation.state)) {
    void window.ServerManOverviewCards.reloadBackup(selectedOverviewProfile());
    return false;
  }
  const scheduledLifecycle = ["STOP_SERVER", "RESTART_SERVER"].includes(operation.kind);
  if (!tracked && !scheduledLifecycle) return false;
  const terminal = !["QUEUED", "RUNNING", "CANCELLING"].includes(operation.state);
  // The operation bar shows the progress; the server state follows the status poll.
  if (!terminal) return true;
  // Reload the overview when the tracked operation finishes.
  if (tracked) overviewState.pendingOperationId = null;
  openOverview();
  return true;
}

// Publish the overview workspace controls used by the shell.
window.ServerManOverview = Object.freeze({
  open: openOverview,
  operationFinished: overviewOperationFinished,
  track: (operationId) => { overviewState.pendingOperationId = operationId; },
  // A changed server state redraws the notice and the server panel only.
  refreshServer: renderOverviewTop,
});
