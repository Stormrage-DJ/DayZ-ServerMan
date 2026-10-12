// Serialize explicit bounds saves and discard presentation of superseded answers.
"use strict";

// One pending save stays pending across load/close invalidation until its call returns.
function mapBoundsCreateSave(refresh, failed) {
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
    const token = generation, submitted = structuredClone(plan);
    pending = true;
    refresh();
    document.getElementById("map-status").textContent = window.ServerManMapWording.boundsSaving;
    try {
      const result = await window.ServerManMapBridge.save(submitted, revision, null);
      if (token !== generation) return;
      if (!validResult(result, submitted)) throw new Error(window.ServerManMapWording.boundsResultUnknown);
      const status = accepted(submitted, result);
      // Routine save success stays quiet; a recovery after failure is announced.
      if (failureGeneration === token) window.ServerManMapLayout.status(status ?? window.ServerManMapWording.boundsSaved);
      failureGeneration = null;
    } catch (error) {
      if (token === generation) { failureGeneration = token; failed(error); }
    } finally {
      // A late answer may release busy controls, but cannot replace current loaded state or Closing.
      pending = false;
      refresh();
    }
  };

  return Object.freeze({run, busy: () => pending, generation: () => generation,
    invalidate: () => { generation += 1; }});
}

// This helper implements no autosave timer, undo action or close decision.
window.ServerManMapBoundsSave = Object.freeze({create: mapBoundsCreateSave});
