// Choose, load and draw the optional background image of the plan (R04); the window never sees a path.
"use strict";

// Images are kept by SHA-256; only the shown image and one earlier image stay decoded.
const mapBackgroundState = {model: null, enabled: false, generation: 0, missionKey: null, background: null,
  stored: null, cache: new Map(), reasons: new Map(), selection: null, preview: null, dim: false,
  loading: false, notice: null};

// The shown image is the decoded image of the draft's background reference.
function mapBackgroundShown() {
  const state = mapBackgroundState;
  return state.background ? state.cache.get(state.background.sha256) ?? null : null;
}

// Keep the decoded image and drop older ones, so at most two large images stay in memory.
function mapBackgroundKeep(image) {
  const cache = mapBackgroundState.cache;
  cache.delete(image.sha256);
  cache.set(image.sha256, image);
  while (cache.size > 2) cache.delete(cache.keys().next().value);
}

// Only a writable draft with a plan can take a new image.
function mapBackgroundWritable() {
  const status = mapBackgroundState.model?.status();
  return Boolean(mapBackgroundState.enabled && status?.writable && status.plan);
}

// Status text, the choose label and the Dim image control follow the shown state.
function mapBackgroundRender() {
  const state = mapBackgroundState, words = window.ServerManMapWording, shown = mapBackgroundShown();
  const reason = state.background && !shown ? state.reasons.get(state.background.sha256) ?? null : null;
  const text = state.loading ? words.imageLoading : (state.notice ?? (reason ? `${reason} ${words.imageKept}` :
    (shown ? words.imageShown(shown.file_name) : (state.background ? words.imageLoading : words.noImage))));
  document.getElementById("map-background-status").textContent = text;
  const choose = document.getElementById("map-background-choose");
  choose.textContent = reason || state.notice ? words.chooseAgain : words.chooseImage;
  choose.disabled = state.loading || !mapBackgroundWritable();
  document.getElementById("map-background-dim").disabled = !shown;
  window.ServerManMapCalibration?.refresh();
  window.ServerManMapRaster.redraw();
}

// Read the stored image of the saved plan; an unavailable image keeps its reference and calibration.
async function mapBackgroundReadStored() {
  const state = mapBackgroundState, token = state.generation, expected = state.background;
  if (!expected || !state.missionKey) return;
  state.loading = true;
  mapBackgroundRender();
  try {
    const read = (part) => window.ServerManMapBridge.readBackground(state.missionKey, null, part);
    const first = await read(0);
    if (token !== state.generation) return;
    if (first?.available === false) {
      state.reasons.set(expected.sha256, window.ServerManMapWording.displayReason("image", first) ?? first.reason);
      return;
    }
    const image = await window.ServerManMapBackgroundParts.read(first, read, () => token === state.generation);
    if (!image) return;
    // The stored file must be the image that the plan names
    if (image.sha256 !== expected.sha256) state.reasons.set(expected.sha256, window.ServerManMapWording.displayReason("image", {reason: "replaced"}));
    else mapBackgroundKeep(image);
  } catch (error) {
    if (token === state.generation) state.reasons.set(expected.sha256, window.ServerManMapWording.displayError("image", error));
  } finally {
    if (token === state.generation) { state.loading = false; mapBackgroundRender(); }
  }
}

// The host dialog registers the file and answers with part 0 and the selection token.
async function mapBackgroundChoose() {
  const state = mapBackgroundState;
  if (!mapBackgroundWritable() || state.loading || !state.missionKey) return;
  const token = ++state.generation, words = window.ServerManMapWording;
  state.loading = true;
  state.notice = null;
  mapBackgroundRender();
  try {
    const first = await window.ServerManMapBridge.selectBackground(state.missionKey);
    if (token !== state.generation || first?.cancelled) return;
    const read = (part) => window.ServerManMapBridge.readBackground(state.missionKey, first.selection_id, part);
    const image = await window.ServerManMapBackgroundParts.read(first, read, () => token === state.generation);
    if (!image || !mapBackgroundWritable()) return;
    mapBackgroundKeep(image);
    state.selection = {id: first.selection_id, sha256: image.sha256};
    // The same image keeps its calibration; a new image starts fitted to the terrain bounds
    const plan = state.model.snapshot(), current = plan.background;
    const calibration = current?.sha256 === image.sha256 && current.calibration ? current.calibration :
      window.ServerManMapCalibrationMath.fit(plan.terrain.bounds, image.pixel_width, image.pixel_height);
    state.model.setBackground(words.imageAction, {file_name: image.file_name, sha256: image.sha256,
      byte_size: image.byte_size, pixel_width: image.pixel_width, pixel_height: image.pixel_height, path: null, calibration});
  } catch (error) {
    if (token === state.generation) state.notice = `${window.ServerManMapWording.displayError("image", error)} ${words.imageKept}`;
  } finally {
    if (token === state.generation) { state.loading = false; mapBackgroundRender(); }
  }
}

