// Profile deletion flow with a guarded confirmation dialog.
"use strict";

// Deletion dialog state: the open dialog and the elements suspended behind it.
let profileDeleteDialog = null;
let profileDeleteInert = [];

// Choose the profile to select after a deletion.
function replacementAfterDelete(profileId) {
  const index = profileState.records.findIndex((item) => item.profile_id === profileId);
  // Fall back to the previous entry when no next entry exists.
  return profileState.records[index + 1]?.profile_id
    || profileState.records[index - 1]?.profile_id || null;
}

// Close the dialog, restore the page, and return focus to the opener.
function closeDeleteDialog() {
  const returnFocus = profileDeleteDialog?.returnFocus;
  // Restore the suspended page before removing the dialog.
  profileDeleteInert.forEach(({ element, inert }) => { element.inert = inert; });
  profileDeleteInert = [];
  profileDeleteDialog?.remove(); profileDeleteDialog = null;
  // Return focus to the element that opened the dialog.
  if (returnFocus?.isConnected) returnFocus.focus();
}

// Keep keyboard focus inside the open dialog.
function containDeleteFocus(event) {
  // Treat Escape as closing the dialog.
  if (event.key === "Escape") { event.preventDefault(); closeDeleteDialog(); return; }
  if (event.key !== "Tab" || !profileDeleteDialog) return;
  // Wrap focus between the first and last dialog buttons.
  const buttons = [...profileDeleteDialog.querySelectorAll("button")];
  if (event.shiftKey && document.activeElement === buttons[0]) {
    event.preventDefault(); buttons.at(-1).focus();
  } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
    event.preventDefault(); buttons[0].focus();
  }
}

// Ask the transition guard to discard profile edits before deletion.
function requestDeleteProfile() {
  window.ServerManTransitions.requestOwnerTransition(
    "profiles", "Discard profile changes before deleting this profile.",
    showDeleteConfirmation,
  );
}

// Build and open the delete confirmation for the selected profile.
function showDeleteConfirmation() {
  if (profileDeleteDialog || !profileState.selected) return;
  // Capture the profile context and replacement before the dialog opens.
  const frozen = Object.freeze({
    context: captureProfileContext(),
    replacementProfileId: replacementAfterDelete(profileState.selected.profile_id),
  });
  const dialog = profileNode("section", "panel notice notice-error");
  // Mark the dialog as modal and name it for assistive technology.
  dialog.id = "profile-delete-confirmation"; dialog.setAttribute("role", "alertdialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "profile-delete-title");
  dialog.returnFocus = document.activeElement; dialog.frozen = frozen;
  // Distinguish deleted live data from retained recovery archives.
  const title = profileNode("h2", "", "Delete profile?"); title.id = "profile-delete-title";
  const copy = profileNode(
    "p", "", `Permanently delete ${profileState.selected.display_name}, including its generated configuration, runtime files, exclusive world storage, schedule, and saved preferences. Existing backup archives will remain in the configured backup destination.`,
  );
  const actions = profileNode("div", "action-row");
  const cancel = profileNode("button", "button", "Cancel"); cancel.type = "button";
  cancel.addEventListener("click", closeDeleteDialog);
  const confirm = profileNode("button", "button button-danger", "Delete profile data");
  confirm.type = "button"; confirm.addEventListener("click", confirmDeleteProfile);
  actions.append(cancel, confirm); dialog.append(title, copy, actions);
  dialog.addEventListener("keydown", containDeleteFocus);
  document.body.append(dialog); profileDeleteDialog = dialog;
  // Suspend the page behind the dialog and focus the safe choice first.
  profileDeleteInert = [...document.body.children].filter((item) => item !== dialog)
    .map((element) => ({ element, inert: element.inert }));
  profileDeleteInert.forEach(({ element }) => { element.inert = true; }); cancel.focus();
}

// Submit the deletion and queue the follow-up operation.
async function confirmDeleteProfile() {
  const frozen = profileDeleteDialog?.frozen; if (!frozen) return;
  closeDeleteDialog(); const { context, replacementProfileId } = frozen;
  // Submit the captured revision so a stale deletion cannot run.
  const result = await window.pywebview.api.delete_profile(context.profileId, context.revision);
  if (!profileContextActive(context)) return;
  if (!result.success) return window.ServerManUi.renderHostError(result);
  // Record the queued operation so the workspace can track it.
  profileState.pending = Object.freeze({
    operationId: result.value.operation_id, kind: "delete", context,
    editGeneration: profileState.editGeneration, preferredProfileId: replacementProfileId,
  });
}
