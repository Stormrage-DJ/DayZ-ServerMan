// Operator wording for host error codes and for the result of a finished operation. A code is never printed.
"use strict";

// Text shown for an internal fault and for every message that would show an identifier.
const OPERATION_INTERNAL_TEXT = "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.";
// Text for a blocked state that an earlier operation left behind.
const OPERATION_RECOVERY_TEXT = "An earlier operation did not finish cleanly. Changes are blocked until it is resolved. "
  + "Details are in Logs, Manager diagnostics.";
// Text for a rejected request whose host message cannot be shown: it says what to check.
const OPERATION_REQUEST_TEXT = "The request was not accepted. Check the values you entered, then try again. "
  + "Details are in Logs, Manager diagnostics.";
// The causes of a refused submission, as the host names them in the error details, with their wording.
const operationConflictTexts = Object.freeze({
  QUEUE_FULL: "Too many operations are waiting. Wait for one to finish and try again.",
  SHUTTING_DOWN: "DayZ-ServerMan is closing and starts no new operation.",
  RECOVERY_BLOCK: "Changes are blocked until recovery is resolved.",
});
// Text that replaces the host message, per error code.
const operationErrorTexts = Object.freeze({
  INTERNAL_FAILURE: OPERATION_INTERNAL_TEXT,
  CONTRACT_VERSION_UNSUPPORTED: OPERATION_INTERNAL_TEXT,
  EVENT_CURSOR_EXPIRED: OPERATION_INTERNAL_TEXT,
  REVISION_CONFLICT: "The profile or the settings changed in the meantime. Open another page, return to this one, and try again.",
  CONTROL_CONFLICT: "Another DayZ-ServerMan is using this DayZ installation. Close it and try again.",
  PROCESS_STATE_UNKNOWN: "DayZ-ServerMan could not read the running programs, so the server state is not known. Try again.",
  PROCESS_OWNERSHIP_UNPROVEN: "DayZ-ServerMan cannot confirm that it started this DayZ server, so it will not control it.",
  STOP_TIMEOUT: "DayZ did not close in time. Check the server window, then try again.",
  RECOVERY_REQUIRED: OPERATION_RECOVERY_TEXT,
  UPDATE_RESULT_UNKNOWN: "SteamCMD did not close cleanly, so the update cannot be confirmed. Changes are blocked.",
  AUTHENTICATION_REQUIRED: "Choose Steam account sign-in and enter an account name first.",
  AUTHENTICATION_FAILED: "SteamCMD closed before the sign-in was confirmed.",
  STEAMCMD_UNAVAILABLE: "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
  STEAMCMD_PATH_CHANGED: "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
  ENTITLEMENT_DENIED: "This Steam account may not download the item.",
  CONNECTION_FAILED: "Steam could not be reached.",
  WORKSHOP_CONTENT_FAILED: "The downloaded mod files could not be verified. Run the update again.",
  CACHE_VERIFICATION_FAILED: "The downloaded mod files could not be verified. Run the update again.",
  WORKSHOP_MANIFEST_INVALID: "The downloaded mod files could not be verified. Run the update again.",
  UPDATE_CANCELLED: "Mod update cancelled.",
  PUBLICATION_REQUIRED: "Review and apply the mods before the server starts.",
  PUBLICATION_PREVIEW_STALE: "The mods or the server folder changed after the review. Review and apply again.",
  PUBLICATION_FAILED: "The mods could not be copied to the server folder.",
  PUBLICATION_VERIFICATION_FAILED: "The applied mods could not be verified.",
  BACKUP_UNAVAILABLE: "Backups are not available in this installation.",
  RUNTIME_PROFILE_UNRESOLVED: "Set a runtime profile directory in Profiles first.",
  PENDING_RUNTIME_PROFILE_SUPPORT: "This backup has content that this version cannot restore.",
  UNSUPPORTED_SNAPSHOT_CONTENT: "This backup has content that this version cannot restore.",
  PROFILE_CONTEXT_MISMATCH: "This backup belongs to a different profile.",
  SOURCE_CHANGED: "The legacy folder changed after the preview. Preview it again.",
  MIGRATION_CONFLICT: "The legacy folder changed after the preview. Preview it again.",
  GAMEPLAY_NOT_ENABLED: "Turn on \u201CUse gameplay configuration\u201D in Configuration first.",
  OPERATION_NOT_CANCELLABLE: "This operation can no longer be cancelled.",
});
// Known host sentences of a rejected request, by a fragment of the host message.
const operationRequestTexts = Object.freeze([
  ["must contain only letters, digits, or underscore", "The Steam account name may contain only letters, digits and underscores."],
  ["is required in ACCOUNT mode", "Enter a Steam account name for account sign-in."],
  ["must be empty in ANONYMOUS mode", "Leave the account name empty for anonymous sign-in."],
  ["authentication mode", "Choose a sign-in mode for the Steam account."],
  ["must be ACCOUNT or ANONYMOUS", "Choose a sign-in mode for the Steam account."],
]);
// Server state names that a host message may hold, with their wording inside a sentence.
const operationServerStates = Object.freeze({
  RUNNING_EXTERNAL: "running outside DayZ-ServerMan", RUNNING_MANAGED: "running",
  STOPPED: "stopped", STARTING: "starting", STOPPING: "stopping",
  AMBIGUOUS: "in an unknown state", UNKNOWN: "in an unknown state",
});
// Result of a mod update that ran to its end, by its download state: sentence and look.
const operationDownloadResults = Object.freeze({
  VERIFIED: ["Mods are downloaded and checked.", "success"],
  EMPTY: ["This profile has no Workshop mods.", "success"],
  FAILED: ["Some mods could not be updated.", "failed"],
  UNKNOWN: ["The mod update could not be confirmed.", "failed"],
  CANCELLED: ["Mod update cancelled.", "cancelled"],
});

