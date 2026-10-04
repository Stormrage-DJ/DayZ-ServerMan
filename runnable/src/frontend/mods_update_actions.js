// Update actions of the Mods check header: "Update all" and the start or restart variant,
// the server state that they depend on, and the rule for what follows a finished update.
"use strict";

// Last server state read for the Mods page (null: not confirmed), the label shown last (kept while the
// page's own operation runs), and the pressed action.
const modsActionState = { server: null, label: "Update & start", start: false };
// Why the start action is not offered, per server state.
const modsServerReasons = Object.freeze({
  STARTING: "The server is starting.",
  STOPPING: "The server is stopping.",
  RUNNING_EXTERNAL: "The server runs outside DayZ-ServerMan. Stop it there, then use Update & start.",
});
// Reason for a state that could not be read or is not known.
const MODS_SERVER_UNCONFIRMED = "The server state could not be confirmed. See Overview.";
// What a finished update says when no review follows.
const modsUpdateTexts = Object.freeze({
  current: "All mods are current. Nothing to download or apply.",
  currentRunning: "All mods are current. The server was not restarted. To restart it anyway, use Save & Restart on Overview.",
  empty: "This profile has no Workshop mods.",
  emptyRunning: "This profile has no Workshop mods. The server was not restarted.",
  notStarted: " The server was not started.",
  keepsRunning: " The server keeps running; nothing was applied.",
});

// Describe the start action for the current server state: label, whether it is offered, and why not.
function updateStartPresentation() {
  const state = modsActionState.server;
  // A server that runs or is starting would be restarted; in every other state it would be started.
  const restart = ["RUNNING_MANAGED", "RUNNING_EXTERNAL", "STARTING"].includes(state);
  // While this page's own operation runs, the label keeps naming the action that is in progress.
  const label = modsState.pending ? modsActionState.label : restart ? "Update & restart" : "Update & start";
  // A restart stops the running server, so it is not offered while another profile runs (D11).
  const lock = state === "RUNNING_MANAGED" ? window.ServerManServerState.lockReason() : "";
  if (lock) return { label, enabled: false, reason: lock, locked: true };
  if (state === "STOPPED" || state === "RUNNING_MANAGED") return { label, enabled: true, reason: "" };
  const known = typeof state === "string" && Object.hasOwn(modsServerReasons, state);
  return { label, enabled: false, reason: known ? modsServerReasons[state] : MODS_SERVER_UNCONFIRMED };
}

// Write label, lock, and reason into the visible update buttons.
function syncUpdateActions() {
  const blocked = modsState.busy || !selectedProfile();
  const all = document.getElementById("update-workshop");
  if (all) all.disabled = blocked;
  const start = document.getElementById("update-start");
  if (!start) return;
  const shown = updateStartPresentation();
  modsActionState.label = shown.label;
  if (start.textContent !== shown.label) start.textContent = shown.label;
  start.disabled = blocked || !shown.enabled;
  // The reason of a state that offers no start is the tooltip; the busy lock keeps its own reason.
  if (shown.reason) start.title = shown.reason;
  else if (!start.hasAttribute("aria-disabled")) start.removeAttribute("title");
  syncRestartLockLine(start, shown.locked ? shown.reason : "");
}

// Show the D11 lock reason as visible text under the check header, tied to the action (QF-033).
function syncRestartLockLine(start, reason) {
  const header = document.getElementById("mods-update-header");
  let line = document.getElementById("mods-restart-lock");
  if (!reason) {
    line?.remove();
    if (start.getAttribute("aria-describedby") === "mods-restart-lock") start.removeAttribute("aria-describedby");
    return;
  }
  if (!line && header) {
    line = modsNode("p", "busy-wait-line mods-restart-lock"); line.id = "mods-restart-lock";
    header.after(line);
  }
  if (line && line.textContent !== reason) line.textContent = reason;
  // The busy lock manages the description while it holds the control.
  if (line && !start.hasAttribute("aria-disabled")) start.setAttribute("aria-describedby", "mods-restart-lock");
}

// Read the server state through the shared state; a failed read leaves the state unconfirmed.
async function readModsServerState() {
  const result = await window.ServerManServerState.read();
  modsActionState.server = result?.success && typeof result.value?.state === "string"
    ? result.value.state : null;
  syncUpdateActions();
  return modsActionState.server;
}

// Add the start action and "Update all" to the action group of the check header.
function attachUpdateActions(header) {
  const group = header.querySelector(".mods-header-actions");
  const start = modsNode("button", "button", modsActionState.label);
  start.id = "update-start"; start.type = "button";
  start.addEventListener("click", () => submitModsUpdate(true));
  const all = modsNode("button", "button button-primary", "Update all");
  all.id = "update-workshop"; all.type = "button";
  all.title = "Checks Steam, downloads changed mods, then asks before it applies them to the server folder.";
  all.addEventListener("click", () => submitModsUpdate(false));
  // Both actions queue an operation, so they are locked while another operation runs.
  group.append(window.ServerManBusy.mark(start), window.ServerManBusy.mark(all));
  syncUpdateActions();
  return header;
}

