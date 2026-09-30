// Guided creation of a ready-to-launch server profile.
"use strict";

const profileCreateState = {
  settingsRevision: null, missions: [], idManuallyEdited: false,
};

// Build one aligned label, hint, and control row for guided creation.
function profileCreateRow(labelText, hintText, control) {
  const row = profileNode("div", "profile-create-row");
  const copy = profileNode("div", "profile-create-copy");
  const label = profileNode("label", "profile-create-label", labelText);
  const labeledControl = control.matches("input, select, textarea")
    ? control : control.querySelector("input, select, textarea");
  labeledControl.id = `profile-create-${labeledControl.name}`; label.htmlFor = labeledControl.id;
  copy.append(label, profileNode("small", "field-hint", hintText));
  row.append(copy, control); return row;
}

// Build one input row and expose both its wrapper and input.
function profileCreateInput(label, hint, name, value, type = "text", required = false) {
  const input = document.createElement("input");
  input.name = name; input.type = type; input.value = value; input.required = required;
  if (type === "number") { input.min = "1"; input.max = "65535"; input.step = "1"; }
  return { row: profileCreateRow(label, hint, input), input };
}

// Convert a display name into a valid initial profile identifier.
function profileIdFromName(value) {
  const slug = String(value).normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64)
    .replace(/-+$/g, "");
  return slug || "server";
}

// Mark the creation draft dirty without asking the edit-form serializer to read it.
function markProfileCreateDirty() {
  profileState.editGeneration += 1; profileState.dirty = true;
  window.ServerManTransitions.setDirty("profiles", true);
  const status = document.getElementById("profile-unsaved");
  if (status) { status.textContent = "Unsaved profile changes"; status.classList.add("is-dirty"); }
}

// Show or hide the custom mission input and update the resolved-path hint.
function updateMissionChoice(form) {
  const select = form.elements.mission_choice;
  const custom = form.querySelector("[data-custom-mission]");
  const customMode = select.value === "__custom__";
  custom.hidden = !customMode; custom.querySelector("input").required = customMode;
  const path = customMode ? custom.querySelector("input").value : select.value;
  form.querySelector("[data-mission-path]").textContent = path
    ? `DayZ path: ${path}` : "Choose an installed mission or enter a custom relative path.";
}

// Keep the generated operational paths aligned with the draft identifier.
function updateGeneratedProfilePaths(form) {
  const id = form.elements.profile_id.value || "<profile-id>";
  form.querySelector("[data-generated-config]").textContent = `serverman\\${id}\\serverDZ.cfg`;
  form.querySelector("[data-generated-runtime]").textContent = `serverman\\${id}\\profile`;
}

// Build the mission selector and its explicit custom-path mode.
function appendMissionControl(container, form) {
  const select = document.createElement("select"); select.name = "mission_choice";
  profileCreateState.missions.forEach((mission) => {
    const option = profileNode("option", "", mission.display_name);
    option.value = mission.relative_path; select.append(option);
  });
  const customOption = profileNode("option", "", "Custom mission…");
  customOption.value = "__custom__"; select.append(customOption);
  if (!profileCreateState.missions.length) select.value = "__custom__";
  const missionControl = profileNode("div", "profile-create-control-stack");
  const pathHint = profileNode("small", "field-hint profile-create-path-hint");
  pathHint.dataset.missionPath = ""; missionControl.append(select, pathHint);
  container.append(profileCreateRow(
    "Mission", "Choose an installed mission, or select Custom mission for a modded map.", missionControl,
  ));
  const custom = profileCreateInput(
    "Custom mission path", "Path relative to the DayZ folder, usually below mpmissions.",
    "custom_mission", "mpmissions\\",
  );
  custom.row.dataset.customMission = ""; custom.row.classList.add("profile-create-custom-mission");
  container.append(custom.row);
  select.addEventListener("change", () => { updateMissionChoice(form); markProfileCreateDirty(); });
  custom.input.addEventListener("input", () => updateMissionChoice(form));
}