// Sentences of "apply mods and restart" for a failed or cancelled end, chosen by the last working phase.
const operationRestartApplyTexts = Object.freeze({
  cancelledRunning: "Cancelled. The server keeps running; nothing was applied.",
  cancelledStopped: "Cancelled. The server stays stopped; the mods were not applied.",
  preflight: "The reviewed plan is out of date, or the server state changed. The server keeps running; nothing was applied. Update again.",
  stop: "The server could not be stopped. Nothing was applied.",
  stopNotRunning: "The server was already stopped, so it was not restarted. Nothing was applied. To apply the mods and start the server, use Update & start.",
  backup: "Server stopped, but the backup failed. The mods were not applied and the server was not started again.",
  check: "Mods applied, but the server folder changed before the start. The server stays stopped.",
  apply: "Server stopped, but the mods could not be applied. The server folder is as it was. The server was not started again.",
  applyBlocked: "Server stopped, but the mods could not be applied.",
  applyRefused: "The server was stopped, but its state changed before the mods could be applied. Nothing was applied; the server folder is as it was.",
  startCheck: "Mods applied, but the server folder changed before the start. The server was not started.",
  start: "Mods applied, but the server start did not succeed or could not be confirmed. Check the server state on Overview.",
});
// Error codes of a write guard that refused the apply because the server was not stopped.
const operationGuardCodes = Object.freeze(["CONTROL_CONFLICT", "EXTERNAL_PROCESS", "PROCESS_STATE_UNKNOWN"]);

// Item states of a verification that mean no problem; a mod in any other state needs the operator.
const operationVerifyGood = Object.freeze({ source: "VERIFIED", target: "MATCHES_SOURCE" });

// Find the name of the server state that a host message names last; null when it names none.
function namedServerStateName(message) {
  const found = [...String(message).matchAll(/\b(RUNNING_EXTERNAL|RUNNING_MANAGED|STOPPED|STARTING|STOPPING|AMBIGUOUS|UNKNOWN)\b/g)];
  return found.length ? found.at(-1)[1] : null;
}

