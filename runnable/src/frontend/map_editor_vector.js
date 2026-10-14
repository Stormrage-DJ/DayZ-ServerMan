// Draw loot zones, encounters, the selection and the outline draft on the vector layer above the grid.
"use strict";

// Build one SVG element; geometry stays in attributes, so no inline style is needed.
function mapVectorElement(tag, attributes) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

// Screen points of a native vertex list in one SVG points attribute
function mapVectorPoints(view, vertices) {
  return vertices.map((vertex) => { const point = view.toScreen(vertex); return `${point.x},${point.y}`; }).join(" ");
}

// One zone: a dark halo under a 2 px line; an explicit tier uses its tier colour, Inherit a dashed line.
// Linked loot draws as a zone with its own attribute; the selection cue is the encounter ring above it (7.4).
function mapVectorZone(view, item, state) {
  const scale = view.snapshot().scale;
  const shape = (attributes) => item.kind === "loot_circle" ?
    mapVectorElement("circle", {...attributes, cx: view.toScreen(item.centre).x, cy: view.toScreen(item.centre).y,
      r: item.radius * scale}) :
    mapVectorElement("polygon", {...attributes, points: mapVectorPoints(view, item.vertices)});
  const classes = ["map-zone", item.tier === null ? "map-zone-inherit" : "map-zone-tier"];
  if (item.id === state.selected && !item.linked) classes.push("map-zone-selected");
  if (state.problems.has(item.id)) classes.push("map-zone-problem");
  const line = {class: classes.join(" "), [item.linked ? "data-map-linked" : "data-map-zone"]: item.id};
  if (item.tier !== null) line.stroke = state.colour(item.tier);
  return [shape({class: "map-zone-halo"}), shape(line)];
}

// The outline draft: edges, the implied closing edge, crossings in the error colour, and its points
function mapVectorDraft(view, vertices) {
  const group = mapVectorElement("g", {id: "map-outline-draft"});
  const crossings = window.ServerManMapOutline.crossings(vertices);
  const edges = vertices.length >= 3 ? vertices.length : vertices.length - 1;
  for (let index = 0; index < edges; index += 1) {
    const from = view.toScreen(vertices[index]), to = view.toScreen(vertices[(index + 1) % vertices.length]);
    const classes = ["map-outline-edge"], halo = ["map-outline-halo"];
    if (index === vertices.length - 1) classes.push("map-outline-closing");
    // A crossing edge is also wider, so colour is not the only cue (WCAG 1.4.1)
    if (crossings.has(index)) { classes.push("map-outline-crossing"); halo.push("map-outline-halo-crossing"); }
    group.append(mapVectorElement("line", {class: halo.join(" "), x1: from.x, y1: from.y, x2: to.x, y2: to.y}),
      mapVectorElement("line", {class: classes.join(" "), "data-map-edge": index, x1: from.x, y1: from.y, x2: to.x, y2: to.y}));
  }
  for (const vertex of vertices) {
    const point = view.toScreen(vertex);
    group.append(mapVectorElement("circle", {class: "map-outline-vertex", cx: point.x, cy: point.y, r: 4}));
  }
  return group;
}

// Square handles of the selected zone, so they differ from the round points of the outline draft
function mapVectorHandles(view, item) {
  return window.ServerManMapDrag.handles(view, item).map((handle) => mapVectorElement("rect", {class: "map-handle",
    "data-map-handle": `${handle.grip}:${handle.index}`, x: handle.x - 4, y: handle.y - 4, width: 8, height: 8}));
}

// Invalid saved data can reach the window read only; a zone without usable geometry is not drawn
function mapVectorDrawable(item) {
  const point = (value) => Number.isFinite(value?.x) && Number.isFinite(value?.z);
  if (!item?.visible) return false;
  if (item.kind === "loot_circle") return point(item.centre) && Number.isFinite(item.radius);
  // An encounter without a spawn radius (AI_Bandits) shows only its centre mark (7.3)
  if (item.kind === "encounter") return point(item.centre) && (item.radius === null || Number.isFinite(item.radius));
  return item.kind === "loot_polygon" && Array.isArray(item.vertices) && item.vertices.every(point);
}

// Append after the grid; the viewport redraw rebuilds the whole layer each time.
function mapVectorDraw(view) {
  // The tools keep the last draft status copy, so a pointer move copies no plan
  const value = window.ServerManMapTools?.value(), svg = document.getElementById("map-vector");
  const snapshot = view.snapshot();
  if (!value || !snapshot.bounds || !snapshot.scale || snapshot.failure) return;
  const plan = value.plan, tiers = value.capabilities?.holdable_tiers ?? [];
  // Tier colours come from the editor tokens by the 1-based value-flag index
  const colours = new Map();
  const colour = (tier) => {
    if (!colours.has(tier)) colours.set(tier, window.ServerManMapRaster.colour(tiers.indexOf(tier) + 1));
    return colours.get(tier);
  };
  const state = {selected: window.ServerManMapLayers.selected(), problems: window.ServerManMapPlanSave.problems(), colour};
  // A drag preview takes the place of its stored zone; an advisory reason draws it in the error colour
  const drag = window.ServerManMapDrag.preview();
  if (drag?.problem) state.problems = new Set([...state.problems, drag.item.id]);
  const shown = (item) => drag && item?.id === drag.item.id ? drag.item : item;
  const zones = mapVectorElement("g", {id: "map-zones"});
  // The list top wins, so zones draw from the list bottom up; hidden zones are not drawn
  const order = [...(plan?.objects ?? [])].reverse().map(shown);
  for (const item of order) {
    // Enabled linked loot is a loot zone at its encounter's list position, under every encounter ring (7.4)
    const zone = item?.kind === "encounter" ? window.ServerManMapLinkedLoot.area(item) : item;
    if (zone && mapVectorDrawable(zone)) zones.append(...mapVectorZone(view, zone, state));
  }
  // Encounters lie above every loot zone, also from the list bottom up (Rendering)
  for (const item of order) {
    if (item?.kind !== "encounter" || !mapVectorDrawable(item)) continue;
    zones.append(window.ServerManMapEncounterDraw.item(view, item, {selected: item.id === state.selected,
      problem: state.problems.has(item.id), status: window.ServerManMapEncounters.status(item, value.capabilities)}));
  }
  // The handles of the selected zone lie above every zone
  const selected = state.selected ? plan?.objects?.find((item) => item?.id === state.selected) : null;
  if (selected) zones.append(...mapVectorHandles(view, shown(selected)));
  svg.append(zones);
  const vertices = window.ServerManMapTools.draft();
  if (vertices.length) svg.append(mapVectorDraft(view, vertices));
  // The Inspect marker lies above the zones and the outline draft
  svg.append(...(window.ServerManMapInspect?.marker(view) ?? []));
}

// Rendering reads the model and tool state; it never changes the draft or saves.
window.ServerManMapVector = Object.freeze({draw: mapVectorDraw, drawable: mapVectorDrawable});
