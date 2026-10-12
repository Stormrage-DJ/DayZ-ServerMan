// Wire authoritative loads, shared draft actions and the current explicit bounds save.
"use strict";

// Keep document state local so future modules can consume the loaded snapshot.
const mapEditorState = {loaded: null, closing: false, loading: false, model: null, terrain: null};

// Draft rendering never estimates the backend's last-applied comparison.
function mapDraftChanged(value) {
  window.ServerManMapLayers.render();
  if (!mapEditorState.loaded) return;
  const current = {...mapEditorState.loaded, ...value};
  const terrain = JSON.stringify(value.plan?.terrain ?? null);
  if (terrain !== mapEditorState.terrain) {
    mapEditorState.terrain = terrain;
    window.ServerManMapViewportUi.load(current);
    window.ServerManMapBounds.load(current);
  }
  window.ServerManMapLayout.draftChanges(value.draft_changes);
  if (!mapEditorState.closing && !mapEditorState.loading) {
    document.getElementById("map-status").textContent = value.dirty ?
      (value.plan ? window.ServerManMapWording.localUnsaved : window.ServerManMapWording.localNoPlan) : mapLoadStatus(current);
  }
}

// Pass one shared model to rows and keys, retaining the existing host API surface.
function mapInitModel() {
  if (mapEditorState.model) return;
  mapEditorState.model = window.ServerManMapModel.create(mapDraftChanged,
    (before, after) => window.ServerManMapBounds.reviewHistory(before, after));
  window.ServerManMapBounds.init(mapAdoptBounds, () => ({...mapEditorState.loaded, ...mapEditorState.model.status()}));
  window.ServerManMapLayers.init(mapEditorState.model);
  window.ServerManMapKeys.init(mapEditorState.model);
}

// Report the current state without saving or changing the loaded document.
function mapLoadStatus(value) {
  const words = window.ServerManMapWording;
  // Backend details include stale-record reload guidance that must stay visible.
  const status = value.read_only ? words.readOnly(value.read_only_reason) :
    (!value.plan ? words.boundsRequired : (value.state === "MISSING" ? words.unsaved : words.loaded));
  return value.detail ? `${status} ${value.detail}` : status;
}

// Keep the submitted snapshot locally, with only actual returned save metadata.
function mapAdoptBounds(plan, result) {
  // Bounds save generations already reject reload/close answers before this callback.
  if (mapEditorState.closing || !mapEditorState.loaded) return;
  const previous = mapEditorState.loaded;
  const value = {...previous, plan, association: result.association, revision: result.revision,
    state: "VALID", draft_changes: result.draft_changes, interrupted_evidence: result.interrupted_evidence};
  mapEditorState.loaded = value;
  window.ServerManMapLayout.identity(value);
  mapEditorState.model.acceptBounds(plan, result);
  window.ServerManMapBounds.enabled(true);
  const status = mapEditorState.model.status().dirty ? window.ServerManMapWording.savedSnapshot : window.ServerManMapWording.boundsSaved;
  document.getElementById("map-status").textContent = status;
  return status;
}

// Render only the latest load, retaining explicit failure and read-only states.
async function mapLoad() {
  if (mapEditorState.closing) return;
  window.ServerManMapViewportUi.init();
  mapInitModel();
  mapEditorState.loading = true;
  mapEditorState.model.enabled(false);
  window.ServerManMapBounds.enabled(false);
  window.ServerManMapViewportUi.enabled(false);
  const region = document.getElementById("map-editor");
  region.setAttribute("aria-busy", "true");
  window.ServerManMapLayout.status(window.ServerManMapWording.loading);
  window.ServerManMapLayout.draftChanges(null);
  try {
    const answer = await window.ServerManMapBridge.load();
    if (answer.stale || mapEditorState.closing) return;
    const value = answer.value;
    mapEditorState.loaded = value;
    // Profile, mission and terrain remain visible even when the plan is read only.
    window.ServerManMapLayout.identity(value);
    window.ServerManMapViewportUi.load(value);
    window.ServerManMapBounds.load(value);
    mapEditorState.terrain = JSON.stringify(value.plan?.terrain ?? null);
    mapEditorState.model.load(value);
    window.ServerManMapLayers.reset();
    mapEditorState.loading = false;
    mapEditorState.model.enabled(true);
    window.ServerManMapBounds.enabled(true);
    window.ServerManMapLayout.status(mapLoadStatus(value));
    window.ServerManMapLayout.draftChanges(value.draft_changes);
    document.body.dataset.mapReady = "true";
    region.dataset.readOnly = String(value.read_only);
    region.setAttribute("aria-busy", "false");
    document.getElementById("map-viewport").focus();
  } catch (error) {
    mapEditorState.loaded = null;
    mapEditorState.model.load(null);
    mapEditorState.loading = false;
    window.ServerManMapViewportUi.load(null);
    window.ServerManMapBounds.load(null);
    document.body.dataset.mapReady = "false";
    region.setAttribute("aria-busy", "false");
    window.ServerManMapLayout.status(error.message || window.ServerManMapWording.failed);
    window.ServerManMapLayout.draftChanges(null);
  }
}

// Start once the profile-bound native API is connected.
function mapStart() {
  window.ServerManMapLayout.init();
  window.ServerManMapViewportUi.init();
  mapInitModel();
  if (window.pywebview?.api) mapLoad();
}

// Keep the immediate close promise until the accepted later close leaf.
window.ServerManMapEditor = Object.freeze({
  load: mapLoad,
  // Freeze pending loads until native destruction or release of the close request.
  settleForClose: async () => {
    mapEditorState.closing = true;
    mapEditorState.model?.enabled(false);
    window.ServerManMapViewportUi.enabled(false);
    window.ServerManMapBounds.enabled(false);
    window.ServerManMapBridge.invalidateLoads();
    window.ServerManMapLayout.status(window.ServerManMapWording.closing);
    return "close";
  },
  // Restore the loaded status and backend comparison after a cancelled application close.
  releaseClose: () => {
    mapEditorState.closing = false;
    if (mapEditorState.loaded) {
      mapEditorState.loading = false;
      mapEditorState.model.enabled(true);
      window.ServerManMapViewportUi.enabled(true);
      window.ServerManMapBounds.enabled(true);
      // Pending answers stay invalidated; the retained snapshot is no longer loading.
      const current = {...mapEditorState.loaded, ...mapEditorState.model.status()};
      window.ServerManMapLayout.draftChanges(current.draft_changes);
      document.getElementById("map-editor").setAttribute("aria-busy", "false");
      window.ServerManMapLayout.status(current.dirty ?
        (current.plan ? window.ServerManMapWording.localUnsaved : window.ServerManMapWording.localNoPlan) : mapLoadStatus(current));
    } else mapLoad();
  },
});

document.addEventListener("DOMContentLoaded", mapStart, {once: true});
window.addEventListener("pywebviewready", mapLoad);
