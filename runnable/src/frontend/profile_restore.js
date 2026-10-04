// Independent retained archive catalog and compact profile reconstruction dialog.
"use strict";
const directRestoreState = { sequence: 0, dialog: null, preview: null, pending: null, workspace: null };

// Use text nodes for every value supplied by archives or the host.
function directNode(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

// Render one browse action independently of profile selection.
async function loadDirectCatalog() {
  const panel = document.getElementById("backup-catalog");
  if (!panel) return;
  panel.replaceChildren(directNode("h2", "Restore profile from backup"),
    directNode("p", "Choose a full backup ZIP to restore a deleted profile."));
  const action = directNode("button", "Restore profile from backup…", "button button-primary");
  action.type = "button";
  action.addEventListener("click", () => browseDirectArchive(action, panel));
  panel.append(action);
}

// Validate only the chosen archive; dismissal leaves the workspace unchanged.
async function browseDirectArchive(action, panel) {
  const sequence = ++directRestoreState.sequence;
  const workspace = window.ServerManWorkspace.capture("backups");
  action.disabled = true;
  panel.querySelector(".notice")?.remove();
  try {
    const result = await window.pywebview.api.select_backup_archive();
    if (sequence !== directRestoreState.sequence || !window.ServerManWorkspace.isActive(workspace) || !panel.isConnected) return;
    if (!result.success) panel.append(directNode("p", window.ServerManOperationMessages.bridgeError(result, "Archive selection failed."), "notice notice-error"));
    else if (!result.value.cancelled) openDirectRestore(result.value, action);
  } catch (_) {
    if (panel.isConnected && window.ServerManWorkspace.isActive(workspace))
      panel.append(directNode("p", "Archive selection failed. Try browsing again.", "notice notice-error"));
  } finally { action.disabled = false; }
}

// Close the native modal and restore focus to its invoking archive action.
function closeDirectRestore() {
  const dialog = directRestoreState.dialog;
  if (!dialog || directRestoreState.pending) return;
  const trigger = dialog.returnFocus;
  dialog.close(); dialog.remove();
  directRestoreState.dialog = null; directRestoreState.preview = null;
  directRestoreState.sequence += 1;
  if (trigger?.isConnected) trigger.focus();
}

// Add one labelled option per row, with a concise explanation.
function directField(dialog, name, title, explanation, type = "text") {
  const row = directNode("label", "", "form-field");
  row.append(directNode("span", title));
  const input = document.createElement(type === "select" ? "select" : "input");
  input.id = `direct-${name}`;
  if (type !== "select") input.type = type;
  row.append(input, directNode("small", explanation)); dialog.append(row);
  input.addEventListener("input", invalidateDirectPreview);
  return input;
}

// Editing any choice invalidates both review and replacement confirmation.
function invalidateDirectPreview() {
  directRestoreState.preview = null;
  directRestoreState.sequence += 1;
  document.getElementById("direct-apply").disabled = true;
  document.getElementById("direct-review").replaceChildren();
}

// Open a focus-contained dialog that remains usable without registered profiles.
function openDirectRestore(backup, trigger) {
  closeDirectRestore();
  const dialog = document.createElement("dialog");
  dialog.className = "panel direct-restore-dialog";
  dialog.id = "direct-restore-dialog"; dialog.returnFocus = trigger;
  dialog.setAttribute("aria-labelledby", "direct-restore-title");
  const title = directNode("h2", "Restore profile"); title.id = "direct-restore-title";
  dialog.append(title, directNode("p", backup.archive_name || backup.backup_id),
    directNode("p", `${backup.display_name} (${backup.profile_id}) · ${window.ServerManBackupDisplay.summary(backup)}`));
  const identifier = directField(dialog, "profile-id", "Profile ID", "Leave blank to suggest a free ID. Existing profiles are preserved.");
  identifier.maxLength = 64;
  const name = directField(dialog, "name", "Display name", "Name shown in profile selectors.");
  name.value = backup.display_name; name.maxLength = 100;
  const policy = directField(dialog, "policy", "World destination", "Isolation preserves existing worlds. Replacement requires confirmation of every affected profile.", "select");
  [["", "Automatic: original if absent, otherwise isolate"], ["allocate_new", "New isolated mission and storage ID"],
    ["preserve_original", "Original mission, only if absent"], ["replace_existing", "Replace selected existing world"]].forEach(([value, text]) => {
    const option = directNode("option", text); option.value = value; policy.append(option);
  });
  ["game-port", "query-port"].forEach((field) => {
    const input = directField(dialog, field, field === "game-port" ? "Game port" : "Steam query port", "Leave blank to preserve free ports or suggest alternatives.", "number");
    input.min = "1"; input.max = "65535";
  });
  const feedback = directNode("div", ""); feedback.id = "direct-review"; feedback.setAttribute("aria-live", "polite");
  dialog.append(feedback);
  const actions = directNode("div", "", "action-row");
  const cancel = directNode("button", "Cancel", "button"); cancel.type = "button";
  cancel.addEventListener("click", closeDirectRestore);
  const review = directNode("button", "Review restore", "button"); review.type = "button";
  review.addEventListener("click", () => reviewDirectRestore(backup.backup_id));
  const apply = directNode("button", "Restore profile", "button button-primary");
  apply.id = "direct-apply"; apply.type = "button"; apply.disabled = true;
  apply.addEventListener("click", applyDirectRestore);
  // The restore submits an operation: lock the button and explain why while another operation runs.
  window.ServerManBusy?.mark(apply, true);
  actions.append(cancel, review, apply); dialog.append(actions);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeDirectRestore(); });
  document.body.append(dialog); directRestoreState.dialog = dialog;
  directRestoreState.workspace = window.ServerManWorkspace.capture("backups");
  dialog.showModal(); cancel.focus();
}

