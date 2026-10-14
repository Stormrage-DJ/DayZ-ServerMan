// Calibrate the map image with three picked points; only the image moves, never objects or the grid (R04).
"use strict";

const mapCalibrationState = {model: null, active: false, points: [], picking: null, preview: null, down: null};

// Native and Both modes take native X and Z; Grid mode takes grid values (UI-UX item 5).
function mapCalibrationGridMode() {
  return window.ServerManMapViewportUi.mode() === "Grid";
}

// Build the three point rows once for each calibration; listeners keep only the row index.
function mapCalibrationRows() {
  const words = window.ServerManMapWording, element = window.ServerManMapDom.element;
  const container = document.getElementById("map-calibration-points");
  container.replaceChildren();
  mapCalibrationState.points.forEach((_point, index) => {
    const row = element("div", "", {class: "map-calibration-point", "data-map-point": String(index)});
    const pick = element("button", words.calibrationPick, {type: "button", class: "button", id: `map-calibration-pick-${index}`});
    pick.addEventListener("click", () => { mapCalibrationState.picking = index; mapCalibrationUpdate(); });
    row.append(element("p", "", {id: `map-calibration-pixel-${index}`}), pick);
    for (const axis of ["first", "second"]) {
      const field = element("div"), id = `map-calibration-${index}-${axis}`;
      const input = element("input", "", {id, type: "text", inputmode: "decimal", autocomplete: "off"});
      // A typed value is an edit when the field changes, not at each key
      input.addEventListener("change", () => mapCalibrationRead(index));
      field.append(element("label", "", {for: id, id: `${id}-label`}), input);
      row.append(field);
    }
    row.append(element("p", "", {id: `map-calibration-beside-${index}`}));
    container.append(row);
  });
  mapCalibrationFields();
}

// Show the stored native value of each point in the current mode's fields.
function mapCalibrationFields() {
  const words = window.ServerManMapWording, grid = mapCalibrationGridMode(), bounds = mapCalibrationBounds();
  mapCalibrationState.points.forEach((point, index) => {
    const labels = grid ? [`${words.grid} X`, `${words.grid} Z`] : ["X (m)", "Z (m)"];
    const values = !point.native ? ["", ""] : (grid ? Object.values(window.ServerManMapCoordinates.grid(point.native, bounds)) :
      [point.native.x, point.native.z]);
    ["first", "second"].forEach((axis, place) => {
      document.getElementById(`map-calibration-${index}-${axis}-label`).textContent = labels[place];
      document.getElementById(`map-calibration-${index}-${axis}`).value = Number.isFinite(values[place]) ? String(values[place]) : "";
    });
  });
  mapCalibrationUpdate();
}

// The current plan bounds convert grid values; they never change during calibration.
function mapCalibrationBounds() {
  return window.ServerManMapCoordinates.bounds(mapCalibrationState.model?.snapshot()?.terrain?.bounds);
}

// Read one row's fields into native metres; invalid text leaves the point incomplete.
function mapCalibrationRead(index) {
  const first = Number(document.getElementById(`map-calibration-${index}-first`).value.trim() || NaN);
  const second = Number(document.getElementById(`map-calibration-${index}-second`).value.trim() || NaN);
  const bounds = mapCalibrationBounds(), point = mapCalibrationState.points[index];
  const native = !Number.isFinite(first) || !Number.isFinite(second) ? null :
    (mapCalibrationGridMode() ? window.ServerManMapCoordinates.fromGrid({h: first, v: second}, bounds) : {x: first, z: second});
  point.native = native && Number.isFinite(native.x) && Number.isFinite(native.z) ? native : null;
  mapCalibrationUpdate();
}

// Solve when every point is complete, preview the result and show the non-blocking warnings.
function mapCalibrationUpdate() {
  const state = mapCalibrationState, words = window.ServerManMapWording, bounds = mapCalibrationBounds();
  if (!state.active) return;
  state.points.forEach((point, index) => {
    document.getElementById(`map-calibration-pixel-${index}`).textContent = `${words.calibrationPoint(index + 1)}: ` +
      (point.pixel ? words.calibrationPixel(point.pixel.x, point.pixel.y) : words.calibrationNoPixel);
    document.getElementById(`map-calibration-pick-${index}`).setAttribute("aria-pressed", String(state.picking === index));
    // The other system shows beside the typed values; a failed conversion shows nothing
    const grid = point.native && bounds ? window.ServerManMapCoordinates.grid(point.native, bounds) : null;
    const beside = !grid || !Number.isFinite(grid.h) || !Number.isFinite(grid.v) ? "" : (mapCalibrationGridMode() ?
      `${words.native} X ${point.native.x.toFixed(3)} m, Z ${point.native.z.toFixed(3)} m` :
      `${words.grid} X ${grid.h.toFixed(4)}, Z ${grid.v.toFixed(4)}`);
    document.getElementById(`map-calibration-beside-${index}`).textContent = beside;
  });
  const complete = state.points.every((point) => point.pixel && point.native);
  let result = state.picking === null ? words.calibrationEnter : words.calibrationNext(state.picking + 1), warning = "";
  if (complete) {
    const solved = window.ServerManMapCalibrationMath.solve(state.points);
    state.preview = solved.calibration ?? null;
    result = solved.error === "collinear" ? words.calibrationCollinear : (solved.error ? words.calibrationSingular : words.calibrationReady);
    const checks = solved.calibration ? window.ServerManMapCalibrationMath.warnings(solved.calibration) : {};
    warning = [checks.shear || checks.scale ? words.calibrationSkew : "", checks.mirror ? words.calibrationMirror : ""].join(" ").trim();
  } else if (state.preview && !state.points.some((point) => point.pixel || point.native)) {
    result = words.calibrationFitted;
  } else state.preview = null;
  document.getElementById("map-calibration-result").textContent = result;
  document.getElementById("map-calibration-warning").textContent = warning;
  document.getElementById("map-calibration-accept").disabled = !state.preview;
  window.ServerManMapBackground.preview(state.preview);
}

