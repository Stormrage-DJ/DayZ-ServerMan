// Overview workspace: server state, lifecycle actions, and profile selection.
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

// Map one server status into a label, style class, and explanation.
function statusPresentation(status) {
  const values = {
    STOPPED: ["Stopped", "status-normal", "The configured server process is not running."],
    RUNNING_MANAGED: ["Running", "status-normal", "DayZ is running under this manager's verified control."],
    RUNNING_EXTERNAL: ["External process", "status-warning", "DayZ is running, but this manager does not own it."],
    STARTING: ["Starting", "status-busy", "The manager is starting DayZ."],
    STOPPING: ["Stopping", "status-busy", "DayZ is saving and closing."],
    AMBIGUOUS: ["Ambiguous", "status-error", "More than one matching DayZ process was found."],
    UNKNOWN: ["Unknown", "status-error", "The DayZ process state could not be proven."],
  };
  // Fall back to an explicit unknown state when no mapping exists.
  return values[status?.state] || ["Unknown", "status-error", "No authoritative state is available."];
}

// Return the profile that matches the overview selection, or null.
function selectedOverviewProfile() {
  return overviewState.profiles.find(
    (profile) => profile.profile_id === overviewState.selectedProfileId,
  ) || null;
}

// Build one metric card with a label, value, and detail line.
function overviewMetric(label, value, detail) {
  const card = overviewNode("article", "metric-card");
  card.append(overviewNode("small", "", label));
  card.append(overviewNode("strong", "metric-value", value));
  card.append(overviewNode("p", "", detail));
  return card;
}

// Build a notice panel with a title and message.
function overviewNotice(title, message, kind = "warning") {
  const notice = overviewNode("section", `notice notice-${kind}`);
  notice.append(overviewNode("h2", "", title), overviewNode("p", "", message));
  return notice;
}

// Build the overview workspace for the selected profile and server state.
function renderOverview() {
  const region = document.getElementById("content-region");
  region.textContent = "";
  const [stateLabel, stateClass, stateDetail] = statusPresentation(overviewState.status);
  const profile = selectedOverviewProfile();
  const settings = overviewState.snapshot.settings;
  const configured = Boolean(settings.dayz_root && settings.dayz_executable);

  // Build the profile selection bar first so the choice stays visible.
  const selection = overviewNode("section", "panel overview-profile-bar");
  const selectionCopy = overviewNode("div", "overview-profile-copy");
  selectionCopy.append(overviewNode("strong", "", "Server profile"));
  selectionCopy.append(overviewNode("small", "", "Select the server this workspace controls."));
  const profileLabel = overviewNode("label", "overview-profile-select");
  profileLabel.append(overviewNode("span", "sr-only", "Server profile"));
  const selector = document.createElement("select");
  selector.id = "overview-profile";
  // Offer an explicit unavailable choice when no profiles exist.
  if (!overviewState.profiles.length) {
    const option = overviewNode("option", "", "No profiles available");
    option.value = ""; selector.append(option);
  }
  overviewState.profiles.forEach((item) => {
    const option = overviewNode("option", "", item.display_name);
    option.value = item.profile_id;
    option.selected = item.profile_id === overviewState.selectedProfileId;
    selector.append(option);
  });
  selector.disabled = !overviewState.profiles.length;
  selector.addEventListener("change", () => window.ServerManProfileContext.select(selector.value));
  profileLabel.append(selector); selection.append(selectionCopy, profileLabel); region.append(selection);

  // Surface recovery, setup, or profile blockers above the live state.
  if (overviewState.snapshot.mutation_block) {
    region.append(overviewNotice(
      "Recovery required",
      String(overviewState.snapshot.mutation_block),
      "recovery",
    ));
  } else if (!configured) {
    region.append(overviewNotice(
      "Complete application setup",
      "Configure the DayZ installation and executable in Settings before starting a server.",
    ));
  } else if (!profile) {
    region.append(overviewNotice(
      "Create a server profile",
      "At least one profile is required before DayZ can be started.",
    ));
  }

  const panel = overviewNode("section", "panel overview-panel");
  const heading = overviewNode("div", "panel-heading");
  const headingCopy = overviewNode("div");
  headingCopy.append(overviewNode("span", "section-label", "Live state"));
  headingCopy.append(overviewNode("h2", "", "Server at a glance"));
  heading.append(headingCopy, overviewNode("span", `status-label ${stateClass}`, stateLabel));
  panel.append(heading);
  const metrics = overviewNode("div", "metric-grid");
  metrics.append(
    overviewMetric("Server state", stateLabel, stateDetail),
    overviewMetric(
      "Managed process",
      overviewState.status.process_id ? `PID ${overviewState.status.process_id}` : "None",
      overviewState.status.diagnostic_code || "No process diagnostic is active.",
    ),
    overviewMetric(
      "Recent operations",
      String(overviewState.snapshot.operations.length),
      "Manager operations retained in the current history.",
    ),
  );
  panel.append(metrics);

  const controls = overviewNode("section", "panel overview-controls");
  const controlsHeading = overviewNode("div", "panel-heading");
  controlsHeading.append(overviewNode("h2", "", "Server control"));
  controls.append(controlsHeading);

  // Build the lifecycle controls and gate each action on the proven state.
  const actions = overviewNode("div", "action-row");
  const blocked = Boolean(overviewState.snapshot.mutation_block);
  const start = overviewAction("Start server", "start", "button button-primary");
  const stop = overviewAction("Save & Stop", "stop", "button");
  const restart = overviewAction("Save & Restart", "restart", "button");
  const backupChoice = window.ServerManOverviewBackup.choice(profile);
  start.disabled = blocked || !configured || !profile || overviewState.status.state !== "STOPPED";
  stop.disabled = blocked || overviewState.status.state !== "RUNNING_MANAGED";
  restart.disabled = blocked || !profile || overviewState.status.state !== "RUNNING_MANAGED";
  actions.append(backupChoice, start, stop, restart);
  controls.append(actions, window.ServerManOverviewSchedule.create(profile));

  const details = overviewNode("section", "panel");
  details.append(overviewNode("h2", "", "Active locations"));
  const list = overviewNode("dl", "detail-list");
  [
    ["DayZ installation", settings.dayz_root || "Not configured"],
    ["DayZ executable", settings.dayz_executable || "Not configured"],
    ["Backup destination", settings.custom_backup_root || overviewState.snapshot.portable_backup_root],
  ].forEach(([term, description]) => {
    list.append(overviewNode("dt", "", term), overviewNode("dd", "", description));
  });
  // Finish with the active locations and release the busy state.
  details.append(list);
  region.append(panel, controls, details);
  region.setAttribute("aria-busy", "false");
}

