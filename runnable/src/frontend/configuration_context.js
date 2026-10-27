// Shared Configuration context, host failures and terminal-operation reconciliation.
"use strict";

// Turn rejected host promises into the same guarded error path as bridge failures.
async function configurationHostResult(call) {
  try { return await call(); }
  catch (_error) { return {success: false, error: {message: "Configuration request failed. Your edits were preserved."}}; }
}

// Match the pending operation to its original workspace and complete draft generation.
function configurationContextCurrent(context) {
  return window.ServerManWorkspace.isActive(context.workspace)
    && context.generation === configurationState.editGeneration;
}

// Discard the complete draft or reload after an incomplete terminal refresh.
function discardConfigurationChanges() {
  if (configurationState.pendingContext) return;
  if (configurationState.refreshRequired) { void loadSelectedConfiguration(); return; }
  if (configurationState.loaded) renderConfigurationFields(configurationState.loaded);
}

// Focus the first editable configuration field.
function focusConfigurationEditor() {
  document.querySelector("[data-configuration-field]")?.focus();
}

// Refresh both authoritative owners only after terminal success, preserving newer workspaces.
async function reloadConfigurationAfterSuccess(context) {
  try {
    const result = await window.ServerManProfileContext.initialize(true, () => configurationContextCurrent(context));
    if (!configurationContextCurrent(context)) return;
    if (!result.success || !await loadSelectedConfiguration(true)) {
      configurationState.refreshRequired = true;
      showConfigurationError(null, "Changes were saved, but refresh failed. Discard changes to reload before editing again.");
    }
  } catch (_error) {
    if (!window.ServerManWorkspace.isActive(context.workspace)) return;
    configurationState.refreshRequired = true;
    showConfigurationError(null, "Changes were saved, but refresh failed. Discard changes to reload before editing again.");
  } finally {
    if (window.ServerManWorkspace.isActive(context.workspace)) {
      configurationState.pendingContext = null;
      setConfigurationControlsDisabled(false);
      if (configurationState.refreshRequired) document.getElementById("configuration-apply").disabled = true;
    }
  }
}

// A queue result is pending; only its matching terminal event can finish the complete draft.
function configurationOperationFinished(operation) {
  if (!configurationState.pendingOperation || operation.operation_id !== configurationState.pendingOperation) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  const context = configurationState.pendingContext;
  configurationState.pendingOperation = null;
  if (!context || !configurationContextCurrent(context)) return true;
  if (operation.state === "SUCCEEDED") void reloadConfigurationAfterSuccess(context);
  else {
    configurationState.pendingContext = null;
    setConfigurationControlsDisabled(false);
    updateUnsavedState();
    showConfigurationError(null, operation.state === "RECOVERY_REQUIRED"
      ? "Changes require recovery. Your attempted values are preserved. Review the operation result before continuing."
      : "Changes were not saved. Your edits were preserved.");
  }
  return true;
}
