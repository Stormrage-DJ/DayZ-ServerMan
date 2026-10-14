// Own the Select, Circle, Polygon and Inspect tools, the outline draft, placement and selection clicks and the tool status.
"use strict";

// The draft outline lives outside the plan and the history, so selection, edits and panning keep it (R12).
const mapToolsState = {model: null, value: null, tool: null, radius: 100, vertices: [], down: null, message: ""};

// Every tool needs confirmed bounds and a usable view transform. Drawing and selection need a writable draft.
// Inspect writes nothing, so it also acts in a read-only draft without a dialog (Product Owner 2026-10-14 00:33:43).
function mapToolsUsable(tool) {
  const view = window.ServerManMapViewportUi.view()?.snapshot(), value = mapToolsState.value;
  const ready = Boolean(value?.plan?.terrain?.bounds_confirmed && view?.scale && !view.failure);
  return ready && (tool === "inspect" ? Boolean(value.enabled && !document.querySelector("dialog")) : Boolean(mapToolsState.model?.editable()));
}

// A tool reason shows in the panel and on the map, and goes to the polite live region; an empty text clears it.
// The map notice has no timer: it stays until the next tool action (UI-UX 23:16:23, WCAG 2.2.1).
function mapToolsMessage(text) {
  mapToolsState.message = text;
  document.getElementById("map-tool-message").textContent = text;
  document.getElementById("map-tool-notice").textContent = text;
  if (text) document.getElementById("map-announcement").textContent = text;
}

// Update the buttons, the outline status and the footer, then redraw the vector layer.
function mapToolsRefresh(value) {
  const state = mapToolsState, words = window.ServerManMapWording;
  if (value !== undefined) state.value = value;
  const usable = mapToolsUsable("circle");
  for (const tool of ["select", "circle", "polygon", "inspect"]) {
    const button = document.getElementById(`map-tool-${tool}`);
    button.disabled = !mapToolsUsable(tool);
    button.setAttribute("aria-pressed", String(state.tool === tool));
  }
  document.getElementById("map-circle-radius").disabled = !usable;
  const count = state.vertices.length;
  const crossing = count ? window.ServerManMapOutline.crossings(state.vertices).size : 0;
  document.getElementById("map-outline-status").textContent = count ? words.outlinePoints(count, crossing) : "";
  document.getElementById("map-outline-finish").disabled = !usable || !count;
  document.getElementById("map-outline-discard").disabled = !count;
  // The footer stays one line high, so the map never resizes while the admin draws
  const footer = document.getElementById("map-tool-status");
  footer.textContent = words.toolStatusShort(state.tool, count);
  footer.title = words.toolStatus(state.tool, count);
  document.getElementById("map-viewport").classList.toggle("map-drawing", ["circle", "polygon"].includes(state.tool) && usable);
  window.ServerManMapViewportUi.redraw();
}

// A tool letter or button chooses a tool; a second press of the active button ends it. The outline stays.
function mapToolsChoose(tool, toggle = false) {
  if (!mapToolsUsable(tool)) return;
  mapToolsState.tool = toggle && mapToolsState.tool === tool ? null : tool;
  mapToolsMessage("");
  mapToolsRefresh();
}

// New zones are visible, unlocked, Rich and Inherit, with an automatic name (D1).
function mapToolsObject(kind, geometry) {
  const model = mapToolsState.model;
  return {id: model.newId(), name: model.nextName(kind), kind, visible: true, locked: false, ...geometry,
    tier: null, abundance: "rich"};
}

// One click is one circle with the last radius, or one outline vertex; a refused click says why.
function mapToolsPlace(point) {
  const state = mapToolsState, words = window.ServerManMapWording, bounds = state.value.plan.terrain.bounds;
  if (!window.ServerManMapOutline.inside(bounds, point)) {
    mapToolsMessage(words[state.tool === "circle" ? "outsideCircle" : "outsidePoint"]);
    return;
  }
  if (state.tool === "circle") {
    const radius = Math.min(state.radius, Math.max(bounds.width, bounds.height));
    state.model.add(words.circleAction, mapToolsObject("loot_circle", {centre: {x: point.x, z: point.z}, radius}));
  } else if (window.ServerManMapOutline.tooClose(state.vertices, point)) {
    mapToolsMessage(words.pointTooClose);
    return;
  } else {
    state.vertices.push({x: point.x, z: point.z});
  }
  mapToolsMessage("");
  mapToolsRefresh();
}

// Selection takes the highest visible zone that covers the point; a hidden zone is never hit (R12)
function mapToolsHit(point) {
  return (mapToolsState.value?.plan?.objects ?? []).find((item) => window.ServerManMapVector.drawable(item) &&
    window.ServerManMapOutline.contains(item, point)) ?? null;
}

