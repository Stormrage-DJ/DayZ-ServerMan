// Update status: the remote update-check state of the selected profile and the Mods check header.
"use strict";

// Shortest pause between two status reads while no check runs, in milliseconds.
const UPDATE_STATUS_IDLE_INTERVAL = 5000;
// Last status of the selected profile, the automatic-check preference, and read bookkeeping.
const updateStatusState = {
  profileId: "", status: null, automatic: true, readAt: 0, reading: false, requestError: "",
};
// Operator wording for each failure code of a check run.
const updateFailureReasons = Object.freeze({
  NETWORK_UNREACHABLE: "Steam could not be reached",
  TIMEOUT: "Steam did not answer in time",
  TLS_FAILURE: "the secure connection to Steam failed",
  HTTP_STATUS: "Steam refused the request",
  RESPONSE_TOO_LARGE: "the answer from Steam was too large",
  RESPONSE_MALFORMED: "the answer from Steam could not be read",
});

// Return the selected profile identifier, or an empty text when none is selected.
function updateStatusProfile() {
  return window.ServerManProfileContext?.selectedId?.() || "";
}

// Reduce a status to the values whose change must redraw the update indicators.
function updateStatusSignature(status) {
  if (!status) return "none";
  const mods = status.mods || {};
  return [status.revision, status.checking, mods.check_state, mods.last_success_at,
    mods.error_code, mods.update_count, mods.pending_apply_count].join("|");
}

// Read the status of the selected profile; resolve true when it differs from the last one.
async function readUpdateStatus() {
  const profileId = updateStatusProfile();
  // Forget the state of another profile before the new read.
  if (profileId !== updateStatusState.profileId) {
    updateStatusState.profileId = profileId; updateStatusState.status = null;
  }
  if (!profileId) return false;
  const before = updateStatusSignature(updateStatusState.status);
  const result = await window.pywebview.api.get_update_status(profileId);
  // Drop an answer for a profile that is no longer selected.
  if (profileId !== updateStatusProfile()) return false;
  updateStatusState.readAt = Date.now();
  updateStatusState.status = result && result.success ? Object.freeze(result.value) : null;
  return before !== updateStatusSignature(updateStatusState.status);
}

// Tell the listening pages that the update state of the selected profile changed.
function announceUpdateStatus() {
  document.dispatchEvent(new CustomEvent("serverman:update-status", {
    detail: { profileId: updateStatusState.profileId, status: updateStatusState.status },
  }));
}

// Ask the host for a mod check; a forced request ignores the automatic-check switch.
async function requestUpdateCheck(force) {
  const result = await window.pywebview.api.request_update_check("mods", force);
  // Keep the failure of an operator request for the header.
  updateStatusState.requestError = !force || (result && result.success) ? ""
    : window.ServerManOperationMessages.bridgeError(result, "The check request failed safely.");
  return result;
}

// Start on Mods open or profile change: request a non-forced check, then read the state.
async function openUpdateStatus() {
  if (!updateStatusProfile()) { await readUpdateStatus(); return; }
  // Send the request and read the effective automatic-check preference together.
  const [, preferences] = await Promise.all([
    requestUpdateCheck(false), window.pywebview.api.get_ui_preferences(),
  ]);
  if (preferences && preferences.success) {
    updateStatusState.automatic = preferences.value.automatic_update_checks !== false;
  }
  await readUpdateStatus();
}

// Refresh on the shell poll: at most once per idle interval, on every poll while a check runs.
async function pollUpdateStatus() {
  // Each poll tick of the visible Mods page also lets the page refresh what it reads itself.
  document.dispatchEvent(new CustomEvent("serverman:mods-tick"));
  const checking = updateStatusState.status?.checking === true;
  const waited = Date.now() - updateStatusState.readAt;
  if (updateStatusState.reading || (!checking && waited < UPDATE_STATUS_IDLE_INTERVAL)) return;
  // Allow one read at a time and announce only a real change.
  updateStatusState.reading = true;
  try {
    if (await readUpdateStatus()) announceUpdateStatus();
  } finally {
    updateStatusState.reading = false;
  }
}

// Request a check, read the fresh state, and announce it; "Check now" passes force.
async function recheckUpdateStatus(force = false) {
  if (!updateStatusProfile()) return;
  await requestUpdateCheck(force);
  await readUpdateStatus();
  announceUpdateStatus();
}

// Format a Steam time (seconds) or a check time (UTC text) in the operator's locale.
function formatUpdateTime(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return null;
  // Pair the compact text with the exact value for a tooltip.
  return Object.freeze({
    text: date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
    exact: date.toLocaleString(undefined, { dateStyle: "full", timeStyle: "long" }),
  });
}

// Explain in operator words why an item or the whole check has no verified answer.
function updateCheckReason(kind) {
  if (kind === "UNKNOWN_ITEM") return "Steam does not list this item";
  if (kind === "FAILED") {
    return updateFailureReasons[updateStatusState.status?.mods?.error_code] || "the check failed";
  }
  // A missing or old check with the switch off is the operator's own setting.
  if (!updateStatusState.automatic) return "automatic checks are off";
  return kind === "STALE" ? "the last check is too old" : "not checked yet";
}

