// Settings workspace: resolve and save application locations; the update-check switch has its own module.
"use strict";

// Operator-selectable installation folders with labels and help text.
const settingsFields = Object.freeze([
  ["dayz_root", "DayZ server folder", "Folder containing the DayZ server installation."],
  ["steamcmd_root", "SteamCMD folder", "Folder containing steamcmd.exe and its Steam library."],
]);
// Derived executable and content paths resolved from the selected folders.
const resolvedSettingsFields = Object.freeze([
  ["dayz_executable", "DayZ executable"],
  ["steamcmd_executable", "SteamCMD executable"],
  ["workshop_content_root", "Workshop content"],
]);
// Every path field saved with the settings payload.
const pathFieldNames = Object.freeze([
  ...settingsFields.map(([role]) => role), "custom_backup_root",
]);
// Notice after a save that set the DayZ server folder while changes were blocked (QF-069).
const SETTINGS_RESTART_TEXT = "Restart DayZ-ServerMan. It then checks the interrupted work in this DayZ server folder.";
// Settings state: revisions, path values, diagnostics, and pending work.
const settingsState = {
  generation: 0, revision: null, paths: null, original: null,
  diagnostics: new Map(), defaultBackup: "", backupMode: "portable",
  pending: null, busy: false,
};

// Build one element for the settings workspace.
function settingsNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Capture the generation and workspace token for one settings request.
function settingsContext() {
  return Object.freeze({
    generation: settingsState.generation,
    workspace: window.ServerManWorkspace.capture("settings", "settings-paths"),
  });
}

// Report whether a captured settings context is still current.
function settingsActive(context) {
  return context && context.generation === settingsState.generation
    && window.ServerManWorkspace.isActive(context.workspace);
}

// Show one status or error notice in the feedback region.
function settingsFeedback(message, error = false) {
  const region = document.getElementById("settings-feedback");
  if (!region) return;
  const notice = settingsNode("div", `notice ${error ? "notice-error" : "notice-success"}`, message);
  notice.setAttribute("role", error ? "alert" : "status");
  notice.tabIndex = -1;
  region.replaceChildren(notice);
  // Focus error notices so assistive technology reports them.
  if (error) notice.focus();
}

// Render the settings workspace for the current state.
function renderSettings() {
  const panel = settingsNode("section", "panel settings-panel");
  panel.append(settingsNode("h2", "", "Application locations"));
  // Explain which folders the operator chooses.
  panel.append(settingsNode(
    "p", "", "Choose the two installation folders. Executable and Workshop paths are resolved automatically.",
  ));
  const fields = settingsNode("div", "settings-fields");
  // Compose the chosen folders, resolved paths, and backup choice.
  settingsFields.forEach((field) => fields.append(pathEditor(...field)));
  fields.append(resolvedLocations());
  fields.append(backupEditor());
  const actions = settingsNode("div", "action-row");
  const dirty = window.ServerManTransitions.hasUnsavedChanges();
  const indicator = settingsNode(
    "span", `unsaved-indicator ${dirty ? "is-dirty" : ""}`,
    dirty ? "Unsaved settings changes" : "Settings are unchanged",
  );
  // Offer saving only while something changed.
  const save = settingsNode("button", "button button-primary", "Save locations");
  save.type = "button"; save.id = "save-path-settings";
  save.disabled = settingsState.busy || !dirty;
  actions.append(indicator, window.ServerManBusy.mark(save));
  const feedback = settingsNode("div", "configuration-feedback");
  feedback.id = "settings-feedback";
  panel.append(fields, actions, feedback);

  // Keep the legacy import available but separate.
  const legacy = settingsNode("section", "panel");
  legacy.append(settingsNode("h2", "", "Legacy manager import"));
  legacy.append(settingsNode(
    "p", "", "Import reviewed legacy data separately. It is not required for a fresh setup.",
  ));
  const openLegacy = settingsNode("button", "button", "Open legacy import");
  openLegacy.type = "button"; openLegacy.id = "open-legacy-import";
  legacy.append(openLegacy);
  // The update-check switch saves at once and is rebuilt from its stored value with every redraw.
  document.getElementById("content-region").replaceChildren(
    panel, window.ServerManSettingsUpdates.render(), legacy);
  // Wire browse, backup mode, save, and legacy actions.
  panel.querySelectorAll("[data-settings-browse]").forEach((button) => {
    button.addEventListener("click", () => chooseSettingsPath(button.dataset.settingsBrowse));
  });
  portableBackupListeners(panel);
  save.addEventListener("click", savePathSettings);
  openLegacy.addEventListener("click", openLegacyImport);
}

// Re-render when the backup destination mode changes.
function portableBackupListeners(panel) {
  panel.querySelectorAll("input[name='backup-mode']").forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      settingsState.generation += 1;
      settingsState.backupMode = radio.value;
      // Clearing the custom folder returns the manager to the portable default.
      if (radio.value === "portable") settingsState.paths.custom_backup_root = null;
      window.ServerManTransitions.setDirty("settings-paths", true);
      renderSettings();
    });
  });
}

