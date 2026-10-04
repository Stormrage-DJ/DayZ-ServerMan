// Steam sign-in panel of the Mods page: one line when sign-in is configured, the full form behind "Change".
"use strict";

// Guidance shown above the fields while no sign-in is configured.
const MODS_SIGNIN_GUIDANCE = "Choose how SteamCMD signs in before the first download. "
  + "Use “Steam account” if Steam refuses anonymous downloads for your mods.";

// Report whether the saved settings hold a usable sign-in: anonymous, or an account with a name.
function modsSigninConfigured(settings) {
  return settings.steam_authentication_mode === "ANONYMOUS"
    || (settings.steam_authentication_mode === "ACCOUNT" && Boolean(settings.steam_account_name));
}

// Word the saved sign-in for the one-line form.
function modsSigninSummary(settings) {
  return settings.steam_authentication_mode === "ANONYMOUS"
    ? "Steam sign-in: anonymous" : `Steam sign-in: account ${settings.steam_account_name}`;
}

// Replace the feedback area of the sign-in panel with a status or alert notice.
function modsSigninFeedback(message, error = false) {
  modsFeedback(message, error, "mods-signin-feedback");
}

// Build the two fields of the form: sign-in mode and account name, filled from the saved settings.
function renderModsSigninFields(settings) {
  const row = modsNode("div", "mods-access-row");
  const modeLabel = modsNode("label", "mods-field");
  modeLabel.append(modsNode("span", "mods-field-label", "Sign-in"));
  const mode = modsNode("select"); mode.id = "steam-auth-mode";
  // Offer the sign-in modes with the saved one selected.
  [["", "Choose mode"], ["ACCOUNT", "Steam account"], ["ANONYMOUS", "Anonymous"]]
    .forEach(([value, label]) => { const option = modsNode("option", "", label); option.value = value;
      option.selected = settings.steam_authentication_mode === value; mode.append(option); });
  modeLabel.append(mode);
  const accountLabel = modsNode("label", "mods-field");
  accountLabel.append(modsNode("span", "mods-field-label", "Steam account name"));
  const account = modsNode("input"); account.id = "steam-account-name"; account.type = "text";
  account.autocomplete = "off"; account.maxLength = 64; account.value = settings.steam_account_name || "";
  accountLabel.append(account);
  row.append(modeLabel, accountLabel);
  mode.addEventListener("change", () => {
    // Clear the account name when anonymous sign-in is chosen.
    if (mode.value === "ANONYMOUS") account.value = "";
    setModsBusy(modsState.busy);
  });
  return row;
}

// Open or close the form; the "Change" button says which, and nothing moves the focus here.
function setModsSigninOpen(open) {
  const form = document.getElementById("mods-signin-form");
  if (!form) return;
  form.hidden = !open;
  document.getElementById("mods-signin-change")?.setAttribute("aria-expanded", String(open));
}

// Put the saved values back into the fields, close the form, and return the focus to "Change".
function cancelModsSignin() {
  const settings = modsState.settings;
  document.getElementById("steam-auth-mode").value = settings.steam_authentication_mode || "";
  document.getElementById("steam-account-name").value = settings.steam_account_name || "";
  setModsBusy(modsState.busy);
  setModsSigninOpen(false);
  document.getElementById("mods-signin-change")?.focus();
}

// Build the form: fields, the credential notice directly after them, then the actions.
function renderModsSigninForm(settings, configured) {
  const form = modsNode("div", "mods-signin-form"); form.id = "mods-signin-form";
  if (!configured) form.append(modsNode("p", "mods-signin-guidance", MODS_SIGNIN_GUIDANCE));
  // Explain where Steam credentials are entered and who owns them.
  const safety = modsNode("div", "notice notice-warning"); safety.setAttribute("role", "status");
  safety.append(modsNode("strong", "", "Credential safety"), modsNode("p", "",
    "Enter passwords and Steam Guard codes only in the visible SteamCMD window. DayZ-ServerMan never asks for them."));
  const actions = modsNode("div", "mods-actions");
  const group = modsNode("div", "mods-action-group");
  // Each of the two actions submits an operation and is locked while one runs.
  [["save-steam-settings", "Save sign-in settings", saveSteamSettings],
    ["authenticate-steamcmd", "Open SteamCMD login", authenticateSteamCmd]].forEach(([id, label, handler]) => {
    const button = modsNode("button", "button", label);
    button.id = id; button.type = "button"; button.addEventListener("click", handler);
    group.append(window.ServerManBusy.mark(button));
  });
  // A configured sign-in can be left unchanged.
  if (configured) {
    const cancel = modsNode("button", "button", "Cancel"); cancel.id = "mods-signin-cancel"; cancel.type = "button";
    cancel.addEventListener("click", cancelModsSignin);
    group.append(cancel);
  }
  actions.append(group);
  form.append(renderModsSigninFields(settings), safety, actions);
  form.hidden = configured;
  return form;
}

// Build the sign-in panel: the line with "Change" when configured, else the heading and the open form.
function renderModsSignin(settings) {
  const configured = modsSigninConfigured(settings);
  const panel = modsNode("section", "panel mods-panel mods-signin");
  if (configured) {
    panel.setAttribute("aria-label", "Steam sign-in");
    const line = modsNode("div", "mods-signin-line");
    const change = modsNode("button", "button", "Change"); change.id = "mods-signin-change"; change.type = "button";
    change.setAttribute("aria-label", "Change Steam sign-in");
    change.setAttribute("aria-expanded", "false"); change.setAttribute("aria-controls", "mods-signin-form");
    change.addEventListener("click", () => setModsSigninOpen(change.getAttribute("aria-expanded") !== "true"));
    line.append(modsNode("span", "mods-signin-summary", modsSigninSummary(settings)), change);
    panel.append(line);
  } else {
    const heading = modsNode("div", "panel-heading");
    heading.append(modsNode("h2", "", "Steam sign-in"));
    panel.append(heading);
  }
  const feedback = modsNode("div", "mods-feedback"); feedback.id = "mods-signin-feedback";
  panel.append(renderModsSigninForm(settings, configured), feedback);
  return panel;
}

// Publish the sign-in panel for the Mods page.
window.ServerManModsSignin = Object.freeze({
  render: renderModsSignin,
  feedback: modsSigninFeedback,
  // An update that ended with a sign-in problem opens the form, so the login action is at hand.
  open: () => setModsSigninOpen(true),
});
