// Shell UI helpers: application status, loading, errors, and operations.
"use strict";

// Navigation titles and one-line descriptions per workspace section.
const sectionCopy = {
  overview: ["Overview", "Server state, lifecycle controls, and the next safe operator action."],
  profiles: ["Profiles", "Create and maintain complete DayZ server launch profiles."],
  configuration: ["Configuration", "Edit the selected server's core configuration with guided controls."],
  tweaks: ["Tweaks", "Fine-tune the selected server with compact, map-aware controls."],
  mods: ["Mods", "Download, update, and apply mods for the selected server."],
  backups: ["Backups", "Create and restore verified server backups."],
  logs: ["Logs", "Inspect manager activity and captured DayZ server output."],
  settings: ["Settings", "Configure portable application paths and SteamCMD authentication."],
};

// Live status messages keyed by their source.
const applicationStatuses = new Map([
  ["host", { text: "Connecting to application host", kind: "is-busy" }],
]);
// Severity ordering used to pick the most important status.
const statusPriority = { "": 0, "is-busy": 1, "is-warning": 2, "is-recovery": 3, "is-error": 4 };

// Build one element node with optional class and text.
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Aggregate source statuses into one header status.
function renderApplicationStatus() {
  const statuses = [...applicationStatuses.values()];
  // Pick the highest-severity message among the sources.
  const overall = statuses.reduce((selected, item) =>
    statusPriority[item.kind] > statusPriority[selected.kind] ? item : selected,
  { text: "Ready", kind: "" });
  const healthy = statuses.every((item) => item.kind === "");
  // Show the ready state when every source is clear.
  const text = healthy ? "Ready" : overall.text;
  const region = document.getElementById("application-status");
  // Publish the text, dot styling, and accessible label.
  document.getElementById("application-status-text").textContent = text;
  region.querySelector(".status-dot").className = `status-dot ${healthy ? "" : overall.kind}`.trim();
  region.setAttribute("aria-label", `Application status: ${text}`);
}

// Record one source status and refresh the header.
function setHostStatus(text, kind = "", source = "application") {
  applicationStatuses.set(source, { text, kind });
  renderApplicationStatus();
}

// Drop one source status and refresh the header.
function clearHostStatus(source) {
  applicationStatuses.delete(source);
  renderApplicationStatus();
}

// Mirror operation state into the application status sources.
function syncOperationStatus(operation) {
  const state = typeof operation?.state === "string" ? operation.state : "UNKNOWN";
  // Show busy progress while the operation is active.
  if (["QUEUED", "RUNNING", "CANCELLING"].includes(state)) {
    setHostStatus(`${operation.progress_phase || "Operation"} — ${operation.progress_percent || 0}%`, "is-busy", "operation");
  } else if (["FAILED", "RECOVERY_REQUIRED"].includes(state)) {
    // Show the failure message as an error.
    const message = operation.error?.message || operation.terminal_error?.message || "Operation failed";
    setHostStatus(message, "is-error", "operation");
  } else {
    // Clear the operation status once it settles.
    clearHostStatus("operation");
  }
}

// Render the loading skeleton for a workspace.
function renderLoading(title, message) {
  const region = document.getElementById("content-region");
  const panel = element("section", "panel skeleton-panel");
  // Show the workspace title and loading message.
  const heading = element("div", "panel-heading");
  heading.append(element("h2", "", title));
  panel.append(heading, element("p", "", message));
  // Show placeholder cards while data loads.
  const grid = element("div", "skeleton-grid");
  grid.setAttribute("aria-hidden", "true");
  grid.append(
    element("div", "skeleton skeleton-card"),
    element("div", "skeleton skeleton-card"),
    element("div", "skeleton skeleton-card"),
  );
  panel.append(grid);
  // Mark the content region busy while loading.
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", "true");
}

