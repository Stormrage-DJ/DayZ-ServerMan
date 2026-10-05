// Overview status board: two lists of rows (updates; backup and schedule) under one visually hidden heading
// "Status". Every row has the same four columns: icon, title, state with its details, and one action.
"use strict";

// Sequence number of the newest backup read, so a late answer of an earlier profile is dropped.
const overviewCardState = { backupSequence: 0 };
// Path data of the row icons; three are the icons of the matching navigation items.
const overviewRowIcons = Object.freeze({
  mods: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9",
  server: "M4 5h16v6H4z M4 13h16v6H4z M7.5 8h.01 M7.5 16h.01",
  backup: "M3 5h18v4H3z M5 9v10h14V9 M10 13h4",
  schedule: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3 2",
});

// Build one row: icon, title, a body that its owner fills, and an action cell.
function overviewRow(id, icon, title) {
  const row = overviewNode("li", "overview-row"); row.id = id;
  // The navigation builder draws the same inline icon; the row gives it its own class.
  const mark = navigationIcon(overviewRowIcons[icon]); mark.setAttribute("class", "overview-row-icon");
  const body = overviewNode("div", "overview-row-body");
  const action = overviewNode("div", "overview-row-action");
  row.append(mark, overviewNode("h3", "overview-row-title", title), body, action);
  return { row, body, action };
}

// Build a compact button of the board that opens a section through the guarded section switch.
function overviewCardLink(label, section) {
  const button = overviewNode("button", "button button-compact", label); button.type = "button";
  button.addEventListener("click", () => setSection(section));
  return button;
}

// Set a text and a class only when they differ, so nothing is announced or redrawn in vain.
function setOverviewCardText(node, text, className = null) {
  if (node.textContent !== text) node.textContent = text;
  if (className !== null && node.className !== className) node.className = className;
}

// Write a status head with its tone and the reason under it; an empty reason hides its line.
function fillOverviewStatus(head, reason, summary, baseClass) {
  setOverviewCardText(head, summary.head ?? summary.text, `status-label overview-status ${baseClass} status-${summary.tone}`);
  setOverviewCardText(reason, summary.reason || "");
  reason.hidden = !summary.reason;
}

// Write the update state into the Mods row: the Mods header summary as head and reason, the last check, the switch.
function fillOverviewUpdates() {
  const row = document.getElementById("overview-updates");
  if (!row) return;
  const updates = window.ServerManUpdateStatus;
  const status = updates.current();
  const profile = Boolean(window.ServerManProfileContext.selectedId());
  const checking = status?.checking === true;
  // A failed request of the operator is shown in place of the summary, as on Mods.
  const failed = updates.requestError();
  const summary = !profile ? { text: "No server profile", tone: "neutral" }
    : failed ? { text: `Could not check: ${failed}`, head: "Could not check", reason: failed, tone: "error" }
      : updates.summary(null, true);
  const reason = row.querySelector(".overview-updates-reason");
  fillOverviewStatus(row.querySelector(".overview-updates-summary"), reason, summary, "overview-updates-summary");
  const success = updates.formatTime(status?.mods?.last_success_at);
  const checked = row.querySelector(".overview-updates-checked");
  setOverviewCardText(checked, success ? `Last checked ${success.text}` : "Not checked yet");
  if (success) checked.title = success.exact; else checked.removeAttribute("title");
  row.querySelector(".mods-update-busy").hidden = !checking;
  // The switch line is not repeated when the reason already says that automatic checks are off.
  const switchLine = row.querySelector(".overview-updates-switch");
  switchLine.hidden = updates.automaticChecks() || reason.textContent === switchLine.textContent;
  // Without a profile "Check now" still checks the server build
  document.getElementById("overview-check-now").disabled = checking;
  window.ServerManServerBuild.fill(status);
  // "Open Mods" is the primary action while something waits for the operator.
  const open = row.querySelector("#overview-open-mods");
  const className = updates.badge().count > 0 ? "button button-compact button-primary" : "button button-compact";
  if (open.className !== className) open.className = className;
}

// Run "Check now" from the board and show the busy mark at once: mods and the server build, or only the
// server build without a profile.
async function checkOverviewUpdates(event) {
  const button = event.currentTarget;
  button.disabled = true;
  const scope = window.ServerManProfileContext.selectedId() ? "all" : "server_build";
  await window.ServerManUpdateStatus.recheck(true, scope);
  fillOverviewUpdates();
}

// Build "Check now" as a link button; it checks mods and the server build (QF-059, T5).
function renderOverviewCheckNow() {
  const check = overviewNode("button", "link-button overview-check-now", "Check now");
  check.id = "overview-check-now"; check.type = "button";
  check.title = "Check Steam now for mod updates and the DayZ server build";
  check.addEventListener("click", checkOverviewUpdates);
  return check;
}

