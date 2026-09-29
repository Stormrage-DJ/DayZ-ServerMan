// Logs workspace: fixed-source log viewing with viewport and scroll preservation.
"use strict";

// Tracks the selected log source, load generation, and busy flag.
const logState = { generation: 0, source: "manager", loading: false };

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
  const copy = logNode("div");
  copy.append(logNode("span", "section-label", "Local diagnostics"));
  copy.append(logNode("h2", "", value.source === "manager" ? "Manager activity" : "DayZ server output"));
  const controls = logNode("div", "log-controls");
  const selector = document.createElement("select");
  selector.setAttribute("aria-label", "Log source");
  // Offer the manager log and the DayZ server log.
  [["manager", "Manager log"], ["server", "DayZ server log"]].forEach(([source, label]) => {
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
  logState.loading = true;
  const viewport = preserveViewport ? captureLogViewport() : null;
  const generation = ++logState.generation;
  const workspace = window.ServerManWorkspace.capture("logs");
  if (showLoading) window.ServerManUi.renderLoading("Loading logs", "Reading manager-owned diagnostics.");
  try {
    // Read a bounded window of entries from the selected source.
    const result = await window.pywebview.api.read_log(logState.source, 300);
    // Ignore responses from a replaced generation or an inactive workspace.
    if (generation !== logState.generation
      || !window.ServerManWorkspace.isActive(workspace)) return;
    if (!result.success) return window.ServerManUi.renderHostError(result);
    renderLogWorkspace(result.value, viewport);
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) window.ServerManUi.renderHostError(null);
  } finally {
    logState.loading = false;
  }
}

// Publish the log workspace controls used by the shell.
window.ServerManLogs = Object.freeze({ open: () => refreshLogs(true, false), refresh: refreshLogs });
