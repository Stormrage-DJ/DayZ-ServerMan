// Wire authoritative loads, shared draft actions and the current explicit bounds save.
"use strict";

// Keep document state local so future modules can consume the loaded snapshot.
const mapEditorState = {loaded: null, closing: false, loading: false, model: null, terrain: null};

// Draft rendering never estimates the backend's last-applied comparison.
function mapDraftChanged(value) {
  // Autosave decides the wait and the save status first, so the redraw below shows current refusal marks
  if (mapEditorState.loaded && !mapEditorState.closing && !mapEditorState.loading) window.ServerManMapAutosave.changed(value);
  window.ServerManMapLayers.render();
  // A load, a close or a change of the dragged zone ends a drag without an action (QA-4.5-F2)
  window.ServerManMapDrag.sync();
  // The tools keep this status copy for drawing; their refresh redraws the vector layer
  window.ServerManMapTools.refresh(value);
  // After the tools, so the tier control sees the current tool availability (QA-4.6-F7)
  window.ServerManMapProperties.render();
  if (!mapEditorState.loaded) return;
  const current = {...mapEditorState.loaded, ...value};
  const terrain = JSON.stringify(value.plan?.terrain ?? null);
  if (terrain !== mapEditorState.terrain) {
    mapEditorState.terrain = terrain;
    window.ServerManMapViewportUi.load(current);
    window.ServerManMapBounds.load(current);
  }
  window.ServerManMapLayout.draftChanges(window.ServerManMapAutosave.draft(value));
  // Undo, redo and calibration can change the image reference; the overlays and objects stay
  window.ServerManMapBackground.draft(value.plan);
}

// Pass one shared model to rows and keys, retaining the existing host API surface.
function mapInitModel() {
  if (mapEditorState.model) return;
  mapEditorState.model = window.ServerManMapModel.create(mapDraftChanged,
    (before, after) => window.ServerManMapBounds.reviewHistory(before, after));
  window.ServerManMapBounds.init(mapAdoptBounds, () => ({...mapEditorState.loaded, ...mapEditorState.model.status()}),
    () => mapEditorState.model.loads());
  window.ServerManMapLayers.init(mapEditorState.model);
  window.ServerManMapKeys.init(mapEditorState.model);
  window.ServerManMapPlanSave.init(mapEditorState.model);
  // The loaded text keeps its notice kind, so reload guidance shows in full again after an undo (QA-5.2-F8)
  window.ServerManMapAutosave.init(mapEditorState.model, (value) => {
    const current = {...mapEditorState.loaded, ...value};
    return [mapLoadStatus(current), mapLoadNotice(current)];
  });
  window.ServerManMapTools.init(mapEditorState.model);
  window.ServerManMapTiers.init();
  window.ServerManMapBackground.init(mapEditorState.model);
  window.ServerManMapCalibration.init(mapEditorState.model);
  // After the calibration, so its Escape and its pick clicks come first (4.5)
  window.ServerManMapDrag.init(mapEditorState.model);
  window.ServerManMapAbundance.init(mapEditorState.model);
  window.ServerManMapProperties.init(mapEditorState.model);
  window.ServerManMapInspect.init(mapEditorState.model);
  window.ServerManMapSetAside.init(mapEditorState.model);
}

// Display reads follow the editor's enabled state; closing drops their late answers.
function mapDisplayEnabled(enabled) {
  window.ServerManMapTiers.enabled(enabled);
  window.ServerManMapBackground.enabled(enabled);
  // The set-aside action also stops while loading or closing, and a cancelled close may load again (5.3)
  window.ServerManMapSetAside.enabled(enabled);
  if (!enabled) window.ServerManMapCalibration.cancel();
}

// Report the current state without saving or changing the loaded document.
function mapLoadStatus(value) {
  const words = window.ServerManMapWording;
  // Backend details include stale-record reload guidance that must stay visible.
  const status = value.read_only ? words.readOnly(value.read_only_reason) :
    (!value.plan ? words.boundsRequired : (value.state === "MISSING" ? words.unsaved : words.loaded));
  const detail = words.detail(value);
  return detail ? `${status} ${detail}` : status;
}

// A read-only reason and reload guidance also show in full on the map.
function mapLoadNotice(value) {
  return value.read_only || value.detail ? "info" : null;
}

// Keep the submitted snapshot locally, with only actual returned save metadata.
function mapAdoptBounds(plan, result) {
  // A load drops the answer before this callback; an answer during a close is committed, so it is adopted (QA-5.2-F5)
  if (!mapEditorState.loaded) return;
  const previous = mapEditorState.loaded;
  const value = {...previous, plan, association: result.association, revision: result.revision,
    state: "VALID", draft_changes: result.draft_changes, interrupted_evidence: result.interrupted_evidence};
  mapEditorState.loaded = value;
  window.ServerManMapLayout.identity(value);
  mapEditorState.model.acceptBounds(plan, result);
  // A close keeps its controls disabled and its "Closing" state; the release shows the current state
  if (mapEditorState.closing) return;
  window.ServerManMapBounds.enabled(true);
  const status = mapEditorState.model.status().dirty ? window.ServerManMapWording.savedSnapshot : window.ServerManMapWording.boundsSaved;
  window.ServerManMapLayout.status(status);
  return status;
}

// Render only the latest load, retaining explicit failure and read-only states.
async function mapLoad() {
  if (mapEditorState.closing) return;
  window.ServerManMapViewportUi.init();
  mapInitModel();
  mapEditorState.loading = true;
  window.ServerManMapAutosave.reset();
  mapEditorState.model.enabled(false);
  window.ServerManMapBounds.enabled(false);
  window.ServerManMapViewportUi.enabled(false);
  mapDisplayEnabled(false);
  const region = document.getElementById("map-editor");
  region.setAttribute("aria-busy", "true");
  window.ServerManMapLayout.status(window.ServerManMapWording.loading, {announce: true});
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
    // The tier overlay and the stored image read in the background; editing does not wait for them
    window.ServerManMapTiers.load(value);
    window.ServerManMapBackground.load(value);
    mapEditorState.terrain = JSON.stringify(value.plan?.terrain ?? null);
    mapEditorState.model.load(value);
    window.ServerManMapLayers.reset();
    mapEditorState.loading = false;
    mapEditorState.model.enabled(true);
    window.ServerManMapBounds.enabled(true);
    mapDisplayEnabled(true);
    // A read-only reason and reload guidance also show in full on the map
    window.ServerManMapLayout.status(mapLoadStatus(value), {notice: mapLoadNotice(value), announce: true});
    window.ServerManMapLayout.draftChanges(value.draft_changes);
    window.ServerManMapSetAside.load(value);
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
    window.ServerManMapTiers.load(null);
    window.ServerManMapBackground.load(null);
    window.ServerManMapSetAside.load(null);
    document.body.dataset.mapReady = "false";
    region.setAttribute("aria-busy", "false");
    window.ServerManMapLayout.status(error.message || window.ServerManMapWording.failed, {notice: "error", announce: true});
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

// The close module owns the settle and the release of this document (map_editor_close.js).
window.ServerManMapClose.init(mapEditorState, mapDisplayEnabled, mapLoad);
window.ServerManMapEditor = Object.freeze({
  load: mapLoad,
  settleForClose: (reason, join) => window.ServerManMapClose.settle(reason, join),
  releaseClose: () => window.ServerManMapClose.release(),
});

document.addEventListener("DOMContentLoaded", mapStart, {once: true});
window.addEventListener("pywebviewready", mapLoad);