// Read an exact optional-input request without substituting archived values.
function directRequest(backupId) {
  const value = (name) => document.getElementById(`direct-${name}`).value;
  const port = (name) => value(name) === "" ? null : Number(value(name));
  return { backup_id: backupId, profile_id: value("profile-id") || null, display_name: value("name") || null,
    storage_policy: value("policy") || null, game_port: port("game-port"), steam_query_port: port("query-port") };
}

// Display concrete destinations and every registered consumer before publication.
async function reviewDirectRestore(backupId) {
  const request = directRequest(backupId);
  const sequence = ++directRestoreState.sequence;
  directRestoreState.preview = null; document.getElementById("direct-apply").disabled = true;
  const result = await window.pywebview.api.preview_profile_restore(...Object.values(request));
  if (sequence !== directRestoreState.sequence || !directRestoreState.dialog
      || !window.ServerManWorkspace.isActive(directRestoreState.workspace)) return;
  const review = document.getElementById("direct-review"); review.replaceChildren();
  if (!result.success) { review.append(directNode("p", window.ServerManOperationMessages.bridgeError(result, "Preview failed."), "notice notice-error")); return; }
  const preview = result.value;
  directRestoreState.preview = { request: Object.freeze(request), value: preview };
  const list = directNode("dl", "");
  [["Profile", `${preview.profile.display_name} (${preview.profile.profile_id})`], ["Configuration", preview.profile.server_config],
    ["Runtime", preview.profile.runtime_profile], ["Mission", preview.mission_root], ["Storage", `storage_${preview.instance_id}`],
    ["Ports", `${preview.game_port} / query ${preview.steam_query_port}`], ["Destination policy", window.ServerManDiagnosticLabels.storagePolicy(preview.storage_policy)]].forEach(([label, value]) => {
    list.append(directNode("dt", label), directNode("dd", value));
  });
  review.append(list, directNode("p", "Restore creates the profile. Start it separately after checking readiness."));
  preview.warnings.forEach((warning) => review.append(directNode("p", warning, "notice notice-warning")));
  if (!preview.server_executable_present) review.append(directNode("p", "Server executable is missing. Install it before starting.", "notice notice-warning"));
  if (preview.missing_mods.length) {
    const mods = directNode("button", `Open Mods: ${preview.missing_mods.join(", ")}`, "button"); mods.type = "button";
    mods.addEventListener("click", () => { closeDirectRestore(); document.querySelector('[data-section="mods"]')?.click(); }); review.append(mods);
  }
  if (preview.storage_policy === "replace_existing") {
    const row = directNode("label", "", "notice notice-warning");
    const confirm = document.createElement("input"); confirm.type = "checkbox"; confirm.id = "direct-overwrite";
    row.append(confirm, document.createTextNode(` Replace the selected world used by: ${preview.affected_profile_ids.join(", ")}. A verified recovery copy will be retained.`));
    review.append(row);
    confirm.addEventListener("change", () => { document.getElementById("direct-apply").disabled = !confirm.checked; });
  } else document.getElementById("direct-apply").disabled = false;
}

