// Keeps the Overview lifecycle state aligned with the shared server state.
"use strict";

// Redraw the notice and the server panel of the visible Overview when the shared server state changed.
function followOverviewStatus(event) {
  const status = event.detail?.status;
  // A failed read keeps the page as it is; the application status names the problem.
  if (!status || event.detail.confirmed === false || shellState.section !== "overview"
      || !overviewState.snapshot || !overviewState.status) return;
  // A page that is loading draws the fresh state itself.
  if (!window.ServerManWorkspace.isActive(window.ServerManWorkspace.capture("overview"))) return;
  overviewState.status = status;
  renderOverviewTop();
}

// The shell reads the status on every poll tick; this page only listens to the change event.
document.addEventListener("serverman:server-status", followOverviewStatus);

// Publish the narrow surface that reads the state at once, for callers that cannot wait for the next tick.
window.ServerManOverviewStatus = Object.freeze({ refresh: () => window.ServerManServerState.refresh() });
