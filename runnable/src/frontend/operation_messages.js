// Operator wording for host error codes. A code is never printed. The result of a finished operation is
// worded in operation_results.js; every document that loads this file also loads that one (8.14).
"use strict";

// Text shown for an internal fault and for every message that would show an identifier.
const OPERATION_INTERNAL_TEXT = "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.";
// Text for a blocked state that an earlier operation left behind.
const OPERATION_RECOVERY_TEXT = "An earlier operation did not finish cleanly. Changes are blocked until it is resolved. "
  + "Details are in Logs, Manager diagnostics.";
// A sentence that states the block; a result whose own sentence states it drops it from its message.
const OPERATION_BLOCK_SENTENCE = /\s*Changes are blocked(?: until [^.]*)?\./g;
// Text for a rejected request whose host message cannot be shown: it says what to check.
const OPERATION_REQUEST_TEXT = "The request was not accepted. Check the values you entered, then try again. "
  + "Details are in Logs, Manager diagnostics.";
// The causes of a refused submission, as the host names them in the error details, with their wording.
const operationConflictTexts = Object.freeze({
  QUEUE_FULL: "Too many operations are waiting. Wait for one to finish and try again.",
  SHUTTING_DOWN: "DayZ-ServerMan is closing and starts no new operation.",
  RECOVERY_BLOCK: "Changes are blocked until recovery is resolved.",
});
// Text that replaces the host message, per error code.
const operationErrorTexts = Object.freeze({
  INTERNAL_FAILURE: OPERATION_INTERNAL_TEXT,
  CONTRACT_VERSION_UNSUPPORTED: OPERATION_INTERNAL_TEXT,
  EVENT_CURSOR_EXPIRED: OPERATION_INTERNAL_TEXT,
  REVISION_CONFLICT: "The profile or the settings changed in the meantime. Open another page, return to this one, and try again.",
  CONTROL_CONFLICT: "Another DayZ-ServerMan is using this DayZ installation. Close it and try again.",
  PROCESS_STATE_UNKNOWN: "DayZ-ServerMan could not read the running programs, so the server state is not known. Try again.",
  PROCESS_OWNERSHIP_UNPROVEN: "DayZ-ServerMan cannot confirm that it started this DayZ server, so it will not control it.",
  STOP_TIMEOUT: "DayZ did not close in time. Check the server window, then try again.",
  RECOVERY_REQUIRED: OPERATION_RECOVERY_TEXT,
  UPDATE_RESULT_UNKNOWN: "SteamCMD did not close cleanly, so the update cannot be confirmed. Changes are blocked.",
  AUTHENTICATION_REQUIRED: "Choose Steam account sign-in and enter an account name first.",
  AUTHENTICATION_FAILED: "SteamCMD closed before the sign-in was confirmed.",
  STEAMCMD_UNAVAILABLE: "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
  STEAMCMD_PATH_CHANGED: "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
  STEAMCMD_BUSY: "SteamCMD is still busy with the server build check. Try again in a minute.",
  STEAMCMD_EXIT_UNPROVEN: "SteamCMD did not close after an earlier run. Close SteamCMD, then restart DayZ-ServerMan.",
  ENTITLEMENT_DENIED: "This Steam account may not download the item.",
  CONNECTION_FAILED: "Steam could not be reached.",
  WORKSHOP_CONTENT_FAILED: "The downloaded mod files could not be verified. Run the update again.",
  CACHE_VERIFICATION_FAILED: "The downloaded mod files could not be verified. Run the update again.",
  WORKSHOP_MANIFEST_INVALID: "The downloaded mod files could not be verified. Run the update again.",
  UPDATE_CANCELLED: "Mod update cancelled.",
  PUBLICATION_REQUIRED: "Review and apply the mods before the server starts.",
  PUBLICATION_PREVIEW_STALE: "The mods or the server folder changed after the review. Review and apply again.",
  PUBLICATION_FAILED: "The mods could not be copied to the server folder.",
  PUBLICATION_VERIFICATION_FAILED: "The applied mods could not be verified.",
  BACKUP_UNAVAILABLE: "Backups are not available in this installation.",
  RUNTIME_PROFILE_UNRESOLVED: "Set a runtime profile directory in Profiles first.",
  PENDING_RUNTIME_PROFILE_SUPPORT: "This backup has content that this version cannot restore.",
  UNSUPPORTED_SNAPSHOT_CONTENT: "This backup has content that this version cannot restore.",
  PROFILE_CONTEXT_MISMATCH: "This backup belongs to a different profile.",
  SOURCE_CHANGED: "The legacy folder changed after the preview. Preview it again.",
  MIGRATION_CONFLICT: "The legacy folder changed after the preview. Preview it again.",
  GAMEPLAY_NOT_ENABLED: "Turn on \u201CUse gameplay configuration\u201D in Configuration first.",
  OPERATION_NOT_CANCELLABLE: "This operation can no longer be cancelled.",
});
// Text per operation kind for a code whose general text names another operation; it wins over the general text.
const operationKindErrorTexts = Object.freeze({
  AUTHENTICATE_STEAMCMD: Object.freeze({
    UPDATE_RESULT_UNKNOWN: "SteamCMD did not close cleanly after the Steam sign-in, so the sign-in cannot be confirmed. Changes are blocked.",
  }),
  APPLY_MISSION_MAP: Object.freeze({
    REVISION_CONFLICT: "The map plan, the profile, the settings or the mission files changed after the preview. Nothing was applied. Preview the changes again and check them before you apply.",
    PUBLICATION_FAILED: "Apply reached its commit point, but a later step failed. The mission files can have changed. Preview the changes again to see what is left to apply.",
  }),
  // A stale review or a late file change of a restore; the window shows the late-change host text (8.8, 8.14).
  // A failure after the commit point has the kind text of 10.32 (Product Owner 2026-10-17 02:14:51).
  RESTORE_MISSION_MAP_ORIGINAL: Object.freeze({
    REVISION_CONFLICT: "The profile, the settings, the mission files or the map editor records changed after the preview. Nothing was restored. Preview the restore again and check it before you restore.",
    PUBLICATION_FAILED: "The restore reached its commit point, but a later step failed. The mission files can have changed. Preview the restore again to see what is left to restore.",
  }),
  RESTORE_MISSION_MAP_PREVIOUS: Object.freeze({
    REVISION_CONFLICT: "The profile, the settings, the mission files or the map editor records changed after the preview. Nothing was restored. Preview the restore again and check it before you restore.",
    PUBLICATION_FAILED: "The restore reached its commit point, but a later step failed. The mission files can have changed. Preview the restore again to see what is left to restore.",
  }),
  // ADOPT-28, a stale adoption review (Product Owner 2026-10-16 14:26:38, evidence 10.1)
  ADOPT_MISSION_MAP: Object.freeze({
    REVISION_CONFLICT: "The profile, the settings, the mission files or the map editor records changed after the preview. Nothing was adopted. Preview the adoption again and check it before you adopt.",
    // A failure after the commit point (Product Owner 2026-10-17 11:41:56, evidence 10.35)
    PUBLICATION_FAILED: "Adoption reached its commit point, but a later step failed. No mission file changed. The map editor records can have changed. Preview the adoption again to see what is left to adopt.",
  }),
});
// Host sentences of one kind that are shown unchanged, by a fragment; never the general request text (8.7).
const operationKindShownTexts = Object.freeze({ APPLY_MISSION_MAP: Object.freeze([
  "Apply needs a choice for each mission file that changed outside the map editor",
  "Apply cannot write an encounter that is not available on this mission",
  "affects values that the map editor sets, so the map editor cannot keep this change", "This mission has no saved map plan.",
]) });
// Host sentences shown unchanged, by their opening text; each names its cause, its file and the outcome.
const operationShownHostTexts = Object.freeze([
  "mapgroupproto.xml has a layout that this setting cannot change safely: ",
  "mapgroupproto.xml differs from its stored original: ",
  "Apply and Restore original are not available for this mission. ",
  // The 8.13 stale zone review, before and under the guard; an ordinary mission has another message (8.14).
  "The profile, the settings, the mission files or the map editor records changed after the review of medical loot zones. ",
]);
// Known host sentences of a rejected request, by a fragment of the host message.
const operationRequestTexts = Object.freeze([
  ["must contain only letters, digits, or underscore", "The Steam account name may contain only letters, digits and underscores."],
  ["is required in ACCOUNT mode", "Enter a Steam account name for account sign-in."],
  ["must be empty in ANONYMOUS mode", "Leave the account name empty for anonymous sign-in."],
  ["authentication mode", "Choose a sign-in mode for the Steam account."],
  ["must be ACCOUNT or ANONYMOUS", "Choose a sign-in mode for the Steam account."],
]);
// Server state names that a host message may hold, with their wording inside a sentence.
const operationServerStates = Object.freeze({
  RUNNING_EXTERNAL: "running outside DayZ-ServerMan", RUNNING_MANAGED: "running",
  STOPPED: "stopped", STARTING: "starting", STOPPING: "stopping",
  AMBIGUOUS: "in an unknown state", UNKNOWN: "in an unknown state",
});