// Submit the exact reviewed request; freeze controls while its handle is acquired.
async function applyDirectRestore() {
  const reviewed = directRestoreState.preview;
  if (!reviewed || directRestoreState.pending) return;
  const confirmation = reviewed.value.storage_policy === "replace_existing"
    ? { preview_fingerprint: reviewed.value.preview_fingerprint, affected_profile_ids: reviewed.value.affected_profile_ids } : null;
  if (confirmation && !document.getElementById("direct-overwrite")?.checked) return;
  const dialog = directRestoreState.dialog;
  dialog.querySelectorAll("button, input, select").forEach((control) => { control.disabled = true; });
  directRestoreState.pending = { operationId: null, workspace: directRestoreState.workspace };
  const result = await window.pywebview.api.restore_profile_from_backup(...Object.values(reviewed.request),
    reviewed.value.manifest_digest, reviewed.value.preview_fingerprint, confirmation);
  if (!result.success) {
    directRestoreState.pending = null;
    dialog.querySelectorAll("button, input, select").forEach((control) => { control.disabled = false; });
    document.getElementById("direct-review").append(directNode("p", window.ServerManOperationMessages.bridgeError(result, "Restore failed."), "notice notice-error"));
    return;
  }
  directRestoreState.pending.operationId = result.value.operation_id;
  window.ServerManOperationBar?.adopt(result.value.operation_id);
  document.getElementById("direct-review").append(directNode("p", "Verifying backup and preparing restoration…", "notice"));
}

// Refresh shared selectors on commit even when the initiating workspace has changed.
function directOperationFinished(operation) {
  const pending = directRestoreState.pending;
  if (!pending || pending.operationId !== operation.operation_id) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  directRestoreState.pending = null;
  if (operation.state === "SUCCEEDED") {
    closeDirectRestore();
    void window.ServerManProfileContext.refreshAndSelect(operation.result.profile.profile_id).then(() => {
      if (window.ServerManWorkspace.isActive(pending.workspace)) void window.ServerManBackups.open();
      const reasons = operation.result.readiness?.reasons || [];
      let message = operation.result.cleanup_pending ? "Profile restored; recovery cleanup is pending."
        : reasons.length ? `Profile restored. ${reasons.join(" ")}` : "Profile restored. Check readiness before starting.";
      if (operation.result.recovery_copy) message += ` World recovery copy: ${operation.result.recovery_copy}`;
      window.ServerManUi.setHostStatus(message, reasons.length || operation.result.cleanup_pending ? "is-warning" : "", "profile-restore");
    });
  } else if (directRestoreState.dialog) {
    directRestoreState.dialog.querySelectorAll("button, input, select").forEach((control) => { control.disabled = false; });
    invalidateDirectPreview();
    document.getElementById("direct-review").append(directNode("p",
      window.ServerManOperationBar?.pageResult(operation).text || "Restore failed.", "notice notice-error"));
  }
  return true;
}
window.ServerManProfileRestore = Object.freeze({ loadCatalog: loadDirectCatalog, operationFinished: directOperationFinished });
