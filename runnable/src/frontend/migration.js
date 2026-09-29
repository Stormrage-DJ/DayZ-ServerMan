// Legacy migration workspace: reviewed import preview, confirmation, and queued apply.
"use strict";

// Tracks the preview, pending operation, and open confirmation for the workflow.
const migrationState = {
  generation: 0,
  preview: null,
  pending: null,
  confirmation: null,
};

// Create one interface element with an optional class and text.
function migrationNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Capture the migration generation and workspace into a frozen token.
function migrationContext() {
  return Object.freeze({
    generation: migrationState.generation,
    workspace: window.ServerManWorkspace.capture("settings"),
  });
}

// Report whether a captured migration token is still current.
function migrationActive(context) {
  return context && context.generation === migrationState.generation
    && window.ServerManWorkspace.isActive(context.workspace);
}

// Replace the migration feedback area with a status or alert notice.
function migrationFeedback(text, kind = "") {
  const feedback = document.getElementById("migration-feedback");
  if (!feedback) return;
  const notice = migrationNode("div", `notice ${kind}`.trim(), text);
  notice.setAttribute("role", kind === "notice-error" ? "alert" : "status");
  notice.tabIndex = -1;
  feedback.replaceChildren(notice);
  notice.focus();
}

// Show a migration failure using the host message, or a safe fallback.
function migrationError(result, fallback) {
  const message = result && result.error && typeof result.error.message === "string"
    ? result.error.message : fallback;
  migrationFeedback(message, "notice-error");
}

// List the item identifiers selected for import.
function selectedMigrationItems() {
  return [...document.querySelectorAll("[data-migration-item]:checked")]
    .map((input) => input.dataset.migrationItem);
}

// Mark the migration owner dirty whenever at least one item is selected.
function markMigrationDirty() {
  window.ServerManTransitions.setDirty("migration", selectedMigrationItems().length > 0);
}

// Build one selectable import item with its warnings and conflicts.
function migrationItem(item, title, summary) {
  const row = migrationNode("article", "backup-item");
  const label = migrationNode("label");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.dataset.migrationItem = item.item_id;
  // Select conflict-free items by default and disable blocked ones.
  checkbox.disabled = !item.selectable;
  checkbox.checked = item.selectable;
  checkbox.addEventListener("change", markMigrationDirty);
  label.append(checkbox, document.createTextNode(` ${title}`));
  row.append(label, migrationNode("p", "", summary));
  item.warnings.forEach((warning) => row.append(migrationNode("p", "notice notice-warning", warning)));
  item.conflicts.forEach((conflict) => row.append(
    migrationNode("p", "notice notice-error", `Needs review: ${conflict.message}`),
  ));
  return row;
}

// Render the immutable import preview and enable review of the selection.
function renderMigrationPreview(preview) {
  // Freeze the preview so later edits cannot change the reviewed import.
  migrationState.preview = window.ServerManTransitions.immutableCopy(preview);
  const results = document.getElementById("migration-results");
  results.replaceChildren();
  const settings = preview.settings;
  results.append(migrationItem(
    settings,
    "DayZ installation settings",
    settings.fields.length
      ? `Set ${settings.fields.map((field) => field.role).join(" and ")}.`
      : "Current installation settings already have values.",
  ));
  // Describe each importable profile with its mods and runtime profile.
  preview.profiles.forEach((item) => {
    const profile = item.profile;
    const summary = profile
      ? `${profile.display_name}: ${profile.mods.length} ordered mods; runtime profile ${profile.runtime_profile}.`
      : "This legacy profile cannot be imported without review.";
    results.append(migrationItem(item, profile ? profile.display_name : "Blocked legacy profile", summary));
  });
  // Describe the legacy backup inventory as external read-only references.
  results.append(migrationItem(
    preview.backup_inventory,
    "Legacy backup external references",
    `${preview.backup_inventory.count} archives (${preview.backup_inventory.size} bytes) will be indexed by safe label, digest, and opaque ID. Policy: ${preview.backup_inventory.status}. External reference only — source remains in legacy folder. Not restorable by DayZ-ServerMan. No archive bytes will be copied.`,
  ));
  results.append(migrationNode(
    "p", "notice", "The source remains unchanged. Legacy UI state and authentication settings are ignored.",
  ));
  // Enable the import review once a preview exists.
  document.getElementById("migration-apply").disabled = false;
  markMigrationDirty();
}

// Let the user pick a legacy folder and build an immutable import preview.
async function chooseLegacyRoot() {
  // Reset any previous preview and dirty state before choosing a folder.
  migrationState.generation += 1;
  migrationState.preview = null;
  window.ServerManTransitions.setDirty("migration", false);
  const context = migrationContext();
  migrationFeedback("Waiting for folder selection.", "notice-busy");
  const selected = await window.pywebview.api.select_legacy_root();
  if (!migrationActive(context)) return;
  if (!selected.success) return migrationError(selected, "The legacy folder is not supported.");
  // Stop quietly when the folder picker was cancelled.
  if (selected.value.cancelled) return migrationFeedback("Folder selection was cancelled.");
  migrationFeedback("Building an immutable import preview.", "notice-busy");
  // Ask the host to convert the selection into an immutable preview.
  const preview = await window.pywebview.api.preview_legacy_import(selected.value.selection_id);
  if (!migrationActive(context)) return;
  if (!preview.success) return migrationError(preview, "The legacy source could not be previewed.");
  renderMigrationPreview(preview.value);
}

// Close the migration confirmation and restore focus and inert state.
function closeMigrationConfirmation(restore = true) {
  const current = migrationState.confirmation;
  if (!current) return;
  current.inerted.forEach(({ element, inert }) => { element.inert = inert; });
  current.dialog.remove();
  migrationState.confirmation = null;
  if (restore && current.returnFocus.isConnected) current.returnFocus.focus();
}