// Sentences of "apply mods and restart" for a failed or cancelled end, chosen by the last working phase.
const operationRestartApplyTexts = Object.freeze({
  cancelledRunning: "Cancelled. The server keeps running; nothing was applied.",
  cancelledStopped: "Cancelled. The server stays stopped; the mods were not applied.",
  preflight: "The reviewed plan is out of date, or the server state changed. The server keeps running; nothing was applied. Update again.",
  stop: "The server could not be stopped. Nothing was applied.",
  stopNotRunning: "The server was already stopped, so it was not restarted. Nothing was applied. To apply the mods and start the server, use Update & start.",
  backup: "Server stopped, but the backup failed. The mods were not applied and the server was not started again.",
  check: "Mods applied, but the server folder changed before the start. The server stays stopped.",
  apply: "Server stopped, but the mods could not be applied. The server folder is as it was. The server was not started again.",
  applyBlocked: "Server stopped, but the mods could not be applied.",
  applyRefused: "The server was stopped, but its state changed before the mods could be applied. Nothing was applied; the server folder is as it was.",
  startCheck: "Mods applied, but the server folder changed before the start. The server was not started.",
  start: "Mods applied, but the server start did not succeed or could not be confirmed. Check the server state on Overview.",
});

// Success of a medical loot-zone toggle where no group changes; it replaces the normal success sentence.
const OPERATION_MEDICAL_UNCHANGED = "No medical loot group on this mission changes with this setting. No file was changed.";
// Way out of a loot-zone refusal where mapgroupproto.xml and its stored original do not match (6.5, PO 15:19:49).
const OPERATION_ZONE_ORIGINAL_WAY_OUT = "DayZ-ServerMan does not guess the original loot of these groups. If mapgroupproto.xml "
  + "changed on purpose, for example after a game or mod update or by another tool, first make sure that its medical "
  + "loot groups have the loot that you want when medical loot zones are off. Then move the stored original to another "
  + "folder. The current file then becomes the original, which is the Off state. Otherwise, put back the "
  + "mapgroupproto.xml that matches the stored original.";
