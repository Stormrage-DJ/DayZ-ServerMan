// Presents process ownership and Steam-query readiness as one honest status.
"use strict";

// Map one lifecycle snapshot into its visible label, style, and explanation.
function overviewStatusPresentation(status) {
  // Readiness refines only a manager-owned running process.
  if (status?.state === "RUNNING_MANAGED") {
    const readiness = {
      STARTING: ["Starting", "status-busy", "DayZ is running and preparing its Steam endpoint."],
      READY: ["Ready", "status-normal", "DayZ is running and answering Steam server queries."],
      UNRESPONSIVE: [
        "Not responding", "status-warning",
        "The DayZ process is running, but its Steam endpoint is not responding.",
      ],
    };
    return readiness[status.readiness]
      || ["Running", "status-busy", "DayZ is running; application readiness is not available."];
  }
  const values = {
    STOPPED: ["Stopped", "status-neutral", "The configured server process is not running."],
    RUNNING_EXTERNAL: [
      "Running outside the manager", "status-warning", "DayZ is running, but this manager does not own it.",
    ],
    STARTING: ["Starting", "status-busy", "The manager is starting DayZ."],
    STOPPING: ["Stopping", "status-busy", "DayZ is saving and closing."],
    AMBIGUOUS: ["Several servers found", "status-error", "More than one matching DayZ process was found."],
    UNKNOWN: ["State unknown", "status-error", "The DayZ process state could not be proven."],
  };
  // Fall back explicitly when the host returns an unknown state.
  return values[status?.state]
    || ["State unknown", "status-error", "No authoritative state is available."];
}

// Describe the managed-process evidence without confusing it with readiness.
function overviewProcessDetail(status) {
  if (status?.diagnostic_code) return window.ServerManDiagnosticLabels.process(status.diagnostic_code);
  if (status?.query_port) return `Steam query port ${status.query_port}`;
  return "No process diagnostic is active.";
}

// Publish the narrow presentation contract used by Overview.
window.ServerManOverviewReadiness = Object.freeze({
  presentation: overviewStatusPresentation,
  processDetail: overviewProcessDetail,
});