// Keep keyboard focus inside the migration confirmation and close it on escape.
function trapMigrationConfirmation(event) {
  const current = migrationState.confirmation;
  if (!current) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeMigrationConfirmation();
    return;
  }
  if (event.key !== "Tab") return;
  const buttons = [...current.dialog.querySelectorAll("button")];
  const first = buttons[0];
  const last = buttons.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
}

// Submit the reviewed import arguments and track the queued operation.
async function submitMigration(argumentsCopy, context) {
  closeMigrationConfirmation(false);
  if (!migrationActive(context)) return;
  const result = await window.pywebview.api.apply_legacy_import(...argumentsCopy);
  // Ignore the response when the workspace moved on.
  if (!migrationActive(context)) return;
  if (!result.success) return migrationError(result, "Legacy import could not be queued.");
  // Track the queued import and clear the dirty marker.
  migrationState.pending = Object.freeze({ context, operationId: result.value.operation_id });
  window.ServerManTransitions.setDirty("migration", false);
  migrationFeedback("Legacy import queued. Source files remain read-only.", "notice-busy");
}

// Open the reviewed confirmation for the selected import items.
function confirmMigration() {
  if (!migrationState.preview || migrationState.confirmation) return;
  const selected = selectedMigrationItems();
  if (!selected.length) return migrationFeedback("Select at least one conflict-free item.", "notice-error");
  const context = migrationContext();
  // Freeze the reviewed preview and selection before confirmation.
  const argumentsCopy = Object.freeze([
    migrationState.preview.preview_id,
    migrationState.preview.preview_fingerprint,
    Object.freeze([...selected]),
  ]);
  const dialog = migrationNode("section", "panel notice notice-warning");
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "migration-confirm-title");
  const title = migrationNode("h2", "", "Import selected legacy data?");
  title.id = "migration-confirm-title";
  dialog.append(title, migrationNode(
    "p", "", `${selected.length} selected item(s) will be converted or indexed. Legacy backup archives remain external and read-only; they are not copied or restorable by DayZ-ServerMan.`,
  ));
  const actions = migrationNode("div", "action-row");
  const cancel = migrationNode("button", "button", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", closeMigrationConfirmation);
  const apply = migrationNode("button", "button button-primary", "Import selected items");
  apply.type = "button";
  apply.addEventListener("click", () => submitMigration(argumentsCopy, context));
  actions.append(cancel, apply);
  dialog.append(actions);
  dialog.addEventListener("keydown", trapMigrationConfirmation);
  // Make the rest of the page inert while the dialog is open.
  const inerted = [...document.body.children].map((element) => ({ element, inert: element.inert }));
  document.body.append(dialog);
  migrationState.confirmation = Object.freeze({
    context, dialog, inerted: Object.freeze(inerted), returnFocus: document.activeElement,
  });
  inerted.forEach(({ element }) => { element.inert = true; });
  cancel.focus();
}

// Build the migration panel with folder selection and import review controls.
function renderMigrationWorkspace() {
  // Reset the preview and generation for a fresh workspace.
  migrationState.generation += 1;
  migrationState.preview = null;
  const panel = migrationNode("section", "panel");
  panel.append(migrationNode("h2", "", "Import legacy manager data"));
  panel.append(migrationNode(
    "p", "", "Choose a supported legacy manager folder. Discovery is explicit and read-only.",
  ));
  const choose = migrationNode("button", "button button-primary", "Choose legacy folder");
  choose.type = "button";
  choose.id = "migration-choose";
  choose.addEventListener("click", chooseLegacyRoot);
  const apply = migrationNode("button", "button", "Review and import");
  apply.type = "button";
  apply.id = "migration-apply";
  apply.disabled = true;
  apply.addEventListener("click", confirmMigration);
  const actions = migrationNode("div", "action-row");
  const back = migrationNode("button", "button", "Back to application locations");
  back.type = "button";
  // Return to settings through the shared transition guard.
  back.addEventListener("click", () => window.ServerManTransitions.requestOwnerTransition(
    "migration", "Discard selected legacy import items before returning to application locations.",
    () => window.ServerManSettings.open(),
  ));
  actions.append(back, choose, apply);
  const feedback = migrationNode("div", "configuration-feedback");
  feedback.id = "migration-feedback";
  const results = migrationNode("div", "backup-list");
  results.id = "migration-results";
  panel.append(actions, feedback, results);
  document.getElementById("content-region").replaceChildren(panel);
}

// Apply progress and terminal events to the queued legacy import.
function migrationOperationFinished(operation) {
  const pending = migrationState.pending;
  // Ignore events for operations this workspace did not queue.
  if (!pending || pending.operationId !== operation.operation_id) return false;
  const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state);
  if (!migrationActive(pending.context)) {
    if (terminal) migrationState.pending = null;
    return true;
  }
  const message = operation.terminal_error && operation.terminal_error.message
    ? operation.terminal_error.message
    : `${operation.progress_phase} — ${operation.progress_percent}%`;
  // Show the latest progress or terminal message for the queued import.
  migrationFeedback(message, operation.state === "FAILED" ? "notice-error" : "notice-busy");
  // Release the pending marker once the operation is terminal.
  if (terminal) migrationState.pending = null;
  return true;
}

// Register the migration owner with the shared unsaved-change guard.
window.ServerManTransitions.registerOwner(
  "migration",
  () => { migrationState.generation += 1; migrationState.preview = null; },
  () => { const item = document.querySelector("[data-migration-item]"); if (item) item.focus(); },
);
// Publish the migration workspace controls used by the settings area.
window.ServerManMigration = Object.freeze({
  open: renderMigrationWorkspace,
  operationFinished: migrationOperationFinished,
});