// Render a clear host failure and mark the application status.
function renderHostError(result) {
  const error = result && result.error ? result.error : {};
  // Fall back to a generic message when the host sent none.
  const message = typeof error.message === "string" ? error.message : "The application host is unavailable.";
  setHostStatus("Application host unavailable", "is-error", "host");
  const region = document.getElementById("content-region");
  // Show the error as an alert panel.
  const panel = element("section", "panel notice notice-error");
  panel.setAttribute("role", "alert");
  panel.append(element("h2", "", "Could not load the workspace"));
  panel.append(element("p", "", message));
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", "false");
}

// Render the empty or unavailable state for a workspace.
function renderState(section, state) {
  const region = document.getElementById("content-region");
  const panel = element("section", "panel");
  // Explain the empty workspace and what to create.
  if (state === "empty") {
    panel.append(element("h2", "", `No ${sectionCopy[section][0].toLowerCase()} available`));
    panel.append(element("p", "", "Create or configure the required data to continue."));
  } else {
    // Offer reload guidance for the unavailable workspace.
    panel.append(element("h2", "", "Workspace unavailable"));
    panel.append(element("p", "", "Reload the workspace or review the latest operation result."));
  }
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", "false");
}

// Render the progress panel for one operation.
function renderOperation(operation) {
  syncOperationStatus(operation);
  const state = typeof operation.state === "string" ? operation.state : "UNKNOWN";
  const phase = typeof operation.progress_phase === "string" ? operation.progress_phase : "unknown";
  // Normalize the reported state and progress values.
  const rawPercent = Number(operation.progress_percent);
  const percent = Number.isFinite(rawPercent) ? Math.max(0, Math.min(100, rawPercent)) : 0;
  const region = document.getElementById("content-region");
  const panel = element("section", "panel operation-panel");
  // Show the operation title and its state label.
  const heading = element("div", "panel-heading");
  heading.append(
    element("h2", "", "Manager operation"),
    element("span", `status-label ${state === "FAILED" ? "status-error" : "status-busy"}`, state),
  );
  panel.append(heading);
  // Expose progress values to assistive technology.
  const progress = element("div", "progress-track");
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Operation progress");
  progress.setAttribute("aria-valuenow", String(percent));
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", "100");
  const bar = element("div", "progress-bar");
  bar.style.width = `${percent}%`;
  progress.append(bar);
  panel.append(progress, element("p", "", `${phase} — ${percent}%`));
  // Show the error message when the host provided one.
  if (operation.error && typeof operation.error.message === "string") {
    panel.append(element("p", "operation-error", operation.error.message));
  }
  // Offer cancellation only when the operation allows it.
  if (operation.cancellable === true) {
    const cancel = element("button", "button", "Cancel safely");
    cancel.type = "button";
    cancel.dataset.cancelOperation = String(operation.operation_id || "");
    panel.append(cancel);
  }
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", state === "RUNNING" || state === "CANCELLING" ? "true" : "false");
}

// Render the shutdown progress panel.
function renderShutdown(snapshot) {
  const state = snapshot && typeof snapshot.state === "string" ? snapshot.state : "DRAINING";
  // Report whether the window can close safely.
  setHostStatus(state === "CLOSED" ? "Safe to close" : "Waiting for safe shutdown",
    state === "CLOSED" ? "" : "is-busy", "shutdown");
  const region = document.getElementById("content-region");
  const panel = element("section", "panel notice notice-warning");
  panel.setAttribute("role", "status");
  panel.append(element("h2", "", "Closing DayZ-ServerMan safely"));
  // Explain why closing is blocked or that closing is safe.
  let message = "Queued work is cancelled. Active work will finish or stop at a declared safe point.";
  if (snapshot && snapshot.blocking_reason === "MANAGED_SERVER_ACTIVE") {
    message = "A managed server is still active. Closing stays blocked until its stopped state is proven.";
  } else if (state === "CLOSED") {
    message = "Manager records are flushed. The window can close.";
  }
  panel.append(element("p", "", message));
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", state === "CLOSED" ? "false" : "true");
}

// Publish the shell UI helpers used by every workspace.
window.ServerManUi = Object.freeze({
  element,
  sectionCopy,
  renderHostError,
  renderLoading,
  renderOperation,
  renderShutdown,
  render: renderState,
  clearHostStatus,
  setHostStatus,
  syncOperationStatus,
});
