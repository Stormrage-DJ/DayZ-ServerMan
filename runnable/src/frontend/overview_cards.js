// Overview cards: the update state of the selected profile and its newest backup.
"use strict";

// Sequence number of the newest backup read, so a late answer of an earlier profile is dropped.
const overviewCardState = { backupSequence: 0 };

// Build one card: a titled panel with a body that its owner fills and a row of actions.
function overviewCard(id, title) {
  const card = overviewNode("article", "panel overview-card"); card.id = id;
  const body = overviewNode("div", "overview-card-body");
  const actions = overviewNode("div", "action-row overview-card-actions");
  card.append(overviewNode("h2", "", title), body, actions);
  return { card, body, actions };
}

// Build a button of a card that opens a section through the guarded section switch.
function overviewCardLink(label, section) {
  const button = overviewNode("button", "button", label); button.type = "button";
  button.addEventListener("click", () => setSection(section));
  return button;
}

// Set a text and a class only when they differ, so nothing is announced or redrawn in vain.
function setOverviewCardText(node, text, className = null) {
  if (node.textContent !== text) node.textContent = text;
  if (className !== null && node.className !== className) node.className = className;
}

// Write the update state into the card: the same summary as the Mods header, the last check, the switch.
function fillOverviewUpdates() {
  const card = document.getElementById("overview-updates");
  if (!card) return;
  const updates = window.ServerManUpdateStatus;
  const status = updates.current();
  const profile = Boolean(window.ServerManProfileContext.selectedId());
  const checking = status?.checking === true;
  // A failed request of the operator is shown in place of the summary, as on Mods.
  const failed = updates.requestError();
  const summary = !profile ? { text: "No server profile", tone: "neutral" }
    : failed ? { text: `Could not check: ${failed}`, tone: "error" } : updates.summary();
  setOverviewCardText(card.querySelector(".overview-updates-summary"), summary.text,
    `status-label overview-updates-summary status-${summary.tone}`);
  const success = updates.formatTime(status?.mods?.last_success_at);
  const checked = card.querySelector(".overview-updates-checked");
  setOverviewCardText(checked, success ? `Last checked ${success.text}` : "Not checked yet");
  if (success) checked.title = success.exact; else checked.removeAttribute("title");
  card.querySelector(".mods-update-busy").hidden = !checking;
  card.querySelector(".overview-updates-switch").hidden = updates.automaticChecks();
  card.querySelector("#overview-check-now").disabled = checking || !profile;
  // "Open Mods" is the primary action while something waits for the operator.
  const open = card.querySelector("#overview-open-mods");
  const className = updates.badge().count > 0 ? "button button-primary" : "button";
  if (open.className !== className) open.className = className;
}

// Run "Check now" from the card and show the busy mark at once.
async function checkOverviewUpdates(event) {
  const button = event.currentTarget;
  button.disabled = true;
  await window.ServerManUpdateStatus.recheck(true);
  fillOverviewUpdates();
}

// Build the "Updates" card; its actions lead to Mods, where the update itself is reviewed.
function renderOverviewUpdates() {
  const { card, body, actions } = overviewCard("overview-updates", "Updates");
  const meta = overviewNode("p", "overview-card-meta");
  meta.append(overviewNode("span", "overview-updates-checked"),
    overviewNode("span", "mods-update-busy", "Checking…"));
  body.append(overviewNode("p", "status-label overview-updates-summary"), meta,
    overviewNode("p", "overview-card-meta overview-updates-switch", "Automatic checks are off."));
  const check = overviewNode("button", "button", "Check now"); check.id = "overview-check-now"; check.type = "button";
  check.addEventListener("click", checkOverviewUpdates);
  const open = overviewCardLink("Open Mods", "mods"); open.id = "overview-open-mods";
  actions.append(check, open);
  // The card is filled once it is in the page, so the first draw and a later redraw take the same path.
  queueMicrotask(fillOverviewUpdates);
  return card;
}

// Write lines into the backup card; the first line is the leading one.
function fillOverviewBackup(lines) {
  const body = document.querySelector("#overview-backup .overview-card-body");
  if (!body) return;
  body.replaceChildren(...lines.map(([text, className], index) =>
    overviewNode(index === 0 ? "strong" : "p", className || (index === 0 ? "overview-card-lead" : "overview-card-meta"), text)));
}

// Describe the newest backup of a history: date, verified content, and whether it can be restored.
function overviewBackupLines(history) {
  const newest = [...history.backups].sort((left, right) =>
    String(right.created_at).localeCompare(String(left.created_at)))[0];
  const lines = [];
  if (!newest) lines.push(["No backup yet"]);
  else {
    const ready = newest.restore_compatibility === "COMPATIBLE";
    lines.push([window.ServerManBackupDisplay.date(newest.created_at)],
      [`Verified · ${window.ServerManBackupDisplay.counts(newest)}`],
      [ready ? "Restore ready" : "Cannot be restored", `status-label ${ready ? "status-normal" : "status-warning"}`]);
  }
  if (!history.runtime_profile) lines.push(["Set a runtime profile directory in Profiles to make backups."]);
  return lines;
}

// Read the backup history of the profile after the page is drawn; the page never waits for it.
async function loadOverviewBackup(profile) {
  const sequence = ++overviewCardState.backupSequence;
  if (!profile) return fillOverviewBackup([["No server profile"]]);
  fillOverviewBackup([["Checking…"]]);
  let result = null;
  try { result = await window.pywebview.api.list_backups(profile.profile_id); } catch (_error) { result = null; }
  // Drop the answer when a newer read started or the card belongs to another profile now.
  if (sequence !== overviewCardState.backupSequence
      || window.ServerManProfileContext.selectedId() !== profile.profile_id) return;
  if (!result?.success || !Array.isArray(result.value?.backups)) {
    return fillOverviewBackup([["Backup history could not be read."]]);
  }
  fillOverviewBackup(overviewBackupLines(result.value));
}

// Build the "Last backup" card and start its late read.
function renderOverviewBackup(profile) {
  const { card, actions } = overviewCard("overview-backup", "Last backup");
  actions.append(overviewCardLink("Open Backups", "backups"));
  queueMicrotask(() => { void loadOverviewBackup(profile); });
  return card;
}

// The updates card follows the update state of the selected profile.
document.addEventListener("serverman:update-status", fillOverviewUpdates);

// Publish the two cards for the Overview page.
window.ServerManOverviewCards = Object.freeze({
  updates: renderOverviewUpdates,
  backup: renderOverviewBackup,
  reloadBackup: loadOverviewBackup,
  link: overviewCardLink,
});