// Ask the host for a folder and adopt the validated path.
async function chooseSettingsPath(role) {
  settingsState.generation += 1;
  const context = settingsContext();
  settingsState.busy = true; renderSettings();
  // Show the workspace busy while the folder dialog is open.
  const result = await window.pywebview.api.select_settings_path(role);
  if (!settingsActive(context)) return;
  settingsState.busy = false;
  if (!result.success) {
    renderSettings();
    return settingsFeedback(window.ServerManOperationMessages.bridgeError(result, "The folder could not be selected."), true);
  }
  if (result.value.cancelled) { renderSettings(); return settingsFeedback("Path selection was cancelled."); }
  // Adopt the chosen path and its resolved companions.
  settingsState.paths[role] = result.value.path;
  Object.assign(settingsState.paths, result.value.resolved_paths || {});
  if (role === "custom_backup_root") settingsState.backupMode = "custom";
  settingsState.diagnostics.set(role === "custom_backup_root" ? "backup_root" : role, result.value);
  window.ServerManTransitions.setDirty("settings-paths", true);
  renderSettings();
  // Return focus to the browse control that changed the path.
  document.querySelector(`[data-settings-browse='${role}']`).focus();
}

// Save the path fields against the loaded revision.
async function savePathSettings() {
  const generation = ++settingsState.generation;
  const context = settingsContext();
  // Omit the custom backup folder while the portable default is active.
  const payload = Object.freeze(Object.fromEntries(
    pathFieldNames.map((field) => [field, field === "custom_backup_root"
      && settingsState.backupMode === "portable" ? null : settingsState.paths[field]]),
  ));
  settingsState.busy = true; renderSettings();
  const result = await window.pywebview.api.save_settings(payload, settingsState.revision);
  if (!settingsActive(context) || generation !== settingsState.generation) return;
  if (!result.success) {
    settingsState.busy = false; renderSettings();
    return settingsFeedback(window.ServerManOperationMessages.bridgeError(result, "Settings could not be saved."), true);
  }
  // Record the queued save; the operation bar shows its progress.
  settingsState.pending = Object.freeze({ operationId: result.value.operation_id, context });
  settingsFeedback("Saving application locations.");
  window.ServerManOperationBar.adopt(result.value.operation_id);
}

// Open the legacy import through the transition guard.
function openLegacyImport() {
  window.ServerManTransitions.requestOwnerTransition(
    "settings-paths", "Discard unsaved location changes before opening legacy import.",
    () => window.ServerManMigration.open(),
  );
}

// Load the application snapshot and render the settings workspace.
async function openSettings(snapshot = null) {
  const generation = ++settingsState.generation;
  const context = settingsContext();
  let current = snapshot;
  if (!current) {
    // Fetch a snapshot when the caller did not provide one.
    const result = await window.pywebview.api.get_application_snapshot();
    if (!settingsActive(context) || generation !== settingsState.generation) return;
    if (!result.success) return window.ServerManUi.renderHostError(result);
    current = result.value;
  }
  if (!settingsActive(context) || generation !== settingsState.generation) return;
  // Adopt the revision and every managed path value.
  settingsState.revision = current.settings.revision;
  settingsState.paths = Object.fromEntries(
    [...pathFieldNames, ...resolvedSettingsFields.map(([role]) => role)]
      .map((field) => [field, current.settings[field]]),
  );
  // Remember the loaded values and derive the backup mode.
  settingsState.original = Object.freeze({ ...settingsState.paths });
  settingsState.backupMode = settingsState.paths.custom_backup_root === null ? "portable" : "custom";
  settingsState.diagnostics = new Map(current.diagnostics.map((item) => [item.role, item]));
  settingsState.defaultBackup = typeof current.portable_backup_root === "string"
    ? current.portable_backup_root : "Manager root\\backups";
  settingsState.busy = false;
  // Clear the dirty flag and render the loaded state.
  window.ServerManTransitions.setDirty("settings-paths", false);
  renderSettings();
  void window.ServerManSettingsUpdates.load();
}

// Track the pending settings save until it reaches a terminal state.
function settingsOperationFinished(operation) {
  const pending = settingsState.pending;
  if (!pending || pending.operationId !== operation.operation_id) return false;
  if (!settingsActive(pending.context)) { settingsState.pending = null; return true; }
  // The operation bar shows the progress while the save continues.
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  settingsState.pending = null; settingsState.busy = false;
  if (operation.state === "SUCCEEDED") {
    // Reload after a successful save; a save that passed the blocks asks for the restart afterwards.
    window.ServerManTransitions.setDirty("settings-paths", false);
    const reload = openSettings();
    const generation = settingsState.generation;
    if (operation.result?.restart_required === true) {
      void reload.then(() => { if (generation === settingsState.generation) settingsFeedback(SETTINGS_RESTART_TEXT); });
    }
  } else {
    // Return the form and report the failure.
    renderSettings();
    settingsFeedback(window.ServerManOperationBar.pageResult(operation).text, true);
  }
  return true;
}

// Register the settings workspace with the shared transition guard.
window.ServerManTransitions.registerOwner(
  "settings-paths",
  () => {
    // Roll the form back to the loaded snapshot.
    settingsState.generation += 1;
    settingsState.paths = { ...settingsState.original };
    settingsState.backupMode = settingsState.paths.custom_backup_root === null ? "portable" : "custom";
  },
  () => { const save = document.getElementById("save-path-settings"); if (save) save.focus(); },
);
// Publish the settings workspace entry points.
window.ServerManSettings = Object.freeze({ open: openSettings, operationFinished: settingsOperationFinished });
