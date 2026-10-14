// Own the outline draft of the Polygon tool: its points, Finish, Backspace, Discard outline and the outline status.
"use strict";

// The draft lives outside the plan and the history, so selection, edits, undo and panning keep it (R12).
const mapOutlineDraftState = {model: null, vertices: []};

// Add one point inside the closed bounds; a point too close to another gives the wording key of the reason, else null
function mapOutlineDraftAdd(point) {
  if (window.ServerManMapOutline.tooClose(mapOutlineDraftState.vertices, point)) return "pointTooClose";
  mapOutlineDraftState.vertices.push({x: point.x, z: point.z});
  return null;
}

// Show the outline status and enable Finish and Discard outline; the tools call this at each refresh
function mapOutlineDraftRender(usable) {
  const count = mapOutlineDraftState.vertices.length;
  const crossing = count ? window.ServerManMapOutline.crossings(mapOutlineDraftState.vertices).size : 0;
  document.getElementById("map-outline-status").textContent = count ? window.ServerManMapWording.outlinePoints(count, crossing) : "";
  document.getElementById("map-outline-finish").disabled = !usable || !count;
  document.getElementById("map-outline-discard").disabled = !count;
}

// Finish refuses locally while a D1 rule fails; a valid outline enters the top and saves at once.
function mapOutlineDraftFinish() {
  const state = mapOutlineDraftState, words = window.ServerManMapWording, tools = window.ServerManMapTools;
  if (!state.vertices.length || !tools.usable("polygon")) return;
  const problem = window.ServerManMapOutline.problem(state.vertices);
  if (problem) {
    tools.message(words[problem]);
    return;
  }
  const object = tools.object("loot_polygon", {vertices: structuredClone(state.vertices)});
  try {
    if (!state.model.add(words.outlineAction, object)) return;
  } catch (error) {
    tools.message(error.message);
    return;
  }
  state.vertices = [];
  tools.message("");
  tools.refresh();
  window.ServerManMapPlanSave.finish(object);
}

// Remove the last point, or discard the whole outline only by its explicit button; a discard is announced.
function mapOutlineDraftRemove(discard) {
  if (discard) mapOutlineDraftState.vertices = []; else mapOutlineDraftState.vertices.pop();
  window.ServerManMapTools.message(discard ? window.ServerManMapWording.outlineDiscarded : "");
  window.ServerManMapTools.refresh();
}

// Wire Finish and Discard outline once; the tools module calls this from its own init.
function mapOutlineDraftInit(model) {
  const state = mapOutlineDraftState, words = window.ServerManMapWording, viewport = document.getElementById("map-viewport");
  if (state.model) return;
  state.model = model;
  document.getElementById("map-outline-finish").textContent = words.finish;
  document.getElementById("map-outline-discard").textContent = words.discardOutline;
  // A button that empties the draft disables itself, so the focus goes to the map, where the tool keys act
  document.getElementById("map-outline-finish").addEventListener("click", () => {
    mapOutlineDraftFinish();
    if (!state.vertices.length) viewport.focus();
  });
  document.getElementById("map-outline-discard").addEventListener("click", () => {
    mapOutlineDraftRemove(true);
    viewport.focus();
  });
}

// The outline save module returns a refused outline to the draft through restore (Outline Finish).
window.ServerManMapOutlineDraft = Object.freeze({init: mapOutlineDraftInit, add: mapOutlineDraftAdd,
  render: mapOutlineDraftRender, finish: mapOutlineDraftFinish, remove: mapOutlineDraftRemove,
  count: () => mapOutlineDraftState.vertices.length, draft: () => structuredClone(mapOutlineDraftState.vertices),
  restore: (vertices) => { mapOutlineDraftState.vertices = structuredClone(vertices); window.ServerManMapTools.refresh(); }});
