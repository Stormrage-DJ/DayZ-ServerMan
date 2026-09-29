// Mods workspace: Steam access settings, workshop downloads, and cancellation.
"use strict";

// Tracks the loaded profiles, inventory, settings, and pending workshop operation.
const modsState = {
  profiles: [], inventory: [], settings: null, pending: null, generation: 0, busy: false,
};
// Capture the profile and workspace generation into a frozen token.
function captureModsContext() {
  return Object.freeze({ generation: modsState.generation,
    profileId: window.ServerManProfileContext?.selectedId?.() || "",
    workspace: window.ServerManWorkspace.capture("mods") });
}
// Report whether a captured token still matches the selected profile and workspace.
function isModsContextActive(context) {
  return Boolean(context && context.generation === modsState.generation
    && window.ServerManProfileContext?.selectedId?.() === context.profileId
    && window.ServerManWorkspace.isActive(context.workspace));
}

// Create one interface element with an optional class and text.
function modsNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Replace the feedback area with a status or alert notice.
function modsFeedback(message, error = false) {
  const region = document.getElementById("mods-feedback");
  if (!region) return;
  const notice = modsNode("div", `notice ${error ? "notice-error" : ""}`.trim(), message);
  notice.setAttribute("role", error ? "alert" : "status");
  region.replaceChildren(notice);
}

// Enable or disable the Steam and workshop controls for a busy host.
function setModsBusy(busy) {
  modsState.busy = busy;
  const hasProfiles = modsState.profiles.length > 0;
  // Lock the Steam settings controls during a host round trip.
  ["save-steam-settings", "authenticate-steamcmd", "steam-auth-mode"].forEach((id) => {
    const node = document.getElementById(id); if (node) node.disabled = busy;
  });
  const account = document.getElementById("steam-account-name");
  if (account) account.disabled = busy || document.getElementById("steam-auth-mode")?.value === "ANONYMOUS";
  const update = document.getElementById("update-workshop");
  if (update) update.disabled = busy || !hasProfiles;
}

// Return the profile that matches the shared selection, or null.
function selectedProfile() {
  const selectedId = window.ServerManProfileContext?.selectedId?.();
  return modsState.profiles.find((profile) => profile.profile_id === selectedId) || null;
}

// Build the Steam access panel and the configured-mod inventory.
function renderMods() {
  const settings = modsState.settings;
  const panel = modsNode("section", "panel mods-panel");
  const heading = modsNode("div", "panel-heading");
  heading.append(modsNode("h2", "", "Steam access"), modsNode("span", "mods-selected-server",
    selectedProfile()?.display_name || "No server selected"));
  panel.append(heading);

  const form = modsNode("div", "mods-access-row");
  const modeLabel = modsNode("label", "mods-field");
  modeLabel.append(modsNode("span", "mods-field-label", "Sign-in"));
  const mode = modsNode("select"); mode.id = "steam-auth-mode";
  // Offer the saved sign-in modes with the current one selected.
  [["", "Choose mode"], ["ACCOUNT", "Steam account"], ["ANONYMOUS", "Anonymous"]]
    .forEach(([value, label]) => { const option = modsNode("option", "", label); option.value = value;
      option.selected = settings.steam_authentication_mode === value; mode.append(option); });
  modeLabel.append(mode);
  const accountLabel = modsNode("label", "mods-field");
  accountLabel.append(modsNode("span", "mods-field-label", "Steam account name"));
  const account = modsNode("input"); account.id = "steam-account-name"; account.type = "text";
  account.autocomplete = "off"; account.maxLength = 64; account.value = settings.steam_account_name || "";
  accountLabel.append(account);
  form.append(modeLabel, accountLabel);

  // Explain where Steam credentials are entered and who owns them.
  const safety = modsNode("div", "notice notice-warning"); safety.setAttribute("role", "status");
  safety.append(modsNode("strong", "", "Credential safety"), modsNode("p", "",
    "Enter passwords and Steam Guard codes only in the visible SteamCMD window. DayZ-ServerMan never asks for them."));
  const actions = modsNode("div", "mods-actions");
  const authenticationActions = modsNode("div", "mods-action-group");
  const updateActions = modsNode("div", "mods-action-group mods-update-actions");
  // Wire the sign-in, login, download, and cancel actions.
  [["save-steam-settings", "Save sign-in settings", authenticationActions],
    ["authenticate-steamcmd", "Open SteamCMD login", authenticationActions],
    ["update-workshop", "Download / update mods", updateActions],
    ["cancel-workshop-operation", "Cancel current operation", updateActions]].forEach(([id, label, group]) => {
      const button = modsNode("button", id === "update-workshop" ? "button button-primary" : "button", label);
      button.id = id; button.type = "button"; group.append(button);
    });
  actions.append(authenticationActions, updateActions);
  panel.append(form, safety, actions, modsNode("div", "", ""));
  panel.lastChild.id = "mods-feedback";
  // Replace the workspace with the rebuilt panel and inventory.
  document.getElementById("content-region").replaceChildren(panel, renderModInventory());
  mode.addEventListener("change", () => {
    // Clear the saved account name when anonymous sign-in is chosen.
    if (mode.value === "ANONYMOUS") account.value = "";
    setModsBusy(modsState.busy);
  });
  setModsBusy(Boolean(modsState.pending));
  document.getElementById("save-steam-settings").addEventListener("click", saveSteamSettings);
  document.getElementById("authenticate-steamcmd").addEventListener("click", authenticateSteamCmd);
  document.getElementById("update-workshop").addEventListener("click", updateWorkshop);
  const cancel = document.getElementById("cancel-workshop-operation");
  cancel.hidden = !modsState.pending; cancel.addEventListener("click", cancelModsOperation);
  if (modsState.pending) setModsBusy(true);
}

