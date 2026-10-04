// Check header of the "Configured mods" panel: summary, last check, busy mark, and "Check now".
"use strict";

// Run "Check now" from the header button and show the busy mark at once.
async function checkUpdatesNow(event) {
  const button = event.currentTarget;
  button.disabled = true;
  const busy = document.getElementById("mods-update-busy");
  if (busy) busy.hidden = false;
  await window.ServerManUpdateStatus.recheck(true);
  // Unlock the action when no page redrew the header after the answer.
  if (button.isConnected) button.disabled = window.ServerManUpdateStatus.current()?.checking === true;
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
  const status = window.ServerManUpdateStatus.current();
  const checking = status?.checking === true;
  // Show a failed operator request in place of the summary.
  const requestError = window.ServerManUpdateStatus.requestError();
  const summary = requestError
    ? { text: `Could not check: ${requestError}`, tone: "error" }
    : window.ServerManUpdateStatus.summary(rows);
  const line = header.querySelector(".mods-update-summary");
  setUpdateText(line, summary.text);
  line.className = `status-label mods-update-summary status-${summary.tone}`;
  // Show the last successful check with its exact value as a tooltip.
  const success = window.ServerManUpdateStatus.formatTime(status?.mods?.last_success_at);
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

// Publish the check header for the Mods page.
window.ServerManUpdateHeader = Object.freeze({
  render: renderUpdateHeader,
  refresh: refreshUpdateHeader,
});