// A pick click maps the screen point through the shown calibration to an image pixel and never selects.
function mapCalibrationClick(event) {
  const state = mapCalibrationState, down = state.down;
  state.down = null;
  if (!state.active || state.picking === null || event.button !== 0) return;
  // A pointer that moved 4 pixels or more is a drag, not a pick
  if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) >= 4) return;
  const element = document.getElementById("map-viewport"), rectangle = element.getBoundingClientRect();
  const native = window.ServerManMapViewportUi.view()?.toNative({x: event.clientX - rectangle.left - element.clientLeft,
    y: event.clientY - rectangle.top - element.clientTop});
  const calibration = window.ServerManMapBackground.current().calibration;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (!native || !calibration) return;
  state.points[state.picking].pixel = window.ServerManMapCalibrationMath.inverse(calibration, native);
  const next = state.points.findIndex((point) => !point.pixel);
  state.picking = next < 0 ? null : next;
  mapCalibrationUpdate();
}

// Start, fit, accept and cancel; Accept stores the solved values as one undo action.
function mapCalibrationStart(fit = false) {
  const state = mapCalibrationState, shown = window.ServerManMapBackground.current().shown, bounds = mapCalibrationBounds();
  if (!shown || !bounds) return;
  state.active = true;
  state.points = [0, 1, 2].map(() => ({pixel: null, native: null}));
  state.picking = fit ? null : 0;
  state.preview = fit ? window.ServerManMapCalibrationMath.fit(bounds, shown.pixel_width, shown.pixel_height) : null;
  document.getElementById("map-calibration").hidden = false;
  mapCalibrationRows();
  mapCalibrationRefresh();
}

// End a calibration; only Accept changes the draft, and Cancel keeps the old values.
function mapCalibrationFinish(accept) {
  const state = mapCalibrationState, background = window.ServerManMapBackground.current().background;
  if (!state.active) return;
  if (accept && state.preview && background) {
    state.model.setBackground(window.ServerManMapWording.calibrateAction, {...background, calibration: {...state.preview}});
  }
  state.active = false;
  state.picking = null;
  state.preview = null;
  document.getElementById("map-calibration").hidden = true;
  window.ServerManMapBackground.preview(null);
  mapCalibrationRefresh();
}

// The start button needs a shown image and a writable draft.
function mapCalibrationRefresh() {
  const status = mapCalibrationState.model?.status(), shown = window.ServerManMapBackground?.current().shown;
  const usable = Boolean(status?.writable && status.plan && shown);
  if (mapCalibrationState.active && !usable) mapCalibrationFinish(false);
  document.getElementById("map-calibrate").disabled = mapCalibrationState.active || !usable;
}

// Calibration owns no save and no path; the model owns the undo action.
window.ServerManMapCalibration = Object.freeze({
  init: (model) => {
    const state = mapCalibrationState, words = window.ServerManMapWording, viewport = document.getElementById("map-viewport");
    state.model = model;
    for (const [id, text] of [["map-calibrate", words.calibrate], ["map-calibration-title", words.calibrate],
      ["map-calibration-guidance", words.calibrationGuidance], ["map-calibration-accept", words.calibrationAccept],
      ["map-calibration-fit", words.calibrationFit], ["map-calibration-cancel", words.calibrationCancel]]) {
      document.getElementById(id).textContent = text;
    }
    document.getElementById("map-calibrate").addEventListener("click", () => mapCalibrationStart());
    document.getElementById("map-calibration-fit").addEventListener("click", () => mapCalibrationStart(true));
    document.getElementById("map-calibration-accept").addEventListener("click", () => mapCalibrationFinish(true));
    document.getElementById("map-calibration-cancel").addEventListener("click", () => mapCalibrationFinish(false));
    document.getElementById("map-coordinate-mode").addEventListener("change", () => { if (state.active) mapCalibrationFields(); });
    viewport.addEventListener("pointerdown", (event) => { state.down = {x: event.clientX, y: event.clientY}; }, true);
    viewport.addEventListener("click", mapCalibrationClick, true);
    // Escape cancels a calibration when no dialog is open and no text field has the focus
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !state.active || document.querySelector("dialog") ||
          event.target?.closest?.("input:not([type=checkbox]):not([type=radio]), textarea, select")) return;
      event.preventDefault();
      mapCalibrationFinish(false);
    });
    mapCalibrationRefresh();
  },
  refresh: mapCalibrationRefresh, cancel: () => mapCalibrationFinish(false),
  state: () => ({active: mapCalibrationState.active, picking: mapCalibrationState.picking,
    points: structuredClone(mapCalibrationState.points),
    preview: mapCalibrationState.preview ? {...mapCalibrationState.preview} : null}),
});
