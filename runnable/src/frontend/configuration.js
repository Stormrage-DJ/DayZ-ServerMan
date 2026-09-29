// Owns the server configuration workspace: guided editing, review, and atomic apply.
"use strict";

// Tracks the loaded target, edited values, review result, and pending apply operation.
const configurationState = {
  loaded: null,
  values: new Map(),
  // Frozen preview result that the apply action is allowed to submit.
  reviewed: null,
  loadSequence: 0,
  // Bumped on every edit so a stale review cannot be applied.
  editGeneration: 0,
  pendingOperation: null,
};

// Create one interface element with an optional class and text.
function configElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Show a configuration failure notice with the host message or a fallback line.
function showConfigurationError(result, fallback) {
  const error = result && result.error ? result.error : {};
  const message = typeof error.message === "string" ? error.message : fallback;
  const notice = configElement("div", "notice notice-error", message);
  notice.setAttribute("role", "alert");
  document.getElementById("configuration-feedback").replaceChildren(notice);
}

// Collect every edited value that differs from the loaded configuration.
function changedValues() {
  return window.ServerManConfigurationEdit.changedValues(configurationState.values);
}

// Refresh the unsaved indicator and review action for the current edits.
function updateUnsavedState() {
  // Invalidate any previous review as soon as the edits change.
  configurationState.editGeneration += 1;
  configurationState.reviewed = null;
  const indicator = document.getElementById("configuration-unsaved");
  const action = document.getElementById("configuration-apply");
  let changes;
  try {
    changes = changedValues();
  } catch (error) {
    window.ServerManTransitions.setDirty("shared-configuration", true);
    indicator.textContent = error.message;
    indicator.className = "unsaved-indicator is-dirty";
    action.disabled = true;
    action.textContent = "Review changes";
    showConfigurationError(null, error.message);
    return;
  }
  // Summarize the unsaved count and gate the review action on it.
  const count = Object.keys(changes).length;
  window.ServerManTransitions.setDirty("shared-configuration", count > 0);
  indicator.textContent = count ? `${count} unsaved change${count === 1 ? "" : "s"}` : "No unsaved changes";
  indicator.className = count ? "unsaved-indicator is-dirty" : "unsaved-indicator";
  action.disabled = count === 0;
  action.textContent = "Review changes";
  document.getElementById("configuration-feedback").replaceChildren();
}

// Build the typed input control for one configuration field.
function configurationInput(field) {
  const input = field.kind === "boolean" ? document.createElement("select") : document.createElement("input");
  input.dataset.configurationField = field.key;
  input.id = `configuration-${field.key.replaceAll(".", "-")}`;
  if (field.kind === "boolean") {
    if (!field.present) {
      const unset = configElement("option", "", "Not set");
      unset.value = "";
      input.append(unset);
    }
    // Offer enabled and disabled states for a boolean setting.
    [["true", "Enabled"], ["false", "Disabled"]].forEach(([value, label]) => {
      const option = configElement("option", "", label);
      option.value = value;
      input.append(option);
    });
    input.value = field.present ? String(field.value) : "";
  } else {
    // Mirror the field kind through the input type and step rules.
    input.type = field.secret ? "password" : field.kind === "string" ? "text" : "number";
    if (field.kind === "number") input.step = "any";
    if (field.kind === "integer") input.step = "1";
    input.value = field.present ? String(field.value) : "";
  }
  // Recompute the unsaved state on every edit.
  input.addEventListener("input", updateUnsavedState);
  input.addEventListener("change", updateUnsavedState);
  return input;
}

// Group consecutive fields that form one paired row.
function configurationRows(fields) {
  const rows = [];
  fields.forEach((field) => {
    const last = rows.at(-1);
    if (field.pair && last?.pair === field.pair) last.fields.push(field);
    else rows.push({ pair: field.pair, fields: [field] });
  });
  return rows;
}

// Render one guided group of available configuration fields.
function renderConfigurationGroup(group, available) {
  const section = configElement("fieldset", "tweak-group configuration-group");
  section.append(configElement("legend", "", group.title));
  if (group.hint) section.append(configElement("p", "tweak-group-hint", group.hint));
  const rows = configElement("div", "guided-setting-list");
  // Keep only the fields the loaded target actually exposes.
  configurationRows(group.fields.filter((definition) => available.has(definition.key))).forEach((row) => {
    // Build one row with its labels and typed inputs.
    const item = configElement("div", "guided-setting-row");
    const controls = configElement("div", `guided-setting-controls${row.fields.length > 1 ? " is-paired" : ""}`);
    row.fields.forEach((definition) => {
      const field = available.get(definition.key);
      const label = configElement("label", "guided-setting-control");
      const caption = configElement("span", "tweak-field-label", definition.label);
      const input = configurationInput(field); caption.htmlFor = input.id;
      label.append(caption, input); controls.append(label);
    });
    item.append(controls, configElement("p", "guided-setting-hint", row.fields[0].hint));
    rows.append(item);
  });
  section.append(rows); return section;
}

