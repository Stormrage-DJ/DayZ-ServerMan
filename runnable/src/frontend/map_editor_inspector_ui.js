// Arrange existing selected-object controls without taking ownership of their values or edits.
"use strict";

// Keep guidance available on demand while errors and current evaluation states remain visible.
function mapInspectorDisclosure(id, title) {
  const note = document.getElementById(id), make = window.ServerManMapDom.element;
  const details = make("details", "", {class: "map-inspector-guidance"});
  details.append(make("summary", title)); note.before(details); details.append(note);
}

// Compose one stable form from the original nodes before property controllers render it.
function mapInspectorInit() {
  const get = (id) => document.getElementById(id), make = window.ServerManMapDom.element;
  get("map-properties-title").hidden = true;
  const fields = get("map-properties-fields"), geometry = get("map-properties-radius-row");
  // Put shared loot settings before geometry and provider-specific controls.
  geometry.before(get("map-linked-loot"), get("map-tier-row"), get("map-zone-capacity-row"));
  mapInspectorDisclosure("map-zone-capacity-explanation", "Capacity limits and loot supply");
  mapInspectorDisclosure("map-global-capacity-explanation", "Capacity limits and loot supply");
  const points = make("p", "", {id: "map-properties-points", hidden: ""});
  geometry.after(points);
  fields.classList.add("map-inspector-form");
}

// Add only real derived geometry and decorative identity to the existing property presentation.
function mapInspectorRender(item) {
  const points = document.getElementById("map-properties-points");
  points.hidden = item?.kind !== "loot_polygon";
  points.textContent = item?.kind === "loot_polygon" ? `Points: ${item.vertices?.length ?? 0}` : "";
  if (!item) return;
  const name = document.getElementById("map-properties-name");
  name.prepend(window.ServerManMapIcons.icon(item.kind === "loot_circle" ? "circle" :
    item.kind === "encounter" ? "encounter" : "polygon"));
}

window.ServerManMapInspectorUi = Object.freeze({init: mapInspectorInit, render: mapInspectorRender});
