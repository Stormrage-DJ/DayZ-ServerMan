// Row marks of the mod table for the page's own update, apply, and restart operations.
// The operation bar shows the whole operation; a row shows what happens to its mod.
"use strict";

// Profile of the marks, per-mod progress entries and outcomes of an update, and the targets of an apply.
const modsProgressState = {
  profileId: "", updating: false, entries: {}, outcomes: {}, applying: [], applyActive: false,
};
// Line below the row state, per item phase of a running update.
const modsProgressLines = Object.freeze({
  queued: "Waiting for download", downloading: "", verifying: "Verifying download",
  done: "", failed: "",
});
// Line of a row that a running update has not reached yet, and of a target of a running apply.
const MODS_PROGRESS_CHECKING = "Checking…";
const MODS_PROGRESS_APPLYING = "Applying to the server folder…";
// Per-mod outcomes of an update that are no failure.
const MODS_PROGRESS_SUCCESS = Object.freeze(["VERIFIED_CURRENT", "DOWNLOADED_VERIFIED", "UPDATED_VERIFIED"]);

// Format a byte count as megabytes or gigabytes for the progress line.
function formatModsSize(bytes) {
  const megabytes = bytes / (1024 * 1024);
  return megabytes >= 1024 ? `${(megabytes / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(megabytes))} MB`;
}

// Forget every mark and redraw the rows that showed one.
function clearModsProgress() {
  Object.assign(modsProgressState, { updating: false, entries: {}, outcomes: {}, applying: [], applyActive: false });
  updateModInventory();
}

// Take the progress of a running operation of this page into the row marks.
function modsProgressChanged(operation) {
  const profileId = selectedProfile()?.profile_id || "";
  // Marks belong to one profile; an operation for another profile marks nothing here.
  if (operation.target_profile_id && operation.target_profile_id !== profileId) return;
  if (operation.kind === "UPDATE_WORKSHOP_ITEMS") {
    const items = Array.isArray(operation.progress_detail?.items) ? operation.progress_detail.items : [];
    const entries = {};
    items.forEach((item) => { entries[String(item?.workshop_id)] = item; });
    Object.assign(modsProgressState, { profileId, updating: true, entries, outcomes: {}, applying: [], applyActive: false });
  } else if (["PUBLISH_MODS_AND_KEYS", "APPLY_MODS_AND_RESTART"].includes(operation.kind)) {
    Object.assign(modsProgressState, { profileId, updating: false, entries: {}, outcomes: {}, applyActive: true });
  } else if (modsProgressState.updating || modsProgressState.applyActive
      || Object.keys(modsProgressState.outcomes).length) {
    // Any other operation outdates the marks of an earlier update.
    clearModsProgress(); return;
  } else return;
  updateModInventory();
}

// Keep the per-mod outcome of a finished update as the row line; an apply that ended leaves no mark.
function modsProgressFinished(operation) {
  const profileId = selectedProfile()?.profile_id || "";
  // The result of an operation for another profile marks nothing here.
  if (operation.target_profile_id && operation.target_profile_id !== profileId) { clearModsProgress(); return; }
  modsProgressState.profileId = profileId;
  const updated = operation.kind === "UPDATE_WORKSHOP_ITEMS";
  const outcomes = {};
  const items = updated && Array.isArray(operation.result?.items) ? operation.result.items : [];
  items.forEach((entry) => {
    const id = entry?.item?.workshop_id;
    if (!id) return;
    outcomes[String(id)] = { text: window.ServerManDiagnosticLabels.modOutcome(entry),
      failed: !MODS_PROGRESS_SUCCESS.includes(entry.outcome) };
  });
  // A failed or cancelled update without a result marks the rows that were still at work.
  if (updated && !items.length) {
    Object.keys(modsProgressState.entries).forEach((id) => {
      if (modsProgressState.entries[id]?.phase !== "done") outcomes[id] = { text: "", failed: true };
    });
  }
  Object.assign(modsProgressState, { updating: false, entries: {}, outcomes, applying: [], applyActive: false });
  updateModInventory();
}

// Remember the reviewed targets that an apply, which was just submitted, copies; a current target is not marked.
function modsProgressApplying(targets) {
  modsProgressState.profileId = selectedProfile()?.profile_id || "";
  modsProgressState.applying = (Array.isArray(targets) ? targets : [])
    .filter((target) => target?.current !== true).map((target) => String(target.workshop_id));
  modsProgressState.applyActive = true; modsProgressState.outcomes = {};
  updateModInventory();
}

// Return the mark of one row: an optional label with its tone, and the lines below the row state.
function modsProgressMark(row) {
  const id = row.workshop_id ? String(row.workshop_id) : "";
  const none = { label: "", tone: "", lines: [] };
  if (!id || row.source_kind !== "workshop"
      || modsProgressState.profileId !== (selectedProfile()?.profile_id || "")) return none;
  const line = (text, tone = "progress") => ({ text, title: "", tone });
  if (modsProgressState.applyActive) {
    return modsProgressState.applying.includes(id) ? { ...none, lines: [line(MODS_PROGRESS_APPLYING)] } : none;
  }
  if (modsProgressState.updating) {
    const entry = modsProgressState.entries[id];
    if (!entry) return { ...none, lines: [line(MODS_PROGRESS_CHECKING)] };
    if (entry.phase === "failed") return { label: "Failed", tone: "failed", lines: [] };
    if (entry.phase === "downloading") {
      // Percent and size appear only when both byte counts are known.
      const known = Number.isInteger(entry.done_bytes) && Number.isInteger(entry.total_bytes) && entry.total_bytes > 0;
      const percent = known ? Math.min(100, Math.floor((entry.done_bytes / entry.total_bytes) * 100)) : 0;
      return { label: "Downloading", tone: "downloading",
        lines: known ? [line(`${percent} % of ${formatModsSize(entry.total_bytes)}`)] : [] };
    }
    const text = modsProgressLines[entry.phase] || "";
    return { ...none, lines: text ? [line(text)] : [] };
  }
  const outcome = modsProgressState.outcomes[id];
  if (!outcome) return none;
  if (outcome.failed) return { label: "Failed", tone: "failed", lines: outcome.text ? [line(outcome.text, "problem")] : [] };
  return { ...none, lines: [line(outcome.text, "outcome")] };
}

// Capture what the marks add to the rows, so a change redraws the table.
function modsProgressSignature() {
  return [modsProgressState.profileId, modsProgressState.updating, modsProgressState.entries,
    modsProgressState.outcomes, modsProgressState.applying, modsProgressState.applyActive];
}

// Publish the row marks for the Mods table and for the operation handling of the page.
window.ServerManModsProgress = Object.freeze({
  changed: modsProgressChanged, finished: modsProgressFinished, applying: modsProgressApplying,
  clear: clearModsProgress, mark: modsProgressMark, signature: modsProgressSignature,
});
