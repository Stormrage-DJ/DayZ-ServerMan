// Apply-mod flow: reviewed publication of downloaded mods and keys into the server folder,
// alone, followed by a server start, or between a stop and a start of a running server.
"use strict";

// Open apply-review dialog, or null when no review is active.
let modPublicationDialog = null;
// Elements made inert while the apply review is open, with their previous state.
let modPublicationInert = [];
// Sentences of the review, per start request and server state.
const modReviewTexts = Object.freeze({
  noStart: "No server start was requested.",
  start: "After the mods are verified in the server folder, DayZ-ServerMan will start this server.",
  inUse: " A mod folder that is in use cannot be replaced; the apply then stops and puts the folders back.",
  useRestart: " Use Update & restart instead.",
  whenStopped: " To be safe, apply the mods when the server is stopped.",
  refused: " Mods cannot be applied to the server folder now.",
  restart: "DayZ-ServerMan will save and stop the server, apply the mods, and start the server again. "
    + "The server is offline during these steps.",
  restartBackup: "DayZ-ServerMan will save and stop the server, create a verified backup, apply the mods, "
    + "and start the server again. The server is offline during these steps.",
  changed: "The server state changed. The mods are downloaded; nothing was applied.",
  short: "No mod folder is copied. Missing key files are added.",
  nothing: "Nothing is written to the server folder: every mod folder and key file is already in place.",
  cancelled: "Apply review cancelled. Downloaded content was not copied to the server.",
  title: "Apply downloaded mods and keys?",
  lead: "Review the server folders that will be updated. Changes use verified rollback protection.",
  notAppliedTitle: "Mods were not applied",
  notAppliedLead: "The reviewed plan was not applied:",
  keepsRunning: " The server keeps running.",
});

// What the review says about a server that is not stopped, per state that the page read.
const modReviewServerStates = Object.freeze({
  RUNNING_MANAGED: "The server is running.",
  RUNNING_EXTERNAL: "The server is running outside DayZ-ServerMan.",
  STARTING: "The server is starting.",
  STOPPING: "The server is stopping.",
});
// The same for a state that is not known or could not be read.
const MOD_REVIEW_STATE_UNCONFIRMED = "The server state could not be confirmed.";
// What the operator can do when a writing apply is refused, per state; another state is not confirmed.
const modReviewRefusedAdvice = Object.freeze({
  RUNNING_MANAGED: " Use Update & restart, or stop the server first.",
  RUNNING_EXTERNAL: " Stop the server first, then update again.",
  STARTING: " Wait until the server runs, then use Update & restart.",
  STOPPING: " Wait until the server is stopped, then update again.",
});
const MOD_REVIEW_REFUSED_UNCONFIRMED = " Check the server state on Overview.";

// Word the server state that the page read; a state that is unknown or was not read is said as that.
function reviewStateSentence(state) {
  const known = typeof state === "string" && Object.hasOwn(modReviewServerStates, state);
  return known ? modReviewServerStates[state] : MOD_REVIEW_STATE_UNCONFIRMED;
}

// Report whether "Update & restart" is offered: the manager runs the server with the selected profile.
function restartOffered(state) {
  return state === "RUNNING_MANAGED" && !window.ServerManServerState.lockReason();
}

// Word the plain apply to a server that is not stopped: the state as it was read, the risk, and the advice.
function unstoppedApplySentence(state) {
  // Only a server that this manager runs can use the restart action.
  return `${reviewStateSentence(state)}${modReviewTexts.inUse}`
    + (restartOffered(state) ? modReviewTexts.useRestart : modReviewTexts.whenStopped);
}

// Word the refusal of a writing apply while the server is not proven stopped: state, refusal, and true advice.
function refusedApplySentence(state) {
  const known = typeof state === "string" && Object.hasOwn(modReviewRefusedAdvice, state);
  // While another profile runs, the restart is not offered, so the advice is to stop the server first.
  const advice = state === "RUNNING_MANAGED" && !restartOffered(state)
    ? modReviewRefusedAdvice.RUNNING_EXTERNAL : modReviewRefusedAdvice[state];
  return `${reviewStateSentence(state)}${modReviewTexts.refused}`
    + (known ? advice : MOD_REVIEW_REFUSED_UNCONFIRMED);
}

// Report whether the reviewed plan would write into the server folder.
function publicationPlanWrites(preview) {
  return (preview.targets || []).some((target) => target.current !== true) || preview.missing_key_count > 0;
}

// List what the reviewed plan writes (QF-055): only the mod folders that are not current and the missing key files,
// or one sentence that nothing is written. The short form never lists a folder.
function publicationReviewLines(preview, short) {
  const folders = short ? [] : (preview.targets || []).filter((target) => target.current !== true)
    .map((target) => `Workshop ${target.workshop_id}: ${target.target_relative}`);
  const missing = Number(preview.missing_key_count) || 0;
  const keys = missing > 0 ? `${missing} of ${preview.key_count} verified key file(s) are added`
    : `${preview.key_count} verified key file(s), all already in place`;
  if (folders.length) return [...folders, keys];
  return missing > 0 ? [modReviewTexts.short, keys] : [modReviewTexts.nothing];
}

