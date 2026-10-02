// Restore workspace: verify, confirm, and apply backup restores.
"use strict";

// Dialog, generation, pending operation, and reviewed preview for restores.
const restoreState = {
  dialog: null,
  backupId: null,
  generation: 0,
  pendingOperation: null,
  preview: null,
};

// Build one element for the restore workspace.
function restoreNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Capture the backup, generation, and profile identity for one request.
function restoreContext(backupId) {
  return Object.freeze({
    backupId,
    generation: restoreState.generation,
    profile: captureBackupProfile(),
  });
}

// Report whether a captured restore context still matches the live view.
function restoreContextActive(context) {
  return Boolean(context && restoreState.backupId === context.backupId
    && context.generation === restoreState.generation
    && isBackupProfileActive(context.profile));
}

// Replace the feedback area with one announced notice.
function restoreFeedback(text, kind = "", role = "status") {
  const node = restoreNode("div", `notice ${kind}`.trim(), window.ServerManBackupDisplay.text(text));
  node.setAttribute("role", role);
  node.tabIndex = -1;
  // Show the notice in the feedback region and focus it.
  document.getElementById("restore-feedback").replaceChildren(node);
  node.focus();
}

// Verify the chosen backup and show the reviewed restore plan.
async function previewRestore(backupId) {
  if (restoreState.dialog || restoreState.pendingOperation) return;
  if (!backupState.history?.backups.some(backup => backup.backup_id === backupId && backup.restore_compatibility === "COMPATIBLE")) return;
  restoreState.generation += 1;
  restoreState.backupId = backupId;
  restoreState.preview = null;
  document.getElementById("restore-panel").hidden = false;
  const context = restoreContext(backupId);
  // Tell the operator that verification is running.
  restoreFeedback("Verifying the backup and current server files.", "notice-busy");
  const result = await window.pywebview.api.preview_restore(context.profile.profileId, backupId);
  if (!restoreContextActive(context)) return;
  if (!result.success) {
    return restoreFeedback(result.error.message, "notice-error", "alert");
  }
  const preview = result.value;
  if (preview.profile_id !== context.profile.profileId || preview.backup_id !== backupId) return;
  restoreState.preview = Object.freeze({ context, value: preview });
  // Summarize the reviewed plan, its targets, and the recovery step.
  const summary = restoreNode("section", "notice notice-warning");
  summary.append(
    restoreNode("h4", "", `Review backup from ${window.ServerManBackupDisplay.date(preview.created_at)}`),
    restoreNode("p", "", `${preview.replacement_count} replacements; ${preview.creation_count} creations.`),
    restoreNode("p", "", preview.recovery_plan),
  );
  const list = restoreNode("ul", "restore-targets");
  preview.targets.forEach((target) => {
    const kind = target.target_kind === "RUNTIME_PROFILE" ? "Runtime profile" : "DayZ configuration";
    list.append(restoreNode("li", "", `${kind} — ${target.action}: ${target.target_relative}`));
  });
  const apply = restoreNode("button", "button button-danger", "Restore this backup");
  apply.type = "button";
  apply.addEventListener("click", showRestoreConfirmation);
  summary.append(list, apply);
  // Show the reviewed summary and focus its apply action.
  document.getElementById("restore-feedback").replaceChildren(summary);
  apply.focus();
}

// Close the confirmation dialog and optionally restore focus.
function closeRestoreDialog(restoreFocus = true) {
  const modal = restoreState.dialog;
  if (!modal) return;
  // Restore the page behind the dialog.
  modal.inerted.forEach(({ element, inert }) => { element.inert = inert; });
  modal.node.remove();
  restoreState.dialog = null;
  // Return focus to the invoking element when requested.
  if (restoreFocus && modal.returnFocus.isConnected) modal.returnFocus.focus();
}

// Keep keyboard focus inside the restore dialog.
function trapRestoreDialog(event) {
  if (!restoreState.dialog) return;
  // Close the dialog on Escape.
  if (event.key === "Escape") {
    event.preventDefault();
    closeRestoreDialog();
    return;
  }
  if (event.key !== "Tab") return;
  // Wrap focus between the first and last buttons.
  const buttons = [...restoreState.dialog.node.querySelectorAll("button")];
  if (event.shiftKey && document.activeElement === buttons[0]) {
    event.preventDefault(); buttons.at(-1).focus();
  } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
    event.preventDefault(); buttons[0].focus();
  }
}