// Admin text of a loot-zone refusal, by the opening of its host reason (6.5). Each row gives the sentence
// that replaces the reason ("" shows the reason) and the way out ("" for none). Python has the same rows.
const operationZoneRefusalTexts = Object.freeze([
  ["mapgroupproto.xml differs from its stored original: ", "", OPERATION_ZONE_ORIGINAL_WAY_OUT],
  ["medical feature target differs from both its original and managed state",
    "mapgroupproto.xml differs from its stored original and from the form that DayZ-ServerMan writes when medical loot zones are on. No file was changed.",
    OPERATION_ZONE_ORIGINAL_WAY_OUT],
  ["mapgroupproto.xml has a layout that this setting cannot change safely: ", "",
    "Correct this in mapgroupproto.xml, then try again."],
  ["medical loot zones cannot change because the map editor manages this mission",
    "Medical loot zones cannot change because the map editor manages this mission.", ""]
]);

// Word an unmappable loot-zone baseline: its reason, the way out and the stored original (6.5).
// A mappable baseline, or a reason without a row, gives null, so the state is never guessed.
function zoneBaselineText(baseline) {
  if (!baseline || typeof baseline !== "object" || baseline.mappable !== false) return null;
  const reason = String(baseline.reason || "");
  const row = operationZoneRefusalTexts.find(([opening]) => reason.startsWith(opening));
  if (!row) return null;
  const text = [row[1] || reason, row[2]].filter(Boolean).join(" ");
  // Only the way out that moves or matches the stored original needs its path.
  const named = row[2] === OPERATION_ZONE_ORIGINAL_WAY_OUT && baseline.stored_original;
  return named ? `${text} Stored original: ${baseline.stored_original}` : text;
}

