// Inspect mode: a click reads the tier and capacity facts of one point; the marker and the answer panel (4.6, R14).
"use strict";

// point: the clicked native point, or null; display: the read of that point; loads: the model load that it belongs to.
const mapInspectState = {model: null, point: null, display: null, loads: null};

// One element of the Inspect panel
function mapInspectElement(id) {
  return document.getElementById(id);
}

// The coordinates of a point as the status area shows them, on one line
function mapInspectCoordinates(result) {
  const view = window.ServerManMapViewportUi.view()?.snapshot();
  return window.ServerManMapCoordinates.display({x: result.x, z: result.z}, view?.bounds, view?.scale, "Both").replace("\n", "; ");
}

// The effective tier of a point and its source: the zone with the tier override, or the original tier
function mapInspectEffective(result) {
  const words = window.ServerManMapInspectWording, tiers = result.effective_tier ? words.tiers(result.effective_tier) : words.unknown;
  if (result.tier_source === "zone") return words.fromZone(tiers, result.tier_object?.name);
  return result.tier_source === "original" ? words.fromOriginal(tiers) : words.unknown;
}

// The answer as terms and descriptions: tiers and their source, the winning zone, hidden contributors and a zone
// that changes capacity only. A point selects no building, so the capacity sentence replaces a capacity list.
function mapInspectRows(result) {
  const words = window.ServerManMapInspectWording, terms = words.terms, presets = window.ServerManMapWording.capacityPresets;
  const winner = result.winner ? [words.winner(result.winner.name, result.winner.hidden),
    result.abundance_only ? words.capacityOnly : ""].filter(Boolean).join(". ") : words.noWinner;
  const abundance = result.abundance ?? {};
  const zone = abundance.zone_preset ? words.zone(presets[abundance.zone_preset] ?? words.signed(abundance.zone)) : words.noZone;
  const rows = [[terms.coordinates, mapInspectCoordinates(result)], [terms.cell, words.cell(result.header_cell)],
    [terms.original, result.original_tier ? words.tiers(result.original_tier) : words.unknown],
    [terms.effective, mapInspectEffective(result)], [terms.winner, winner],
    [terms.capacity, words.adjustment(words.signed(abundance.global), zone, words.signed(abundance.effective))]];
  if (result.not_writable) rows.push([terms.effective, words.notWritableZone(result.not_writable.name, result.not_writable.tier)]);
  const hidden = result.hidden_contributors ?? [];
  if (hidden.length) rows.push([terms.hidden, `${hidden.map((item) => item.name).join(", ")}. ${words.hiddenCount}`]);
  const encounters = result.encounters ?? [];
  rows.push([terms.encounters, encounters.length ? encounters.map((item) => item.name).join(", ") : words.none]);
  return rows;
}

// Draw the panel: the idle hint, "Reading tiers" without a percent (T1), the answer, or the failure.
function mapInspectPaint() {
  const state = mapInspectState, display = state.display, words = window.ServerManMapInspectWording;
  const result = display?.mode === "done" ? display.result : null, list = mapInspectElement("map-inspect-result");
  mapInspectElement("map-inspect-status").textContent = !display ? words.inspectIdle :
    (display.mode === "reading" ? (display.stale ? words.pointStale : words.pointReading) :
      (result ? "" : words.pointNotRead(display.failure.reason)));
  mapInspectElement("map-inspect").setAttribute("aria-busy", String(display?.mode === "reading"));
  list.hidden = !result;
  list.replaceChildren(...(result ? mapInspectRows(result) : []).map(([term, text]) => {
    const row = document.createElement("div");
    row.append(Object.assign(document.createElement("dt"), {textContent: term}), Object.assign(document.createElement("dd"), {textContent: text}));
    return row;
  }));
  // A point answer has no building capacity; its reason sentence comes from the backend catalogue copy
  const capacity = mapInspectElement("map-inspect-capacity");
  capacity.hidden = !result;
  capacity.textContent = result?.capacity_reason === "point_target" ? words.pointCapacity : "";
  mapInspectElement("map-inspect-cancel").hidden = display?.mode !== "reading";
  mapInspectElement("map-inspect-again").hidden = !display?.failure?.again || display.mode !== "failed";
}

// The callbacks of one point read; a replaced display is never drawn.
function mapInspectHandlers(display) {
  const state = mapInspectState, words = window.ServerManMapInspectWording, current = () => state.display === display;
  const objectName = (id) => state.model.snapshot()?.objects.find((item) => item.id === id)?.name ?? null;
  const update = (values) => { if (current()) { Object.assign(display, values); mapInspectPaint(); } };
  return {start: (stale) => update({mode: "reading", stale}), progress: () => {},
    // Only a read that a map click or Read again started is announced, not the read again after an edit
    done: (result) => {
      update({mode: "done", result});
      if (!current() || !display.announce) return;
      display.announce = false;
      mapInspectElement("map-announcement").textContent = words.pointAnnounce(mapInspectEffective(result));
    },
    failed: ({error, lost}) => {
      update({mode: "failed", failure: words.failure(error, lost, objectName)});
      if (current()) mapInspectElement("map-announcement").textContent = words.pointNotRead(display.failure.reason);
    }};
}

