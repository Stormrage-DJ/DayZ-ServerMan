// Update status: the remote update-check state of the selected profile, read in every section,
// and the badge model that the navigation draws from it.
"use strict";

// Shortest pause between two status reads while no check runs, in milliseconds: Mods visible, any other section.
const UPDATE_STATUS_IDLE_INTERVAL = 5000;
const UPDATE_STATUS_BACKGROUND_INTERVAL = 30000;
// Last status of the selected profile, the automatic-check preference, and read bookkeeping.
const updateStatusState = {
  profileId: "", status: null, automatic: true, readAt: 0, reading: false, requestError: "",
  // The request-and-read that is in flight, with the profile it belongs to, and whether it announces
  // the state also when nothing changed.
  opening: null, openingProfile: "", announceAlways: false,
};
// Operator wording for each failure code of a check run.
const updateFailureReasons = Object.freeze({
  NETWORK_UNREACHABLE: "Steam could not be reached",
  TIMEOUT: "Steam did not answer in time",
  TLS_FAILURE: "the secure connection to Steam failed",
  HTTP_STATUS: "Steam refused the request",
  RESPONSE_TOO_LARGE: "the answer from Steam was too large",
  RESPONSE_MALFORMED: "the answer from Steam could not be read",
});

// Return the selected profile identifier, or an empty text when none is selected.
function updateStatusProfile() {
  return window.ServerManProfileContext?.selectedId?.() || "";
}

// Reduce the held status to the values whose change must redraw the update indicators. The profile is part
// of it: the check state is shared by all profiles, so two profiles can have equal values (QF-029).
function updateStatusSignature(status) {
  const profile = updateStatusState.profileId;
  if (!status) return `${profile}|none`;
  const mods = status.mods || {};
  const build = status.server_build || {};
  return [profile, status.revision, status.checking, mods.check_state, mods.last_success_at,
    mods.error_code, mods.update_count, mods.pending_apply_count, mods.not_downloaded_count,
    build.revision, build.state, build.checking, build.waiting, build.paused].join("|");
}

// Read the status of the selected profile; resolve true when it differs from the last one.
async function readUpdateStatus() {
  const profileId = updateStatusProfile();
  // Forget the state of another profile before the new read.
  if (profileId !== updateStatusState.profileId) {
    updateStatusState.profileId = profileId; updateStatusState.status = null;
  }
  const before = updateStatusSignature(updateStatusState.status);
  // Without a profile the host answers with the server build part only
  const result = await window.pywebview.api.get_update_status(profileId || null);
  // Drop an answer for a profile that is no longer selected.
  if (profileId !== updateStatusProfile()) return false;
  updateStatusState.readAt = Date.now();
  updateStatusState.status = result && result.success ? Object.freeze(result.value) : null;
  return before !== updateStatusSignature(updateStatusState.status);
}

// Tell the listening pages that the update state of the selected profile changed.
function announceUpdateStatus() {
  document.dispatchEvent(new CustomEvent("serverman:update-status", {
    detail: { profileId: updateStatusState.profileId, status: updateStatusState.status },
  }));
}

// Ask the host for a check of the scope (mods by default); a forced request ignores the automatic-check switch.
async function requestUpdateCheck(force, scope = "mods") {
  const result = await window.pywebview.api.request_update_check(scope, force);
  // Keep the failure of an operator request for the header.
  updateStatusState.requestError = !force || (result && result.success) ? ""
    : window.ServerManOperationMessages.bridgeError(result, "The check request failed safely.");
  return result;
}

// Request a non-forced check and read the state once; a changed state is announced to the listening pages.
async function runUpdateStatusOpen() {
  const before = updateStatusSignature(updateStatusState.status);
  if (!updateStatusProfile()) {
    await readUpdateStatus();
  } else {
    // Send the request and read the effective automatic-check preference together.
    const [, preferences] = await Promise.all([
      requestUpdateCheck(false), window.pywebview.api.get_ui_preferences(),
    ]);
    if (preferences && preferences.success) {
      updateStatusState.automatic = preferences.value.automatic_update_checks !== false;
    }
    await readUpdateStatus();
  }
  const always = updateStatusState.announceAlways;
  updateStatusState.announceAlways = false;
  if (always || before !== updateStatusSignature(updateStatusState.status)) announceUpdateStatus();
}

