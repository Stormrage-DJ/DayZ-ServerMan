// Operator wording for the result of a finished operation: the success sentences and the entry point (8.14).
// The failure wording and the error texts are in their own files. Each document that loads the error
// texts also loads this file.
"use strict";

// Result of a mod update that ran to its end, by its download state: sentence and look.
const operationDownloadResults = Object.freeze({
  VERIFIED: ["Mods are downloaded and checked.", "success"],
  EMPTY: ["This profile has no Workshop mods.", "success"],
  FAILED: ["Some mods could not be updated.", "failed"],
  UNKNOWN: ["The mod update could not be confirmed.", "failed"],
  CANCELLED: ["Mod update cancelled.", "cancelled"],
});

// Item states of a verification that mean no problem; a mod in any other state needs the operator.
const operationVerifyGood = Object.freeze({ source: "VERIFIED", target: "MATCHES_SOURCE" });

// Count the mods of a verification result that have a problem with the download or with the server copy.
function verifyProblemCount(items) {
  return (Array.isArray(items) ? items : []).filter((item) =>
    item?.source_state !== operationVerifyGood.source || item?.target_state !== operationVerifyGood.target).length;
}

// Build the success sentence and look of a finished "apply mods and restart" from its start outcome.
function restartApplySuccess(result, labels) {
  if (result.start_state === "NOT_NEEDED") {
    return { sentence: "All mods are current. The server was not restarted. To restart it anyway, use Save & Restart on Overview.", look: "success" };
  }
  if (result.start_state === "CANCELLED") {
    return { sentence: "Mods and keys applied. The server start was cancelled. The server stays stopped.", look: "success" };
  }
  if (result.start_state === "FAILED") {
    const reason = operationErrorText({ code: result.start_error, message: "" });
    return { sentence: `Server stopped and mods applied, but the server could not be started: ${reason}`, look: "failed" };
  }
  return { sentence: result.backup ? "Server backed up, mods applied and server restarted." : labels.success, look: "success" };
}

// Build the success sentence and look of a finished operation from its result.
function successPresentation(operation, labels) {
  const result = operation.result || {};
  if (operation.kind === "UPDATE_WORKSHOP_ITEMS" && Object.hasOwn(operationDownloadResults, String(result.download_state))) {
    const [sentence, look] = operationDownloadResults[result.download_state];
    return { sentence, look };
  }
  // A stop or restart names the backup that it made.
  if (operation.kind === "STOP_SERVER" && result.backup) return { sentence: "Server stopped and backed up.", look: "success" };
  if (operation.kind === "RESTART_SERVER" && result.backup) return { sentence: "Server backed up and restarted.", look: "success" };
  // A verification that found problems says how many mods need the operator.
  const problems = operation.kind === "VERIFY_WORKSHOP_FILES" ? verifyProblemCount(result.items) : 0;
  if (problems) {
    return { sentence: problems === 1 ? "1 mod has a problem." : `${problems} mods have problems.`, look: "warning" };
  }
  if (operation.kind === "APPLY_MODS_AND_RESTART") return restartApplySuccess(result, labels);
  // A medical toggle that changed no group says that no file was changed.
  if (operation.kind === "APPLY_MEDICAL_FEATURE" && result.message === OPERATION_MEDICAL_UNCHANGED) {
    return { sentence: OPERATION_MEDICAL_UNCHANGED, look: "success" };
  }
  if (operation.kind === "PUBLISH_MODS_AND_KEYS") {
    // An apply names the outcome of the server start that it was asked to do.
    if (result.start_state === "STARTED") return { sentence: `${labels.success} The server was started.`, look: "success" };
    if (result.start_state === "CANCELLED") return { sentence: `${labels.success} The server start was cancelled.`, look: "success" };
    if (result.start_state === "NOT_REQUESTED") return { sentence: `${labels.success} Server start was not requested.`, look: "success" };
    if (result.start_state === "FAILED") {
      const reason = operationErrorText({ code: result.start_error, message: "" });
      return { sentence: `${labels.success} The server could not be started: ${reason}`, look: "failed" };
    }
  }
  return { sentence: labels.success, look: "success" };
}

// A backup without the editor records that were not valid (application/backups.py EDITOR_RECORDS_OMITTED)
const OPERATION_EDITOR_RECORDS_OMITTED = "The backup was created without the map editor records of this profile, "
  + "because they are not valid. A restore of this backup does not bring them back.";

// Name the older backups that retention deleted, or failed to delete (application/backups.py retention_notice)
function retentionNotice(result) {
  const backup = result.backup || result;
  const parts = [];
  if (Array.isArray(backup.pruned_backups) && backup.pruned_backups.length) {
    parts.push(`Deleted the older backups beyond the newest ${backup.backups_to_keep} of this profile: `
      + `${backup.pruned_backups.join(", ")}.`);
  }
  if (Array.isArray(backup.prune_failures) && backup.prune_failures.length) {
    parts.push(`These older backups could not be deleted: ${backup.prune_failures.join(", ")}.`);
  }
  return parts.join(" ");
}

// Describe a finished operation: look, result sentence, message, and both joined as one text.
// ServerManOperationMessages.result in operation_messages.js is this function.
function operationResult(operation, seenPhase = null) {
  const labels = window.ServerManOperationLabels.kind(operation?.kind);
  const phase = operation?.last_working_phase || seenPhase;
  let look = "failed"; let sentence = labels.failure; let message = "";
  // A failed or cancelled operation is worded in operation_failures.js.
  if (operation?.state === "SUCCEEDED") {
    ({ sentence, look } = successPresentation(operation, labels));
    // A backup create, or a stop or restart with backup, that left out the editor records says so
    const result = operation.result || {};
    if (result.editor_records_omitted === true || result.backup?.editor_records_omitted === true) message = OPERATION_EDITOR_RECORDS_OMITTED;
    // Older backups that retention deleted are named after it
    message = [message, retentionNotice(result)].filter(Boolean).join(" ");
  } else if (operation?.state === "CANCELLED") {
    ({ sentence, look } = cancelPresentation(operation, phase, labels));
  } else {
    ({ sentence, look, message } = failurePresentation(operation, phase, labels));
  }
  return Object.freeze({ look, sentence, message, text: message ? `${sentence} ${message}` : sentence });
}
