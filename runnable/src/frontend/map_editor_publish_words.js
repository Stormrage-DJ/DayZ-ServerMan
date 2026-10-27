// Window texts of Apply and Restore from the editor (8.18), and the wording of their refusals and lane results.
// The shared catalogues word the lane; this module adds only the window texts and the order of the lookups.
"use strict";

// Product Owner texts of 2026-10-15 15:57:10 (decisions 6a, 6b and 9) and 16:03:40 in evidence 8.10, word for word
const mapPublishWordsFixed = Object.freeze({
  apply: "Apply…", restore: "Restore", previous: "Restore previous state…", original: "Restore original…",
  term: "Map operation", reading: "Preparing the preview", checkAgain: "Check again",
  paused: Object.freeze({
    apply: "Editing and autosave are paused until Apply ends. You can still move and zoom the map and select " +
      "items. Closing the map editor does not stop Apply.",
    restore: "Editing and autosave are paused until the restore ends. You can still move and zoom the map and " +
      "select items. Closing the map editor does not stop the restore."}),
  // ADOPT-32 (Product Owner 2026-10-17 13:07:29 in evidence 10.16)
  pausedAdopt: "Editing and autosave are paused until the adoption ends. You can still move and zoom the map and " +
    "select items. Closing the map editor does not stop the adoption.",
  cleared: "No unfinished mission map change is left. Preview again before you apply or restore.",
  deferred: "DayZ-ServerMan cannot check the unfinished mission map change while another operation runs. " +
    "Check again when that operation ends.",
  notOpened: "The Apply preview did not open because the map editor could not save the current draft. Apply uses " +
    "only the saved map plan.",
  // Product Owner 2026-10-16 15:50:59 in evidence 8.18: the refresh loaded another saved revision again
  reloaded: "The saved map plan changed, so the map editor loaded it again. Undo and Redo start again from this plan.",
});
// The lane kind of each mode; the recovery of every mode belongs to the Apply kind (Architect ruling 4)
const mapPublishKinds = Object.freeze({apply: "APPLY_MISSION_MAP", previous: "RESTORE_MISSION_MAP_PREVIOUS",
  original: "RESTORE_MISSION_MAP_ORIGINAL", adopt: "ADOPT_MISSION_MAP"});
// The opening of the host text of a late file change under a restore kind (Product Owner 07:10:16, section 2)
const mapPublishRestoreStopped = "Restore stopped because ";
// Between the kind label and the state or phase label in the one-line slot (open point of the 8.18 evidence)
const mapPublishJoin = " · ";

// Word a synchronous refusal (Architect 2026-10-15 15:46:42, ruling 3). details.reason comes first, in the editor
// wording. Then the shared catalogue words it with the lane kind of the call. Without an envelope, the message shows.
function mapPublishRefusal(error, mode) {
  const failure = error?.result?.error, reason = failure?.details?.reason, words = window.ServerManMapWording;
  if (!failure) return error?.message || window.ServerManOperationMessages.error(null);
  if (reason === "invalid_saved_data") return words.readOnly(reason);
  if ((failure.code === "REVISION_CONFLICT" && reason === "stale_revision") || failure.code === "PROFILE_CONTEXT_MISMATCH") {
    return words.error(error.result);
  }
  return window.ServerManOperationMessages.error(failure, undefined, mapPublishKinds[mode]);
}

// A refusal of the lane admission by a mission map recovery block offers Check again (UI-UX section 5)
function mapPublishBlocked(error) {
  const failure = error?.result?.error, details = failure?.details ?? {};
  return failure?.code === "RECOVERY_REQUIRED" || (failure?.code === "MUTATION_CONFLICT" &&
    details.reason === "RECOVERY_BLOCK" && [undefined, mapPublishKinds.apply].includes(details.owner));
}

// The ended text of an own operation is the activity-view text of its kind (Architect ruling 6). Under a restore
// kind, a late file change shows the host text, which names the file (Product Owner 15:57:10, decision 7a).
function mapPublishResult(record) {
  const result = window.ServerManOperationMessages.result(record), host = record?.terminal_error?.message;
  const restore = [mapPublishKinds.previous, mapPublishKinds.original].includes(record?.kind);
  if (!restore || record.terminal_error?.code !== "REVISION_CONFLICT" || typeof host !== "string" ||
      !host.startsWith(mapPublishRestoreStopped)) return result;
  return Object.freeze({...result, message: host, text: `${result.sentence} ${host}`});
}

// The slot text while the own operation waits or runs: the kind label, then the state or the working phase label
function mapPublishProgress(record) {
  const labels = window.ServerManOperationLabels, name = labels.kind(record.kind).name;
  const waiting = labels.state(record.state), phase = labels.phase(record.kind, record.progress_phase).text;
  if (["ACCEPTED", "QUEUED"].includes(record.state)) return `${name}${mapPublishJoin}${waiting}`;
  return labels.isWorkingPhase(record.progress_phase) ? `${name}${mapPublishJoin}${phase}` : name;
}

window.ServerManMapPublishWords = Object.freeze({
  ...mapPublishWordsFixed, kinds: mapPublishKinds, refusal: mapPublishRefusal, blocked: mapPublishBlocked,
  result: mapPublishResult, progress: mapPublishProgress,
  // A remaining block shows its block reason (Architect ruling 4)
  blockReason: (reason) => window.ServerManHostSentences.blockReason(reason, mapPublishKinds.apply),
  pausedText: (mode) => mode === "adopt" ? mapPublishWordsFixed.pausedAdopt :
    mapPublishWordsFixed.paused[mode === "apply" ? "apply" : "restore"],
});
