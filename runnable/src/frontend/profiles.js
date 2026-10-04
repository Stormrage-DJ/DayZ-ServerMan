// Profile workspace: create, edit, save, and delete server profiles.
"use strict";

// Workspace state for the profile form and its pending operations.
const profileState = {
  contextGeneration: 0, editGeneration: 0, loadGeneration: 0,
  records: [], selected: null, pending: null, baseline: "", dirty: false,
};

// Build one plain element for the profile form.
function profileNode(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

// Build one labeled profile input with optional type and requirement.
function profileField(labelText, name, value, type = "text", required = false) {
  const label = profileNode("label", "configuration-field", labelText);
  const input = document.createElement("input");
  input.name = name; input.type = type; input.value = value ?? ""; input.required = required;
  // Constrain numeric fields to the supported port range.
  if (type === "number") { input.min = "1"; input.max = "65535"; input.step = "1"; }
  label.append(input); return label;
}

// Reduce a profile value to a stable comparison string.
function profileFingerprint(value) {
  return window.ServerManTransitions.canonicalFingerprint(value);
}

// Capture the workspace, profile identity, and revision for one request.
function captureProfileContext() {
  return Object.freeze({
    workspace: window.ServerManWorkspace.capture("profiles"),
    contextGeneration: profileState.contextGeneration,
    profileId: profileState.selected?.profile_id ?? null,
    revision: profileState.selected?.revision ?? null,
  });
}

// Report whether a captured profile context still matches the live workspace.
function profileContextActive(context) {
  return Boolean(context
    && window.ServerManWorkspace.isActive(context.workspace)
    && shellState.section === "profiles"
    && context.contextGeneration === profileState.contextGeneration
    && context.profileId === (profileState.selected?.profile_id ?? null)
    && context.revision === (profileState.selected?.revision ?? null));
}

// Recompute the unsaved indicator from the current form contents.
function setProfileDirty() {
  const form = document.getElementById("profile-form");
  if (!form) return;
  profileState.editGeneration += 1;
  // Compare the form fingerprint against the saved baseline.
  try { profileState.dirty = profileFingerprint(captureProfile(form)) !== profileState.baseline; }
  catch (_error) { profileState.dirty = true; }
  window.ServerManTransitions.setDirty("profiles", profileState.dirty);
  // Publish the state to the transition guard and the status line.
  const status = document.getElementById("profile-unsaved");
  status.textContent = profileState.dirty ? "Unsaved profile changes" : "No unsaved profile changes";
  status.classList.toggle("is-dirty", profileState.dirty);
}

// Read the form into a profile record and validate the game port.
function captureProfile(form) {
  const data = new FormData(form);
  const portText = String(data.get("game_port") || "");
  // Reject a game port that is not a whole number.
  if (!/^\d+$/.test(portText)) throw new Error("Game port must be a whole integer.");
  const port = Number(portText);
  // Enforce the supported port range.
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new Error("Game port must be from 1 through 65535.");
  }
  return { profile_id: String(data.get("profile_id") || ""),
    display_name: String(data.get("display_name") || ""),
    server_executable: String(data.get("server_executable") || ""),
    server_config: String(data.get("server_config") || ""),
    runtime_profile: data.get("runtime_enabled") === "on"
      ? String(data.get("runtime_profile") || "") : null,
    mission_root: String(data.get("mission_root") || "") || null,
    game_port: port, mods: [...form.querySelectorAll("[data-mod-row]")].map(modValue),
    extra_arguments: String(data.get("extra_arguments") || "").split(/\r?\n/).filter(Boolean) };
}

// Select another profile through the transition guard.
function chooseProfile(profileId) {
  const selected = profileState.records.find((item) => item.profile_id === profileId) || null;
  window.ServerManTransitions.requestOwnerTransition(
    "profiles", "Discard profile changes to select another profile.",
    () => renderProfileForm(selected),
  );
}

// Drop profile draft state without rendering during an in-flight page transition.
function discardProfileChanges() {
  profileState.contextGeneration += 1; profileState.editGeneration += 1;
  profileState.loadGeneration += 1; profileState.pending = null; profileState.dirty = false;
  window.ServerManTransitions.setDirty("profiles", false);
}