// Open the mods workspace with a fresh snapshot, profiles, and inventory.
async function openMods() {
  const initialized = await window.ServerManProfileContext.initialize();
  if (!initialized.success) { window.ServerManUi.renderHostError(initialized); return; }
  const generation = ++modsState.generation;
  const workspace = window.ServerManWorkspace.capture("mods");
  const profileId = window.ServerManProfileContext?.selectedId?.();
  // Load the snapshot, profiles, and inventory together.
  const [snapshot, profiles, inventory] = await Promise.all([
    window.pywebview.api.get_application_snapshot(), window.pywebview.api.list_profiles(),
    profileId ? window.pywebview.api.list_mod_inventory(profileId)
      : Promise.resolve({ success: true, value: [] }),
  ]);
  // Ignore the batch when the workspace or generation moved on.
  if (generation !== modsState.generation || !window.ServerManWorkspace.isActive(workspace)) return;
  if (!snapshot.success || !profiles.success || !inventory.success) {
    window.ServerManUi.renderHostError(!snapshot.success ? snapshot
      : !profiles.success ? profiles : inventory);
    return;
  }
  modsState.settings = snapshot.value.settings;
  modsState.profiles = profiles.value;
  modsState.inventory = inventory.value;
  renderMods();
  // Reconcile a pending workshop operation with the fresh snapshot.
  if (modsState.pending) {
    const terminal = (snapshot.value.operations || []).find(
      (operation) => operation.operation_id === modsState.pending.id,
    );
    if (terminal) modsOperationFinished(terminal);
  }
}

// Save the Steam sign-in settings through the shared operation flow.
async function saveSteamSettings() {
  const context = captureModsContext();
  const mode = document.getElementById("steam-auth-mode").value;
  const account = document.getElementById("steam-account-name").value.trim() || null;
  const result = await window.pywebview.api.save_steam_settings(
    modsState.settings.revision, mode, account,
  );
  if (!isModsContextActive(context)) return;
  acceptModsOperation(result, "Saving Steam authentication settings");
}

// Open the visible SteamCMD login window through the shared operation flow.
async function authenticateSteamCmd() {
  const context = captureModsContext();
  const result = await window.pywebview.api.authenticate_steamcmd(modsState.settings.revision);
  if (!isModsContextActive(context)) return;
  acceptModsOperation(result, "Opening visible SteamCMD authentication");
}

// Queue a workshop download or update for the selected profile.
async function updateWorkshop() {
  const profile = selectedProfile();
  if (!profile) return modsFeedback("Select a profile first.", true);
  const context = captureModsContext();
  const result = await window.pywebview.api.update_workshop_items(
    profile.profile_id, profile.revision, profile.semantic_digest, modsState.settings.revision,
    modsState.settings.steam_authentication_mode, modsState.settings.steam_account_name,
    false,
  );
  if (!isModsContextActive(context)) return;
  acceptModsOperation(result, "Downloading or updating mods");
}