// Read and validate the guided creation form.
function captureProfileProvision(form) {
  const data = new FormData(form); const portText = String(data.get("game_port") || "");
  if (!/^\d+$/.test(portText)) throw new Error("Game port must be a whole integer.");
  const port = Number(portText);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new Error("Game port must be from 1 through 65535.");
  }
  const mission = String(data.get("mission_choice") || "") === "__custom__"
    ? String(data.get("custom_mission") || "") : String(data.get("mission_choice") || "");
  if (!mission) throw new Error("Choose an installed mission or enter its relative directory.");
  return {
    profile_id: String(data.get("profile_id") || ""),
    display_name: String(data.get("display_name") || ""),
    server_executable: String(data.get("server_executable") || "DayZServer_x64.exe"),
    mission_root: mission,
    game_port: port,
    mods: [],
    extra_arguments: String(data.get("extra_arguments") || "").split(/\r?\n/).filter(Boolean),
  };
}

// Render the guided form once mission discovery has completed.
function renderProfileCreateForm() {
  profileState.contextGeneration += 1; profileState.editGeneration += 1;
  profileState.selected = null; profileState.pending = null; profileState.dirty = false;
  window.ServerManTransitions.setDirty("profiles", false);
  const region = document.getElementById("content-region"); region.textContent = "";
  const panel = profileNode("section", "panel profile-panel profile-create-panel");
  const heading = profileNode("div", "panel-heading profile-create-heading");
  const headingCopy = profileNode("div");
  headingCopy.append(profileNode("h2", "", "New server profile"), profileNode(
    "p", "panel-description", "Create the server configuration and runtime folder together.",
  ));
  heading.append(headingCopy);
  const form = document.createElement("form"); form.id = "profile-form"; form.noValidate = true;
  const basics = profileNode("section", "profile-create-section");
  basics.append(profileNode("h3", "", "Server identity"));
  const display = profileCreateInput(
    "Display name", "Name shown in DayZ-ServerMan and used as the initial server name.",
    "display_name", "", "text", true,
  );
  const id = profileCreateInput(
    "Profile ID", "Portable folder name, generated from the display name.",
    "profile_id", "server", "text", true,
  );
  const idInput = id.input; idInput.pattern = "[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?";
  const port = profileCreateInput(
    "Game port", "UDP port used by this server. Use a unique port for simultaneous servers.",
    "game_port", 2302, "number", true,
  );
  port.row.classList.add("profile-create-port");
  basics.append(display.row, id.row, port.row);
  const missionSection = profileNode("section", "profile-create-section");
  missionSection.append(profileNode("h3", "", "Mission")); appendMissionControl(missionSection, form);
  const generated = profileNode("section", "profile-create-section");
  generated.append(profileNode("h3", "", "Files created for this profile"));
  generated.append(profileNode(
    "p", "panel-description profile-create-file-note",
    "Compatible files left by a deleted profile are reused and never overwritten.",
  ));
  const files = profileNode("dl", "profile-generated-files");
  const configName = profileNode("dt", "", "Server configuration");
  const configPath = profileNode("dd"); configPath.dataset.generatedConfig = "";
  const runtimeName = profileNode("dt", "", "Runtime profile");
  const runtimePath = profileNode("dd"); runtimePath.dataset.generatedRuntime = "";
  files.append(configName, configPath, runtimeName, runtimePath); generated.append(files);
  const advanced = document.createElement("details"); advanced.className = "profile-create-advanced";
  advanced.append(profileNode("summary", "", "Advanced launch options"));
  const executable = profileCreateInput(
    "Server executable", "Executable relative to the configured DayZ folder.",
    "server_executable", "DayZServer_x64.exe", "text", true,
  );
  const textarea = document.createElement("textarea"); textarea.name = "extra_arguments";
  advanced.append(executable.row, profileCreateRow(
    "Extra arguments", "Optional process arguments, one per line.", textarea,
  ));
  const feedback = profileNode("p", "configuration-feedback"); feedback.id = "profile-feedback";
  feedback.setAttribute("role", "status");
  const actions = profileNode("div", "action-row profile-create-actions");
  const unsaved = profileNode("span", "unsaved-indicator", "No unsaved profile changes");
  unsaved.id = "profile-unsaved";
  const cancel = profileNode("button", "button", "Cancel"); cancel.type = "button";
  cancel.addEventListener("click", cancelProfileCreation);
  const create = profileNode("button", "button button-primary", "Create profile"); create.type = "submit";
  actions.append(unsaved, cancel, create);
  form.append(basics, missionSection, generated, advanced, feedback, actions);
  panel.append(heading, form); region.append(panel);
  display.input.addEventListener("input", (event) => {
    if (!profileCreateState.idManuallyEdited) idInput.value = profileIdFromName(event.target.value);
    updateGeneratedProfilePaths(form);
  });
  idInput.addEventListener("input", () => {
    profileCreateState.idManuallyEdited = true; updateGeneratedProfilePaths(form);
  });
  form.addEventListener("input", markProfileCreateDirty);
  form.addEventListener("submit", submitProfileProvision);
  updateMissionChoice(form); updateGeneratedProfilePaths(form); display.input.focus();
}

