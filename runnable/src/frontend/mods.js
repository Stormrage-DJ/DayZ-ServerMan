// Mods workspace: Steam access settings, the mod inventory, and the requests that queue mod operations.
"use strict";

// Tracks the loaded profiles, inventory, settings, and pending workshop operation.
const modsState = {
  profiles: [], inventory: [], settings: null, pending: null, generation: 0, busy: false,
  refresh: 0,
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
  // Lock the Steam settings controls during a host round trip.
  ["save-steam-settings", "authenticate-steamcmd", "steam-auth-mode"].forEach((id) => {
    const node = document.getElementById(id); if (node) node.disabled = busy;
  });
  const account = document.getElementById("steam-account-name");
  if (account) account.disabled = busy || document.getElementById("steam-auth-mode")?.value === "ANONYMOUS";
  window.ServerManModsActions.sync();
  window.ServerManModsVerify.sync();
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
  // Wire the sign-in and login actions; each submits an operation and is locked while one runs.
  // The update actions are in the check header of "Configured mods".
  [["save-steam-settings", "Save sign-in settings"],
    ["authenticate-steamcmd", "Open SteamCMD login"]].forEach(([id, label]) => {
      const button = modsNode("button", "button", label);
      button.id = id; button.type = "button"; authenticationActions.append(window.ServerManBusy.mark(button));
    });
  actions.append(authenticationActions);
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
  if (modsState.pending) setModsBusy(true);
}

// Open the mods workspace with a fresh snapshot, profiles, and inventory.
async function openMods() {
  const initialized = await window.ServerManProfileContext.initialize();
  if (!initialized.success) { window.ServerManUi.renderHostError(initialized); return; }
  const generation = ++modsState.generation;
  const workspace = window.ServerManWorkspace.capture("mods");
  const profileId = window.ServerManProfileContext?.selectedId?.();
  // Request an update check and read its state before the inventory, so a later change is seen.
  const updates = window.ServerManUpdateStatus.open();
  // Load the snapshot, profiles, and inventory together; the server state decides the start action.
  void window.ServerManModsActions.read();
  window.ServerManModsProgress.clear();
  const [snapshot, profiles, inventory] = await Promise.all([
    window.pywebview.api.get_application_snapshot(), window.pywebview.api.list_profiles(),
    profileId ? updates.then(() => window.pywebview.api.list_mod_inventory(profileId))
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

// Re-read the inventory after an update-state change and redraw only the inventory panel.
async function refreshModsInventory() {
  const context = captureModsContext();
  if (!isModsContextActive(context)) return;
  // Show the new check state at once, then fetch the rows that belong to it.
  updateModInventory();
  if (!context.profileId) return;
  const ticket = ++modsState.refresh;
  const inventory = await window.pywebview.api.list_mod_inventory(context.profileId);
  // Keep the visible rows when the page moved on, a newer refresh started, or the read failed.
  if (!isModsContextActive(context) || ticket !== modsState.refresh || !inventory?.success) return;
  modsState.inventory = inventory.value;
  updateModInventory();
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

// Redraw the inventory panel whenever the update state of the selected profile changes.
document.addEventListener("serverman:update-status", () => { void refreshModsInventory(); });
// Publish the mods workspace controls used by the shell.
window.ServerManMods = Object.freeze({ open: openMods, operationFinished: modsOperationFinished });
