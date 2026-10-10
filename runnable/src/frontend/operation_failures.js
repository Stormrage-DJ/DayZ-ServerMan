// Operator wording for a finished operation that failed or was cancelled. The phase that worked last
// chooses the sentence. The sentence tables and the error texts stay in operation_messages.js.
"use strict";

// Error codes of a write guard that refused the apply because the server was not stopped.
const operationGuardCodes = Object.freeze(["CONTROL_CONFLICT", "EXTERNAL_PROCESS", "PROCESS_STATE_UNKNOWN"]);

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

// Build the sentence and look of a cancelled operation from the phase that it reached.
function cancelPresentation(operation, phase, labels) {
  // A stop or restart that never reached its backup did not stop the server.
  const untouched = ["STOP_SERVER", "RESTART_SERVER"].includes(operation.kind)
    && !String(phase || "").startsWith("BACKUP_");
  let sentence = untouched ? window.ServerManOperationLabels.kind(null).cancelled : labels.cancelled;
  if (operation.kind === "APPLY_MODS_AND_RESTART") sentence = restartApplySentence("CANCELLED", phase);
  return { sentence, look: "cancelled" };
}

// Build the sentence, look, and message of an operation that failed or that left changes blocked.
function failurePresentation(operation, phase, labels) {
  const failure = operation?.terminal_error || operation?.error;
  const texts = operationRestartApplyTexts;
  let look = "failed";
  // The phase that worked last chooses the sentence of a stop, a restart, or an apply.
  let sentence = lifecycleFailureSentence(operation?.kind, phase, labels.failure);
  if (operation?.kind === "APPLY_MODS_AND_RESTART") sentence = restartApplySentence(operation.state, phase, failure) || sentence;
  // An apply with a start that failed in the check before the start, or in the start, did apply the mods.
  if (operation?.kind === "PUBLISH_MODS_AND_KEYS" && phase === "VERIFY_BEFORE_START") sentence = texts.startCheck;
  if (operation?.kind === "PUBLISH_MODS_AND_KEYS" && phase === "START_SERVER") sentence = texts.start;
  let message = operationErrorText(failure, "", operation?.kind);
  // These sentences already say what the text of their error code or of the named state says.
  const code = operation?.terminal_error?.code;
  if ((sentence === texts.preflight && code === "PUBLICATION_PREVIEW_STALE") || sentence === texts.stopNotRunning
      || ([texts.check, texts.startCheck].includes(sentence) && code === "PUBLICATION_FAILED")) message = "";
  // A launch failure of a start or restart is already said by the sentence.
  if (operation?.terminal_error?.code === "LAUNCH_FAILED"
      && ["START_SERVER", "RESTART_SERVER"].includes(operation.kind)) message = "";
  // The result sentence states the block once, so the message does not state it again.
  if (operation?.state === "RECOVERY_REQUIRED") {
    look = "recovery"; sentence = `${sentence} Changes are blocked.`; message = message.replace(OPERATION_BLOCK_SENTENCE, "").trim();
  }
  return { look, sentence, message };
}
