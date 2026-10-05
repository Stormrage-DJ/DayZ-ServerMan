// Server build status: the operator wording of the DayZ server build check and its row of the Overview
// status board. No raw state, reason, class or code is printed.
"use strict";

// Why the newest build could not be read, per failure code of the check run.
const serverBuildFailureReasons = Object.freeze({
  STEAMCMD_UNAVAILABLE: "SteamCMD could not be used. Check its folder in Settings",
  TIMEOUT: "SteamCMD did not answer in time",
  STEAMCMD_FAILED: "SteamCMD reported an error",
  NOT_CONNECTED: "SteamCMD could not connect to Steam",
  OUTPUT_UNREADABLE: "the answer from Steam could not be read",
  STEAMCMD_EXIT_UNPROVEN: "SteamCMD did not close after the check. Close SteamCMD, then restart DayZ-ServerMan",
});
// Why the installed build is not known.
const serverBuildUnknownReasons = Object.freeze({
  NO_DAYZ_FOLDER: "no DayZ server folder is set in Settings",
  NO_MANIFEST: "Steam has no installation record for this folder",
  MANIFEST_AMBIGUOUS: "two Steam installation records were found for this folder",
  MANIFEST_UNREADABLE: "the Steam installation record could not be read",
  NOT_INSTALLED: "Steam does not report the server as fully installed",
});
// What Steam reports for an update that has not finished.
const serverBuildPendingTexts = Object.freeze({
  UPDATE_REQUIRED: "Steam has a DayZ server update queued.",
  UPDATE_RUNNING: "Steam is updating the DayZ server.",
  FILES_DAMAGED: "Steam reports missing or damaged DayZ server files.",
  BRANCH_CHANGE: "Steam has a change of the DayZ server branch queued.",
});
// Guidance per installation class: for an available update, then for a pending one (D2).
const serverBuildGuidance = Object.freeze({
  STEAM_CLIENT: ["This DayZ server folder belongs to your Steam library. Stop the server, then update “DayZ Server” through Steam.",
    "Let Steam finish the update. Start the server after it has finished."],
  STEAMCMD: ["Stop the server, then update it with SteamCMD. DayZ-ServerMan does not update the server build yet.",
    "Finish the update with SteamCMD. Start the server after it has finished."],
  UNKNOWN: ["Stop the server, then update it with the program that installed it, Steam or SteamCMD.",
    "Finish the update with the program that installed the server. Start the server after it has finished."],
});
const SERVER_BUILD_CHECKING_TEXT = "Checking the DayZ server build…";
const SERVER_BUILD_WAITING_TEXT = "The server build check waits until the current task has finished.";
// An unproven SteamCMD exit stopped every check of this session (QF-050).
const SERVER_BUILD_PAUSED_TEXT = "Server build checks are paused: an earlier SteamCMD run did not end cleanly. "
  + "Restart DayZ-ServerMan to check again.";

// Explain why the check has no current answer.
function serverBuildCheckReason(build) {
  const automatic = window.ServerManUpdateStatus.automaticChecks();
  if (build.reason === "NEVER") return automatic ? "not checked yet" : "automatic checks are off";
  if (build.reason === "STALE") return automatic ? "the last check is too old" : "automatic checks are off";
  if (build.reason === "BRANCH_NOT_LISTED") {
    return `Steam shows the branch “${build.installed_branch}” only after a sign-in`;
  }
  if (build.reason === "STEAMCMD_NOT_CONFIGURED") return "SteamCMD is not set up. Set its folder in Settings";
  return serverBuildFailureReasons[build.error_code] || "the check failed";
}

