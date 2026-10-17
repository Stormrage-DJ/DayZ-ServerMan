// Own the Select, Circle, Polygon, Encounter and Inspect tools, placement and selection clicks and the tool status.
// The outline draft of the Polygon tool is in map_editor_outline_draft.js.
"use strict";

// The active tool, the last circle radius and the press of the current click; the outline draft is not here.
// paused: the pause text of an own Apply or restore, which holds the map notice until the operation ends (8.18).
const mapToolsState = {model: null, value: null, tool: null, radius: 100, down: null, message: "", paused: ""};
// Tool buttons in toolbar order (UI-UX 10:22:15: Encounter between Polygon and Inspect)
const mapToolsNames = Object.freeze(["select", "circle", "polygon", "encounter", "inspect"]);

// Every tool needs confirmed bounds and a usable view transform. Drawing and selection need a writable draft.
// Inspect writes nothing, so it also acts in a read-only draft without a dialog (Product Owner 2026-10-14 00:33:43).
// While an own Apply or restore runs, Select stays and Inspect stops: it would read files while they change (8.18).
function mapToolsUsable(tool) {
  const view = window.ServerManMapViewportUi.view()?.snapshot(), value = mapToolsState.value;
  const ready = Boolean(value?.plan?.terrain?.bounds_confirmed && view?.scale && !view.failure), open = !document.querySelector("dialog");
  if (ready && value.paused && ["inspect", "select"].includes(tool)) return tool === "select" && Boolean(value.enabled && value.object_list_available && open);
  return ready && (tool === "inspect" ? Boolean(value.enabled && open) : Boolean(mapToolsState.model?.editable()));
}

// A tool reason shows in the panel and on the map, and goes to the polite live region; an empty text clears it.
// The map notice has no timer: it stays until the next tool action (UI-UX 23:16:23, WCAG 2.2.1).
function mapToolsMessage(text) {
  mapToolsState.message = text;
  document.getElementById("map-tool-message").textContent = text;
  document.getElementById("map-tool-notice").textContent = text || mapToolsState.paused;
  if (text) document.getElementById("map-announcement").textContent = text;
}

// Update the buttons, the outline status and the footer, then redraw the vector layer.
function mapToolsRefresh(value) {
  const state = mapToolsState, words = window.ServerManMapWording;
  if (value !== undefined) state.value = value;
  const usable = mapToolsUsable("circle"), choice = window.ServerManMapEncounterTool.render(usable);
  // A load or a refresh that makes the chosen encounter type unavailable ends the Encounter tool (QA-7.3-F2)
  if (state.tool === "encounter" && !choice.available) state.tool = null;
  for (const tool of mapToolsNames) {
    const button = document.getElementById(`map-tool-${tool}`);
    button.disabled = !mapToolsUsable(tool);
    button.setAttribute("aria-pressed", String(state.tool === tool));
  }
  document.getElementById("map-circle-radius").disabled = !usable;
  window.ServerManMapOutlineDraft.render(usable);
  const count = window.ServerManMapOutlineDraft.count();
  // The footer stays one line high, so the map never resizes while the admin draws; its title names the encounter type
  const footer = document.getElementById("map-tool-status");
  footer.textContent = words.toolStatusShort(state.tool, count);
  footer.title = state.tool === "encounter" ? window.ServerManMapEncounterWording.toolTitle(
    window.ServerManMapEncounterWording.name(choice.provider)) : words.toolStatus(state.tool, count);
  document.getElementById("map-viewport").classList.toggle("map-drawing",
    ["circle", "polygon", "encounter"].includes(state.tool) && usable);
  window.ServerManMapViewportUi.redraw();
}