// Render the create or edit form for one profile.
function renderProfileForm(record) {
  if (!record) { void openProfileCreation(); return; }
  // Reset per-open state and clear the content region.
  profileState.contextGeneration += 1; profileState.editGeneration += 1;
  profileState.selected = record; profileState.pending = null;
  const region = document.getElementById("content-region"); region.textContent = "";
  const panel = profileNode("section", "panel profile-panel");
  const heading = profileNode("div", "panel-heading");
  const title = profileNode("h2", "", "Edit profile"); heading.append(title);
  // The profile is chosen in the sidebar; the heading line of the page names it again.
  window.ServerManPageContext?.setText("");
  const create = profileNode("button", "button button-primary", "New profile"); create.type = "button";
  create.addEventListener("click", openProfileCreation); heading.append(create);
  const form = document.createElement("form"); form.id = "profile-form"; form.noValidate = true;
  const value = record || { mods: [], extra_arguments: [], runtime_profile: null };
  // Compose the basic profile fields.
  const fields = profileNode("div", "configuration-fields profile-basics");
  fields.append(profileField("Display name", "display_name", value.display_name, "text", true),
    profileField("Profile ID", "profile_id", value.profile_id, "text", true),
    profileField("Game port", "game_port", value.game_port || 2302, "number", true),
    profileField("Server executable", "server_executable", value.server_executable, "text", true),
    profileField("Server config", "server_config", value.server_config, "text", true),
    profileField("Mission root (optional)", "mission_root", value.mission_root));
  fields.querySelector('[name="profile_id"]').readOnly = true;
  // Offer an optional explicit runtime profile directory.
  const runtime = profileNode("fieldset", "profile-runtime");
  runtime.append(profileNode("legend", "", "Runtime profile"));
  const enabledLabel = profileNode("label", "profile-runtime-toggle");
  const enabled = document.createElement("input"); enabled.type = "checkbox";
  enabled.name = "runtime_enabled"; enabled.checked = value.runtime_profile !== null;
  enabledLabel.append(enabled, profileNode("span", "", "Use explicit runtime profile"));
  runtime.append(enabledLabel);
  const runtimePath = profileField("DayZ-relative runtime directory", "runtime_profile", value.runtime_profile);
  runtime.append(runtimePath); const runtimeInput = runtimePath.querySelector("input");
  runtimeInput.disabled = !enabled.checked; runtimeInput.required = enabled.checked;
  enabled.addEventListener("change", () => { runtimeInput.disabled = !enabled.checked;
    runtimeInput.required = enabled.checked; setProfileDirty(); });
  // List ordered mod entries with per-row actions.
  const mods = profileNode("div", "profile-mods"); mods.id = "profile-mods";
  mods.append(profileNode("h3", "", "Ordered mod entries"));
  const table = profileNode("div", "profile-mod-table"); table.setAttribute("role", "table");
  table.setAttribute("aria-label", "Ordered mods");
  const header = profileNode("div", "profile-mod-header"); header.setAttribute("role", "row");
  ["#", "Directory", "Launch scope", "Source", "Workshop ID", "Actions"].forEach((text) => {
    const cell = profileNode("span", "", text); cell.setAttribute("role", "columnheader"); header.append(cell);
  });
  const modRows = profileNode("div", "profile-mod-list"); modRows.setAttribute("role", "rowgroup");
  value.mods.forEach((mod) => modRows.append(renderMod(mod)));
  table.append(header, modRows); mods.append(table); refreshModRows(modRows);
  // Keep list-level mod actions together below the ordered editor.
  mods.append(renderProfileModTools(modRows));
  const extras = profileNode("label", "configuration-field", "Extra direct-process arguments, one per line");
  const textarea = document.createElement("textarea"); textarea.name = "extra_arguments";
  textarea.value = value.extra_arguments.join("\n"); extras.append(textarea);
  const feedback = profileNode("p", "configuration-feedback"); feedback.id = "profile-feedback";
  feedback.setAttribute("role", "status");
  // Offer save, preview, and delete actions with an unsaved indicator.
  const actions = profileNode("div", "action-row");
  const unsaved = profileNode("span", "unsaved-indicator", "No unsaved profile changes");
  unsaved.id = "profile-unsaved"; const save = profileNode("button", "button button-primary", "Save profile");
  save.type = "submit"; actions.append(unsaved, window.ServerManBusy?.mark(save) || save);
  if (record) { const preview = profileNode("button", "button", "Preview launch command"); preview.type = "button";
    preview.addEventListener("click", previewProfileCommand); actions.append(preview);
    const remove = profileNode("button", "button button-danger", "Delete profile"); remove.type = "button";
    remove.addEventListener("click", requestDeleteProfile);
    actions.append(window.ServerManBusy?.mark(remove) || remove); }
  form.append(fields, runtime, mods, extras, feedback, actions);
  // Track edits and queue saves through the transition-aware handlers.
  form.addEventListener("input", setProfileDirty); form.addEventListener("change", setProfileDirty);
  form.addEventListener("submit", saveProfile); panel.append(heading, form); region.append(panel);
  // Record the saved baseline so later edits can be detected.
  profileState.baseline = profileFingerprint(captureProfile(form)); profileState.dirty = false;
  window.ServerManTransitions.setDirty("profiles", false);
}