// Draw the image with the view transform times the calibration; Dim image draws it at alpha 0.5.
function mapBackgroundDraw(frame) {
  const state = mapBackgroundState, shown = mapBackgroundShown();
  const calibration = mapBackgroundCalibration();
  if (!shown || !calibration) return false;
  const {context, state: view, ratio} = frame, {a, b, c, d, e, f} = calibration, scale = view.scale;
  // Screen x = offsetX + scale·X and screen y = offsetY − scale·Z, with X and Z from the calibration
  context.setTransform(ratio * scale * a, -ratio * scale * d, ratio * scale * b, -ratio * scale * e,
    ratio * (view.offsetX + scale * c), ratio * (view.offsetY - scale * f));
  context.globalAlpha = state.dim ? 0.5 : 1;
  context.imageSmoothingEnabled = true;
  context.drawImage(shown.image, 0, 0);
  return true;
}

// The preview of a running calibration replaces the stored values for display only.
function mapBackgroundCalibration() {
  const state = mapBackgroundState, shown = mapBackgroundShown();
  if (!shown) return null;
  if (state.preview) return state.preview;
  if (state.background.calibration) return state.background.calibration;
  // A reference without calibration shows fitted to the current bounds until it is calibrated
  const bounds = state.model?.snapshot()?.terrain?.bounds;
  return bounds ? window.ServerManMapCalibrationMath.fit(bounds, shown.pixel_width, shown.pixel_height) : null;
}

// Follow the draft: undo and redo can change the image reference or its calibration.
function mapBackgroundDraft(plan) {
  const state = mapBackgroundState, next = plan?.background ?? null;
  if (JSON.stringify(next) === JSON.stringify(state.background)) {
    mapBackgroundRender();
    return;
  }
  state.background = structuredClone(next);
  if (next && !state.cache.has(next.sha256) && next.sha256 === state.stored && !state.reasons.has(next.sha256)) {
    mapBackgroundReadStored();
  } else if (next && !state.cache.has(next.sha256) && !state.reasons.has(next.sha256)) {
    state.reasons.set(next.sha256, window.ServerManMapWording.displayReason("image", {reason: "missing"}));
  }
  mapBackgroundRender();
}

// An authoritative load abandons every transfer and reads the stored image again.
function mapBackgroundLoad(value) {
  const state = mapBackgroundState;
  state.generation += 1;
  state.loading = false;
  state.selection = null;
  state.preview = null;
  state.notice = null;
  state.reasons.clear();
  // The stored file is checked again, so a missing or replaced file is explained after each load
  state.cache.clear();
  state.missionKey = value?.association?.mission_key ?? null;
  state.background = structuredClone(value?.plan?.background ?? null);
  state.stored = state.background?.sha256 ?? null;
  if (state.background) mapBackgroundReadStored();
  mapBackgroundRender();
}

// Calibration and saves use this narrow surface; no host path or file read is exposed.
window.ServerManMapBackground = Object.freeze({
  init: (model) => {
    const state = mapBackgroundState, words = window.ServerManMapWording;
    state.model = model;
    document.getElementById("map-image-title").textContent = words.imageTitle;
    document.getElementById("map-background-dim-label").textContent = words.dimImage;
    document.getElementById("map-background-choose").addEventListener("click", mapBackgroundChoose);
    const dim = document.getElementById("map-background-dim");
    // Dim image is display only: it starts off at each open and is never stored
    dim.checked = false;
    dim.addEventListener("change", () => { state.dim = dim.checked; window.ServerManMapRaster.redraw(); });
    window.ServerManMapRaster.register("image", mapBackgroundDraw);
    mapBackgroundRender();
  },
  load: mapBackgroundLoad, draft: mapBackgroundDraft, choose: mapBackgroundChoose,
  enabled: (enabled) => {
    const state = mapBackgroundState, next = state.background;
    state.enabled = enabled;
    if (!enabled) { state.generation += 1; state.loading = false; }
    // A cancelled close stopped the stored read; read the stored image again, as the tier layer does
    else if (next && !state.loading && next.sha256 === state.stored && !state.cache.has(next.sha256) &&
        !state.reasons.has(next.sha256)) mapBackgroundReadStored();
    mapBackgroundRender();
  },
  current: () => ({shown: mapBackgroundShown(), background: structuredClone(mapBackgroundState.background),
    calibration: mapBackgroundCalibration()}),
  preview: (calibration) => { mapBackgroundState.preview = calibration ? {...calibration} : null; window.ServerManMapRaster.redraw(); },
  // A save sends the token only while the draft names the chosen image.
  selection: (plan) => (plan?.background && plan.background.sha256 === mapBackgroundState.selection?.sha256 ?
    mapBackgroundState.selection.id : null),
});