// Phrase a count with its singular or plural noun.
function updateCountText(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}

// Summarize the update state in one line; mod rows refine the "all current" wording.
function updateSummary(rows = null) {
  const status = updateStatusState.status;
  if (!status || !status.mods) return { text: "Update status is unavailable", tone: "neutral" };
  const mods = status.mods;
  // The first check of this session is still running.
  if (status.checking && mods.check_state === "NEVER") {
    return { text: "Checking for updates…", tone: "neutral" };
  }
  // Name what needs the operator, in the order of the table states.
  const parts = [];
  if (mods.update_count > 0) {
    parts.push(`${updateCountText(mods.update_count, "update", "updates")} available`);
  }
  if (mods.pending_apply_count > 0) parts.push(`${mods.pending_apply_count} downloaded - not applied`);
  const pending = parts.length > 0;
  if (mods.check_state !== "OK") parts.push(`Could not check: ${updateCheckReason(mods.check_state)}`);
  if (parts.length) {
    return { text: parts.join(" · "),
      tone: pending ? "warning" : mods.check_state === "FAILED" ? "error" : "neutral" };
  }
  // Nothing is pending: say "current" only when every row is verified or local.
  if (!Array.isArray(rows)) return { text: "No updates available", tone: "normal" };
  if (!rows.length) return { text: "No mods to check", tone: "neutral" };
  const settled = rows.every((row) => ["CURRENT", "LOCAL"].includes(row.state));
  if (!settled) return { text: "No updates available", tone: "neutral" };
  return { text: rows.length === 1 ? "The mod is current" : `All ${rows.length} mods are current`,
    tone: "normal" };
}

// Set a text only when it differs, so assistive technology hears real changes only.
function setUpdateText(node, text, title = "") {
  if (node.textContent !== text) node.textContent = text;
  if (title) node.title = title; else node.removeAttribute("title");
}

// Run "Check now" from the header button and show the busy mark at once.
async function checkUpdatesNow(event) {
  const button = event.currentTarget;
  button.disabled = true;
  const busy = document.getElementById("mods-update-busy");
  if (busy) busy.hidden = false;
  await recheckUpdateStatus(true);
  // Unlock the action when no page redrew the header after the answer.
  if (button.isConnected) button.disabled = updateStatusState.status?.checking === true;
}

// Build the check header of the "Configured mods" panel: summary, last check, and "Check now".
function renderUpdateHeader(rows) {
  const node = window.ServerManUi.element;
  const header = node("div", "mods-update-header"); header.id = "mods-update-header";
  // Announce summary changes politely without moving the focus.
  const copy = node("div", "mods-update-copy"); copy.setAttribute("role", "status");
  const summary = node("strong", "status-label mods-update-summary"); summary.id = "mods-update-summary";
  const meta = node("span", "mods-update-meta");
  const checked = node("span", "mods-last-checked"); checked.id = "mods-last-checked";
  const busy = node("span", "mods-update-busy", "Checking…"); busy.id = "mods-update-busy";
  meta.append(checked, busy); copy.append(summary, meta);
  // Offer the on-demand check beside the summary, in a group that takes further header actions.
  const button = node("button", "button", "Check now");
  button.id = "check-updates-now"; button.type = "button";
  button.addEventListener("click", checkUpdatesNow);
  const actions = node("div", "mods-header-actions"); actions.append(button);
  header.append(copy, actions);
  fillUpdateHeader(header, rows);
  return header;
}

// Write the current summary, last check time, busy mark, and action state into a header.
function fillUpdateHeader(header, rows) {
  const status = updateStatusState.status;
  const checking = status?.checking === true;
  // Show a failed operator request in place of the summary.
  const summary = updateStatusState.requestError
    ? { text: `Could not check: ${updateStatusState.requestError}`, tone: "error" }
    : updateSummary(rows);
  const line = header.querySelector(".mods-update-summary");
  setUpdateText(line, summary.text);
  line.className = `status-label mods-update-summary status-${summary.tone}`;
  // Show the last successful check with its exact value as a tooltip.
  const success = formatUpdateTime(status?.mods?.last_success_at);
  setUpdateText(header.querySelector(".mods-last-checked"),
    success ? `Last checked ${success.text}` : "Not checked yet", success ? success.exact : "");
  // Mark a running check and lock the action until it ends.
  header.querySelector(".mods-update-busy").hidden = !checking;
  header.querySelector("#check-updates-now").disabled = checking;
}

// Refresh the visible Mods check header in place.
function refreshUpdateHeader(rows) {
  const header = document.getElementById("mods-update-header");
  if (header) fillUpdateHeader(header, rows);
}

// Publish the update state for the Mods page and later shell indicators.
window.ServerManUpdateStatus = Object.freeze({
  open: openUpdateStatus,
  poll: pollUpdateStatus,
  recheck: recheckUpdateStatus,
  current: () => updateStatusState.status,
  automaticChecks: () => updateStatusState.automatic,
  reason: updateCheckReason,
  formatTime: formatUpdateTime,
  summary: updateSummary,
  renderHeader: renderUpdateHeader,
  refreshHeader: refreshUpdateHeader,
});
