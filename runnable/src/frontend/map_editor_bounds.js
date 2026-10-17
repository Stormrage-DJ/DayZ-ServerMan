// Own terrain bounds controls, explicit confirmation and native placement review.
"use strict";

// Bounds state is independent of viewport numeric availability and stored geometry.
// failure: the last failed bounds save ({reason, reload, at}) for the close dialog (UI-UX 2026-10-14 02:21:57).
// paused: an own Apply or restore runs; a running bounds save goes on, and its answer is adopted (8.18).
const mapBoundsState = {value: null, enabled: false, identifier: null, save: null, accepted: null, current: null, reviewing: false,
  held: null, failure: null, paused: false};

// Only a current writable association can submit a bounds change.
function mapBoundsWritable() {
  const state = mapBoundsState;
  return Boolean(state.enabled && !state.paused && state.value && !state.value.read_only && state.value.association?.mission_key);
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
  // A close keeps its "Closing" state; the failure shows when the controls are enabled again (QA-5.2-F9)
  if (!mapBoundsState.enabled) {
    mapBoundsState.held = error;
    return;
  }
  const text = `${window.ServerManMapWording.boundsNotSaved}: ${error.message}`;
  document.getElementById("map-bounds-error").textContent = text;
  window.ServerManMapLayout.status(text, {notice: "error", announce: true});
}

// Record the bare reason of a failed bounds save, then show the failure; a reload refusal blocks a save at close.
function mapBoundsRefused(error) {
  const words = window.ServerManMapWording, unknown = Boolean(error.unconfirmed);
  mapBoundsState.failure = {at: Date.now(), reason: unknown ? words.saveUnconfirmed : window.ServerManMapPlanSave.reason(error),
    reload: unknown || ["REVISION_CONFLICT", "PROFILE_CONTEXT_MISMATCH", "NOT_FOUND"].includes(error.result?.error?.code)};
  mapBoundsFailed(error);
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
  // A running plan save changes the revision, so the bounds save follows it with the then current draft
  while (window.ServerManMapPlanSave.busy()) await new Promise((resolve) => setTimeout(resolve, 50));
  if (token !== state.save.generation() || !mapBoundsWritable()) return;
  await mapBoundsSubmit(bounds, source, previous);
}

// Save the bounds with the current draft; the confirmation and the close dialog both submit here.
async function mapBoundsSubmit(bounds, source, previous) {
  const state = mapBoundsState, helpers = window.ServerManMapBoundsPlan;
  try {
    const current = state.current();
    if (!current.plan && !state.identifier) state.identifier = current.draft_identifier ?? helpers.identifier();
    const candidate = helpers.candidate(current, bounds, source, state.identifier);
    await state.save.run(candidate, current.revision, (plan, result) => state.accepted(plan, result, previous));
  } catch (error) { mapBoundsRefused(error); }
}

// Typed bounds that differ from the bounds of the draft are unsaved work (R15, Product Owner 2026-10-14 00:52:59).
// savable: valid values that a manual bounds save can still submit; review: valid values that need the placement review.
function mapBoundsTyped() {
  const state = mapBoundsState, helpers = window.ServerManMapBoundsPlan, current = state.current?.();
  const saved = current?.plan?.terrain?.bounds ?? null, fields = {};
  for (const name of helpers.fields) {
    // The check reads the text only; it never marks a field invalid
    fields[name] = {value: document.getElementById(`map-bounds-${name}`).value, setAttribute: () => {}};
  }
  const changed = helpers.fields.some((name) => {
    const text = fields[name].value.trim();
    return saved ? !(text !== "" && Number(text) === saved[name]) : text !== "";
  });
  const bounds = changed ? helpers.read(fields).bounds : null;
  const review = Boolean(bounds && helpers.needsReview(current, bounds));
  const savable = Boolean(bounds && !state.value?.read_only && state.value?.association?.mission_key && !review);
  return {changed, savable, review, bounds};
}

// Initialize one form; native values are text inputs so browser steps cannot narrow D1.
function mapBoundsInit(accepted, current = () => mapBoundsState.value, loads = () => 0) {
  const state = mapBoundsState;
  if (state.save) return;
  state.accepted = accepted;
  state.current = current;
  state.save = window.ServerManMapBoundsSave.create(mapBoundsRefresh, mapBoundsRefused, loads);
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
  // A load replaces the state that a held or recorded failure described
  state.held = null;
  state.failure = null;
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
  if (enabled && state.held) {
    const error = state.held;
    state.held = null;
    mapBoundsFailed(error);
  }
}

// The controller has no public host method and does not change Apply policy.
window.ServerManMapBounds = Object.freeze({init: mapBoundsInit, load: mapBoundsLoad,
  enabled: mapBoundsEnabled, reviewHistory: mapBoundsReviewHistory, busy: () => Boolean(mapBoundsState.save?.busy()),
  pause: (value) => { mapBoundsState.paused = value; if (mapBoundsState.save) mapBoundsRefresh(); },
  // The close dialog (5.4): typed bounds, their save while the editor closes, and their discard
  typed: mapBoundsTyped, reset: () => mapBoundsLoad(mapBoundsState.current()), failure: () => mapBoundsState.failure,
  saveTyped: () => { const typed = mapBoundsTyped(); return typed.savable ? mapBoundsSubmit(typed.bounds, "manual", mapBoundsState.value) : null; }});
