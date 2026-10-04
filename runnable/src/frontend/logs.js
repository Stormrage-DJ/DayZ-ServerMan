// Logs workspace: fixed-source log viewing with viewport and scroll preservation.
"use strict";

// Tracks source revisions so background refreshes avoid replacing unchanged content.
const logState = {
  generation: 0,
  source: "manager",
  loading: false,
  revisions: new Map(),
  lastAutomaticAt: 0,
};

const LOG_SOURCES = Object.freeze({
  manager: Object.freeze({
    label: "Manager activity",
    heading: "Manager activity",
    description: "Completed operations, warnings, errors, and scheduled actions.",
  }),
  manager_diagnostics: Object.freeze({
    label: "Manager diagnostics",
    heading: "Manager diagnostics",
    description: "Raw structured records for troubleshooting.",
  }),
  server: Object.freeze({
    label: "DayZ server output",
    heading: "DayZ server output",
    description: "Console output captured from the selected DayZ server process.",
  }),
});

// Create one interface element through the shared element helper.
function logNode(tag, className = "", text = "") {
  return window.ServerManUi.element(tag, className, text);
}

// Record the log scroll position and end-following state before a refresh.
function captureLogViewport() {
  const output = document.querySelector(".log-output");
  if (!output) return null;
  return Object.freeze({
    focused: document.activeElement === output,
    followsEnd: output.scrollHeight - output.clientHeight - output.scrollTop <= 2,
    scrollTop: output.scrollTop,
  });
}

// Restore focus and scroll position after a refresh, keeping end-following.
function restoreLogViewport(output, viewport) {
  if (!output || !viewport) return;
  if (viewport.focused) output.focus({ preventScroll: true });
  const maximum = Math.max(0, output.scrollHeight - output.clientHeight);
  output.scrollTop = viewport.followsEnd ? maximum : Math.min(viewport.scrollTop, maximum);
}

// Render the log panel for the selected source, its lines, and any truncation note.
function renderLogWorkspace(value, viewport = null) {
  const region = document.getElementById("content-region");
  const panel = logNode("section", "panel logs-panel");
  const heading = logNode("div", "panel-heading");
  const copy = logNode("div", "log-heading-copy");
  const sourceDetails = LOG_SOURCES[value.source] || LOG_SOURCES.manager;
  copy.append(logNode("h2", "", sourceDetails.heading));
  copy.append(logNode("p", "log-description", sourceDetails.description));
  const controls = logNode("div", "log-controls");
  const selector = document.createElement("select");
  selector.setAttribute("aria-label", "Log source");
  // Keep operator activity separate from opt-in technical diagnostics.
  Object.entries(LOG_SOURCES).forEach(([source, details]) => {
    const label = details.label;
    const option = logNode("option", "", label);
    option.value = source; option.selected = source === logState.source; selector.append(option);
  });
  selector.addEventListener("change", () => { logState.source = selector.value; refreshLogs(true, false); });
  const refresh = logNode("button", "button", "Refresh");
  refresh.type = "button"; refresh.addEventListener("click", () => refreshLogs(true));
  controls.append(selector, refresh); heading.append(copy, controls); panel.append(heading);
  let output = null;
  // Show an empty state until the selected source has log lines.
  if (!value.lines.length) {
    panel.append(logNode("p", "empty-log", "No log entries are available yet."));
  } else {
    output = logNode("pre", "log-output", value.lines.join("\n"));
    output.tabIndex = 0;
    output.setAttribute("aria-label", "Log output");
    panel.append(output);
  }
  // Note when only the newest bounded portion was returned.
  if (value.truncated) {
    panel.append(logNode("p", "log-note", "Showing the newest bounded portion of this log."));
  }
  // Swap in the panel and restore the previous viewport.
  region.replaceChildren(panel);
  restoreLogViewport(output, viewport);
  region.setAttribute("aria-busy", "false");
}

// Load the selected log source and render it without losing the viewport.
async function refreshLogs(showLoading = false, preserveViewport = true) {
  // Keep one load in flight and refresh only while the logs section is open.
  if (logState.loading || shellState.section !== "logs") return;
  const now = Date.now();
  if (!showLoading && now - logState.lastAutomaticAt < 5000) return;
  if (!showLoading) logState.lastAutomaticAt = now;
  logState.loading = true;
  const viewport = preserveViewport ? captureLogViewport() : null;
  const generation = ++logState.generation;
  const source = logState.source;
  const workspace = window.ServerManWorkspace.capture("logs");
  if (showLoading) window.ServerManUi.renderLoading("Loading logs", "Reading manager-owned diagnostics.");
  try {
    // Read a bounded window of entries from the selected source.
    const result = await window.pywebview.api.read_log(source, 300);
    // Ignore responses from a replaced generation or an inactive workspace.
    if (generation !== logState.generation
      || !window.ServerManWorkspace.isActive(workspace)) return;
    if (!result.success) return window.ServerManUi.renderHostError(result);
    const previousRevision = logState.revisions.get(source);
    logState.revisions.set(source, result.value.revision);
    if (!showLoading && previousRevision === result.value.revision) return;
    renderLogWorkspace(result.value, viewport);
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) window.ServerManUi.renderHostError(null);
  } finally {
    logState.loading = false;
  }
}

// Show one log source: reload it when Logs is visible, else switch to Logs through the unsaved-change guard.
function showLogSource(source) {
  logState.source = source;
  if (shellState.section === "logs") refreshLogs(true, false);
  else setSection("logs");
}

// Publish the log workspace controls used by the shell.
window.ServerManLogs = Object.freeze({
  open: () => refreshLogs(true, false), refresh: refreshLogs, showSource: showLogSource,
});