// Find the name of the server state that a host message names last; null when it names none.
function namedServerStateName(message) {
  const found = [...String(message).matchAll(/\b(RUNNING_EXTERNAL|RUNNING_MANAGED|STOPPED|STARTING|STOPPING|AMBIGUOUS|UNKNOWN)\b/g)];
  return found.length ? found.at(-1)[1] : null;
}

// Find the server state that a host message names last, as wording inside a sentence.
function namedServerState(message) {
  const name = namedServerStateName(message);
  return name ? operationServerStates[name] : null;
}

// Turn a host error into operator text; the fallback replaces a missing error, the kind of its operation picks its text.
function operationErrorText(error, fallback = OPERATION_INTERNAL_TEXT, kind = null) {
  if (!error || typeof error !== "object") return fallback;
  // A medical loot-zone refusal names its way out and the stored original (6.5).
  const zone = zoneBaselineText(error.details?.zone_baseline);
  if (zone) return zone;
  const code = typeof error.code === "string" ? error.code : "";
  const message = typeof error.message === "string" ? error.message : "";
  // A message that names a server state becomes one sentence about that state.
  const state = namedServerState(message);
  if (state || code === "EXTERNAL_PROCESS") {
    return `This cannot be done while the server is ${state || operationServerStates.RUNNING_EXTERNAL}.`;
  }
  if (code === "MUTATION_CONFLICT") return mutationConflictText(error.details?.reason, message, error.details?.owner);
  // A host sentence that already says what to do is shown as it is, whatever its code.
  if (operationShownHostTexts.some((opening) => message.startsWith(opening))) return message;
  if (Object.hasOwn(operationKindShownTexts, kind) && operationKindShownTexts[kind].some((part) => message.includes(part))) return message;
  const own = typeof kind === "string" && Object.hasOwn(operationKindErrorTexts, kind) ? operationKindErrorTexts[kind] : {};
  if (Object.hasOwn(own, code)) return own[code];
  if (Object.hasOwn(operationErrorTexts, code)) return operationErrorTexts[code];
  // A rejected request with a known host sentence has its own wording.
  const known = code === "INVALID_REQUEST" && operationRequestTexts.find(([fragment]) => message.includes(fragment));
  if (known) return known[1];
  // Any other host sentence is shown, with its identifiers translated in place.
  const sentence = window.ServerManHostSentences.sentence(message);
  if (sentence) return sentence;
  if (code === "INVALID_REQUEST") return OPERATION_REQUEST_TEXT;
  return message ? OPERATION_INTERNAL_TEXT : fallback;
}

// Word a refused submission by its cause: a full queue, a recovery block with its reason, or the closing manager.
function mutationConflictText(reason, message, owner = null) {
  // A host that names no cause is read by its message.
  const cause = typeof reason === "string" && reason ? reason
    : message.includes("queue is full") ? "QUEUE_FULL"
      : message.includes("lane is draining") ? "SHUTTING_DOWN" : "RECOVERY_BLOCK";
  if (cause === "RECOVERY_BLOCK") {
    return `${operationConflictTexts.RECOVERY_BLOCK} ${window.ServerManHostSentences.blockReason(message, owner)}`;
  }
  if (Object.hasOwn(operationConflictTexts, cause)) return operationConflictTexts[cause];
  return window.ServerManHostSentences.sentence(message) || OPERATION_INTERNAL_TEXT;
}

// Word the Overview notice of a recovery block: the reason, what it blocks, and where the details are.
function recoveryNoticeText(reason, owner = null) {
  return `${window.ServerManHostSentences.blockReason(reason, owner)} Changes are blocked until this is resolved. `
    + "Details are in Logs, Manager diagnostics.";
}

// Word the text of a failed bridge call for a page notice.
function bridgeErrorText(result, fallback) {
  return operationErrorText(result?.error, fallback);
}

// Publish the message lookups used by the bar and by every page notice. operationResult is declared in
// operation_results.js, which each document composes into the same script element.
window.ServerManOperationMessages = Object.freeze({
  error: operationErrorText,
  bridgeError: bridgeErrorText,
  result: operationResult,
  recoveryText: OPERATION_RECOVERY_TEXT,
  recoveryNotice: recoveryNoticeText,
  medicalUnchanged: OPERATION_MEDICAL_UNCHANGED,
  zoneBaseline: zoneBaselineText,
});