// Track a queued host operation and show its progress message.
function acceptModsOperation(result, message) {
  // Report failures without leaving any pending state behind.
  if (!result || !result.success) return modsFeedback(
    result && result.error ? result.error.message : "The request failed safely.", true);
  const profile = selectedProfile();
  // Record the queued operation against the current generation and profile.
  modsState.pending = Object.freeze({ id: result.value.operation_id,
    generation: modsState.generation, profileId: profile ? profile.profile_id : "" });
  setModsBusy(true);
  const cancel = document.getElementById("cancel-workshop-operation");
  if (cancel) { cancel.hidden = false; cancel.disabled = false; }
  modsFeedback(message);
}

// Request cancellation of the pending workshop operation.
async function cancelModsOperation() {
  const pending = modsState.pending;
  if (!pending) return;
  const context = captureModsContext();
  const button = document.getElementById("cancel-workshop-operation");
  if (button) button.disabled = true;
  const result = await window.pywebview.api.request_operation_cancellation(pending.id);
  // Ignore responses when the workspace or pending operation changed.
  if (!isModsContextActive(context) || modsState.pending?.id !== pending.id) return;
  if (!result || !result.success) {
    if (button) button.disabled = false;
    return modsFeedback(result?.error?.message || "Cancellation failed safely.", true);
  }
  modsFeedback("Cancellation requested. Waiting for the current safe point.");
}

// Apply operation events to the mods workspace and its action states.
function modsOperationFinished(operation) {
  if (!operation || !modsState.pending || operation.operation_id !== modsState.pending.id) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) {
    // Keep the cancel control state current while work continues.
    const cancel = document.getElementById("cancel-workshop-operation");
    if (cancel) cancel.disabled = !operation.cancellable || operation.state === "CANCELLING";
    modsFeedback(operation.state === "CANCELLING"
      ? "Cancellation requested. Waiting for a safe point."
      : `${operation.progress_phase}: ${operation.progress_percent}%`);
    return true;
  }
  // Clear the pending operation and unlock the controls on terminal states.
  modsState.pending = null;
  setModsBusy(false);
  const cancel = document.getElementById("cancel-workshop-operation");
  if (cancel) cancel.hidden = true;
  // Report a terminal failure without further processing.
  if (operation.state !== "SUCCEEDED") {
    modsFeedback(operation.terminal_error ? operation.terminal_error.message : "Operation failed safely.", true);
    return true;
  }
  // Reopen the workspace after settings are saved.
  if (operation.kind === "SAVE_STEAM_SETTINGS") { openMods(); return true; }
  if (operation.kind === "AUTHENTICATE_STEAMCMD") {
    modsFeedback("SteamCMD authentication completed. Credentials remain owned by SteamCMD.");
    return true;
  }
  if (operation.kind === "UPDATE_WORKSHOP_ITEMS") {
    const result = operation.result || {};
    if (!["VERIFIED", "EMPTY"].includes(result.download_state)) {
      const exitDetail = Number.isInteger(result.steamcmd_exit_code)
        ? ` (exit code ${result.steamcmd_exit_code})` : "";
      const summary = result.download_state === "UNKNOWN"
        ? (result.steamcmd_summary
          ? `${result.steamcmd_summary}${exitDetail}`
          : `SteamCMD exited without a verifiable update result${exitDetail}. Open SteamCMD login, then retry.`)
        : `Workshop update stopped with state: ${result.download_state || "FAILED"}.`;
      modsFeedback(summary, true);
      renderModsItems(result.items);
      return true;
    }
    renderModsItems(operation.result?.items);
    window.ServerManModPublication.reviewFromUpdate(operation);
    return true;
  }
  if (window.ServerManModPublication.operationFinished(operation)) return true;
  const result = operation.result || {};
  if (result.profile_id && result.profile_id !== selectedProfile()?.profile_id) return true;
  const summary = result.start_error === "PUBLICATION_REQUIRED"
    ? "Downloads were checked. Review and apply the mods before the server starts."
    : `${(result.items || []).length} Workshop item outcomes recorded: ${result.download_state}.`;
  modsFeedback(summary, ["FAILED", "UNKNOWN", "CANCELLED"].includes(result.download_state));
  renderModsItems(result.items);
  return true;
}

// Publish the mods workspace controls used by the shell.
window.ServerManMods = Object.freeze({ open: openMods, operationFinished: modsOperationFinished });
