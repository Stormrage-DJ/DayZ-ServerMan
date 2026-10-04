// Overview "Server" panel: the state once, the uptime, the process details, and the four lifecycle controls.
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

// Word a duration: under a minute, minutes, hours and minutes, and days and hours from 48 hours on.
function overviewDuration(milliseconds) {
  const minutes = Math.floor(Math.max(0, milliseconds) / 60000);
  if (minutes < 1) return "less than a minute";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

// Describe the uptime line: text, and the start time as tooltip when it is known.
function overviewUptime(status, now = Date.now()) {
  const running = ["RUNNING_MANAGED", "RUNNING_EXTERNAL"].includes(status?.state);
  if (!running) return { text: "Not running", title: "" };
  const started = status.state === "RUNNING_MANAGED" && status.started_at ? new Date(status.started_at) : null;
  if (!started || Number.isNaN(started.getTime())) return { text: "Uptime not known", title: "" };
  return { text: `Running for ${overviewDuration(now - started.getTime())}`,
    title: `Started ${started.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` };
}

// Write the uptime line only when its text changed.
function refreshOverviewUptime() {
  const line = document.getElementById("overview-uptime");
  if (!line || !overviewState.status) return;
  const uptime = overviewUptime(overviewState.status);
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
    // The notice above the panel says the same in full; the control refers to it when it is shown.
    if (document.getElementById("overview-running-notice")) {
      button.setAttribute("aria-describedby", "overview-running-notice");
    }
  }
  // A lifecycle action submits an operation, so it is locked while another one runs or waits.
  return window.ServerManBusy?.mark(button) || button;
}

// Build the closed "Process details" disclosure: process identifier, query port, and the diagnostic note.
function overviewProcessDetails(status) {
  const details = overviewNode("details", "overview-process"); details.id = "overview-process";
  const summary = overviewNode("summary", "", "Process details"); summary.id = "overview-process-summary";
  details.append(summary);
  const list = overviewNode("dl", "detail-list");
  [
    ["Process ID", status.process_id ? String(status.process_id) : "None"],
    ["Steam query port", status.query_port ? String(status.query_port) : "Not known"],
    ["Note", status.diagnostic_code
      ? window.ServerManDiagnosticLabels.process(status.diagnostic_code) : "No problem reported."],
  ].forEach(([term, description]) => {
    list.append(overviewNode("dt", "", term), overviewNode("dd", "", description));
  });
  details.append(list);
  return details;
}

// Build the "Server" panel for the state, the selected profile, and the blockers of the page.
function renderOverviewServer(context) {
  const [stateLabel, stateClass, stateDetail] = window.ServerManOverviewReadiness.presentation(context.status);
  const panel = overviewNode("section", "panel overview-panel"); panel.id = "overview-server";
  const copy = overviewNode("div", "overview-server-state");
  const uptime = overviewUptime(context.status);
  const uptimeLine = overviewNode("p", "overview-uptime", uptime.text); uptimeLine.id = "overview-uptime";
  if (uptime.title) uptimeLine.title = uptime.title;
  // The heading takes the focus when the control that held it is off after a redraw.
  const title = overviewNode("h2", "", "Server"); title.id = "overview-server-title"; title.tabIndex = -1;
  copy.append(title,
    overviewNode("strong", `status-label overview-state ${stateClass}`, stateLabel),
    overviewNode("p", "overview-state-detail", stateDetail), uptimeLine, overviewProcessDetails(context.status));
  // All four controls stay visible in every state; a control that is off says why.
  const controls = overviewNode("div", "overview-controls");
  const actions = overviewNode("div", "action-row");
  const reasons = overviewControlStates(context);
  const backupChoice = window.ServerManOverviewBackup.choice(context.profile);
  const start = overviewAction("Start server", "start", reasons.start);
  const stop = overviewAction("Save & Stop", "stop", reasons.stop);
  const restart = overviewAction("Save & Restart", "restart", reasons.restart);
  actions.append(backupChoice, start, stop, restart);
  controls.append(actions);
  panel.append(copy, controls);
  return panel;
}

// The uptime follows the clock, so it is recomputed on each poll tick of the shell.
document.addEventListener("serverman:poll-tick", refreshOverviewUptime);

// Publish the server panel for the Overview page.
window.ServerManOverviewServer = Object.freeze({
  render: renderOverviewServer,
  uptime: overviewUptime,
  refreshUptime: refreshOverviewUptime,
});
