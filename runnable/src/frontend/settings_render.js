// Settings rendering: path editors, the backup destination choice, resolved locations, and path diagnostics.
"use strict";

// Look up the diagnostic that belongs to a path role.
function diagnosticFor(role) {
  // The custom backup folder shares the backup root diagnostic.
  return settingsState.diagnostics.get(role === "custom_backup_root" ? "backup_root" : role);
}

// Turn one path diagnostic into operator wording and a status class; the host text stays in the diagnostics log.
function diagnosticCopy(role) {
  const wording = window.ServerManDiagnosticLabels.path(role, diagnosticFor(role));
  return [wording.label, wording.sentence, wording.ready ? "status-normal" : "status-warning"];
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
