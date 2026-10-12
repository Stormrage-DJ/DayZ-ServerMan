// Own full-precision native-to-screen view state; geometry belongs to the plan.
"use strict";

// Each window owns its view state and notifies rendering after navigation.
function mapCreateViewport(changed) {
  const state = {bounds: null, width: 0, height: 0, scale: 0, fitScale: 0, offsetX: 0, offsetY: 0, failure: null};

  // Native Z rises while screen Y falls; origin offsets remain in CSS pixels.
  const toScreen = (point) => {
    if (!state.bounds || !(state.scale > 0) || !(state.width > 0) || !(state.height > 0)) return {x: null, y: null};
    const axes = state.failure?.axes;
    const x = state.offsetX + point.x * state.scale, y = state.offsetY - point.z * state.scale;
    return {x: !axes?.x && Number.isFinite(point.x) && Number.isFinite(x) ? x : null,
      y: !axes?.z && Number.isFinite(point.z) && Number.isFinite(y) ? y : null};
  };
  const toNativeAxes = (point) => {
    if (!state.bounds || !(state.scale > 0)) return null;
    const native = {x: (point.x - state.offsetX) / state.scale, z: (state.offsetY - point.y) / state.scale};
    const axes = state.failure?.axes;
    return {x: !axes?.x && Number.isFinite(point.x) && Number.isFinite(native.x) ? native.x : null,
      z: !axes?.z && Number.isFinite(point.y) && Number.isFinite(native.z) ? native.z : null};
  };

  // Whole coordinate targets require both checked axes; future edit consumers must use this seam.
  const toNative = (point) => {
    const native = toNativeAxes(point);
    return native && [native.x, native.z].every(Number.isFinite) ? native : null;
  };

  // Loaded bounds stay exact even when a dependent operation cannot use them.
  const assess = () => {
    const axes = state.bounds ? window.ServerManMapCoordinates.axes(state.bounds) : null;
    state.failure = axes && (axes.x || axes.z) ? Object.freeze({operation: "native/grid conversion", axes}) : null;
  };

  // Full bounds fit at or below the absolute ceiling; tiny terrain may leave unused space.
  const measureFit = () => {
    state.fitScale = state.bounds && state.width > 0 && state.height > 0 ?
      Math.min(state.width / state.bounds.width, state.height / state.bounds.height, 20) : 0;
  };

  // Keep the chosen native point at its CSS screen anchor when scale changes.
  const anchor = (native, screen, scale) => {
    const offsetX = screen.x - native.x * scale;
    const offsetY = screen.y + native.z * scale;
    if (![scale, offsetX, offsetY].every(Number.isFinite)) return false;
    state.scale = scale;
    state.offsetX = offsetX;
    state.offsetY = offsetY;
    return true;
  };

  // Fit complete bounds with the native centre at the viewport centre.
  const fit = () => {
    if (!state.bounds || !state.fitScale || state.failure) return false;
    if (!anchor({x: state.bounds.x_min + state.bounds.width / 2, z: state.bounds.z_min + state.bounds.height / 2},
      {x: state.width / 2, y: state.height / 2}, state.fitScale)) return false;
    changed();
    return true;
  };

  // Replace copied extents only when their values change; reload keeps an unchanged view.
  const setBounds = (value) => {
    const bounds = window.ServerManMapCoordinates.bounds(value);
    if (JSON.stringify(bounds) === JSON.stringify(state.bounds)) return;
    state.bounds = bounds;
    assess();
    measureFit();
    if (!fit()) {
      if (!state.failure) state.scale = 0;
      changed();
    }
  };

  // Preserve the visible native centre across resize, with the current fit-derived clamp.
  const resize = (width, height) => {
    const centre = toNative({x: state.width / 2, y: state.height / 2});
    state.width = Number.isFinite(width) ? Math.max(0, width) : 0;
    state.height = Number.isFinite(height) ? Math.max(0, height) : 0;
    assess();
    measureFit();
    if (state.failure && state.fitScale) {
      changed();
      return;
    }
    if (centre && state.fitScale) {
      anchor(centre, {x: width / 2, y: height / 2}, Math.max(state.fitScale / 2, Math.min(20, state.scale)));
    } else if (state.fitScale) {
      fit();
      return;
    } else {
      state.scale = 0;
    }
    changed();
  };

  // Wheel deltas use the accepted 1.2 factor per 100 CSS pixels, including Ctrl-wheel.
  const zoom = (screen, deltaPixels) => {
    const native = toNative(screen);
    if (!native || !Number.isFinite(deltaPixels)) return false;
    const scale = Math.max(state.fitScale / 2, Math.min(20, state.scale * Math.pow(1.2, -deltaPixels / 100)));
    if (!anchor(native, screen, scale)) return false;
    changed();
    return true;
  };

  // Panning changes CSS offsets only and cannot alter stored coordinates.
  const pan = (x, y) => {
    const offsetX = state.offsetX + x, offsetY = state.offsetY + y;
    if (!state.bounds || !state.scale || state.failure || ![x, y, offsetX, offsetY].every(Number.isFinite)) return false;
    state.offsetX = offsetX;
    state.offsetY = offsetY;
    changed();
    return true;
  };

  return Object.freeze({setBounds, resize, fit, zoom, pan, toScreen, toNative, toNativeAxes,
    snapshot: () => Object.freeze({...state})});
}

// A factory keeps view state independent between editor documents.
window.ServerManMapViewport = Object.freeze({create: mapCreateViewport});
