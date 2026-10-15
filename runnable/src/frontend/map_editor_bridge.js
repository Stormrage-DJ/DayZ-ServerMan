// Call the profile-bound editor API and reject answers superseded by a newer read.
"use strict";

// The load generation belongs to this document, not the shared shell.
let mapLoadGeneration = 0;

// Invoke a named editor method and preserve failure envelopes for shared wording.
async function mapApiCall(method, args) {
  const api = window.pywebview?.api;
  if (!api || typeof api[method] !== "function") throw new Error("The map editor is not connected.");
  const answer = await api[method](...args);
  if (!answer || typeof answer.success !== "boolean") throw new Error("The map editor received an invalid answer.");
  if (!answer.success) {
    const error = new Error(window.ServerManMapWording.error(answer));
    error.result = answer;
    throw error;
  }
  return answer.value;
}

// Expose only implemented calls; no profile selector or arbitrary dispatch is public.
window.ServerManMapBridge = Object.freeze({
  // A newer load or a closing document invalidates this answer before rendering.
  load: async () => {
    const generation = ++mapLoadGeneration;
    try {
      const value = await mapApiCall("load_plan", []);
      return generation === mapLoadGeneration ? {stale: false, value} : {stale: true};
    } catch (error) {
      if (generation !== mapLoadGeneration) return {stale: true};
      throw error;
    }
  },
  // Forward only the saved form and its accepted revision/image guards.
  save: (plan, revision, selection) => mapApiCall("save_plan", [plan, revision, selection]),
  // Display reads name the loaded mission key; the host binds the profile and never takes a path.
  readTierLayer: (missionKey, tier) => mapApiCall("read_tier_layer", [missionKey, tier]),
  readBackground: (missionKey, selection, part) => mapApiCall("read_background", [missionKey, selection, part]),
  selectBackground: (missionKey) => mapApiCall("select_background", [missionKey]),
  // Inspect reads a point or an object of the draft (null: the saved plan); continue and cancel name the job (4.6).
  inspect: (plan, target) => mapApiCall("inspect", [plan, target]),
  continueInspection: (job) => mapApiCall("continue_inspection", [job]),
  cancelInspection: (job) => mapApiCall("cancel_inspection", [job]),
  // Set aside renames invalid saved data of the loaded mission; the window confirms first (5.3).
  setAside: (missionKey) => mapApiCall("set_aside_plan", [missionKey]),
  // The Apply and restore previews read and write nothing; the host binds the profile (8.9, 8.10)
  previewApply: () => mapApiCall("preview_plan", []),
  previewRestore: (mode) => mapApiCall("preview_restore", [mode]),
  // Full-screen affects only this document's native editor window.
  toggleFullScreen: () => mapApiCall("toggle_full_screen", []),
  // Stop a late load from replacing the closing state.
  invalidateLoads: () => { mapLoadGeneration += 1; },
});