// Follow the draft: an edit reads the point again, so a deleted winner shows the next zone or the original.
// A load ends the point of the earlier plan. The tier control follows the selection in the same step.
function mapInspectSync() {
  const state = mapInspectState, model = state.model;
  if (!model) return;
  const status = model.status(), selected = window.ServerManMapLayers.selected();
  if (state.loads !== model.loads()) Object.assign(state, {loads: model.loads(), point: null, display: null});
  // A read-only draft can be inspected too; it writes nothing (Product Owner 2026-10-14 00:33:43)
  const usable = status.enabled && Boolean(status.plan);
  // Inspect sends the stored-form draft only while it differs from the saved revision (Editor Bridge Methods)
  const context = usable ? {plan: status.dirty || status.revision === null || status.revision === undefined ? status.plan : null,
    base: `${model.loads()}\n${JSON.stringify(status.plan)}`} : null;
  const item = context && selected && status.object_list_available ? status.plan.objects.find((object) => object.id === selected) ?? null : null;
  window.ServerManMapTierControl.render(item, status, context);
  let request = null;
  if (context && state.point) {
    const key = `${state.point.x},${state.point.z}\n${context.base}`;
    if (state.display?.key !== key) state.display = {key, mode: "reading", stale: false};
    request = {key, plan: context.plan, target: {point: {...state.point}}, on: mapInspectHandlers(state.display)};
  } else if (!context) state.display = null;
  window.ServerManMapInspectRead.want("point", request);
  mapInspectPaint();
}

// A click of the Inspect tool replaces the earlier point and its read.
function mapInspectPoint(point) {
  mapInspectState.point = {x: point.x, z: point.z};
  mapInspectSync();
  if (mapInspectState.display) mapInspectState.display.announce = true;
  window.ServerManMapViewportUi.redraw();
}

// Escape and Cancel read clear the marker and cancel the point read; editing goes on.
function mapInspectClear() {
  Object.assign(mapInspectState, {point: null, display: null});
  mapInspectSync();
  window.ServerManMapViewportUi.redraw();
}

// The marker lies above the zones: a ring and a cross at the clicked point, or nothing.
function mapInspectMarker(view) {
  const point = mapInspectState.point ? view.toScreen(mapInspectState.point) : null;
  if (!point) return [];
  const element = (tag, attributes) => {
    const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, String(value));
    return shape;
  };
  const group = element("g", {id: "map-inspect-marker"});
  const cross = `M ${point.x - 11} ${point.y} H ${point.x + 11} M ${point.x} ${point.y - 11} V ${point.y + 11}`;
  for (const name of ["map-inspect-halo", "map-inspect-ring"]) {
    group.append(element("circle", {class: name, cx: point.x, cy: point.y, r: 7}), element("path", {class: name, d: cross}));
  }
  return [group];
}

// Escape outside text entry clears the Inspect marker; a drag cancel and a calibration Cancel take it first.
function mapInspectKey(event) {
  if (event.key !== "Escape" || event.defaultPrevented || document.querySelector("dialog") || !mapInspectState.point ||
      event.target?.closest?.("input:not([type=checkbox]):not([type=radio]), textarea, select")) return;
  mapInspectClear();
}

// Wire the panel, the tier control and Escape once; the app calls init after the Properties panel.
function mapInspectInit(model) {
  const state = mapInspectState, words = window.ServerManMapInspectWording;
  if (state.model) return;
  state.model = model;
  window.ServerManMapTierControl.init(model);
  for (const [id, text] of [["map-inspect-title", words.inspectTitle], ["map-tool-inspect", words.inspectTool],
    ["map-inspect-cancel", words.cancelRead], ["map-inspect-again", words.readAgain]]) mapInspectElement(id).textContent = text;
  mapInspectElement("map-inspect-cancel").addEventListener("click", () => {
    mapInspectClear();
    mapInspectElement("map-viewport").focus();
  });
  // The action hides while the read runs, so the focus goes to the map, where Inspect clicks act
  mapInspectElement("map-inspect-again").addEventListener("click", () => {
    mapInspectElement("map-viewport").focus();
    if (mapInspectState.display) mapInspectState.display.announce = true;
    window.ServerManMapInspectRead.retry("point");
  });
  document.addEventListener("keydown", mapInspectKey);
  mapInspectSync();
}

// The tools route Inspect clicks here; the vector layer draws the marker; Properties calls sync at each render.
window.ServerManMapInspect = Object.freeze({init: mapInspectInit, sync: mapInspectSync, point: mapInspectPoint,
  clear: mapInspectClear, marker: mapInspectMarker});