// Return the leading sentence and its tone.
function serverBuildSummary(build) {
  if (build.state === "CURRENT") return { text: "DayZ server is up to date.", tone: "normal" };
  if (build.state === "UPDATE_AVAILABLE") {
    const newer = Number(build.available_build) > Number(build.installed_build);
    return { tone: "warning", text: newer ? `DayZ server update available: build ${build.available_build}.`
      : `Steam lists a different DayZ server build: ${build.available_build}.` };
  }
  if (build.state === "UPDATE_PENDING") {
    const text = build.reason === "TARGET_BUILD" && build.target_build !== null
      ? `Steam has a DayZ server update queued: build ${build.target_build}.`
      : serverBuildPendingTexts[build.reason] || "Steam has a DayZ server update queued.";
    return { text, tone: "warning" };
  }
  if (build.state === "UNKNOWN_INSTALLATION") {
    const reason = serverBuildUnknownReasons[build.reason] || "the Steam installation record could not be read";
    return { text: `The installed DayZ server build is not known: ${reason}.`, tone: "neutral" };
  }
  // The row shows a short head and the reason under it in normal weight.
  const reason = serverBuildCheckReason(build);
  return { text: `Could not check the DayZ server build: ${reason}.`, head: "Could not check",
    reason: `${reason[0].toUpperCase()}${reason.slice(1)}.`, tone: build.check_state === "FAILED" ? "error" : "neutral" };
}

// Return the detail line: installed build, Steam build with its time, and the last check.
function serverBuildDetail(build) {
  const parts = [];
  if (build.installed_build !== null && build.installed_build !== undefined) {
    const branch = build.installed_branch && build.installed_branch !== "public" ? ` (branch ${build.installed_branch})` : "";
    parts.push(`Installed: build ${build.installed_build}${branch}`);
  }
  if (build.available_build !== null && build.available_build !== undefined) {
    const since = window.ServerManUpdateStatus.formatTime(build.available_time);
    parts.push(`Steam: build ${build.available_build}${since ? `, on Steam since ${since.text}` : ""}`);
  }
  const checked = window.ServerManUpdateStatus.formatTime(build.last_success_at);
  parts.push(checked ? `Last checked ${checked.text}` : "Not checked yet");
  return parts.join(" · ");
}

// Return every line of the server part: summary with tone, detail, guidance and the busy line.
function serverBuildLines(build) {
  const guidance = serverBuildGuidance[build.ownership] || serverBuildGuidance.UNKNOWN;
  const advice = build.state === "UPDATE_AVAILABLE" ? guidance[0] : build.state === "UPDATE_PENDING" ? guidance[1] : "";
  // A check that saw the unproven exit itself already names it in line 1
  const paused = build.paused === true && build.error_code !== "STEAMCMD_EXIT_UNPROVEN";
  const busy = paused ? SERVER_BUILD_PAUSED_TEXT : build.checking ? SERVER_BUILD_CHECKING_TEXT
    : build.waiting ? SERVER_BUILD_WAITING_TEXT : "";
  return Object.freeze({ ...serverBuildSummary(build), detail: serverBuildDetail(build), advice, busy });
}

// Build the "DayZ server" row of the status board; the board fills it on every update-state change.
function renderServerBuildPart() {
  const { row, body } = window.ServerManOverviewCards.row("overview-build", "server", "DayZ server");
  row.hidden = true;
  // Guidance stands before the build numbers; every line after the head wraps beside the others.
  const metas = overviewNode("div", "overview-metas");
  metas.append(overviewNode("p", "overview-meta overview-reason overview-build-reason"),
    overviewNode("p", "overview-meta overview-reason overview-build-advice"),
    overviewNode("p", "overview-meta overview-build-busy"), overviewNode("p", "overview-meta overview-build-detail"));
  body.append(overviewNode("p", "status-label overview-status overview-build-summary"), metas);
  return row;
}

// Write the server build state into its row; an answer without it hides the row.
function fillServerBuildPart(status) {
  const row = document.getElementById("overview-build");
  if (!row) return;
  const build = status?.server_build;
  row.hidden = !build;
  if (!build) return;
  const lines = serverBuildLines(build);
  window.ServerManOverviewCards.status(row.querySelector(".overview-build-summary"),
    row.querySelector(".overview-build-reason"), lines, "overview-build-summary");
  setOverviewCardText(row.querySelector(".overview-build-detail"), lines.detail);
  for (const [selector, text] of [[".overview-build-advice", lines.advice], [".overview-build-busy", lines.busy]]) {
    const node = row.querySelector(selector);
    setOverviewCardText(node, text);
    node.hidden = !text;
  }
}

// Publish the wording and the card part for the Overview page.
window.ServerManServerBuild = Object.freeze({
  lines: serverBuildLines,
  render: renderServerBuildPart,
  fill: fillServerBuildPart,
});
