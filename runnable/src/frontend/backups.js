// Backup workspace: profile-scoped history, confirmation, and the result of a backup creation.
"use strict";
// Tracks the loaded history, pending operation, and profile generation for this workspace.
const backupState = {
  confirmation: null, history: null,
  // Monotonic counter that discards stale history responses.
  loadSequence: 0,
  pendingOperation: null,
  // Bumped whenever the profile picker is rebuilt so old captures expire.
  profileGeneration: 0,
  profiles: [],
};
// Capture the selected profile and current workspace generation for a host request.
function captureBackupProfile() {
  const select = document.getElementById("backup-profile");
  return Object.freeze({
    generation: backupState.profileGeneration,
    profileId: select ? select.value : "",
    workspace: window.ServerManWorkspace.capture("backups"),
  });
}
// Report whether a captured token still matches the profile picker and workspace.
function isBackupProfileActive(context) {
  const select = document.getElementById("backup-profile");
  return Boolean(context && select
    && context.generation === backupState.profileGeneration
    && context.profileId === select.value
    && window.ServerManWorkspace.isActive(context.workspace));
}
// Create one interface element with an optional class and text.
function backupNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
// Show a backup failure notice with the host message or a safe fallback.
function backupError(result, fallback) {
  const notice = backupNode("div", "notice notice-error",
    window.ServerManBackupDisplay.text(window.ServerManOperationMessages.bridgeError(result, fallback)));
  // Present the failure as an alert and move focus to the notice.
  notice.setAttribute("role", "alert");
  notice.tabIndex = -1;
  document.getElementById("backup-feedback").replaceChildren(notice);
  notice.focus();
}
// Load the backup history for the selected profile.
async function loadBackupHistory(boundContext = null) {
  const context = boundContext || captureBackupProfile();
  // Bind the load to the captured profile so late responses cannot overwrite newer history.
  const sequence = ++backupState.loadSequence;
  document.getElementById("backup-history").setAttribute("aria-busy", "true");
  const result = await window.pywebview.api.list_backups(context.profileId);
  // Ignore the response when the profile or a newer load has replaced this one.
  if (!isBackupProfileActive(context) || sequence !== backupState.loadSequence) return;
  document.getElementById("backup-history").setAttribute("aria-busy", "false");
  if (!result.success) return backupError(result, "Backup history could not be verified.");
  // Reject a history that belongs to a different profile.
  if (result.value.profile_id !== context.profileId) return;
  renderBackupHistory(result.value);
}
// Close the confirmation dialog and restore the previous focus and inert state.
function closeBackupConfirmation(restoreFocus = true) {
  const confirmation = backupState.confirmation;
  if (!confirmation) return;
  confirmation.inerted.forEach(({ element, inert }) => { element.inert = inert; });
  confirmation.dialog.remove();
  backupState.confirmation = null;
  if (restoreFocus && confirmation.returnFocus.isConnected) confirmation.returnFocus.focus();
}
// Keep keyboard focus inside the confirmation dialog and close it on escape.
function containBackupConfirmationFocus(event) {
  const confirmation = backupState.confirmation;
  if (!confirmation) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeBackupConfirmation();
    return;
  }
  if (event.key !== "Tab") return;
  // Wrap focus between the first and last dialog buttons.
  const buttons = [...confirmation.dialog.querySelectorAll("button")];
  const first = buttons[0];
  const last = buttons.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