// Choose the row of the review: its sentence and what the confirm button submits (null: only "Close").
function publicationVariant(start, state, preview, backup) {
  if (start && state === "STOPPED") return { key: "start", sentence: modReviewTexts.start, submit: "publish" };
  if (start && restartOffered(state)) {
    return { key: backup ? "restart-backup" : "restart", submit: "restart",
      sentence: backup ? modReviewTexts.restartBackup : modReviewTexts.restart };
  }
  if (start) return { key: "changed", sentence: modReviewTexts.changed, submit: null };
  if (state === "STOPPED") return { key: "plain", sentence: modReviewTexts.noStart, submit: "publish" };
  // A plain apply while the server is not stopped follows the policy that the host reports (D10: refuse).
  if (preview.plain_apply_guarded !== true) {
    return { key: "attempt", sentence: unstoppedApplySentence(state), submit: "publish" };
  }
  return publicationPlanWrites(preview)
    ? { key: "refused", sentence: refusedApplySentence(state), submit: null }
    : { key: "plain", sentence: modReviewTexts.noStart, submit: "publish" };
}

// Return the variant of a frozen review for the server state that the page read last.
function currentPublicationVariant(frozen) {
  return publicationVariant(frozen.arguments.startRequested, window.ServerManModsActions.serverState(),
    frozen.preview, window.ServerManProfileContext.backupAfterStop(frozen.arguments.profileId) === true);
}

// Close the apply review and restore focus and inert state.
function closeModPublicationReview() {
  const returnFocus = modPublicationDialog?.returnFocus;
  modPublicationInert.forEach(({ element, inert }) => { element.inert = inert; });
  modPublicationInert = [];
  modPublicationDialog?.remove(); modPublicationDialog = null;
  if (returnFocus?.isConnected) returnFocus.focus();
}

// Cancel the review and say that nothing was copied; a running server is named as untouched.
function cancelModPublicationReview() {
  const restart = modPublicationDialog?.variant?.submit === "restart";
  closeModPublicationReview();
  modsFeedback(`${modReviewTexts.cancelled}${restart ? modReviewTexts.keepsRunning : ""}`);
}

// Keep keyboard focus inside the apply review and treat escape as cancel.
function containModPublicationFocus(event) {
  if (event.key === "Escape") { event.preventDefault(); cancelModPublicationReview(); return; }
  if (event.key !== "Tab" || !modPublicationDialog) return;
  // Wrap focus between the first and last dialog buttons.
  const buttons = [...modPublicationDialog.querySelectorAll("button")];
  if (event.shiftKey && document.activeElement === buttons[0]) {
    event.preventDefault(); buttons.at(-1).focus();
  } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
    event.preventDefault(); buttons[0].focus();
  }
}

// Extract the frozen apply arguments from the finished update operation.
function publicationArguments(operation) {
  const result = operation.result || {};
  return Object.freeze({
    profileId: result.profile_id,
    profileRevision: result.profile_revision,
    profileDigest: result.semantic_profile_digest,
    settingsRevision: result.settings_revision,
    updateOperationId: operation.operation_id,
    startRequested: result.start_requested,
  });
}

// Report whether the frozen context still matches the profile and workspace.
function publicationContextActive(frozen) {
  return isModsContextActive(frozen.context)
    && selectedProfile()?.profile_id === frozen.arguments.profileId;
}

// Read the apply preview of a finished download; null when the view moved on or the read failed.
async function previewModPublication(operation) {
  const args = publicationArguments(operation);
  const frozen = { context: captureModsContext(), arguments: args };
  // Require a profile and start intent from an operation that still matches this view.
  if (!args.profileId || typeof args.startRequested !== "boolean"
      || !publicationContextActive(frozen)) return null;
  modsFeedback("Checking which downloaded mods need to be applied.");
  const response = await window.pywebview.api.preview_mod_publication(
    args.profileId, args.profileRevision, args.profileDigest,
    args.settingsRevision, args.updateOperationId,
  );
  if (!publicationContextActive(frozen)) return null;
  // Report a failed preview without touching the server folder.
  if (!response?.success) {
    modsFeedback(window.ServerManOperationMessages.bridgeError(response, "The apply preview failed safely."), true);
    return null;
  }
  return Object.freeze({ ...frozen, preview: Object.freeze(response.value) });
}

// Read the preview and open the review for it.
async function reviewModPublication(operation) {
  const review = await previewModPublication(operation);
  if (review) showModPublicationReview(review);
}

