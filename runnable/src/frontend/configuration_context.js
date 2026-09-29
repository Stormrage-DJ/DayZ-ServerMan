// Configuration context helpers: discard, focus, profile switch, and completion handling.
"use strict";

// Restore the editor to the last loaded configuration values.
function discardConfigurationChanges() {
  if (configurationState.loaded) renderConfigurationFields(configurationState.loaded);
}

// Focus the first editable configuration field.
function focusConfigurationEditor() {
  const input = document.querySelector("[data-configuration-field]");
  if (input) input.focus();
}

// Switch the shared profile context while the editor stays on the loaded target.
function requestProfile(profileId) {
  // Keep the picker on the loaded profile until the new target loads.
  const current = configurationState.loaded?.profile_id || profileId;
  document.getElementById("configuration-profile").value = current;
  window.ServerManProfileContext.select(profileId);
}

// Reload the configuration after a successful apply while the controls stay locked.
async function reloadConfigurationAfterSuccess() {
  setConfigurationControlsDisabled(true);
  try { await loadSelectedConfiguration(true); }
  finally {
    if (window.ServerManWorkspace.isActive(
      window.ServerManWorkspace.capture("configuration", "shared-configuration"),
    )) setConfigurationControlsDisabled(false);
  }
}

// React to the terminal event of the pending apply operation.
function configurationOperationFinished(operation) {
  // Ignore events for operations this workspace did not queue.
  if (!configurationState.pendingOperation
      || operation.operation_id !== configurationState.pendingOperation) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  // Clear the pending marker once the operation reaches a terminal state.
  configurationState.pendingOperation = null;
  // Reload on success; otherwise unlock the editor and refresh the unsaved state.
  if (operation.state === "SUCCEEDED") void reloadConfigurationAfterSuccess();
  else {
    setConfigurationControlsDisabled(false);
    configurationState.reviewed = null;
    document.getElementById("configuration-apply").textContent = "Review changes";
    updateUnsavedState();
  }
  return true;
}
