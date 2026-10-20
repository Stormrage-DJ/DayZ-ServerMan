// Move, resize and vertex drags of the selected zone; the preview stays outside the draft until release (R12).
"use strict";

// pending: a press on the selected zone that is not a drag yet; drag: the active drag with its preview;
// suppress: the click that follows a drag must not place or select.
const mapDragState = {model: null, pending: null, drag: null, suppress: false};

// A pointer must move 4 CSS pixels to begin a drag.
// Each 8 px handle has a 12 CSS pixel reach from its centre, giving a 24 px hit area.
const mapDragThreshold = 4;
const mapDragReach = 12;

// The stored geometry fields of one zone or encounter, as one comparable value; an encounter moves like a circle (7.3)
function mapDragGeometry(item) {
  return item.kind !== "loot_polygon" ? {centre: item.centre, radius: item.radius} : {vertices: item.vertices};
}

// Screen points of the handles: the east edge point of a circle, or each outline vertex.
// A locked zone and a draft that cannot change show no handles.
function mapDragCandidates(view, item) {
  if (!item || item.locked || !mapDragState.model?.editable() || !window.ServerManMapVector.drawable(item)) return [];
  // An encounter without a spawn radius has no resize handle
  const circle = item.kind !== "loot_polygon", round = Number.isFinite(item.radius);
  const points = circle ? (round ? [{x: item.centre.x + item.radius, z: item.centre.z}] : []) : item.vertices;
  return points.map((point, index) => ({grip: circle ? "radius" : "vertex", index, ...view.toScreen(point)}))
    .filter((handle) => Number.isFinite(handle.x) && Number.isFinite(handle.y));
}

// Visible handles follow Control; raw candidates remain available for ordinary near-handle movement.
function mapDragHandles(view, item) {
  return window.ServerManMapDragInput.held() ? mapDragCandidates(view, item) : [];
}

// The wording key of the advisory D1 reason that refuses a shape, or null; the backend check stays authoritative
function mapDragProblem(item, bounds) {
  const outline = window.ServerManMapOutline;
  if (item.kind !== "loot_polygon") return outline.inside(bounds, item.centre) ? null : "dragOutside";
  if (!item.vertices.every((point) => outline.inside(bounds, point))) return "dragOutside";
  if (item.vertices.some((point, index) => outline.tooClose(item.vertices.slice(index + 1), point))) return "dragClose";
  return {outlineNeedsPoints: "dragCrosses", outlineCrosses: "dragCrosses", outlineArea: "dragArea"}[outline.problem(item.vertices)] ?? null;
}

// The preview for one pointer position; a pointer back at its start gives the start geometry exactly
function mapDragApply(drag, native) {
  const item = structuredClone(drag.item), dx = native.x - drag.from.x, dz = native.z - drag.from.z;
  if (dx === 0 && dz === 0) return item;
  const shift = (point) => ({x: point.x + dx, z: point.z + dz});
  if (drag.kind === "move" && item.kind !== "loot_polygon") item.centre = shift(item.centre);
  else if (drag.kind === "move") item.vertices = item.vertices.map(shift);
  else if (drag.kind === "vertex") item.vertices[drag.index] = shift(item.vertices[drag.index]);
  else {
    // The radius grows by the distance gained from the centre, from 1 m to the larger terrain extent (D1)
    const distance = (point) => Math.hypot(point.x - item.centre.x, point.z - item.centre.z);
    item.radius = Math.min(drag.limit, Math.max(1, item.radius + distance(native) - distance(drag.from)));
  }
  return item;
}

// CSS pixel position of a pointer inside the map viewport
function mapDragPosition(event) {
  const element = document.getElementById("map-viewport"), rectangle = element.getBoundingClientRect();
  return {x: event.clientX - rectangle.left - element.clientLeft, y: event.clientY - rectangle.top - element.clientTop};
}

// A primary press on a handle or inside the selected zone can start a drag; a calibration never does
function mapDragDown(event) {
  const state = mapDragState, view = window.ServerManMapViewportUi.view();
  state.suppress = false;
  state.pending = null;
  if (event.button !== 0 || state.drag || !state.model.editable() || window.ServerManMapCalibration.state().active ||
      document.querySelector("dialog") || document.getElementById("map-viewport").classList.contains("map-panning")) return;
  const plan = state.model.snapshot(), id = window.ServerManMapLayers.selected();
  const item = plan?.objects.find((object) => object.id === id), screen = mapDragPosition(event);
  const native = view?.toNative(screen);
  if (!item || !native || !window.ServerManMapVector.drawable(item)) return;
  // Control edits the nearest handle within reach. An ordinary press there moves the whole selected object.
  const handle = mapDragCandidates(view, item).map((point) => ({...point, gap: Math.hypot(point.x - screen.x, point.y - screen.y)}))
    .filter((point) => point.gap <= mapDragReach).sort((first, second) => first.gap - second.gap)[0];
  const inside = item.kind === "encounter" ? window.ServerManMapEncounters.covers(item, native, view) :
    window.ServerManMapOutline.contains(item, native);
  const kind = event.ctrlKey && handle ? handle.grip : (inside || handle ? (item.locked ? "locked" : "move") : null);
  if (!kind) return;
  const bounds = plan.terrain.bounds;
  state.pending = {pointer: event.pointerId, screen, from: native, kind, index: handle?.index ?? null, item,
    epoch: state.model.epoch(), bounds, limit: Math.max(bounds.width, bounds.height)};
}