// Start at the first snapshot, on Mods open, on a profile change, and after an operation that can change
// mod content. Callers for the same profile share one request-and-read.
function openUpdateStatus() {
  const profileId = updateStatusProfile();
  if (updateStatusState.opening && updateStatusState.openingProfile === profileId) {
    return updateStatusState.opening;
  }
  const flight = runUpdateStatusOpen().finally(() => {
    if (updateStatusState.opening === flight) updateStatusState.opening = null;
  });
  updateStatusState.opening = flight; updateStatusState.openingProfile = profileId;
  return flight;
}

// Refresh on every shell poll, in every section: one read per idle interval, on every poll while a check runs.
async function pollUpdateStatus(modsVisible = false) {
  const build = updateStatusState.status?.server_build;
  const checking = updateStatusState.status?.checking === true || build?.checking === true || build?.waiting === true;
  const waited = Date.now() - updateStatusState.readAt;
  const interval = modsVisible ? UPDATE_STATUS_IDLE_INTERVAL : UPDATE_STATUS_BACKGROUND_INTERVAL;
  if (updateStatusState.reading || (!checking && waited < interval)) return;
  // Allow one read at a time and announce only a real change.
  updateStatusState.reading = true;
  try {
    if (await readUpdateStatus()) announceUpdateStatus();
  } finally {
    updateStatusState.reading = false;
  }
}

// Request a check, read the fresh state, and announce it; "Check now" passes force and its scope. A request
// that is not forced shares the request-and-read of the shell, so one finished operation sends one request.
async function recheckUpdateStatus(force = false, scope = "mods") {
  if (!updateStatusProfile() && scope === "mods") return;
  if (!force) { updateStatusState.announceAlways = true; await openUpdateStatus(); return; }
  await requestUpdateCheck(true, scope);
  await readUpdateStatus();
  announceUpdateStatus();
}

// Format a Steam time (seconds) or a check time (UTC text) in the operator's locale.
function formatUpdateTime(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return null;
  // Pair the compact text with the exact value for a tooltip.
  return Object.freeze({
    text: date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
    exact: date.toLocaleString(undefined, { dateStyle: "full", timeStyle: "long" }),
  });
}

// Explain in operator words why an item or the whole check has no verified answer.
function updateCheckReason(kind) {
  if (kind === "UNKNOWN_ITEM") return "Steam does not list this item";
  if (kind === "FAILED") {
    return updateFailureReasons[updateStatusState.status?.mods?.error_code] || "the check failed";
  }
  // A missing or old check with the switch off is the operator's own setting.
  if (!updateStatusState.automatic) return "automatic checks are off";
  return kind === "STALE" ? "the last check is too old" : "not checked yet";
}

// Phrase a count with its singular or plural noun.
function updateCountText(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}

