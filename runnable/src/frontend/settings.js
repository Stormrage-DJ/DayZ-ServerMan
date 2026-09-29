// Settings workspace: resolve and save application locations.
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

// Look up the diagnostic that belongs to a path role.
function diagnosticFor(role) {
  // The custom backup folder shares the backup root diagnostic.
  return settingsState.diagnostics.get(role === "custom_backup_root" ? "backup_root" : role);
}

// Turn one path diagnostic into display text and a status class.
function diagnosticCopy(role) {
  const diagnostic = diagnosticFor(role);
  // Describe unchecked paths and what to do next.
  if (!diagnostic) return ["Not checked", "Choose a location to validate it.", "status-warning"];
  const ready = diagnostic.status === "READY";
  return [diagnostic.status, `${diagnostic.message}. ${diagnostic.action}`, ready ? "status-normal" : "status-warning"];
}

// Build one read-only path field with browse button and diagnostic line.
function pathEditor(role, labelText, description) {
  const field = settingsNode("article", "settings-path");
  const label = settingsNode("label", "", labelText);
  label.htmlFor = `settings-${role}`;
  const descriptionNode = settingsNode("p", "configuration-path", description);
  descriptionNode.id = `settings-${role}-description`;
  const input = document.createElement("input");
  input.id = `settings-${role}`;
  input.readOnly = true;
  // Show the stored path or a placeholder.
  input.value = settingsState.paths[role] || "Not configured";
  input.setAttribute("aria-describedby", descriptionNode.id);
  // Offer folder selection through the host dialog.
  const browse = settingsNode("button", "button", "Choose folder");
  browse.type = "button";
  browse.dataset.settingsBrowse = role;
  browse.disabled = settingsState.busy;
  const [status, copy, statusClass] = diagnosticCopy(role);
  const statusNode = settingsNode("p", `status-label ${statusClass}`, `${status}: ${copy}`);
  field.append(label, descriptionNode, input, browse, statusNode);
  return field;
}

// Build one backup destination choice with its stored path.
function backupChoice(input, title, description, path, browse = null) {
  const choice = settingsNode("article", `backup-choice${input.checked ? " is-selected" : ""}`);
  choice.dataset.backupChoice = input.value;
  const label = settingsNode("label", "backup-choice-heading"); label.htmlFor = input.id;
  const copy = settingsNode("span", "backup-choice-copy");
  copy.append(settingsNode("strong", "", title), settingsNode("small", "", description));
  label.append(input, copy);
  const location = settingsNode("code", "backup-choice-path", path);
  choice.append(label, location);
  if (browse) choice.append(browse);
  return choice;
}

// Build the backup destination selector.
function backupEditor() {
  const fieldset = settingsNode("fieldset", "settings-path settings-backup");
  // Explain what the choice controls and offer both destinations.
  fieldset.append(settingsNode("legend", "", "Backup destination"));
  fieldset.append(settingsNode("p", "configuration-path",
    "Choose where this manager stores new verified backups."));
  const portable = document.createElement("input");
  portable.type = "radio"; portable.name = "backup-mode"; portable.value = "portable";
  portable.id = "backup-mode-portable"; portable.checked = settingsState.backupMode === "portable";
  portable.disabled = settingsState.busy;
  const custom = document.createElement("input");
  custom.type = "radio"; custom.name = "backup-mode"; custom.value = "custom";
  custom.id = "backup-mode-custom"; custom.checked = settingsState.backupMode === "custom";
  custom.disabled = settingsState.busy;
  const browse = settingsNode("button", "button", "Choose folder");
  browse.type = "button"; browse.dataset.settingsBrowse = "custom_backup_root";
  browse.disabled = settingsState.busy || !custom.checked;
  const options = settingsNode("div", "backup-options");
  options.append(
    backupChoice(portable, "Portable default", "Moves with the DayZ-ServerMan folder.", settingsState.defaultBackup),
    backupChoice(custom, "Custom folder", "Uses a fixed folder elsewhere on this computer.",
      settingsState.paths.custom_backup_root || "No custom folder selected", browse),
  );
  const [status, copy, statusClass] = diagnosticCopy("custom_backup_root");
  fieldset.append(options, settingsNode("p", `status-label ${statusClass}`, `${status}: ${copy}`));
  return fieldset;
}

// List the automatically resolved paths.
function resolvedLocations() {
  const section = settingsNode("section", "settings-resolved");
  section.append(settingsNode("h3", "", "Resolved automatically"), settingsNode(
    "p", "configuration-path",
    "These paths follow the selected folders and are checked when settings are saved.",
  ));
  const list = settingsNode("dl", "detail-list");
  // Show each resolved path or a prompt to choose its folder.
  resolvedSettingsFields.forEach(([role, label]) => {
    list.append(settingsNode("dt", "", label),
      settingsNode("dd", "", settingsState.paths[role] || "Select the related folder"));
  });
  section.append(list);
  return section;
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
  actions.append(indicator, save);
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
  document.getElementById("content-region").replaceChildren(panel, legacy);
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
  if (!result.success) { renderSettings(); return settingsFeedback(result.error.message, true); }
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
  if (!result.success) { settingsState.busy = false; renderSettings(); return settingsFeedback(result.error.message, true); }
  // Record the queued save so progress can be tracked.
  settingsState.pending = Object.freeze({ operationId: result.value.operation_id, context });
  settingsFeedback("Saving application locations.");
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
}

// Track the pending settings save until it reaches a terminal state.
function settingsOperationFinished(operation) {
  const pending = settingsState.pending;
  if (!pending || pending.operationId !== operation.operation_id) return false;
  if (!settingsActive(pending.context)) { settingsState.pending = null; return true; }
  // Keep showing progress while the save continues.
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) {
    settingsFeedback(`${operation.progress_phase} — ${operation.progress_percent}%`); return true;
  }
  settingsState.pending = null; settingsState.busy = false;
  if (operation.state === "SUCCEEDED") {
    // Reload after a successful save.
    window.ServerManTransitions.setDirty("settings-paths", false);
    openSettings();
  } else {
    // Return the form and report the failure.
    renderSettings();
    const message = operation.terminal_error?.message || "Settings could not be saved.";
    settingsFeedback(message, true);
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