// Replace the form with groups built from the loaded field set.
function renderConfigurationFields(loaded) {
  configurationState.loaded = loaded;
  configurationState.reviewed = null;
  // Rebuild the editable value map from the loaded target.
  configurationState.values = new Map(loaded.fields.map((field) => [field.key, field]));
  const form = document.getElementById("configuration-fields");
  form.replaceChildren();
  const available = new Map(loaded.fields.map((field) => [field.key, field]));
  // Render every catalog group that has available fields.
  window.ServerManConfigurationCatalog.serverGroups.forEach((group) => {
    form.append(renderConfigurationGroup(group, available));
  });
  // Show the target path and refresh the unsaved state for the new form.
  document.getElementById("configuration-path").textContent = loaded.relative_path;
  updateUnsavedState();
}

// Load the selected profile's configuration target for editing.
async function loadSelectedConfiguration(preserveOnFailure = false) {
  const workspace = window.ServerManWorkspace.capture("configuration", "shared-configuration");
  const profileId = document.getElementById("configuration-profile").value;
  const target = "server";
  // Capture the load and edit generation that this response must satisfy.
  const sequence = ++configurationState.loadSequence;
  const editGeneration = ++configurationState.editGeneration;
  configurationState.reviewed = null;
  if (!preserveOnFailure) configurationState.loaded = null;
  document.getElementById("configuration-fields").setAttribute("aria-busy", "true");
  const result = await window.pywebview.api.load_configuration(profileId, target);
  // Ignore responses from a replaced workspace, load, or edit generation.
  if (!window.ServerManWorkspace.isActive(workspace)
      || sequence !== configurationState.loadSequence
      || editGeneration !== configurationState.editGeneration) return false;
  // Reject a response that describes a different profile or target.
  if (result.success && (result.value.profile_id !== profileId || result.value.target !== target)) return;
  document.getElementById("configuration-fields").setAttribute("aria-busy", "false");
  if (!result.success) {
    showConfigurationError(result, "Configuration could not be loaded.");
    return false;
  }
  renderConfigurationFields(result.value);
  return true;
}

// Capture the reviewed edit arguments and fingerprint for the current form.
function captureConfigurationEdit() {
  return window.ServerManConfigurationEdit.captureEdit(
    configurationState.loaded,
    configurationState.values,
  );
}

// Enable or disable every configuration control during a host round trip.
function setConfigurationControlsDisabled(disabled) {
  document.querySelectorAll(
    "#configuration-profile, [data-configuration-field], #configuration-discard",
  ).forEach((control) => { control.disabled = disabled; });
}

// Return the current edit fingerprint, or null when the form is incomplete.
function currentConfigurationFingerprint() {
  try {
    return captureConfigurationEdit().fingerprint;
  } catch (_error) {
    return null;
  }
}

// Preview the edits first, then apply the reviewed request atomically.
async function reviewOrApplyConfiguration() {
  let captured;
  try {
    captured = captureConfigurationEdit();
  } catch (error) {
    showConfigurationError(null, error.message);
    return;
  }
  // First press: preview the edits and freeze the reviewed arguments.
  if (!configurationState.reviewed) {
    const workspace = window.ServerManWorkspace.capture("configuration", "shared-configuration");
    const generation = configurationState.editGeneration;
    const result = await window.pywebview.api.preview_configuration(...captured.args);
    // Ignore the preview when the workspace, generation, or fingerprint changed.
    if (
      !window.ServerManWorkspace.isActive(workspace)
      || generation !== configurationState.editGeneration
      || captured.fingerprint !== currentConfigurationFingerprint()
    ) return;
    if (!result.success) return showConfigurationError(result, "Changes could not be previewed.");
    configurationState.reviewed = Object.freeze({
      generation,
      fingerprint: captured.fingerprint,
      args: captured.args,
      result: result.value,
    });
    // Report the validated change count and switch the action to apply.
    const summary = configElement(
      "div",
      "notice",
      `${result.value.changed_fields.length} validated change(s). Apply writes the selected profile target atomically.`,
    );
    summary.setAttribute("role", "status");
    document.getElementById("configuration-feedback").replaceChildren(summary);
    document.getElementById("configuration-apply").textContent = "Apply changes";
    return;
  }
  // Second press: submit the frozen review unless the edits moved on.
  const reviewed = configurationState.reviewed;
  if (
    reviewed.generation !== configurationState.editGeneration
    || reviewed.fingerprint !== captured.fingerprint
  ) {
    updateUnsavedState();
    return showConfigurationError(null, "Configuration changed after review. Review the changes again.");
  }
  // Lock the controls while the apply request is in flight.
  setConfigurationControlsDisabled(true);
  document.getElementById("configuration-apply").disabled = true;
  const workspace = window.ServerManWorkspace.capture("configuration", "shared-configuration");
  const result = await window.pywebview.api.apply_configuration(...reviewed.args);
  if (!window.ServerManWorkspace.isActive(workspace)) return;
  if (!result.success) {
    setConfigurationControlsDisabled(false);
    document.getElementById("configuration-apply").disabled = false;
    return showConfigurationError(result, "Changes could not be applied.");
  }
  if (reviewed !== configurationState.reviewed) return;
  // Track the queued operation so terminal events can refresh the workspace.
  configurationState.pendingOperation = result.value.operation_id;
  window.ServerManUi.setHostStatus("Configuration apply queued", "is-busy", "operation");
  document.getElementById("configuration-apply").disabled = true;
}

