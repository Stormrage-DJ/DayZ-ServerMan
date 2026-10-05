// Overview server strip: the state once, the process facts written out while the server runs, the
// "Process details" disclosure while it does not, an always-visible diagnostic note, and the four lifecycle controls.
"use strict";

// Why a lifecycle control is off, per server state that the page read.
const overviewStateReasons = Object.freeze({
  STOPPED: "The server is not running.",
  RUNNING_MANAGED: "The server is already running.",
  STARTING: "The server is starting.",
  STOPPING: "The server is stopping.",
  RUNNING_EXTERNAL: "The server runs outside DayZ-ServerMan. Stop it there.",
});
// The same for a state that is not known, and for the three blockers that come before the state.
const overviewBlockReasons = Object.freeze({
  unconfirmed: "The server state could not be confirmed.",
  recovery: "Changes are blocked until recovery is resolved.",
  setup: "Complete the setup in Settings first.",
  profile: "Create a server profile first.",
});
// States in which a server process runs; only these show the uptime and the process facts in line.
const OVERVIEW_RUNNING_STATES = Object.freeze(["RUNNING_MANAGED", "RUNNING_EXTERNAL"]);

// Word a duration: under a minute, minutes, hours and minutes, and days and hours from 48 hours on.
function overviewDuration(milliseconds) {
  const minutes = Math.floor(Math.max(0, milliseconds) / 60000);
  if (minutes < 1) return "less than a minute";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

// Describe the uptime: text, and the start time as tooltip when it is known. A server that does not run has
// no uptime, so the text is empty and no line is drawn (QF-057).
function overviewUptime(status, now = Date.now()) {
  if (!OVERVIEW_RUNNING_STATES.includes(status?.state)) return { text: "", title: "" };
  const started = status.state === "RUNNING_MANAGED" && status.started_at ? new Date(status.started_at) : null;
  if (!started || Number.isNaN(started.getTime())) return { text: "Uptime not known", title: "" };
  return { text: `Running for ${overviewDuration(now - started.getTime())}`,
    title: `Started ${started.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` };
}

// Write the uptime only when its text changed; without a running server there is no uptime element.
function refreshOverviewUptime() {
  const line = document.getElementById("overview-uptime");
  if (!line || !overviewState.status) return;
  const uptime = overviewUptime(overviewState.status);
  if (!uptime.text) return;
  if (line.textContent !== uptime.text) line.textContent = uptime.text;
  if (uptime.title) line.title = uptime.title; else line.removeAttribute("title");
}

// Decide for each lifecycle control whether it is on, and the reason when it is off.
function overviewControlStates(context) {
  const state = context.status.state;
  const stateReason = Object.hasOwn(overviewStateReasons, state)
    ? overviewStateReasons[state] : overviewBlockReasons.unconfirmed;
  const lock = window.ServerManServerState.lockReason();
  // The first blocker that applies gives the reason; an empty reason means that the control is on.
  const first = (...reasons) => reasons.find(Boolean) || "";
  const blocked = context.blocked ? overviewBlockReasons.recovery : "";
  const noProfile = context.profile ? "" : overviewBlockReasons.profile;
  const running = state === "RUNNING_MANAGED" ? "" : stateReason;
  return {
    start: first(blocked, context.configured ? "" : overviewBlockReasons.setup, noProfile,
      state === "STOPPED" ? "" : stateReason),
    stop: first(blocked, running, lock),
    restart: first(blocked, noProfile, running, lock),
  };
}

// Build one lifecycle button wired to the shared confirmation; a reason turns it off and explains why.
function overviewAction(label, action, reason) {
  const primary = action === "start" && !reason;
  const button = overviewNode("button", primary ? "button button-primary" : "button", label);
  // The identifier lets a redraw give the focus back to the same button.
  button.type = "button"; button.id = `overview-${action}`;
  button.addEventListener("click", () => confirmLifecycleAction(action));
  if (reason) {
    button.disabled = true;
    button.title = reason;
    // The notice above the strip says the same in full; the control refers to it when it is shown.
    if (document.getElementById("overview-running-notice")) {
      button.setAttribute("aria-describedby", "overview-running-notice");
    }
  }
  // A lifecycle action submits an operation, so it is locked while another one runs or waits.
  return window.ServerManBusy?.mark(button) || button;
}

// Return the process values: identifier, query port, and the note; an empty value is said as such.
function overviewProcessValues(status) {
  return [
    ["Process ID", status.process_id ? String(status.process_id) : "None"],
    ["Steam query port", status.query_port ? String(status.query_port) : "Not known"],
    ["Note", status.diagnostic_code
      ? window.ServerManDiagnosticLabels.process(status.diagnostic_code) : "No problem reported."],
  ];
}

// Build the facts of a running process in one line: uptime, process identifier, and query port.
function overviewProcessFacts(status) {
  const facts = overviewNode("p", "overview-meta overview-facts");
  const uptime = overviewUptime(status);
  const line = overviewNode("span", "", uptime.text); line.id = "overview-uptime";
  if (uptime.title) line.title = uptime.title;
  const [pid, port] = overviewProcessValues(status);
  facts.append(line, overviewNode("span", "", `${pid[0]} ${pid[1]}`), overviewNode("span", "", `${port[0]} ${port[1]}`));
  return facts;
}

// Build the "Process details" toggle and its closed list; the list keeps the empty values reachable (D17).
function overviewProcessDisclosure(status) {
  const toggle = overviewNode("button", "link-button overview-process-toggle", "Process details");
  toggle.type = "button"; toggle.id = "overview-process-toggle";
  toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", "overview-process");
  const list = overviewNode("dl", "detail-list overview-process"); list.id = "overview-process"; list.hidden = true;
  overviewProcessValues(status).forEach(([term, description]) => {
    list.append(overviewNode("dt", "", term), overviewNode("dd", "", description));
  });
  toggle.addEventListener("click", () => setOverviewProcessOpen(list.hidden));
  return { toggle, list };
}

// Open or close the process list; a redraw calls it to keep the operator's choice.
function setOverviewProcessOpen(open) {
  const list = document.getElementById("overview-process");
  const toggle = document.getElementById("overview-process-toggle");
  if (!list || !toggle) return;
  list.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
}

// Build the server strip for the state, the selected profile, and the blockers of the page.
function renderOverviewServer(context) {
  const status = context.status;
  const [stateLabel, stateClass, stateDetail] = window.ServerManOverviewReadiness.presentation(status);
  const panel = overviewNode("section", "panel overview-panel"); panel.id = "overview-server";
  panel.setAttribute("aria-labelledby", "overview-server-title");
  const copy = overviewNode("div", "overview-server-state");
  // The heading takes the focus when the control that held it is off after a redraw.
  const title = overviewNode("h2", "overview-eyebrow", "Server"); title.id = "overview-server-title"; title.tabIndex = -1;
  const row = overviewNode("div", "overview-state-row");
  row.append(title, overviewNode("strong", `status-label overview-state ${stateClass}`, stateLabel));
  // The player count follows the state while the server runs; its names panel is the last part of the strip (D18).
  const players = window.ServerManOverviewPlayers.strip(status);
  row.append(...players.row);
  // A problem of the process is always visible, in warning colour, beside the detail sentence.
  const metas = overviewNode("div", "overview-metas");
  metas.append(overviewNode("p", "overview-meta overview-state-detail", stateDetail));
  if (status.diagnostic_code) {
    metas.append(overviewNode("p", "overview-meta overview-process-note",
      window.ServerManDiagnosticLabels.process(status.diagnostic_code)));
  }
  copy.append(row, metas);
  // A running process has its facts written out; otherwise the empty values sit behind "Process details".
  if (OVERVIEW_RUNNING_STATES.includes(status.state)) copy.append(overviewProcessFacts(status));
  else {
    const disclosure = overviewProcessDisclosure(status);
    row.append(disclosure.toggle);
    copy.append(disclosure.list);
  }
  // All four controls stay visible in every state; a control that is off says why.
  const controls = overviewNode("div", "overview-controls");
  const reasons = overviewControlStates(context);
  const buttons = overviewNode("div", "action-row overview-buttons");
  buttons.append(overviewAction("Start server", "start", reasons.start),
    overviewNode("span", "overview-button-gap"),
    overviewAction("Save & Stop", "stop", reasons.stop), overviewAction("Save & Restart", "restart", reasons.restart));
  buttons.querySelector(".overview-button-gap").setAttribute("aria-hidden", "true");
  // "Backup after stop" stands above the buttons it affects; the focus order follows the reading order.
  controls.append(window.ServerManOverviewBackup.choice(context.profile), buttons);
  panel.append(copy, controls);
  if (players.panel) panel.append(players.panel);
  return panel;
}

// The uptime follows the clock, so it is recomputed on each poll tick of the shell.
document.addEventListener("serverman:poll-tick", refreshOverviewUptime);

// Publish the server strip for the Overview page.
window.ServerManOverviewServer = Object.freeze({
  render: renderOverviewServer,
  uptime: overviewUptime,
  refreshUptime: refreshOverviewUptime,
  setProcessOpen: setOverviewProcessOpen,
});