// A click places by the tool or reads one point in Inspect mode; Shift+click, and a click in Select-only mode,
// select and never place.
// A selecting click on an empty place clears the selection.
function mapToolsClick(event) {
  const state = mapToolsState, down = state.down;
  state.down = null;
  // A pointer that moved 4 pixels or more is a drag, so a drag paints nothing (R06)
  if (event.button !== 0 || event.defaultPrevented || !down ||
      Math.hypot(event.clientX - down.x, event.clientY - down.y) >= 4) return;
  const element = event.currentTarget, rectangle = element.getBoundingClientRect();
  const point = window.ServerManMapViewportUi.view().toNative({x: event.clientX - rectangle.left - element.clientLeft,
    y: event.clientY - rectangle.top - element.clientTop});
  if (!point || !mapToolsUsable(event.shiftKey ? "select" : state.tool)) return;
  if (event.shiftKey || state.tool === "select") {
    mapToolsMessage("");
    window.ServerManMapLayers.select(mapToolsHit(point)?.id ?? null);
  } else if (state.tool === "inspect") {
    window.ServerManMapInspect.point(point);
  } else if (state.tool) {
    try { mapToolsPlace(point); } catch (error) { mapToolsMessage(error.message); }
  }
}

// Finish refuses locally while a D1 rule fails; a valid outline enters the top and saves at once.
function mapToolsFinish() {
  const state = mapToolsState, words = window.ServerManMapWording;
  if (!state.vertices.length || !mapToolsUsable("polygon")) return;
  const problem = window.ServerManMapOutline.problem(state.vertices);
  if (problem) {
    mapToolsMessage(words[problem]);
    return;
  }
  const object = mapToolsObject("loot_polygon", {vertices: structuredClone(state.vertices)});
  try {
    if (!state.model.add(words.outlineAction, object)) return;
  } catch (error) {
    mapToolsMessage(error.message);
    return;
  }
  state.vertices = [];
  mapToolsMessage("");
  mapToolsRefresh();
  window.ServerManMapPlanSave.finish(object);
}

// Remove the last vertex, or discard the whole outline only by its explicit button; a discard is announced.
function mapToolsOutline(discard) {
  if (discard) mapToolsState.vertices = []; else mapToolsState.vertices.pop();
  mapToolsMessage(discard ? window.ServerManMapWording.outlineDiscarded : "");
  mapToolsRefresh();
}

// Letters act only on the map or with no control focused, and never with Ctrl, Alt or Meta (WCAG 2.1.4).
function mapToolsKey(event) {
  const viewport = document.getElementById("map-viewport");
  if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey || document.querySelector("dialog") ||
      ![viewport, document.body].includes(event.target) || viewport.classList.contains("map-panning")) return;
  const actions = {v: () => mapToolsChoose("select"), c: () => mapToolsChoose("circle"), p: () => mapToolsChoose("polygon"),
    i: () => mapToolsChoose("inspect"), Enter: mapToolsFinish, Backspace: () => mapToolsOutline(false)};
  const run = actions[event.key.length === 1 ? event.key.toLowerCase() : event.key];
  if (!run) return;
  event.preventDefault();
  run();
}

// The radius field is the last chosen radius; an invalid value keeps the previous radius.
function mapToolsRadius(field) {
  const state = mapToolsState, bounds = state.value?.plan?.terrain?.bounds;
  const limit = bounds ? Math.max(bounds.width, bounds.height) : 100000, radius = Number(field.value);
  const valid = field.value.trim() !== "" && Number.isFinite(radius) && radius >= 1 && radius <= limit;
  field.setAttribute("aria-invalid", String(!valid));
  if (valid) state.radius = radius;
  mapToolsMessage(valid ? "" : window.ServerManMapWording.radiusInvalid(limit));
}

// Wire the controls and the map once; placement listens after the pan and calibration capture handlers.
function mapToolsInit(model) {
  const state = mapToolsState, words = window.ServerManMapWording, viewport = document.getElementById("map-viewport");
  if (state.model) return;
  state.model = model;
  state.value = model.status();
  for (const [id, text] of [["map-tool-select", words.selectTool], ["map-tool-circle", words.circleTool], ["map-tool-polygon", words.polygonTool],
    ["map-circle-radius-label", words.circleRadius], ["map-outline-finish", words.finish],
    ["map-outline-discard", words.discardOutline]]) {
    document.getElementById(id).textContent = text;
  }
  for (const tool of ["select", "circle", "polygon", "inspect"]) {
    document.getElementById(`map-tool-${tool}`).addEventListener("click", () => mapToolsChoose(tool, true));
  }
  const radius = document.getElementById("map-circle-radius");
  radius.value = String(state.radius);
  radius.addEventListener("change", () => mapToolsRadius(radius));
  // A button that empties the draft disables itself, so the focus goes to the map, where the tool keys act
  document.getElementById("map-outline-finish").addEventListener("click", () => {
    mapToolsFinish();
    if (!state.vertices.length) viewport.focus();
  });
  document.getElementById("map-outline-discard").addEventListener("click", () => {
    mapToolsOutline(true);
    viewport.focus();
  });
  viewport.addEventListener("pointerdown", (event) => { state.down = {x: event.clientX, y: event.clientY}; });
  viewport.addEventListener("click", mapToolsClick);
  document.addEventListener("keydown", mapToolsKey);
  mapToolsRefresh();
}

// The outline save module returns a refused outline to the draft through restore.
window.ServerManMapTools = Object.freeze({init: mapToolsInit, refresh: mapToolsRefresh, usable: mapToolsUsable,
  value: () => mapToolsState.value, draft: () => structuredClone(mapToolsState.vertices),
  restore: (vertices) => { mapToolsState.vertices = structuredClone(vertices); mapToolsRefresh(); },
  message: mapToolsMessage, hit: (point) => mapToolsHit(point)?.id ?? null, choose: (tool) => mapToolsChoose(tool)});