// Build one lifecycle button wired to the shared confirmation.
function overviewAction(label, action, className) {
  const button = overviewNode("button", className, label);
  button.type = "button";
  button.addEventListener("click", () => confirmLifecycleAction(action));
  return button;
}

// Ask for confirmation of a lifecycle action and resolve to the choice.
function lifecycleConfirmation(action, backupAfterStop) {
  const labels = {
    start: ["Start DayZ server?", "Start the selected profile now.", "Start server"],
    stop: ["Save and stop DayZ?", "Request a graceful save and wait for DayZ to close.", "Save & Stop"],
    restart: ["Save and restart DayZ?", "Save and stop the managed process, then start the selected profile.", "Save & Restart"],
  };
  return new Promise((resolve) => {
    const returnFocus = document.activeElement;
    // Describe the action, its consequence, and the confirm label.
    const [title, baseMessage, confirmText] = labels[action];
    const message = backupAfterStop && action !== "start"
      ? `${baseMessage} A verified backup will be created after DayZ stops.` : baseMessage;
    // Build the dialog and its focus-trapping controls.
    const dialog = overviewNode("section", "panel lifecycle-confirmation");
    dialog.id = "lifecycle-confirmation";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lifecycle-confirmation-title");
    const heading = overviewNode("h2", "", title);
    heading.id = "lifecycle-confirmation-title";
    const actions = overviewNode("div", "action-row");
    const cancel = overviewNode("button", "button", "Cancel");
    const confirm = overviewNode("button", "button button-primary", confirmText);
    // Make the rest of the page inert while the dialog is open.
    const inerted = [...document.body.children]
      .filter((element) => element !== dialog)
      .map((element) => ({ element, inert: element.inert }));
    // Restore the page and resolve the promise when the dialog closes.
    const finish = (accepted) => {
      inerted.forEach(({ element, inert }) => { element.inert = inert; });
      dialog.remove();
      if (returnFocus?.isConnected) returnFocus.focus();
      resolve(accepted);
    };
    cancel.type = "button"; confirm.type = "button";
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
      if (event.key !== "Tab") return;
      if (event.shiftKey && document.activeElement === cancel) {
        event.preventDefault(); confirm.focus();
      } else if (!event.shiftKey && document.activeElement === confirm) {
        event.preventDefault(); cancel.focus();
      }
    });
    actions.append(cancel, confirm);
    dialog.append(heading, overviewNode("p", "", message), actions);
    document.body.append(dialog);
    inerted.forEach(({ element }) => { element.inert = true; });
    cancel.focus();
  });
}

// Confirm and submit the requested lifecycle action for the selected profile.
async function confirmLifecycleAction(action) {
  const profile = selectedOverviewProfile();
  const backupAfterStop = window.ServerManOverviewBackup.enabled(profile);
  // Stop when the confirmation dialog is declined.
  if (!await lifecycleConfirmation(action, backupAfterStop)) return;
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
  // Track the queued operation and show its initial state.
  overviewState.pendingOperationId = result.value.operation_id;
  const operation = await window.pywebview.api.get_operation(result.value.operation_id);
  if (!operation.success) return window.ServerManUi.renderHostError(operation);
  window.ServerManUi.renderOperation(operation.value);
}

// Load the overview state and render it for the selected profile.
async function openOverview(snapshot = null) {
  const generation = ++overviewState.generation;
  const workspace = window.ServerManWorkspace.capture("overview");
  window.ServerManUi.renderLoading("Loading server state", "Reconciling DayZ and manager state.");
  try {
    // Load the snapshot, profiles, and server status together.
    const [snapshotResult, profilesResult, statusResult] = await Promise.all([
      snapshot ? Promise.resolve({ success: true, value: snapshot })
        : window.pywebview.api.get_application_snapshot(),
      window.pywebview.api.list_profiles(),
      window.pywebview.api.get_server_status(),
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

// React to tracked lifecycle and scheduled server operations.
function overviewOperationFinished(operation) {
  const tracked = operation.operation_id === overviewState.pendingOperationId;
  const scheduledLifecycle = ["STOP_SERVER", "RESTART_SERVER"].includes(operation.kind);
  if (!tracked && !scheduledLifecycle) return false;
  const terminal = !["QUEUED", "RUNNING", "CANCELLING"].includes(operation.state);
  // Keep rendering progress while the operation is still active.
  if (!terminal) {
    window.ServerManUi.renderOperation(operation);
    return true;
  }
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
});
