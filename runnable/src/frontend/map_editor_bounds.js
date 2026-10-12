// Own terrain bounds controls, explicit confirmation and native placement review.
"use strict";

// Bounds state is independent of viewport numeric availability and stored geometry.
const mapBoundsState = {value: null, enabled: false, identifier: null, save: null, accepted: null, current: null, reviewing: false};

// Only a current writable association can submit a bounds change.
function mapBoundsWritable() {
  const state = mapBoundsState;
  return Boolean(state.enabled && state.value && !state.value.read_only && state.value.association?.mission_key);
}

// Busy/load/close freezes controls; a failed viewport axis never freezes this form.
function mapBoundsRefresh() {
  const state = mapBoundsState, writable = mapBoundsWritable();
  document.getElementById("map-bounds-fields").disabled = !writable || state.save.busy() || state.reviewing;
  document.getElementById("map-bounds-fields").setAttribute("aria-busy", String(state.save.busy()));
  document.getElementById("map-bounds-detected").disabled = !writable || state.save.busy() || state.reviewing ||
    !state.value?.capabilities?.detected_bounds;
}

// Keep backend refusals and reload guidance visible while retaining the unsaved fields.
function mapBoundsFailed(error) {
  const text = `${window.ServerManMapWording.boundsNotSaved}: ${error.message}`;
  document.getElementById("map-bounds-error").textContent = text;
  window.ServerManMapLayout.status(text);
}

// Confirmation changes only terrain metadata; placement changes are never inferred.
async function mapBoundsConfirm(bounds, source) {
  const state = mapBoundsState, helpers = window.ServerManMapBoundsPlan;
  if (!mapBoundsWritable() || state.save.busy() || state.reviewing) return;
  const previous = state.value, value = state.current(), token = state.save.generation();
  document.getElementById("map-bounds-error").textContent = "";
  if (helpers.needsReview(value, bounds)) {
    state.reviewing = true;
    mapBoundsRefresh();
    const choice = await window.ServerManMapDom.dialog(window.ServerManMapWording.boundsReviewTitle,
      helpers.review(value, bounds), [{label: window.ServerManMapWording.boundsCancel, value: "cancel"},
        {label: window.ServerManMapWording.boundsReviewSave, value: "save"}]);
    state.reviewing = false;
    mapBoundsRefresh();
    if (choice !== "save") return;
  }
  if (token !== state.save.generation() || !mapBoundsWritable()) return;
  try {
    if (!value.plan && !state.identifier) state.identifier = value.draft_identifier ?? helpers.identifier();
    const candidate = helpers.candidate(value, bounds, source, state.identifier);
    await state.save.run(candidate, value.revision, (plan, result) => state.accepted(plan, result, previous));
  } catch (error) { mapBoundsFailed(error); }
}

// Initialize one form; native values are text inputs so browser steps cannot narrow D1.
function mapBoundsInit(accepted, current = () => mapBoundsState.value) {
  const state = mapBoundsState;
  if (state.save) return;
  state.accepted = accepted;
  state.current = current;
  state.save = window.ServerManMapBoundsSave.create(mapBoundsRefresh, mapBoundsFailed);
  const words = window.ServerManMapWording;
  for (const [id, text] of [["map-bounds-title", words.boundsTitle], ["map-bounds-save", words.boundsManualSave],
    ["map-bounds-detected", words.boundsDetectedSave], ["map-bounds-guidance", words.boundsGuidance]]) {
    document.getElementById(id).textContent = text;
  }
  document.getElementById("map-bounds-save").addEventListener("click", () => {
    const fields = Object.fromEntries(window.ServerManMapBoundsPlan.fields.map((name) =>
      [name, document.getElementById(`map-bounds-${name}`)]));
    const answer = window.ServerManMapBoundsPlan.read(fields);
    document.getElementById("map-bounds-error").textContent = answer.problems.join(" ");
    if (answer.bounds) mapBoundsConfirm(answer.bounds, "manual");
  });
  document.getElementById("map-bounds-detected").addEventListener("click", () => {
    const bounds = state.value?.capabilities?.detected_bounds;
    if (bounds) mapBoundsConfirm(bounds, "detected");
  });
  mapBoundsRefresh();
}

// History traversal keeps the same explicit review gate before changing confirmed placements.
async function mapBoundsReviewHistory(before, after) {
  const state = mapBoundsState, helpers = window.ServerManMapBoundsPlan;
  if (!before || !after || !helpers.needsReview({plan: before}, after.terrain.bounds)) return true;
  if (!mapBoundsWritable() || state.reviewing) return false;
  state.reviewing = true;
  mapBoundsRefresh();
  const words = window.ServerManMapWording;
  const choice = await window.ServerManMapDom.dialog(words.boundsReviewTitle,
    helpers.review({plan: before}, after.terrain.bounds, words.boundsHistoryReviewMessage), [{label: words.boundsCancel, value: "cancel"},
      {label: words.boundsReviewHistory, value: "accept"}]);
  state.reviewing = false;
  mapBoundsRefresh();
  return choice === "accept";
}

// Display exact loaded/detected values; no confirmation or save occurs during load.
function mapBoundsLoad(value) {
  const state = mapBoundsState, words = window.ServerManMapWording;
  state.value = value;
  state.identifier = null;
  const bounds = value?.plan?.terrain?.bounds;
  for (const name of window.ServerManMapBoundsPlan.fields) {
    const field = document.getElementById(`map-bounds-${name}`);
    field.value = bounds && Number.isFinite(bounds[name]) ? String(bounds[name]) : "";
    field.setAttribute("aria-invalid", "false");
  }
  const detected = value?.capabilities?.detected_bounds;
  document.getElementById("map-bounds-detection").textContent = detected ?
    `${words.boundsDetected}: ${JSON.stringify(detected)}` : words.boundsNoDetection;
  document.getElementById("map-bounds-confirmation").textContent =
    value?.plan?.terrain?.bounds_confirmed ? words.boundsConfirmed : words.boundsUnconfirmed;
  document.getElementById("map-bounds-error").textContent = "";
  if (value && value.capabilities?.terrain_id === null &&
      (!value.plan || value.plan.terrain.terrain_id === window.ServerManMapBoundsPlan.terrainId(value))) {
    const identity = `${words.unknownTerrain} (${words.boundsMission} ${value.mission_name})`;
    document.getElementById("map-terrain").textContent = identity;
    document.getElementById("map-terrain").title = identity;
  }
  mapBoundsRefresh();
}

// Invalidate only our review/save generation; closing retains the accepted immediate promise.
function mapBoundsEnabled(enabled) {
  const state = mapBoundsState;
  state.enabled = enabled;
  if (!state.save) return;
  if (!enabled) {
    state.save.invalidate();
    if (state.reviewing) document.querySelector("dialog")?.dispatchEvent(new Event("cancel", {cancelable: true}));
  }
  mapBoundsRefresh();
}

// The controller has no public host method and does not change Apply policy.
window.ServerManMapBounds = Object.freeze({init: mapBoundsInit, load: mapBoundsLoad,
  enabled: mapBoundsEnabled, reviewHistory: mapBoundsReviewHistory});