// Build the Mods row; "Check now" follows the last check, and the action leads to Mods, where the update itself
// is reviewed.
function renderOverviewUpdates() {
  const { row, body, action } = overviewRow("overview-updates", "mods", "Mods");
  const metas = overviewNode("div", "overview-metas");
  const reason = overviewNode("p", "overview-meta overview-reason overview-updates-reason"); reason.hidden = true;
  metas.append(reason, overviewNode("p", "overview-meta overview-updates-checked"), renderOverviewCheckNow(),
    overviewNode("p", "overview-meta mods-update-busy", "Checking…"),
    overviewNode("p", "overview-meta overview-updates-switch", "Automatic checks are off."));
  body.append(overviewNode("p", "status-label overview-status overview-updates-summary"), metas);
  const open = overviewCardLink("Open Mods", "mods"); open.id = "overview-open-mods";
  action.append(open);
  return row;
}

// Write the backup row: a status line with its tone, then the detail lines.
function fillOverviewBackup(head, tone = "neutral", details = []) {
  const body = document.querySelector("#overview-backup .overview-row-body");
  if (!body) return;
  const metas = overviewNode("div", "overview-metas");
  metas.append(...details.map((text) => overviewNode("p", "overview-meta", text)));
  body.replaceChildren(overviewNode("p", `status-label overview-status status-${tone}`, head),
    ...(details.length ? [metas] : []));
}

// Describe the newest backup of a history: date and restore readiness, verified content, and the setup hint.
function overviewBackupLines(history) {
  const newest = [...history.backups].sort((left, right) =>
    String(right.created_at).localeCompare(String(left.created_at)))[0];
  const hint = history.runtime_profile ? [] : ["Set a runtime profile directory in Profiles to make backups."];
  if (!newest) return ["No backup yet", "neutral", hint];
  const ready = newest.restore_compatibility === "COMPATIBLE";
  return [`${window.ServerManBackupDisplay.date(newest.created_at)} · ${ready ? "Restore ready" : "Cannot be restored"}`,
    ready ? "normal" : "warning", [`Verified · ${window.ServerManBackupDisplay.counts(newest)}`, ...hint]];
}

// Read the backup history of the profile after the page is drawn; the page never waits for it.
async function loadOverviewBackup(profile) {
  const sequence = ++overviewCardState.backupSequence;
  if (!profile) return fillOverviewBackup("No server profile");
  fillOverviewBackup("Checking…");
  let result = null;
  try { result = await window.pywebview.api.list_backups(profile.profile_id); } catch (_error) { result = null; }
  // Drop the answer when a newer read started or the row belongs to another profile now.
  if (sequence !== overviewCardState.backupSequence
      || window.ServerManProfileContext.selectedId() !== profile.profile_id) return;
  if (!result?.success || !Array.isArray(result.value?.backups)) {
    return fillOverviewBackup("Backup history could not be read.", "error");
  }
  fillOverviewBackup(...overviewBackupLines(result.value));
}

// Build the "Last backup" row and start its late read.
function renderOverviewBackup(profile) {
  const { row, action } = overviewRow("overview-backup", "backup", "Last backup");
  action.append(overviewCardLink("Open Backups", "backups"));
  queueMicrotask(() => { void loadOverviewBackup(profile); });
  return row;
}

// Build the status board: the update rows, then the backup and schedule rows, named by a hidden "Status" heading.
function renderOverviewBoard(profile) {
  const board = overviewNode("section", "panel overview-board");
  const title = overviewNode("h2", "sr-only", "Status"); title.id = "overview-board-title";
  board.setAttribute("aria-labelledby", title.id);
  const updates = overviewNode("ul", "overview-list");
  updates.append(renderOverviewUpdates(), window.ServerManServerBuild.render());
  const rest = overviewNode("ul", "overview-list");
  rest.append(renderOverviewBackup(profile), ...window.ServerManOverviewSchedule.create(profile));
  board.append(title, updates, rest);
  // The rows are filled once they are in the page, so the first draw and a later redraw take the same path.
  queueMicrotask(fillOverviewUpdates);
  return board;
}

// The Mods row follows the update state of the selected profile.
document.addEventListener("serverman:update-status", fillOverviewUpdates);

// Publish the status board for the Overview page.
window.ServerManOverviewCards = Object.freeze({
  board: renderOverviewBoard,
  reloadBackup: loadOverviewBackup,
  row: overviewRow,
  status: fillOverviewStatus,
  link: overviewCardLink,
});
