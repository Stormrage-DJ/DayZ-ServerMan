// Shell UI helpers: application status, loading, host errors, and the shutdown panel.
"use strict";

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
  // Repeat a status that needs attention on the menu button, because a narrow window hides the sidebar.
  const dot = document.getElementById("menu-status-dot");
  if (dot) {
    dot.className = `status-dot ${overall.kind}`.trim();
    dot.hidden = healthy;
    document.getElementById("menu-button").setAttribute("aria-label",
      healthy ? "Open navigation" : `Open navigation. Application status: ${text}`);
  }
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
  // Word the host error for the operator; fall back to a generic message when the host sent none.
  const message = window.ServerManOperationMessages.bridgeError(result, "The application host is unavailable.");
  setHostStatus("Application host unavailable", "is-error", "host");
  // The state of the running operation is no longer known, so the bar hides its active row.
  window.ServerManOperationBar?.hostError();
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
    panel.append(element("h2", "", `No ${window.ServerManSections.get(section).title.toLowerCase()} available`));
    panel.append(element("p", "", "Create or configure the required data to continue."));
  } else {
    // Offer reload guidance for the unavailable workspace.
    panel.append(element("h2", "", "Workspace unavailable"));
    panel.append(element("p", "", "Reload the workspace or review the latest operation result."));
  }
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", "false");
}

// Render the shutdown progress panel.
function renderShutdown(snapshot) {
  const state = snapshot && typeof snapshot.state === "string" ? snapshot.state : "DRAINING";
  // Report whether the window can close safely.
  setHostStatus(state === "CLOSED" ? "Safe to close" : "Waiting for safe shutdown",
    state === "CLOSED" ? "" : "is-busy", "shutdown");
  // The bar keeps showing the operation that is finishing, without Cancel and without the queue.
  window.ServerManOperationBar?.shutdown();
  const region = document.getElementById("content-region");
  const panel = element("section", "panel notice notice-warning");
  panel.setAttribute("role", "status");
  panel.append(element("h2", "", "Closing DayZ-ServerMan safely"));
  // Explain why closing is blocked or that closing is safe.
  let message = "Waiting work is cancelled. Work in progress finishes or stops at a safe moment.";
  if (snapshot && snapshot.blocking_reason === "MANAGED_SERVER_ACTIVE") {
    message = "A managed server is still active. Closing stays blocked until its stopped state is proven.";
  } else if (state === "CLOSED") {
    message = "Everything is saved. The window can close.";
  }
  panel.append(element("p", "", message));
  region.replaceChildren(panel);
  region.setAttribute("aria-busy", state === "CLOSED" ? "false" : "true");
}

// Publish the shell UI helpers used by every workspace.
window.ServerManUi = Object.freeze({
  element,
  renderHostError,
  renderLoading,
  renderShutdown,
  render: renderState,
  clearHostStatus,
  setHostStatus,
});
