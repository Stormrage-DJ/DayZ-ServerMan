// Keeps the Overview lifecycle state aligned with the live DayZ process.
"use strict";

// Prevent overlapping status reads while the shared event poll is active.
const overviewStatusRefresh = { loading: false };

// Refresh the visible process state and redraw only when it changed.
async function refreshOverviewStatus() {
  if (overviewStatusRefresh.loading || shellState.section !== "overview"
      || !overviewState.snapshot || !overviewState.status) return;
  overviewStatusRefresh.loading = true;
  const workspace = window.ServerManWorkspace.capture("overview");
  const generation = overviewState.generation;
  try {
    const result = await window.pywebview.api.get_server_status();
    // Ignore a response after navigation or a full Overview reload.
    if (!window.ServerManWorkspace.isActive(workspace)
        || generation !== overviewState.generation) return;
    if (!result.success) return window.ServerManUi.setHostStatus(
      "Server status could not be refreshed", "is-warning", "lifecycle",
    );
    window.ServerManUi.clearHostStatus("lifecycle");
    const current = overviewState.status;
    const changed = current.state !== result.value.state
      || current.process_id !== result.value.process_id
      || current.diagnostic_code !== result.value.diagnostic_code
      || current.readiness !== result.value.readiness
      || current.query_port !== result.value.query_port;
    if (changed) { overviewState.status = result.value; renderOverview(); }
  } catch (_error) {
    if (window.ServerManWorkspace.isActive(workspace)) {
      window.ServerManUi.setHostStatus(
        "Server status could not be refreshed", "is-warning", "lifecycle",
      );
    }
  } finally {
    overviewStatusRefresh.loading = false;
  }
}

// Publish the narrow refresh surface used by the application event loop.
window.ServerManOverviewStatus = Object.freeze({ refresh: refreshOverviewStatus });