// A tool letter or button chooses a tool; a second press of the active button ends it. The outline stays.
function mapToolsChoose(tool, toggle = false) {
  if (!mapToolsUsable(tool)) return;
  // An unavailable encounter type chooses no tool; its reason goes to the panel, the map and the live region
  const choice = tool === "encounter" ? window.ServerManMapEncounterTool.choice() : null;
  if (choice && !choice.available) {
    mapToolsMessage(choice.reason);
    return;
  }
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

// One click is one circle with the last radius, one encounter, or one outline vertex; a refused click says why.
function mapToolsPlace(point) {
  const state = mapToolsState, words = window.ServerManMapWording, bounds = state.value.plan.terrain.bounds;
  if (state.tool === "encounter") {
    const refusal = window.ServerManMapEncounterTool.place(point);
    mapToolsMessage(refusal ?? "");
    if (!refusal) mapToolsRefresh();
    return;
  }
  if (!window.ServerManMapOutline.inside(bounds, point)) {
    mapToolsMessage(words[state.tool === "circle" ? "outsideCircle" : "outsidePoint"]);
    return;
  }
  if (state.tool === "circle") {
    const radius = Math.min(state.radius, Math.max(bounds.width, bounds.height));
    state.model.add(words.circleAction, mapToolsObject("loot_circle", {centre: {x: point.x, z: point.z}, radius}));
  } else {
    const refusal = window.ServerManMapOutlineDraft.add(point);
    if (refusal) {
      mapToolsMessage(words[refusal]);
      return;
    }
  }
  mapToolsMessage("");
  mapToolsRefresh();
}

// Selection takes an encounter whose line or mark is within reach. Else it takes the highest visible zone that covers
// the point. A hidden item is never hit (R12, UI-UX 10:22:15).
function mapToolsHit(point) {
  const objects = mapToolsState.value?.plan?.objects ?? [];
  return window.ServerManMapEncounters.hit(objects, point, window.ServerManMapViewportUi.view()) ??
    objects.find((item) => window.ServerManMapVector.drawable(item) && window.ServerManMapOutline.contains(item, point)) ?? null;
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

// Letters act only on the map or with no control focused, and never with Ctrl, Alt or Meta (WCAG 2.1.4).
function mapToolsKey(event) {
  const viewport = document.getElementById("map-viewport");
  if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey || document.querySelector("dialog") ||
      ![viewport, document.body].includes(event.target) || viewport.classList.contains("map-panning")) return;
  const actions = {v: () => mapToolsChoose("select"), c: () => mapToolsChoose("circle"), p: () => mapToolsChoose("polygon"),
    e: () => mapToolsChoose("encounter"), i: () => mapToolsChoose("inspect"), Enter: window.ServerManMapOutlineDraft.finish,
    Backspace: () => window.ServerManMapOutlineDraft.remove(false)};
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
    ["map-circle-radius-label", words.circleRadius]]) {
    document.getElementById(id).textContent = text;
  }
  for (const tool of mapToolsNames) {
    document.getElementById(`map-tool-${tool}`).addEventListener("click", () => mapToolsChoose(tool, true));
  }
  const radius = document.getElementById("map-circle-radius");
  radius.value = String(state.radius);
  radius.addEventListener("change", () => mapToolsRadius(radius));
  window.ServerManMapOutlineDraft.init(model);
  window.ServerManMapEncounterTool.init(model);
  viewport.addEventListener("pointerdown", (event) => { state.down = {x: event.clientX, y: event.clientY}; });
  viewport.addEventListener("click", mapToolsClick);
  document.addEventListener("keydown", mapToolsKey);
  mapToolsRefresh();
}

// The outline save module returns a refused outline to the draft through restore; the outline draft owns it.
window.ServerManMapTools = Object.freeze({init: mapToolsInit, refresh: mapToolsRefresh, usable: mapToolsUsable,
  value: () => mapToolsState.value, draft: () => window.ServerManMapOutlineDraft.draft(),
  restore: (vertices) => window.ServerManMapOutlineDraft.restore(vertices), object: mapToolsObject,
  message: mapToolsMessage, hit: (point) => mapToolsHit(point)?.id ?? null, choose: (tool) => mapToolsChoose(tool),
  active: () => mapToolsState.tool, end: () => { mapToolsState.tool = null; }, radius: () => mapToolsState.radius,
  // The pause text shows on the map without an announcement; an empty text ends it (UI-UX section 7)
  pause: (text) => { mapToolsState.paused = text; document.getElementById("map-tool-notice").textContent = text; }});
