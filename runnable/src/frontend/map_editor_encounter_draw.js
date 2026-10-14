// Draw one encounter on the vector layer: its spawn ring and a centre mark of fixed screen size (UI-UX 10:22:15).
"use strict";

// Ring width and dashes by state; the shape, not the colour alone, tells the state (WCAG 1.4.1)
const mapEncounterDrawRings = Object.freeze({
  available: {width: 3, halo: 5, dashes: "10 4 2 4"}, unavailable: {width: 3, halo: 5, dashes: "10 4 2 4"},
  excluded: {width: 2, halo: 4, dashes: "2 6"},
});
// Centre mark of each state: a cross, a warning triangle, or a "not" sign
const mapEncounterDrawMarks = Object.freeze({available: "cross", unavailable: "triangle", excluded: "not"});

// Build one SVG element with its attributes
function mapEncounterDrawElement(tag, attributes) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

// The centre mark: each part has a dark halo under it, so it shows on every image
function mapEncounterDrawMark(mark, x, y) {
  const parts = [];
  const line = (x1, y1, x2, y2) => parts.push(["line", {x1, y1, x2, y2}]);
  if (mark === "cross") {
    line(x - 7, y, x + 7, y);
    line(x, y - 7, x, y + 7);
  } else if (mark === "not") {
    parts.push(["circle", {cx: x, cy: y, r: 6}]);
    line(x - 4.2, y + 4.2, x + 4.2, y - 4.2);
  } else {
    parts.push(["polygon", {points: `${x},${y - 9} ${x + 8},${y + 7} ${x - 8},${y + 7}`}]);
    line(x, y - 3, x, y + 1.5);
    line(x, y + 3.5, x, y + 4.5);
  }
  const halo = parts.map(([tag, attributes]) => mapEncounterDrawElement(tag, {...attributes, class: "map-encounter-mark-halo"}));
  const shown = parts.map(([tag, attributes]) => mapEncounterDrawElement(tag, {...attributes, class: "map-encounter-mark-line"}));
  return [...halo, ...shown];
}

// One encounter group; state: {selected, problem, status}. A selected ring is solid in the action colour.
function mapEncounterDrawItem(view, item, state) {
  const status = state.status, name = status.excluded ? "excluded" : (status.unavailable ? "unavailable" : "available");
  const classes = ["map-encounter", `map-encounter-${name}`];
  if (state.selected) classes.push("map-encounter-selected");
  if (state.problem) classes.push("map-encounter-problem");
  const group = mapEncounterDrawElement("g", {class: classes.join(" "), "data-map-encounter": item.id, "data-map-state": name});
  const centre = view.toScreen(item.centre), ring = mapEncounterDrawRings[name];
  if (Number.isFinite(item.radius)) {
    const circle = {cx: centre.x, cy: centre.y, r: item.radius * view.snapshot().scale};
    const dashes = state.selected ? {} : {"stroke-dasharray": ring.dashes};
    // A selected ring is solid and 3 px in every state (UI-UX 10:22:15, QA-7.3-F4)
    const width = state.selected ? 3 : ring.width, halo = state.selected ? 5 : ring.halo;
    group.append(mapEncounterDrawElement("circle", {...circle, ...dashes, class: "map-encounter-halo", "stroke-width": halo}),
      mapEncounterDrawElement("circle", {...circle, ...dashes, class: "map-encounter-ring", "stroke-width": width}));
  }
  const mark = mapEncounterDrawElement("g", {class: "map-encounter-mark", "data-map-mark": mapEncounterDrawMarks[name]});
  mark.append(...mapEncounterDrawMark(mapEncounterDrawMarks[name], centre.x, centre.y));
  group.append(mark);
  return group;
}

// Rendering only: no draft, history or save state lives here
window.ServerManMapEncounterDraw = Object.freeze({item: mapEncounterDrawItem});