// Open the modal confirmation for the reviewed restore.
function showRestoreConfirmation() {
  const reviewed = restoreState.preview;
  if (!reviewed || !restoreContextActive(reviewed.context) || restoreState.dialog) return;
  const dialog = restoreNode("section", "panel notice notice-recovery");
  // Describe the dialog as modal and label it.
  dialog.id = "restore-confirmation";
  dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "restore-confirm-title");
  const title = restoreNode("h2", "", "Restore this backup?");
  title.id = "restore-confirm-title";
  // State that configuration files are replaced and recovery copies are created.
  const warning = restoreNode("p", "",
    "DayZ configuration files will be replaced. Verified recovery copies are created first.");
  const actions = restoreNode("div", "action-row");
  const cancel = restoreNode("button", "button button-primary", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", closeRestoreDialog);
  const confirm = restoreNode("button", "button button-danger", "Restore now");
  confirm.type = "button";
  confirm.addEventListener("click", applyRestore);
  actions.append(cancel, confirm);
  dialog.append(title, warning, actions);
  dialog.addEventListener("keydown", trapRestoreDialog);
  // Suspend the page, record the dialog state, and focus the safe choice.
  const inerted = [...document.body.children].map((element) => ({ element, inert: element.inert }));
  document.body.append(dialog);
  restoreState.dialog = Object.freeze({
    inerted: Object.freeze(inerted), node: dialog, returnFocus: document.activeElement,
  });
  inerted.forEach(({ element }) => { element.inert = true; });
  cancel.focus();
}

// Submit the reviewed restore through the host service.
async function applyRestore() {
  const reviewed = restoreState.preview;
  closeRestoreDialog(false);
  if (!reviewed || !restoreContextActive(reviewed.context)) return;
  // Tell the operator that the verified restore is being submitted.
  restoreFeedback("Submitting verified restore.", "notice-busy");
  const value = reviewed.value;
  // Submit the reviewed revisions and digests so stale data cannot be restored.
  const result = await window.pywebview.api.apply_restore(
    value.profile_id, value.backup_id, value.profile_revision, value.settings_revision,
    value.manifest_digest, value.fingerprint,
  );
  if (!restoreContextActive(reviewed.context) || reviewed !== restoreState.preview) return;
  if (!result.success) return restoreFeedback(result.error.message, "notice-error", "alert");
  // Record the queued operation and report the waiting state.
  restoreState.pendingOperation = Object.freeze({
    context: reviewed.context, operationId: result.value.operation_id,
  });
  restoreFeedback("Restore queued. Waiting for source verification.", "notice-busy");
}

// Track the restore operation until it reaches a terminal state.
function restoreOperationFinished(operation) {
  const pending = restoreState.pendingOperation;
  if (!pending || pending.operationId !== operation.operation_id) return false;
  const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state);
  const active = restoreContextActive(pending.context);
  if (terminal) restoreState.pendingOperation = null;
  if (!active) return true;
  // Report progress while the restore is still running.
  if (!terminal) {
    restoreFeedback(`${operation.progress_phase} — ${operation.progress_percent}%`, "notice-busy");
  } else if (operation.state === "SUCCEEDED") {
    // Confirm success only after every target verified.
    restoreFeedback("Restore completed and every published target verified.", "notice-success");
    restoreState.preview = null;
  } else if (operation.state === "RECOVERY_REQUIRED") {
    // Explain the blocked state when recovery is required.
    restoreFeedback(
      "Recovery required. New mutations are blocked; inspect restore recovery diagnostics.",
      "notice-recovery", "alert",
    );
  } else {
    const message = operation.terminal_error ? operation.terminal_error.message : "Restore did not complete.";
    restoreFeedback(message, "notice-error", "alert");
  }
  return true;
}

// Render the restore panel for the current backup history.
async function renderRestore(history) {
  // Invalidate earlier renders and drop the previous panel.
  restoreState.generation += 1;
  const renderGeneration = restoreState.generation;
  const profileContext = captureBackupProfile();
  restoreState.preview = null;
  const prior = document.getElementById("restore-panel");
  if (prior) prior.remove();
  const panel = restoreNode("section", "panel restore-panel");
  panel.id = "restore-panel";
  restoreState.backupId = null;
  panel.hidden = true;
  panel.append(restoreNode("h3", "", "Review restore"));
  const feedback = restoreNode("div", "configuration-feedback");
  feedback.id = "restore-feedback";
  panel.append(feedback);
  document.querySelector(".backup-panel").append(panel);
  // Inspect recovery state before allowing new restores.
  const inspection = await window.pywebview.api.inspect_restore_recovery();
  if (renderGeneration !== restoreState.generation
      || !isBackupProfileActive(profileContext) || !inspection.success) return;
  if (inspection.value.blocked) {
    panel.hidden = false;
    restoreFeedback(
      "Recovery required. Restore journals are uncertain and all new mutations are blocked.",
      "notice-recovery", "alert",
    );
  }
}

// Keep the profile selector pinned while a restore is under review.
function blockRestoreProfileChange(select) {
  if (!restoreState.dialog || !restoreState.preview) return false;
  select.value = restoreState.preview.context.profile.profileId;
  return true;
}

// Publish the restore workspace entry points.
window.ServerManRestore = Object.freeze({
  blockProfileChange: blockRestoreProfileChange,
  operationFinished: restoreOperationFinished,
  render: renderRestore,
  preview: previewRestore,
});