// Validate the form and submit the captured profile to the host.
async function saveProfile(event) {
  event.preventDefault(); const form = event.currentTarget;
  try {
    if (!form.reportValidity()) return;
    // Freeze the payload so later edits cannot alter the queued save.
    const profile = window.ServerManTransitions.immutableCopy(captureProfile(form));
    const context = captureProfileContext(); const editGeneration = profileState.editGeneration;
    const result = await window.pywebview.api.save_profile(profile, context.revision);
    if (!profileContextActive(context)) return;
    if (!result.success) return window.ServerManUi.renderHostError(result);
    // Record the queued save; the operation bar shows it from here on.
    profileState.pending = Object.freeze({ operationId: result.value.operation_id,
      kind: "save", context, editGeneration, preferredProfileId: profile.profile_id });
    document.getElementById("profile-feedback").textContent = "";
    window.ServerManOperationBar?.adopt(result.value.operation_id);
  } catch (error) { document.getElementById("profile-feedback").textContent = error.message;
    form.querySelector("input")?.focus(); }
}

// Ask the host to preview the launch command for the selected profile.
async function previewProfileCommand() {
  const context = captureProfileContext(); if (context.profileId === null) return;
  const result = await window.pywebview.api.preview_profile_command(context.profileId);
  if (!profileContextActive(context)) return;
  if (!result.success) return window.ServerManUi.renderHostError(result);
  document.getElementById("profile-feedback").textContent = result.value.argv.join("\n");
}

// Load profiles and render the workspace while guarding against stale edits.
async function openProfilesWorkspace(
  preferredProfileId = null, requirePreferred = false, expectedEditGeneration = null,
) {
  const workspace = window.ServerManWorkspace.capture("profiles");
  const loadGeneration = ++profileState.loadGeneration;
  const editGeneration = profileState.editGeneration;
  // Load the profile list under the captured generations.
  const result = await window.pywebview.api.list_profiles();
  if (!window.ServerManWorkspace.isActive(workspace) || loadGeneration !== profileState.loadGeneration) return;
  const changedDuringLoad = editGeneration !== profileState.editGeneration;
  const newerThanSubmitted = expectedEditGeneration !== null
    && expectedEditGeneration !== profileState.editGeneration;
  // Preserve unsaved edits and explain why the workspace was not replaced.
  if (changedDuringLoad || newerThanSubmitted
    || (expectedEditGeneration === null && profileState.dirty)) {
    window.ServerManUi.setHostStatus("Unsaved profile edits preserved", "is-warning", "workspace"); return;
  }
  if (!result.success) return window.ServerManUi.renderHostError(result);
  profileState.records = result.value;
  // Decide which profile should be shown after the load.
  const retained = preferredProfileId ?? profileState.selected?.profile_id ?? null;
  const selected = result.value.find((item) => item.profile_id === retained) || null;
  if (requirePreferred && retained !== null && selected === null) {
    window.ServerManUi.setHostStatus("Saved profile was not returned by storage", "is-error", "workspace"); return;
  }
  window.ServerManUi.clearHostStatus("workspace");
  renderProfileForm(selected || (retained === null ? result.value[0] || null : null));
}

// Track the pending profile operation until it reaches a terminal state.
function profileOperationFinished(operation) {
  const pending = profileState.pending;
  if (!pending || operation.operation_id !== pending.operationId) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  // Consume the pending entry on every terminal state.
  profileState.pending = null;
  if (!profileContextActive(pending.context)) return true;
  if (operation.state !== "SUCCEEDED") {
    if (pending.kind === "provision") {
      const form = document.getElementById("profile-form");
      [...(form?.elements || [])].forEach((element) => { element.disabled = false; });
      const feedback = document.getElementById("profile-feedback");
      const message = window.ServerManOperationBar?.pageResult(operation).text || "Profile creation failed.";
      if (feedback) { feedback.textContent = message; feedback.classList.add("notice", "notice-error"); }
      window.ServerManUi.setHostStatus("Profile creation needs attention", "is-error", "operation");
      return true;
    }
    // A failed save or delete keeps the form and its values; the operation bar reads the result.
    return true;
  }
  if (pending.kind === "provision") { void finishProfileProvision(pending, operation); return true; }
  // Explain when newer edits blocked the automatic reload.
  if (pending.editGeneration !== profileState.editGeneration) {
    document.getElementById("profile-feedback").textContent =
      "The operation succeeded. Newer profile edits were preserved; reload before another save.";
    return true;
  }
  // Reconcile the authoritative catalog before reopening saved or deleted profiles.
  void finishStoredProfileMutation(pending); return true;
}

// Refresh shared profile state after an ordinary save or delete operation.
async function finishStoredProfileMutation(pending) {
  profileState.dirty = false; window.ServerManTransitions.setDirty("profiles", false);
  const refreshed = await window.ServerManProfileContext.refreshAndSelect(
    pending.preferredProfileId,
  );
  if (!refreshed.success) return window.ServerManUi.renderHostError(refreshed);
  return openProfilesWorkspace(
    refreshed.value.selected_profile_id,
    pending.kind === "save",
  );
}

// Register the profile workspace with the shared transition guard.
window.ServerManTransitions.registerOwner("profiles", discardProfileChanges,
  () => document.querySelector("#profile-form input")?.focus());
// Publish the profile workspace entry points.
window.ServerManProfiles = Object.freeze({ open: openProfilesWorkspace,
  operationFinished: profileOperationFinished, hasDirty: () => profileState.dirty });