// Find the server state that a host message names last, as wording inside a sentence.
function namedServerState(message) {
  const name = namedServerStateName(message);
  return name ? operationServerStates[name] : null;
}

// Turn a host error into operator text; the fallback replaces a missing error.
function operationErrorText(error, fallback = OPERATION_INTERNAL_TEXT) {
  if (!error || typeof error !== "object") return fallback;
  const code = typeof error.code === "string" ? error.code : "";
  const message = typeof error.message === "string" ? error.message : "";
  // A message that names a server state becomes one sentence about that state.
  const state = namedServerState(message);
  if (state || code === "EXTERNAL_PROCESS") {
    return `This cannot be done while the server is ${state || operationServerStates.RUNNING_EXTERNAL}.`;
  }
  if (code === "MUTATION_CONFLICT") return mutationConflictText(error.details?.reason, message);
  if (Object.hasOwn(operationErrorTexts, code)) return operationErrorTexts[code];
  // A rejected request with a known host sentence has its own wording.
  const known = code === "INVALID_REQUEST" && operationRequestTexts.find(([fragment]) => message.includes(fragment));
  if (known) return known[1];
  // Any other host sentence is shown, with its identifiers translated in place.
  const sentence = window.ServerManHostSentences.sentence(message);
  if (sentence) return sentence;
  if (code === "INVALID_REQUEST") return OPERATION_REQUEST_TEXT;
  return message ? OPERATION_INTERNAL_TEXT : fallback;
}

// Word a refused submission by its cause: a full queue, a recovery block with its reason, or the closing manager.
function mutationConflictText(reason, message) {
  // A host that names no cause is read by its message.
  const cause = typeof reason === "string" && reason ? reason
    : message.includes("queue is full") ? "QUEUE_FULL"
      : message.includes("lane is draining") ? "SHUTTING_DOWN" : "RECOVERY_BLOCK";
  if (cause === "RECOVERY_BLOCK") {
    return `${operationConflictTexts.RECOVERY_BLOCK} ${window.ServerManHostSentences.blockReason(message)}`;
  }
  if (Object.hasOwn(operationConflictTexts, cause)) return operationConflictTexts[cause];
  return window.ServerManHostSentences.sentence(message) || OPERATION_INTERNAL_TEXT;
}

// Word the Overview notice of a recovery block: the reason, what it blocks, and where the details are.
function recoveryNoticeText(reason) {
  return `${window.ServerManHostSentences.blockReason(reason)} Changes are blocked until this is resolved. `
    + "Details are in Logs, Manager diagnostics.";
}

// Count the mods of a verification result that have a problem with the download or with the server copy.
function verifyProblemCount(items) {
  return (Array.isArray(items) ? items : []).filter((item) =>
    item?.source_state !== operationVerifyGood.source || item?.target_state !== operationVerifyGood.target).length;
}

// Word the text of a failed bridge call for a page notice.
function bridgeErrorText(result, fallback) {
  return operationErrorText(result?.error, fallback);
}

// Choose the failure sentence of a stop or restart from the phase that worked last.
function lifecycleFailureSentence(kind, phase, generic) {
  const backup = String(phase || "").startsWith("BACKUP_");
  if (kind === "STOP_SERVER" && backup) return "Server stopped, but the backup failed.";
  if (kind === "RESTART_SERVER" && backup) {
    return "Server stopped, but the backup failed. The server was not started again.";
  }
  if (kind === "RESTART_SERVER" && phase === "START_SERVER") {
    return "Server stopped, but it could not be started again.";
  }
  return generic;
}

