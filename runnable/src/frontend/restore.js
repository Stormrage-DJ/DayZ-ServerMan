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

// Notice for a restore that did not finish cleanly, after a restore and when the history opens.
const RESTORE_RECOVERY_TEXT = "Recovery required. Changes are blocked until an unfinished restore is resolved. "
  + "Details are in Logs, Manager diagnostics.";

// Notice for a recovery check that waits, because a running operation holds the DayZ installation.
const RESTORE_DEFERRED_TEXT = "Restore recovery is checked again when the running operation finishes.";

// Word a blocked recovery inspection: the reason that the host names, else the general notice.
// The host names a reason when the server is not proven stopped, so the operator learns what to do.
function restoreRecoveryText(inspection) {
  const reason = inspection?.reason;
  const sentences = window.ServerManHostSentences;
  return reason && sentences ? sentences.blockReason(reason) : RESTORE_RECOVERY_TEXT;
}

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
    return restoreFeedback(window.ServerManOperationMessages.bridgeError(result, "The backup could not be reviewed."),
      "notice-error", "alert");
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
    const action = window.ServerManDiagnosticLabels.restoreAction(target.action);
    list.append(restoreNode("li", "", `${kind} — ${action}: ${target.target_relative}`));
  });
  const apply = restoreNode("button", "button button-danger", "Restore this backup");
  apply.type = "button";
  apply.addEventListener("click", showRestoreConfirmation);
  window.ServerManBusy?.mark(apply);
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
  window.ServerManBusy?.mark(confirm, true);
  actions.append(cancel, confirm);
  dialog.append(title, warning, actions);
  dialog.addEventListener("keydown", trapRestoreDialog);
  // Suspend the page, record the dialog state, and focus the safe choice.
  const inerted = [...document.body.children]
    .filter((element) => !element.hasAttribute("data-announcer"))
    .map((element) => ({ element, inert: element.inert }));
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
  if (!result.success) {
    return restoreFeedback(window.ServerManOperationMessages.bridgeError(result, "The restore could not be started."),
      "notice-error", "alert");
  }
  // Record the queued operation; the operation bar shows the waiting state and the progress.
  restoreState.pendingOperation = Object.freeze({
    context: reviewed.context, operationId: result.value.operation_id,
  });
  document.getElementById("restore-feedback").replaceChildren();
  window.ServerManOperationBar?.adopt(result.value.operation_id);
}

// Track the restore operation until it reaches a terminal state.
function restoreOperationFinished(operation) {
  const pending = restoreState.pendingOperation;
  if (!pending || pending.operationId !== operation.operation_id) return false;
  const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state);
  const active = restoreContextActive(pending.context);
  if (terminal) restoreState.pendingOperation = null;
  if (!active || !terminal) return true;
  // The page shows the result itself, so the operation bar does not announce it again.
  const outcome = window.ServerManOperationBar?.pageResult(operation);
  if (operation.state === "SUCCEEDED") {
    // Confirm success only after every target verified.
    restoreFeedback("Restore completed and every published target verified.", "notice-success");
    restoreState.preview = null;
  } else if (operation.state === "RECOVERY_REQUIRED") {
    // Explain the blocked state when recovery is required.
    restoreFeedback(RESTORE_RECOVERY_TEXT, "notice-recovery", "alert");
  } else {
    const message = outcome?.text || "Restore did not complete.";
    restoreFeedback(message, operation.state === "CANCELLED" ? "" : "notice-error",
      operation.state === "CANCELLED" ? "status" : "alert");
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
  if (inspection.value.deferred) {
    // The journal may belong to the running restore, so nothing is claimed to be interrupted; focus stays.
    panel.hidden = false;
    const notice = restoreNode("div", "notice", RESTORE_DEFERRED_TEXT);
    notice.setAttribute("role", "status");
    feedback.replaceChildren(notice);
  } else if (inspection.value.blocked) {
    panel.hidden = false;
    restoreFeedback(restoreRecoveryText(inspection.value), "notice-recovery", "alert");
  }
}

// Close an open restore review: forget the reviewed plan and expire every request of it.
function closeRestoreReview() {
  closeRestoreDialog(false);
  restoreState.generation += 1;
  restoreState.backupId = null;
  restoreState.preview = null;
}

// Publish the restore workspace entry points.
window.ServerManRestore = Object.freeze({
  close: closeRestoreReview,
  operationFinished: restoreOperationFinished,
  render: renderRestore,
  preview: previewRestore,
});
