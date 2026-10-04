// Mods operations: tracks the operation that this page queued and shows its result in the page.
// The operation bar shows name, phase, progress, and Cancel; this module keeps the result detail in context.
"use strict";

// Operation kinds whose messages belong to the sign-in panel.
const modsSigninKinds = Object.freeze(["SAVE_STEAM_SETTINGS", "AUTHENTICATE_STEAMCMD"]);

// Track a queued host operation and show its start message in the panel that the action belongs to.
function acceptModsOperation(result, message, signin = false) {
  const say = signin ? window.ServerManModsSignin.feedback : modsFeedback;
  // Report failures without leaving any pending state behind.
  if (!result || !result.success) return say(
    window.ServerManOperationMessages.bridgeError(result, "The request failed safely."), true);
  const profile = selectedProfile();
  // Record the queued operation against the current generation and profile.
  modsState.pending = Object.freeze({ id: result.value.operation_id,
    generation: modsState.generation, profileId: profile ? profile.profile_id : "" });
  setModsBusy(true);
  // A new operation outdates the row marks of the one before it.
  window.ServerManModsProgress.clear();
  say(message);
  // Hand the accepted operation to the operation bar without waiting for the next poll.
  window.ServerManOperationBar.adopt(result.value.operation_id);
}

// Apply operation events to the mods workspace and its action states.
function modsOperationFinished(operation) {
  if (!operation || !modsState.pending || operation.operation_id !== modsState.pending.id) return false;
  // A sign-in operation reports beside the sign-in form, every other one under the check header.
  const say = modsSigninKinds.includes(operation.kind) ? window.ServerManModsSignin.feedback : modsFeedback;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) {
    // Keep the row marks of a verification current; phase, percent, and Cancel are in the operation bar.
    window.ServerManModsVerify.progress(operation);
    window.ServerManModsProgress.changed(operation);
    if (operation.state === "CANCELLING") say(window.ServerManOperationLabels.cancelling(operation.kind));
    return true;
  }
  // Clear the pending operation and unlock the controls on terminal states.
  modsState.pending = null;
  setModsBusy(false);
  // Refresh the update state and rows, because the operation may have changed mod content.
  void window.ServerManUpdateStatus.recheck();
  void window.ServerManModsActions.read();
  window.ServerManModsProgress.finished(operation);
  if (operation.kind === "VERIFY_WORKSHOP_FILES") return window.ServerManModsVerify.finished(operation);
  // An update reports its own result: the unverified cases, "everything is current", or the apply review.
  if (operation.kind === "UPDATE_WORKSHOP_ITEMS") {
    return window.ServerManModsActions.finished(operation, window.ServerManOperationBar.pageResult(operation));
  }
  // Report a failure or a cancellation with the same wording as the operation bar.
  if (operation.state !== "SUCCEEDED") {
    const outcome = window.ServerManOperationBar.pageResult(operation);
    say(outcome.text, outcome.look !== "cancelled");
    return true;
  }
  // Reopen the workspace after settings are saved.
  if (operation.kind === "SAVE_STEAM_SETTINGS") { openMods(); return true; }
  const outcome = window.ServerManOperationBar.pageResult(operation);
  if (operation.kind === "AUTHENTICATE_STEAMCMD") {
    say(`${outcome.sentence} Credentials remain owned by SteamCMD.`);
    return true;
  }
  if (window.ServerManModPublication.operationFinished(operation)) return true;
  const result = operation.result || {};
  if (result.profile_id && result.profile_id !== selectedProfile()?.profile_id) return true;
  const summary = result.start_error === "PUBLICATION_REQUIRED"
    ? "Downloads were checked. Review and apply the mods before the server starts." : outcome.text;
  modsFeedback(summary, outcome.look === "failed");
  renderModsItems(result.items);
  return true;
}