// Choose the sentence of a failed or cancelled "apply mods and restart"; null means the generic sentence.
function restartApplySentence(state, phase, error = null) {
  const name = typeof phase === "string" ? phase : "";
  const texts = operationRestartApplyTexts;
  // Before the stop the server still runs; every later safe point leaves it stopped.
  if (state === "CANCELLED") return ["", "preflight"].includes(name) ? texts.cancelledRunning : texts.cancelledStopped;
  if (!name) return null;
  // The server state that the host names in its refusal, when it names one.
  const named = namedServerStateName(typeof error?.message === "string" ? error.message : "");
  if (name === "preflight") return texts.preflight;
  // A stop that was refused because the server was already stopped did not fail to stop it.
  if (name === "STOP_SERVER") return named === "STOPPED" ? texts.stopNotRunning : texts.stop;
  if (name.startsWith("BACKUP_")) return texts.backup;
  if (name === "VERIFY_BEFORE_START") return texts.check;
  // A failure of the start handoff proves neither a changed folder nor a stopped server.
  if (name === "START_SERVER") return texts.start;
  if (state === "RECOVERY_REQUIRED") return texts.applyBlocked;
  // A write guard that found the server no longer stopped applied nothing.
  return named && named !== "STOPPED" && operationGuardCodes.includes(error?.code) ? texts.applyRefused : texts.apply;
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

// Describe a finished operation: look, result sentence, message, and both joined as one text.
function operationResult(operation, seenPhase = null) {
  const labels = window.ServerManOperationLabels.kind(operation?.kind);
  const phase = operation?.last_working_phase || seenPhase;
  let look = "failed"; let sentence = labels.failure; let message = "";
  if (operation?.state === "SUCCEEDED") {
    ({ sentence, look } = successPresentation(operation, labels));
  } else if (operation?.state === "CANCELLED") {
    // A stop or restart that never reached its backup did not stop the server.
    const untouched = ["STOP_SERVER", "RESTART_SERVER"].includes(operation.kind)
      && !String(phase || "").startsWith("BACKUP_");
    look = "cancelled";
    sentence = untouched ? window.ServerManOperationLabels.kind(null).cancelled : labels.cancelled;
    if (operation.kind === "APPLY_MODS_AND_RESTART") sentence = restartApplySentence("CANCELLED", phase);
  } else {
    const failure = operation?.terminal_error || operation?.error;
    const texts = operationRestartApplyTexts;
    sentence = lifecycleFailureSentence(operation?.kind, phase, labels.failure);
    if (operation?.kind === "APPLY_MODS_AND_RESTART") sentence = restartApplySentence(operation.state, phase, failure) || sentence;
    // An apply with a start that failed in the check before the start, or in the start, did apply the mods.
    if (operation?.kind === "PUBLISH_MODS_AND_KEYS" && phase === "VERIFY_BEFORE_START") sentence = texts.startCheck;
    if (operation?.kind === "PUBLISH_MODS_AND_KEYS" && phase === "START_SERVER") sentence = texts.start;
    message = operationErrorText(failure, "");
    // These sentences already say what the text of their error code or of the named state says.
    const code = operation?.terminal_error?.code;
    if ((sentence === texts.preflight && code === "PUBLICATION_PREVIEW_STALE") || sentence === texts.stopNotRunning
        || ([texts.check, texts.startCheck].includes(sentence) && code === "PUBLICATION_FAILED")) message = "";
    // A launch failure of a start or restart is already said by the sentence.
    if (operation?.terminal_error?.code === "LAUNCH_FAILED"
        && ["START_SERVER", "RESTART_SERVER"].includes(operation.kind)) message = "";
    if (operation?.state === "RECOVERY_REQUIRED") { look = "recovery"; sentence = `${sentence} Changes are blocked.`; }
  }
  return Object.freeze({ look, sentence, message, text: message ? `${sentence} ${message}` : sentence });
}

// Publish the message lookups used by the bar and by every page notice.
window.ServerManOperationMessages = Object.freeze({
  error: operationErrorText,
  bridgeError: bridgeErrorText,
  result: operationResult,
  recoveryText: OPERATION_RECOVERY_TEXT,
  recoveryNotice: recoveryNoticeText,
});