// Submit the confirmed backup request and show its queued progress.
async function confirmBackupCreation(argumentsCopy, context) {
  closeBackupConfirmation(false);
  if (!isBackupProfileActive(context)) return;
  // Disable creation while the request is in flight.
  document.getElementById("backup-create").disabled = true;
  const submitting = backupNode("div", "notice notice-busy", "Starting backup creation.");
  submitting.setAttribute("role", "status");
  submitting.tabIndex = -1;
  document.getElementById("backup-feedback").replaceChildren(submitting);
  submitting.focus();
  const result = await window.pywebview.api.create_backup(...argumentsCopy);
  if (!isBackupProfileActive(context)) return;
  if (!result.success) {
    document.getElementById("backup-create").disabled = false;
    return backupError(result, "Backup creation could not be queued.");
  }
  // Record the queued operation so terminal events refresh this workspace.
  backupState.pendingOperation = Object.freeze({
    context,
    operationId: result.value.operation_id,
  });
  // The operation bar shows the waiting state, the phase, and the progress from here on.
  document.getElementById("backup-feedback").replaceChildren();
  window.ServerManOperationBar?.adopt(result.value.operation_id);
}
// Open the reviewed confirmation dialog before a backup is created.
function showBackupConfirmation() {
  if (!backupState.history || backupState.confirmation) return;
  const context = captureBackupProfile();
  if (backupState.history.profile_id !== context.profileId) return;
  const profile = backupState.profiles.find((item) => item.profile_id === backupState.history.profile_id);
  if (!profile) return;
  // Freeze the reviewed revisions so the confirmed request cannot drift.
  const argumentsCopy = Object.freeze([
    backupState.history.profile_id,
    backupState.history.profile_revision,
    backupState.history.settings_revision,
  ]);
  // Build the alert dialog and wire its cancel and confirm actions.
  const dialog = backupNode("section", "notice notice-warning");
  dialog.id = "backup-confirmation";
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "backup-confirm-title");
  const title = backupNode("h3", "", "Create backup?");
  title.id = "backup-confirm-title";
  dialog.append(title, backupNode("p", "",
    `Server: ${profile.display_name}. Its configuration, runtime, complete mission and world persistence will be copied and verified. Mod directories are not included.`));
  const actions = backupNode("div", "action-row");
  const cancel = backupNode("button", "button", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", closeBackupConfirmation);
  const create = backupNode("button", "button button-primary", "Create backup");
  create.type = "button";
  create.addEventListener("click", () => confirmBackupCreation(argumentsCopy, context));
  window.ServerManBusy?.mark(create, true);
  actions.append(cancel, create);
  dialog.append(actions);
  dialog.addEventListener("keydown", containBackupConfirmationFocus);
  // Make the rest of the page inert while the dialog is open.
  const inerted = [...document.body.children]
    .filter((element) => !element.hasAttribute("data-announcer"))
    .map((element) => ({ element, inert: element.inert }));
  document.body.append(dialog);
  backupState.confirmation = Object.freeze({
    context,
    dialog,
    inerted: Object.freeze(inerted),
    returnFocus: document.activeElement,
  });
  inerted.forEach(({ element }) => { element.inert = true; });
  cancel.focus();
}
// Build the backup panel, wire the profile picker, and start the first load.
function renderBackupWorkspace(profiles) {
  backupState.profiles = profiles;
  // Expire old history and captures for the new profile set.
  backupState.history = null;
  backupState.profileGeneration += 1;
  const panel = backupNode("section", "panel backup-panel");
  const heading = backupNode("div", "panel-heading");
  heading.append(backupNode("h2", "", "Create backup"),
    backupNode("span", "status-label status-normal", "Manual"));
  const profileLabel = backupNode("label", "", "Profile");
  const select = document.createElement("select");
  select.id = "backup-profile";
  // Fill the picker with each profile, preferring the shared selection.
  profiles.forEach((profile) => {
    const option = backupNode("option", "", profile.display_name);
    option.value = profile.profile_id; option.selected = profile.profile_id === window.ServerManProfileContext?.selectedId?.();
    select.append(option);
  });
  profileLabel.append(select);
  const destination = backupNode("p", "configuration-path", "Verifying destination…");
  destination.id = "backup-destination";
  const feedback = backupNode("div", "configuration-feedback");
  feedback.id = "backup-feedback";
  const create = backupNode("button", "button button-primary", "Create backup");
  create.id = "backup-create";
  create.type = "button";
  create.disabled = true;
  create.addEventListener("click", showBackupConfirmation);
  window.ServerManBusy?.mark(create);
  const history = backupNode("div", "backup-list");
  history.id = "backup-history";
  history.setAttribute("aria-busy", "true");
  panel.append(heading, profileLabel, destination, create, feedback,
    backupNode("h3", "", "Latest backups"), history);
  document.getElementById("content-region").replaceChildren(panel);
  const catalog = backupNode("section", "panel"); catalog.id = "backup-catalog";
  document.getElementById("content-region").append(catalog);
  if (window.ServerManProfileRestore) void window.ServerManProfileRestore.loadCatalog();
  // Route profile changes through the shared selection and restore guards.
  select.addEventListener("change", () => {
    if (window.ServerManRestore && window.ServerManRestore.blockProfileChange(select)) return;
    if (backupState.confirmation) {
      select.value = backupState.confirmation.context.profileId;
      return;
    }
    window.ServerManProfileContext.select(select.value);
  });
  // Start with the history for the currently selected profile.
  if (profiles.length) loadBackupHistory();
  else { select.disabled = true; history.textContent = "Create or restore a profile to make new backups."; }
}
// Open the backup workspace for the profiles reported by the host.
async function openBackupWorkspace() {
  const workspace = window.ServerManWorkspace.capture("backups");
  const result = await window.pywebview.api.list_profiles();
  if (!window.ServerManWorkspace.isActive(workspace)) return;
  if (!result.success) return window.ServerManUi.renderHostError(result);
  // Defer to the shared empty state when no profiles exist.
  if (!Array.isArray(result.value)) return window.ServerManUi.renderHostError(result);
  renderBackupWorkspace(result.value);
}
// Apply operation events to the queued backup request and show its result in the page.
function backupOperationFinished(operation) {
  const pending = backupState.pendingOperation;
  // Ignore events for operations this workspace did not queue.
  if (!pending || operation.operation_id !== pending.operationId) return false;
  const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state);
  const active = isBackupProfileActive(pending.context);
  // Clear the pending operation once it reaches a terminal state.
  if (terminal) backupState.pendingOperation = null;
  if (!active || !terminal) return true;
  // Show the result with the wording of the operation bar; the bar then leaves the announcement to this notice.
  const outcome = window.ServerManOperationBar?.pageResult(operation)
    || { look: operation.state === "SUCCEEDED" ? "success" : "failed", text: "The backup did not complete." };
  const status = backupNode("div", `notice ${({ success: "notice-success", failed: "notice-error",
    recovery: "notice-recovery" })[outcome.look] || ""}`.trim(), outcome.text);
  status.id = "backup-operation";
  status.setAttribute("role", ["failed", "recovery"].includes(outcome.look) ? "alert" : "status");
  document.getElementById("backup-feedback").replaceChildren(status);
  // Reload the history after success, or re-enable creation after a failure.
  if (operation.state === "SUCCEEDED") void loadBackupHistory(pending.context);
  else document.getElementById("backup-create").disabled = false;
  return true;
}
// Publish the backup workspace controls used by the shell.
window.ServerManBackups = Object.freeze({
  open: openBackupWorkspace,
  operationFinished: backupOperationFinished,
});
