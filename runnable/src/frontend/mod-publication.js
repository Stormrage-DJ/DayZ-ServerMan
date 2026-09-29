// Apply-mod flow: reviewed publication of downloaded mods and keys into the server folder.
"use strict";

// Open apply-review dialog, or null when no review is active.
let modPublicationDialog = null;
// Elements made inert while the apply review is open, with their previous state.
let modPublicationInert = [];

// Close the apply review and restore focus and inert state.
function closeModPublicationReview() {
  const returnFocus = modPublicationDialog?.returnFocus;
  modPublicationInert.forEach(({ element, inert }) => { element.inert = inert; });
  modPublicationInert = [];
  modPublicationDialog?.remove(); modPublicationDialog = null;
  if (returnFocus?.isConnected) returnFocus.focus();
}

// Keep keyboard focus inside the apply review and treat escape as cancel.
function containModPublicationFocus(event) {
  // Cancel the review on escape; downloaded content was not copied to the server.
  if (event.key === "Escape") {
    event.preventDefault(); closeModPublicationReview();
    modsFeedback("Apply review cancelled. Downloaded content was not copied to the server.");
    return;
  }
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

// Build the apply preview for a finished download before any file is copied.
async function reviewModPublication(operation) {
  const args = publicationArguments(operation);
  const frozen = Object.freeze({ context: captureModsContext(), arguments: args });
  // Require a profile and start intent from an operation that still matches this view.
  if (!args.profileId || typeof args.startRequested !== "boolean"
      || !publicationContextActive(frozen)) return;
  modsFeedback("Checking which downloaded mods need to be applied.");
  // Ask the host which downloaded mods need to be applied.
  const response = await window.pywebview.api.preview_mod_publication(
    args.profileId, args.profileRevision, args.profileDigest,
    args.settingsRevision, args.updateOperationId,
  );
  if (!publicationContextActive(frozen)) return;
  // Report a failed preview without touching the server folder.
  if (!response?.success) {
    modsFeedback(response?.error?.message || "The apply preview failed safely.", true);
    return;
  }
  showModPublicationReview(frozen, response.value);
}

// Show the reviewed apply dialog with its targets, keys, and actions.
function showModPublicationReview(frozen, preview) {
  if (!publicationContextActive(frozen) || modPublicationDialog) return;
  const dialog = modsNode("section", "panel notice notice-warning");
  dialog.id = "mod-publication-confirmation"; dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "mod-publication-title");
  dialog.returnFocus = document.activeElement;
  dialog.frozen = Object.freeze({ ...frozen, preview: Object.freeze(preview) });
  const title = modsNode("h2", "", "Apply downloaded mods and keys?");
  title.id = "mod-publication-title";
  dialog.append(title, modsNode("p", "",
    "Review the server folders that will be updated. Changes use verified rollback protection."));
  dialog.append(modsNode("p", "", frozen.arguments.startRequested
    ? "After the mods are verified in the server folder, DayZ-ServerMan will start this server."
    : "No server start was requested."));
  // Describe the update targets and the verified key files.
  const list = modsNode("ul", "restore-targets");
  (preview.targets || []).forEach((target) => list.append(
    modsNode("li", "", `Workshop ${target.workshop_id}: ${target.target_relative}`),
  ));
  list.append(modsNode("li", "", `${preview.key_count} verified key file(s)`));
  // Wire cancel and confirm actions for the review.
  const actions = modsNode("div", "action-row");
  const cancel = modsNode("button", "button", "Cancel"); cancel.type = "button";
  cancel.addEventListener("click", closeModPublicationReview);
  const confirm = modsNode("button", "button button-primary", "Apply mods and keys");
  confirm.type = "button"; confirm.addEventListener("click", confirmModPublication);
  actions.append(cancel, confirm); dialog.append(list, actions);
  dialog.addEventListener("keydown", containModPublicationFocus);
  document.body.append(dialog); modPublicationDialog = dialog;
  // Make the rest of the page inert while the dialog is open.
  modPublicationInert = [...document.body.children].filter((item) => item !== dialog)
    .map((element) => ({ element, inert: element.inert }));
  modPublicationInert.forEach(({ element }) => { element.inert = true; });
  cancel.focus();
}

// Submit the confirmed apply request through the shared operation flow.
async function confirmModPublication() {
  const frozen = modPublicationDialog?.frozen;
  if (!frozen || !publicationContextActive(frozen)) {
    closeModPublicationReview(); return;
  }
  const { arguments: args, preview } = frozen;
  closeModPublicationReview();
  // Publish against the reviewed fingerprint of the server folders.
  const response = await window.pywebview.api.publish_mods_and_keys(
    args.profileId, args.profileRevision, args.profileDigest, args.settingsRevision,
    args.updateOperationId, preview.publication_fingerprint,
  );
  if (!publicationContextActive(frozen)) return;
  acceptModsOperation(response, "Applying downloaded mods and keys");
}

// Report the result of an apply operation, including the server start outcome.
function modPublicationFinished(operation) {
  if (operation.kind !== "PUBLISH_MODS_AND_KEYS") return false;
  if (operation.state !== "SUCCEEDED") return false;
  const result = operation.result || {};
  // Leave other profiles' results to their own workspace.
  if (result.profile_id !== selectedProfile()?.profile_id) return true;
  const start = result.start_state === "STARTED"
    ? " Server start was authorized and completed."
    : result.start_state === "FAILED"
      ? ` Mods are verified, but server start failed: ${result.start_error}.`
      : result.start_state === "CANCELLED"
        ? " Mods are verified, but server start was cancelled before launch."
      : " Server start was not requested.";
  modsFeedback(`Mods and keys are applied and verified.${start}`, result.start_state === "FAILED");
  return true;
}

// Publish the apply review controls used by the mods workspace.
window.ServerManModPublication = Object.freeze({
  reviewFromUpdate: reviewModPublication,
  operationFinished: modPublicationFinished,
  closeReview: closeModPublicationReview,
});
