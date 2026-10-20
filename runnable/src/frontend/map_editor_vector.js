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

// Region interiors use screen-space patterns so zoom never changes their visual density.
function mapVectorInterior(shape, colour, state) {
  const id = `map-authored-hatch-${state.patternIndex++}`;
  const opacity = window.ServerManMapLayerDisplay.fraction();
  const definitions = mapVectorElement("defs", {});
  const pattern = mapVectorElement("pattern", {id, patternUnits: "userSpaceOnUse", width: 10, height: 10});
  pattern.append(mapVectorElement("path", {d: "M-2 2 2-2M0 10 10 0M8 12 12 8", stroke: colour,
    "stroke-width": 1, "stroke-opacity": 0.45, fill: "none"}));
  definitions.append(pattern);
  // Separate opacity preserves every boundary and cue when original tiers are dimmed.
  return [definitions, shape({class: "map-zone-wash", fill: colour, "fill-opacity": 0.12 * opacity}),
    shape({class: "map-zone-hatch", fill: `url(#${id})`, "fill-opacity": opacity})];
}

// Keep tier identity, independent selection, and errors visible on every completed region.
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
  const colour = item.tier === null ? "var(--color-text-muted)" : state.colour(item.tier);
  line.stroke = state.problems.has(item.id) ? "var(--color-status-error)" : colour;
  const result = mapVectorInterior(shape, colour, state);
  result.push(shape({class: "map-zone-halo"}), shape(line));
  // Paint the full selection width last; it never changes the underlying tier or provider identity.
  if (item.id === state.selected && !item.linked) result.push(shape({class: "map-zone-selection-outer"}),
    shape({class: "map-zone-selection"}));
  // A selected error keeps a separate warning mark when its base boundary is covered.
  if (item.id === state.selected && state.problems.has(item.id)) {
    const anchor = item.centre ?? item.vertices?.[0];
    if (anchor) {
      const point = view.toScreen(anchor);
      result.push(mapVectorElement("path", {class: "map-zone-error-marker",
        d: `M${point.x} ${point.y - 24}l8 14h-16Z`}));
    }
  }
  return result;
}

// The outline draft: edges, the implied closing edge, crossings in the error colour, and its points
function mapVectorDraft(view, vertices) {
  const group = mapVectorElement("g", {id: "map-outline-draft"});
  const crossings = window.ServerManMapOutline.crossings(vertices);
  // Invalid or incomplete geometry keeps its edges without suggesting a valid area.
  if (vertices.length >= 3 && !window.ServerManMapOutline.problem(vertices)) {
    group.append(mapVectorElement("polygon", {class: "map-outline-wash", points: mapVectorPoints(view, vertices),
      "fill-opacity": 0.12 * window.ServerManMapLayerDisplay.fraction()}));
  }
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
  const state = {selected: window.ServerManMapLayers.selected(), problems: window.ServerManMapPlanSave.problems(), colour,
    patternIndex: 0};
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
      problem: state.problems.has(item.id), status: window.ServerManMapEncounters.status(item, value.capabilities),
      interior: (shape, colour) => mapVectorInterior(shape, colour, state)}));
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