// Load missions before offering creation so the user cannot choose stale content.
async function openProfileCreation() {
  window.ServerManTransitions.requestOwnerTransition(
    "profiles", "Discard profile changes and create a new profile.", async () => {
      profileCreateState.idManuallyEdited = false;
      const workspace = window.ServerManWorkspace.capture("profiles");
      const loadGeneration = ++profileState.loadGeneration;
      const editGeneration = profileState.editGeneration;
      const result = await window.pywebview.api.list_profile_missions();
      if (!window.ServerManWorkspace.isActive(workspace) || shellState.section !== "profiles"
        || loadGeneration !== profileState.loadGeneration) return;
      if (editGeneration !== profileState.editGeneration || profileState.dirty) {
        window.ServerManUi.setHostStatus("Unsaved profile edits preserved", "is-warning", "workspace");
        return;
      }
      if (!result.success) return window.ServerManUi.renderHostError(result);
      profileCreateState.settingsRevision = result.value.settings_revision;
      profileCreateState.missions = result.value.missions; renderProfileCreateForm();
    },
  );
}

// Return to the remembered profile without changing pages.
function cancelProfileCreation() {
  const selectedId = window.ServerManProfileContext.selectedId();
  const selected = profileState.records.find((item) => item.profile_id === selectedId)
    || profileState.records[0] || null;
  window.ServerManTransitions.requestOwnerTransition(
    "profiles", "Discard this new profile draft.", () => renderProfileForm(selected),
  );
}

// Queue the atomic provisioning operation.
async function submitProfileProvision(event) {
  event.preventDefault(); const form = event.currentTarget;
  try {
    if (!form.reportValidity()) return;
    const profile = window.ServerManTransitions.immutableCopy(captureProfileProvision(form));
    const context = captureProfileContext(); const editGeneration = profileState.editGeneration;
    const result = await window.pywebview.api.provision_profile(
      profile, profileCreateState.settingsRevision,
    );
    if (!profileContextActive(context)) return;
    if (!result.success) return window.ServerManUi.renderHostError(result);
    profileState.pending = Object.freeze({
      operationId: result.value.operation_id, kind: "provision", context,
      editGeneration, preferredProfileId: profile.profile_id,
    });
    [...form.elements].forEach((element) => { element.disabled = true; });
    document.getElementById("profile-feedback").textContent = "Creating profile and server files…";
  } catch (error) {
    document.getElementById("profile-feedback").textContent = error.message;
  }
}

// Reconcile a completed provisioning result with every shared selector.
async function finishProfileProvision(pending, operation) {
  profileState.dirty = false; window.ServerManTransitions.setDirty("profiles", false);
  const refreshed = await window.ServerManProfileContext.refreshAndSelect(
    pending.preferredProfileId,
  );
  if (!refreshed.success) return window.ServerManUi.renderHostError(refreshed);
  await openProfilesWorkspace(pending.preferredProfileId, true);
  const feedback = document.getElementById("profile-feedback");
  if (!feedback) return;
  const reused = operation.result?.reused_files === true;
  feedback.textContent = operation.result?.readiness?.ready
    ? reused
      ? "Profile recreated and selected. Existing server files were reused; it is ready to start."
      : "Profile created and selected. It is ready to start."
    : `Profile created, but needs attention: ${operation.result?.readiness?.reasons?.join(" ") || "Check its paths."}`;
  const go = profileNode("button", "button button-primary", "Go to Overview"); go.type = "button";
  go.addEventListener("click", () => setSection("overview")); feedback.after(go);
}