// Show the reviewed apply dialog with its sentence, targets, keys, and actions.
function showModPublicationReview(review, options = {}) {
  if (!publicationContextActive(review) || modPublicationDialog) return;
  const preview = review.preview;
  const variant = currentPublicationVariant(review);
  const dialog = modsNode("section", "panel notice notice-warning");
  dialog.id = "mod-publication-confirmation"; dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "mod-publication-title");
  dialog.returnFocus = document.activeElement;
  dialog.frozen = review; dialog.variant = variant; dialog.options = options;
  // A row that offers no action asks no question: it says what happened, then shows the plan.
  const title = modsNode("h2", "", variant.submit ? modReviewTexts.title : modReviewTexts.notAppliedTitle);
  title.id = "mod-publication-title";
  const sentence = modsNode("p", "mod-publication-variant", variant.sentence);
  if (variant.submit) dialog.append(title, modsNode("p", "", modReviewTexts.lead), sentence);
  else dialog.append(title, sentence, modsNode("p", "", modReviewTexts.notAppliedLead));
  // Describe only what the apply writes: the folders that are not current and the missing key files.
  const list = modsNode("ul", "restore-targets");
  publicationReviewLines(preview, options.short === true).forEach((line) => list.append(modsNode("li", "", line)));
  // A row that submits nothing has only "Close".
  const actions = modsNode("div", "action-row");
  const cancel = modsNode("button", "button", variant.submit ? "Cancel" : "Close"); cancel.type = "button";
  cancel.addEventListener("click", cancelModPublicationReview);
  actions.append(cancel);
  if (variant.submit) {
    const confirm = modsNode("button", "button button-primary",
      variant.submit === "restart" ? "Stop server, apply and restart" : "Apply mods and keys");
    confirm.type = "button"; confirm.addEventListener("click", confirmModPublication);
    actions.append(window.ServerManBusy.mark(confirm, true));
  }
  dialog.append(list, actions);
  dialog.addEventListener("keydown", containModPublicationFocus);
  document.body.append(dialog); modPublicationDialog = dialog;
  // Make the rest of the page inert while the dialog is open.
  modPublicationInert = [...document.body.children]
    .filter((item) => item !== dialog && !item.hasAttribute("data-announcer"))
    .map((element) => ({ element, inert: element.inert }));
  modPublicationInert.forEach(({ element }) => { element.inert = true; });
  cancel.focus();
}

// Submit the confirmed apply; a server state that changed since the review redraws it and submits nothing.
async function confirmModPublication() {
  const dialog = modPublicationDialog;
  const frozen = dialog?.frozen;
  if (!frozen || !publicationContextActive(frozen)) { closeModPublicationReview(); return; }
  const reviewed = dialog.variant;
  await window.ServerManModsActions.read();
  if (modPublicationDialog !== dialog) return;
  if (!publicationContextActive(frozen)) { closeModPublicationReview(); return; }
  const variant = currentPublicationVariant(frozen);
  closeModPublicationReview();
  if (variant.key !== reviewed.key) { showModPublicationReview(frozen, dialog.options); return; }
  const { arguments: args, preview } = frozen;
  const fields = [args.profileId, args.profileRevision, args.profileDigest, args.settingsRevision,
    args.updateOperationId, preview.publication_fingerprint];
  // A running server is stopped, updated, and started again by one host operation.
  const restart = variant.submit === "restart";
  const response = restart
    ? await window.pywebview.api.apply_mods_and_restart(...fields, variant.key === "restart-backup")
    : await window.pywebview.api.publish_mods_and_keys(...fields);
  if (!publicationContextActive(frozen)) return;
  acceptModsOperation(response, restart ? "Stopping the server, applying mods and starting it again"
    : "Applying downloaded mods and keys");
  // Mark the reviewed targets that are copied in their rows while the apply runs.
  if (response?.success) window.ServerManModsProgress.applying(preview.targets);
}

// Report the result of an apply or restart operation, including the server start outcome.
function modPublicationFinished(operation) {
  if (!["PUBLISH_MODS_AND_KEYS", "APPLY_MODS_AND_RESTART"].includes(operation.kind)) return false;
  if (operation.state !== "SUCCEEDED") return false;
  const result = operation.result || {};
  // Leave other profiles' results to their own workspace.
  if (result.profile_id !== selectedProfile()?.profile_id) return true;
  // Word the apply and the outcome of the requested server start like the operation bar.
  const outcome = window.ServerManOperationBar.pageResult(operation);
  modsFeedback(outcome.text, outcome.look === "failed");
  return true;
}

// Publish the apply review controls used by the mods workspace.
window.ServerManModPublication = Object.freeze({
  reviewFromUpdate: reviewModPublication,
  preview: previewModPublication,
  show: showModPublicationReview,
  operationFinished: modPublicationFinished,
  closeReview: closeModPublicationReview,
});
