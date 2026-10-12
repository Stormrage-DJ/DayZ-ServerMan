// Wire local navigation controls and cursor text to view and rendering modules.
"use strict";

// Display state lasts for this window and is never stored in the loaded plan.
const mapViewportUiState = {view: null, mode: "Both", cursor: null, enabled: false, input: null};

// Redraw the plain view and coordinates without changing polite status announcements.
function mapViewportUiDraw() {
  const state = mapViewportUiState;
  if (!state.view) return;
  window.ServerManMapViewportRender.draw(state.view, state.mode);
  const snapshot = state.view.snapshot();
  const measured = snapshot.bounds && snapshot.width > 0 && snapshot.height > 0;
  document.getElementById("map-fit").disabled = !state.enabled || !measured || !snapshot.scale || Boolean(snapshot.failure);
  document.getElementById("map-coordinate-mode").disabled = !state.enabled || !measured;
  const point = state.cursor ? state.view.toNativeAxes(state.cursor) : null;
  const text = window.ServerManMapCoordinates.display(point, snapshot.bounds, snapshot.scale, state.mode);
  const coordinates = document.getElementById("map-coordinates");
  coordinates.textContent = text;
  coordinates.title = text;
}

// Unknown bounds, loading and close freeze navigation while preserving local mode.
function mapViewportUiEnabled(enabled) {
  mapViewportUiState.enabled = enabled;
  mapViewportUiDraw();
  if (!enabled) mapViewportUiState.input?.reset();
}

// Initialize listeners once; resizing never installs duplicate gestures or resets mode.
function mapViewportUiInit() {
  const state = mapViewportUiState;
  if (state.view) return;
  const words = window.ServerManMapWording;
  const element = document.getElementById("map-viewport");
  state.view = window.ServerManMapViewport.create(mapViewportUiDraw);
  const fit = document.getElementById("map-fit");
  fit.textContent = words.fitMap;
  fit.addEventListener("click", () => { if (state.enabled) state.view.fit(); });
  document.getElementById("map-coordinate-mode-label").textContent = words.coordinateMode;
  document.getElementById("map-navigation-help").textContent = words.navigationHelp;
  const mode = document.getElementById("map-coordinate-mode");
  for (const option of mode.options) option.textContent = words[option.value.toLowerCase()];
  mode.addEventListener("change", () => {
    if (!["Grid", "Native", "Both"].includes(mode.value)) return;
    state.mode = mode.value;
    mapViewportUiDraw();
  });
  state.input = window.ServerManMapViewportInput.attach(element, state.view, (point) => {
    state.cursor = point;
    mapViewportUiDraw();
  }, () => state.enabled && Boolean(state.view.snapshot().scale) && !state.view.snapshot().failure);
  // CSS dimensions, not backing-store pixels or calibration, govern the transform.
  const resize = () => state.view.resize(element.clientWidth, element.clientHeight);
  new ResizeObserver(resize).observe(element);
  resize();
  mapViewportUiEnabled(false);
}

// Load only authoritative plan extents; missing plans do not borrow detected guesses.
function mapViewportUiLoad(value) {
  mapViewportUiState.cursor = null;
  mapViewportUiState.view.setBounds(value?.plan?.terrain?.bounds);
  mapViewportUiEnabled(Boolean(value));
  mapViewportUiDraw();
}

// Internal display integration stays separate from the editor's public host API surface.
window.ServerManMapViewportUi = Object.freeze({init: mapViewportUiInit, load: mapViewportUiLoad,
  enabled: mapViewportUiEnabled, redraw: mapViewportUiDraw,
  view: () => mapViewportUiState.view});
