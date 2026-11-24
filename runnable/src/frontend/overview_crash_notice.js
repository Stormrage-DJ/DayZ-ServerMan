// Overview crash notice: the plain text about an automatic restart of the server, when it shows, and the
// cancel of a pending restart. The notice reads the one status reply; it opens no popup and plays no sound.
"use strict";

const OVERVIEW_CRASH_PENDING = "The server stopped unexpectedly. DayZ-ServerMan will start it again in about 10 seconds. Press Save & Stop to cancel.";
const OVERVIEW_CRASH_RESTARTED = "The server stopped unexpectedly and DayZ-ServerMan started it again.";
// One text for each reason with which the manager gives up.
const OVERVIEW_GIVE_UP_TEXTS = Object.freeze({
  guard_reached: "The server stopped again after 3 automatic restarts in 10 minutes. DayZ-ServerMan leaves it stopped.",
  ended_before_ready: "The restarted server stopped before it was ready. DayZ-ServerMan does not try again.",
  start_failed: "DayZ-ServerMan could not start the server again.",
});
// The text for a reason that this table does not hold.
const OVERVIEW_GIVE_UP_FALLBACK = "DayZ-ServerMan did not start the server again after it stopped unexpectedly.";

// Return the text of the visible notice, or null. Pending and gave_up show while the server is stopped;
// restarted shows while it runs and its start time is not later than the time of the notice.
function overviewCrashNoticeText(status) {
  const crash = status?.auto_restart;
  if (!crash) return null;
  const stopped = status.state === "STOPPED";
  if (crash.kind === "pending") return stopped ? OVERVIEW_CRASH_PENDING : null;
  if (crash.kind === "gave_up") {
    if (!stopped) return null;
    return Object.hasOwn(OVERVIEW_GIVE_UP_TEXTS, crash.reason)
      ? OVERVIEW_GIVE_UP_TEXTS[crash.reason] : OVERVIEW_GIVE_UP_FALLBACK;
  }
  // A missing or invalid time gives NaN, and the comparison is then false.
  if (crash.kind === "restarted") {
    return !stopped && Date.parse(status.started_at) <= Date.parse(crash.at) ? OVERVIEW_CRASH_RESTARTED : null;
  }
  return null;
}

// Report whether the pending notice is visible; the Stop button then cancels the restart.
function overviewCrashPending(status) {
  return overviewCrashNoticeText(status) === OVERVIEW_CRASH_PENDING;
}

// Build the notice line for the strip, or null when no notice shows; a pending or given-up notice is a warning.
function overviewCrashNotice(status) {
  const text = overviewCrashNoticeText(status);
  if (text === null) return null;
  const warning = status.auto_restart.kind !== "restarted";
  const line = overviewNode("p", `overview-meta overview-crash-notice${warning ? " overview-process-note" : ""}`, text);
  line.id = "overview-crash-notice";
  return line;
}

// Cancel a pending restart, then read the status again; a failed answer goes to the host error line.
async function cancelOverviewCrashRestart() {
  const result = await window.pywebview.api.cancel_crash_restart();
  if (!result || !result.success) return window.ServerManUi.renderHostError(result);
  return window.ServerManServerState.read();
}

// Publish the notice for the server strip and the lifecycle actions.
window.ServerManOverviewCrashNotice = Object.freeze({
  text: overviewCrashNoticeText, pending: overviewCrashPending, notice: overviewCrashNotice,
  cancel: cancelOverviewCrashRestart,
});