// Summarize the update state in one line; mod rows refine the "all current" wording. `named` says "mod" in the
// lines that would be ambiguous outside the Mods page (the Overview card also shows the server build, QF-049).
function updateSummary(rows = null, named = false) {
  const noun = named ? "mod " : "";
  const status = updateStatusState.status;
  if (!status || !status.mods) return { text: "Update status is unavailable", tone: "neutral" };
  const mods = status.mods;
  // The first check of this session is still running.
  if (status.checking && mods.check_state === "NEVER") {
    return { text: `Checking for ${noun}updates…`, tone: "neutral" };
  }
  // Name what needs the operator, in the order of the table states.
  const parts = [];
  if (mods.update_count > 0) {
    parts.push(`${updateCountText(mods.update_count, `${noun}update`, `${noun}updates`)} available`);
  }
  if (mods.pending_apply_count > 0) parts.push(`${mods.pending_apply_count} downloaded - not applied`);
  // A configured Workshop mod without its content folder needs a download (QF-054).
  if (mods.not_downloaded_count > 0) parts.push(`${mods.not_downloaded_count} not downloaded`);
  const pending = parts.length > 0;
  // The Overview shows a short head and the reason under it; the Mods header keeps the one-line text.
  const why = mods.check_state === "OK" ? "" : updateCheckReason(mods.check_state);
  if (why) parts.push(`Could not check: ${why}`);
  if (parts.length) {
    const head = pending ? parts.slice(0, why ? -1 : parts.length).join(" · ") : "Could not check";
    const reason = !why ? "" : pending ? `Could not check: ${why}.` : `${why[0].toUpperCase()}${why.slice(1)}.`;
    return { text: parts.join(" · "), head, reason,
      tone: pending ? "warning" : mods.check_state === "FAILED" ? "error" : "neutral" };
  }
  // Nothing is pending: say "current" only when every row is verified or local.
  if (!Array.isArray(rows)) return { text: `No ${noun}updates available`, tone: "normal" };
  if (!rows.length) return { text: "No mods to check", tone: "neutral" };
  const settled = rows.every((row) => ["CURRENT", "LOCAL"].includes(row.state));
  if (!settled) return { text: "No updates available", tone: "neutral" };
  return { text: rows.length === 1 ? "The mod is current" : `All ${rows.length} mods are current`,
    tone: "normal" };
}

// Set a text only when it differs, so assistive technology hears real changes only.
function setUpdateText(node, text, title = "") {
  if (node.textContent !== text) node.textContent = text;
  if (title) node.title = title; else node.removeAttribute("title");
}

// Phrase the parts of a pending count for the accessible name: updates first, then downloaded mods.
function updateBadgeParts(mods) {
  const parts = [];
  if (mods.update_count > 0) parts.push(`${updateCountText(mods.update_count, "update", "updates")} available`);
  if (mods.pending_apply_count > 0) parts.push(`${mods.pending_apply_count} downloaded - not applied`);
  // A configured Workshop mod without its content folder needs a download (QF-054).
  if (mods.not_downloaded_count > 0) parts.push(`${mods.not_downloaded_count} not downloaded`);
  return parts;
}

// Describe the badge of the Mods navigation item: its form, its text, and the words after "Mods".
function updateBadge() {
  const status = updateStatusState.status;
  const none = { form: "none", text: "", name: "", count: 0 };
  // A status that was read for another profile says nothing about the selected one.
  if (!updateStatusProfile() || updateStatusState.profileId !== updateStatusProfile()) return none;
  if (!status || !status.mods) return none;
  const mods = status.mods;
  const count = (Number(mods.update_count) || 0) + (Number(mods.pending_apply_count) || 0)
    + (Number(mods.not_downloaded_count) || 0);
  // A pending count is shown in every check state; the name says when the check behind it is not fresh.
  if (count > 0) {
    const suffix = mods.check_state === "FAILED" ? ["last check failed"]
      : mods.check_state === "OK" ? [] : ["not checked recently"];
    return { form: "count", text: count > 99 ? "99+" : String(count), count,
      name: [...updateBadgeParts(mods), ...suffix].join(", ") };
  }
  if (mods.check_state === "OK") return none;
  if (mods.check_state === "FAILED") return { form: "failed", text: "!", name: "could not check for updates", count };
  // Only the first check shows the turning ring; a later check leaves the badge as it is.
  if (status.checking && mods.check_state === "NEVER") {
    return { form: "checking", text: "", name: "checking for updates", count };
  }
  return { form: "unchecked", text: "", name: "updates not checked", count };
}

// Publish the update state for the shell indicators and the Mods page.
window.ServerManUpdateStatus = Object.freeze({
  open: openUpdateStatus,
  poll: pollUpdateStatus,
  recheck: recheckUpdateStatus,
  current: () => updateStatusState.status,
  automaticChecks: () => updateStatusState.automatic,
  // Settings stores the switch and hands the new value over; the next read uses it.
  setAutomaticChecks: (enabled) => { updateStatusState.automatic = enabled !== false; },
  requestError: () => updateStatusState.requestError,
  reason: updateCheckReason,
  formatTime: formatUpdateTime,
  summary: updateSummary,
  badge: updateBadge,
});
