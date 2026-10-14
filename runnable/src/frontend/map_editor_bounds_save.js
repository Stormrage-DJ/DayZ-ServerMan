// Serialize explicit bounds saves and discard presentation of superseded answers.
"use strict";

// One pending save stays pending across load/close invalidation until its call returns.
// A success binds to the load count: only a load drops it, because a close cannot undo a commit (QA-5.2-F5).
function mapBoundsCreateSave(refresh, failed, loads = () => 0) {
  let generation = 0;
  let pending = false;
  let failureGeneration = null;

  // Adopt only actual returned metadata; a success envelope does not contain a plan echo.
  const validResult = (result, plan) => Number.isSafeInteger(result?.revision) && result.revision >= 0 &&
    typeof result.draft_changes === "boolean" &&
    ["profile_id", "mission_root", "mission_key", "runtime_profile_key"].every((name) =>
      result.association?.[name] === plan.association[name]);

  // The submitted immutable snapshot is the local saved document; the backend owns its revision.
  const run = async (plan, revision, accepted) => {
    if (pending) return;
    const token = generation, load = loads(), submitted = structuredClone(plan);
    pending = true;
    refresh();
    window.ServerManMapLayout.status(window.ServerManMapWording.boundsSaving);
    try {
      // A chosen image goes with its selection token, so the save stores its path
      const selection = window.ServerManMapBackground?.selection(submitted) ?? null;
      const result = await window.ServerManMapBridge.save(submitted, revision, selection);
      if (load !== loads()) return;
      if (!validResult(result, submitted)) throw Object.assign(new Error(window.ServerManMapWording.boundsResultUnknown), {unconfirmed: true});
      const status = accepted(submitted, result);
      // Routine save success stays quiet; a recovery after failure is announced, but never over Closing.
      if (failureGeneration === token && token === generation) window.ServerManMapLayout.status(status ?? window.ServerManMapWording.boundsSaved, {announce: true});
      failureGeneration = null;
    } catch (error) {
      // A failure binds to the load count like a success; the bounds module holds it while a close lasts (QA-5.2-F9)
      if (load === loads()) { failureGeneration = generation; failed(error); }
    } finally {
      // A late answer may release busy controls; it never replaces a newer load or the Closing state.
      pending = false;
      refresh();
    }
  };

  return Object.freeze({run, busy: () => pending, generation: () => generation,
    invalidate: () => { generation += 1; }});
}

// This helper implements no autosave timer, undo action or close decision.
window.ServerManMapBoundsSave = Object.freeze({create: mapBoundsCreateSave});