// Queue the update for the selected profile; the start flag says whether a start or restart follows.
async function submitModsUpdate(start) {
  const profile = selectedProfile();
  if (!profile) return modsFeedback("Select a profile first.", true);
  const context = captureModsContext();
  modsActionState.start = start;
  const result = await window.pywebview.api.update_workshop_items(
    profile.profile_id, profile.revision, profile.semantic_digest, modsState.settings.revision,
    modsState.settings.steam_authentication_mode, modsState.settings.steam_account_name,
    start,
  );
  if (!isModsContextActive(context)) return;
  acceptModsOperation(result, "Downloading or updating mods");
}

// Report whether a finished update found everything current: no download and every mod already applied.
function everythingCurrent(result) {
  const items = Array.isArray(result.items) ? result.items : [];
  return result.download_state === "VERIFIED" && result.process_id === null && items.length > 0
    && items.every((entry) => entry?.cache_proof?.verification_kind === "APPLIED_STATE");
}

// Report an update that did not end with verified downloads: sentence, SteamCMD detail, and the per-mod list.
function reportUnverifiedUpdate(operation, outcome) {
  const result = operation.result || {};
  const started = typeof result.start_requested === "boolean" ? result.start_requested : modsActionState.start;
  // Say what the requested start or restart did not do.
  const suffix = !started ? ""
    : modsActionState.server === "RUNNING_MANAGED" ? modsUpdateTexts.keepsRunning : modsUpdateTexts.notStarted;
  const exitDetail = Number.isInteger(result.steamcmd_exit_code)
    ? ` (exit code ${result.steamcmd_exit_code})` : "";
  // An unconfirmed update adds what SteamCMD reported and what to do next.
  const detail = result.download_state !== "UNKNOWN" ? ""
    : result.steamcmd_summary ? ` ${result.steamcmd_summary}${exitDetail}`
      : ` SteamCMD exited without a verifiable update result${exitDetail}. Open SteamCMD login, then retry.`;
  modsFeedback(`${outcome.text}${detail}${suffix}`, outcome.look !== "cancelled");
  renderModsItems(result.items);
  // A sign-in problem opens the sign-in form, so the login action is at hand; the focus stays.
  const items = Array.isArray(result.items) ? result.items : [];
  if (result.download_state === "UNKNOWN" || items.some(window.ServerManDiagnosticLabels.signInFailed)) {
    window.ServerManModsSignin.open();
  }
}

// Decide what follows a verified update: a sentence when nothing is to apply, else the apply review.
async function reviewAfterUpdate(operation) {
  const result = operation.result || {};
  const start = result.start_requested === true;
  const context = captureModsContext();
  const state = await readModsServerState();
  if (!isModsContextActive(context)) return;
  const restart = start && state === "RUNNING_MANAGED";
  const empty = result.download_state === "EMPTY";
  // A profile without Workshop mods needs a review only to confirm a start.
  if (empty && !start) return modsFeedback(modsUpdateTexts.empty);
  if (empty && restart) return modsFeedback(modsUpdateTexts.emptyRunning);
  const review = await window.ServerManModPublication.preview(operation);
  if (!review) return;
  // Everything is current only when the apply would also add no key file.
  const current = everythingCurrent(result) && review.preview.missing_key_count === 0;
  if (current && !start) return modsFeedback(modsUpdateTexts.current);
  if (current && restart) return modsFeedback(modsUpdateTexts.currentRunning);
  if (!current && !empty) renderModsItems(result.items);
  window.ServerManModPublication.show(review, { short: current || empty });
}

// Handle a finished update operation of this page; the rows already hold the per-mod outcome.
function modsUpdateFinished(operation, outcome) {
  const result = operation.result || {};
  if (operation.state !== "SUCCEEDED" || !["VERIFIED", "EMPTY"].includes(result.download_state)) {
    reportUnverifiedUpdate(operation, outcome);
    return true;
  }
  modsFeedback(outcome.sentence);
  void reviewAfterUpdate(operation);
  return true;
}

// Follow the shared server state: the shell reads it on every poll tick and announces a change.
document.addEventListener("serverman:server-status", (event) => {
  // A state that the last read did not confirm offers no start, like a failed read of this page.
  const confirmed = event.detail?.confirmed !== false && typeof event.detail?.status?.state === "string";
  modsActionState.server = confirmed ? event.detail.status.state : null;
  syncUpdateActions();
});
// Publish the update actions for the Mods page and its apply review.
window.ServerManModsActions = Object.freeze({
  attach: attachUpdateActions, sync: syncUpdateActions, read: readModsServerState,
  serverState: () => modsActionState.server, finished: modsUpdateFinished,
});