// Past the threshold a press becomes a drag; each move replaces the preview and its advisory reason
function mapDragMove(event) {
  const state = mapDragState, words = window.ServerManMapWording, element = document.getElementById("map-viewport");
  mapDragSync();
  const current = state.drag ?? state.pending;
  if (!current || event.pointerId !== current.pointer) return;
  // A primary button that is up ends the press or the drag without an action (QA-4.5-F1)
  if ((event.buttons & 1) === 0) {
    mapDragCancel();
    return;
  }
  const screen = mapDragPosition(event);
  if (!state.drag) {
    if (Math.hypot(screen.x - current.screen.x, screen.y - current.screen.y) < mapDragThreshold) return;
    state.pending = null;
    state.suppress = true;
    // A locked zone refuses the edit and says why (R12)
    if (current.kind === "locked") { window.ServerManMapTools.message(words.objectLocked); return; }
    state.drag = {...current, preview: current.item, problem: null,
      invalid: Boolean(mapDragProblem(current.item, current.bounds))};
    try { element.setPointerCapture(event.pointerId); } catch { /* a pointer that is no longer active has no capture */ }
    element.classList.add("map-dragging");
    window.ServerManMapTools.message("");
  }
  const drag = state.drag, native = window.ServerManMapViewportUi.view().toNative(screen);
  if (!native) return;
  drag.preview = mapDragApply(drag, native);
  // A zone that the window already finds invalid gets no advice; the backend decides (Outline Finish)
  const problem = drag.invalid ? null : mapDragProblem(drag.preview, drag.bounds);
  if (problem !== drag.problem) window.ServerManMapTools.message(problem ? words.dragWarning(words[problem]) : "");
  drag.problem = problem;
  window.ServerManMapViewportUi.redraw();
}

// End the drag display; the draft has not changed
function mapDragEnd() {
  const drag = mapDragState.drag, element = document.getElementById("map-viewport");
  mapDragState.drag = null;
  mapDragState.pending = null;
  if (!drag) return null;
  element.classList.remove("map-dragging");
  if (element.hasPointerCapture(drag.pointer)) element.releasePointerCapture(drag.pointer);
  window.ServerManMapViewportUi.redraw();
  return drag;
}

// Escape, pointer cancellation, an outside release or lost focus cancels the gesture.
// Stored geometry and undo history stay unchanged.
function mapDragCancel() {
  if (mapDragState.drag || mapDragState.pending) mapDragState.suppress = true;
  const drag = mapDragEnd();
  if (drag?.problem) window.ServerManMapTools.message("");
}

// Release: one undo action holds the plan before and after the drag; no change adds none
function mapDragUp(event) {
  const state = mapDragState, words = window.ServerManMapWording, model = state.model;
  mapDragSync();
  if (state.pending?.pointer === event.pointerId) state.pending = null;
  if (state.drag?.pointer !== event.pointerId) return;
  const drag = mapDragEnd();
  if (drag.problem) {
    window.ServerManMapTools.message(words.dragRefused(words[drag.problem]));
    return;
  }
  const label = {move: words.moveAction, radius: words.resizeAction, vertex: words.vertexAction}[drag.kind];
  try {
    model.action(label, (plan) => Object.assign(plan.objects.find((object) => object.id === drag.item.id) ?? {},
      structuredClone(mapDragGeometry(drag.preview))));
  } catch (error) {
    window.ServerManMapTools.message(error.message);
  }
}

// A changed geometry, model epoch or edit gate cancels the gesture before commit.
// The preview is discarded without adding an action (QA-4.5-F2).
function mapDragSync() {
  const state = mapDragState, current = state.drag ?? state.pending, geometry = (item) => JSON.stringify(mapDragGeometry(item));
  if (!current) return;
  const item = state.model.snapshot()?.objects.find((object) => object.id === current.item.id);
  if (current.epoch !== state.model.epoch() || !item || geometry(item) !== geometry(current.item) ||
      !state.model.editable() || !item.visible || (item.locked && current.kind !== "locked") ||
      window.ServerManMapLayers.selected() !== item.id || window.ServerManMapCalibration.state().active ||
      document.querySelector("dialog") || document.getElementById("map-viewport").classList.contains("map-panning")) mapDragCancel();
}

// Wire the map once; the pan handlers consume their own gestures before these listeners
function mapDragInit(model) {
  const state = mapDragState, element = document.getElementById("map-viewport");
  if (state.model) return;
  state.model = model;
  window.ServerManMapDragInput.init(element);
}

// The vector layer draws the preview and the handles; the model owns the undo action.
window.ServerManMapDrag = Object.freeze({init: mapDragInit, handles: mapDragHandles, cancel: mapDragCancel, sync: mapDragSync,
  active: () => Boolean(mapDragState.drag),
  preview: () => mapDragState.drag ? {item: structuredClone(mapDragState.drag.preview), problem: Boolean(mapDragState.drag.problem)} : null});