// Build the configuration panel and bind its profile, discard, and apply controls.
function renderConfigurationWorkspace(profiles) {
  // Register this workspace as an owner of the shared unsaved-change guard.
  window.ServerManTransitions.registerOwner(
    "shared-configuration", discardConfigurationChanges, focusConfigurationEditor,
  );
  const region = document.getElementById("content-region");
  const panel = configElement("section", "panel configuration-panel");
  const heading = configElement("div", "panel-heading");
  const headingCopy = configElement("div", "mods-heading-copy");
  headingCopy.append(configElement("h2", "", "Server configuration"),
    configElement("p", "", "Edit the selected profile's serverDZ configuration with guided, typed controls."));
  heading.append(headingCopy, configElement("span", "status-label status-normal", "Profile scoped"));
  const selectors = configElement("div", "configuration-selectors");
  const profileLabel = configElement("label", "", "Profile");
  const profileSelect = document.createElement("select");
  profileSelect.id = "configuration-profile";
  // Fill the profile picker, preferring the shared selection.
  profiles.forEach((profile) => {
    const option = configElement("option", "", profile.display_name);
    option.value = profile.profile_id;
    profileSelect.append(option);
  });
  profileSelect.value = window.ServerManProfileContext?.selectedId?.() || profileSelect.value;
  profileLabel.append(profileSelect);
  selectors.append(profileLabel);
  const path = configElement("p", "configuration-path");
  path.append("Target: ", configElement("code", "", "Loading…"));
  path.querySelector("code").id = "configuration-path";
  const fields = configElement("div", "configuration-fields");
  fields.id = "configuration-fields";
  const feedback = configElement("div", "configuration-feedback");
  feedback.id = "configuration-feedback";
  const actions = configElement("div", "action-row");
  const indicator = configElement("span", "unsaved-indicator", "No unsaved changes");
  indicator.id = "configuration-unsaved";
  const discard = configElement("button", "button", "Discard changes");
  discard.id = "configuration-discard";
  discard.type = "button";
  discard.addEventListener("click", discardConfigurationChanges);
  const apply = configElement("button", "button button-primary", "Review changes");
  apply.id = "configuration-apply";
  apply.type = "button";
  apply.disabled = true;
  apply.addEventListener("click", reviewOrApplyConfiguration);
  actions.append(indicator, discard, apply);
  panel.append(heading, selectors, path, fields, feedback, actions);
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", "false");
  // Route picker changes through the shared profile context.
  profileSelect.addEventListener("change", (event) => requestProfile(event.target.value));
  // Start with the selected profile's configuration target.
  loadSelectedConfiguration();
}

// Open the configuration workspace for the profiles reported by the host.
async function openConfigurationWorkspace() {
  const workspace = window.ServerManWorkspace.capture("configuration");
  const result = await window.pywebview.api.list_profiles();
  if (!window.ServerManWorkspace.isActive(workspace)) return;
  if (!result.success) return window.ServerManUi.renderHostError(result);
  if (!Array.isArray(result.value) || !result.value.length) {
    return window.ServerManUi.render("configuration", "empty");
  }
  renderConfigurationWorkspace(result.value);
}

// Publish the configuration workspace controls used by the shell.
window.ServerManConfiguration = Object.freeze({
  open: openConfigurationWorkspace,
  operationFinished: configurationOperationFinished,
  requestProfile,
});
