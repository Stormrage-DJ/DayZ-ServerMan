// Show and edit the selected item's properties: name, circle and encounter centre and radius, Delete; zoom to the selection.
"use strict";

const mapPropertiesState = {model: null, shown: null};

// Field IDs of the editable properties; the centre and radius fields show for a circle and an encounter
const mapPropertiesFields = Object.freeze({name: "map-properties-name-field", x: "map-properties-centre-x",
  z: "map-properties-centre-z", radius: "map-properties-radius"});

// The selected item of the draft, also a hidden or locked one, or null
function mapPropertiesItem() {
  const id = window.ServerManMapLayers.selected();
  return id ? mapPropertiesState.model?.snapshot()?.objects.find((item) => item.id === id) ?? null : null;
}

// The terrain bounds of the draft; they limit a centre and a radius (D1)
function mapPropertiesBounds() {
  return mapPropertiesState.model?.snapshot()?.terrain?.bounds ?? null;
}

// The fields follow the selection and the draft; a focused field keeps the admin's typing
function mapPropertiesRender() {
  const state = mapPropertiesState, words = window.ServerManMapWording, item = mapPropertiesItem();
  if (!state.model) return;
  // The tier control and the Inspect point follow the same selection and draft changes (4.6), as do the capacity controls (5.1)
  window.ServerManMapInspect.sync();
  window.ServerManMapAbundance.render();
  document.getElementById("map-properties-empty").hidden = Boolean(item);
  document.getElementById("map-properties-empty").textContent = words.noSelection;
  document.getElementById("map-properties-fields").hidden = !item;
  const fields = Object.fromEntries(Object.entries(mapPropertiesFields).map(([key, id]) => [key, document.getElementById(id)]));
  if (state.shown !== item?.id) {
    document.getElementById("map-properties-message").textContent = "";
    for (const field of Object.values(fields)) field.setAttribute("aria-invalid", "false");
  }
  if (!item) {
    state.shown = null;
    window.ServerManMapEncounterProperties.render(null, true, null);
    return;
  }
  // The kind goes through the wording lookup, with a fallback for a kind that the window does not know
  const type = words.objectTypes[item.kind] ?? words.objectFallback;
  document.getElementById("map-properties-name").textContent = `${item.name} (${type})`;
  // Lock and visibility affect editing and display only, not the mission settings (R12)
  document.getElementById("map-properties-state").textContent = [item.locked ? words.lockedNote : "",
    item.visible ? "" : words.hiddenNote].filter(Boolean).join(" ");
  const circle = ["loot_circle", "encounter"].includes(item.kind), locked = item.locked || !state.model.editable();
  // An encounter has a spawn radius, and one without a radius (AI_Bandits) has the centre fields only (PO decision 5)
  const encounter = item.kind === "encounter", radius = !encounter || item.radius !== null;
  document.getElementById("map-properties-radius-row").hidden = !circle;
  document.getElementById("map-properties-radius-label").textContent = encounter ? window.ServerManMapEncounterWording.spawnRadius : words.objectRadius;
  document.getElementById("map-properties-radius-label").hidden = fields.radius.hidden = !radius;
  document.getElementById("map-properties-delete").disabled = locked;
  window.ServerManMapEncounterProperties.render(item, locked, state.model.status().capabilities);
  const values = {name: item.name, x: item.centre?.x, z: item.centre?.z, radius: item.radius};
  for (const [key, field] of Object.entries(fields)) {
    field.disabled = locked || (key !== "name" && !circle);
    if (key !== "name" && !circle) continue;
    if (document.activeElement !== field || state.shown !== item.id) field.value = String(values[key]);
  }
  state.shown = item.id;
}

// One change of a field is one undo action; a refused value keeps the stored value and shows the reason
function mapPropertiesCommit(field, refusal, patch) {
  const item = mapPropertiesItem();
  if (!item) return;
  let reason = refusal;
  if (!reason) {
    try { mapPropertiesState.model.edit(item.id, patch(item)); } catch (error) { reason = error.message; }
  }
  field.setAttribute("aria-invalid", String(Boolean(reason)));
  document.getElementById("map-properties-message").textContent = reason ?? "";
  // An accepted value shows as stored, for example a trimmed name (QA-4.5-F6)
  const stored = reason ? null : mapPropertiesItem(), key = Object.keys(mapPropertiesFields).find((name) => mapPropertiesFields[name] === field.id);
  if (stored) field.value = String({name: stored.name, x: stored.centre?.x, z: stored.centre?.z, radius: stored.radius}[key]);
}

// The refusal texts name the circle or the encounter (UI-UX 10:22:15)
function mapPropertiesWords() {
  return mapPropertiesItem()?.kind === "encounter" ? window.ServerManMapEncounterWording : window.ServerManMapWording;
}

// A radius from 1 m to the larger terrain extent; the edit happens at change (Enter or leaving the field)
function mapPropertiesRadius(field) {
  const bounds = mapPropertiesBounds(), limit = bounds ? Math.max(bounds.width, bounds.height) : 100000;
  const radius = Number(field.value), words = mapPropertiesWords();
  const valid = field.value.trim() !== "" && Number.isFinite(radius) && radius >= 1 && radius <= limit;
  mapPropertiesCommit(field, valid ? null : words.radiusEditInvalid(limit), () => ({radius}));
}

// A centre axis inside the closed terrain bounds; the other axis keeps its stored value (UI-UX 15:44:25)
function mapPropertiesCentre(field, axis) {
  const bounds = mapPropertiesBounds(), value = Number(field.value);
  const low = axis === "x" ? bounds?.x_min : bounds?.z_min, extent = axis === "x" ? bounds?.width : bounds?.height;
  const valid = Boolean(bounds) && field.value.trim() !== "" && Number.isFinite(value) && value >= low && value <= low + extent;
  mapPropertiesCommit(field, valid ? null : mapPropertiesWords().centreInvalid(bounds),
    (item) => ({centre: {...item.centre, [axis]: value}}));
}

// Delete is one undo action without a confirmation; a locked item refuses it, and the map notice says what happened.
// The deleted item's controls go away, so the focus moves to the map, where the keys act (QA-4.5-F5).
function mapPropertiesDelete() {
  const item = mapPropertiesItem(), words = window.ServerManMapWording, tools = window.ServerManMapTools;
  if (!item) {
    tools.message(words.selectFirst);
    return;
  }
  try {
    if (item.locked) throw new Error(words.objectLocked);
    if (!mapPropertiesState.model.remove(words.deleteAction, item.id)) return;
    tools.message(words.deleted(item.name));
    document.getElementById("map-viewport").focus();
  } catch (error) {
    tools.message(error.message);
  }
}

// The native box of a zone, or null for geometry that the window cannot use
function mapPropertiesBox(item) {
  const reach = item.radius ?? 0;
  const points = ["loot_circle", "encounter"].includes(item.kind) ?
    [{x: item.centre?.x - reach, z: item.centre?.z - reach}, {x: item.centre?.x + reach, z: item.centre?.z + reach}] :
    (Array.isArray(item.vertices) ? item.vertices : []);
  const xs = points.map((point) => point?.x), zs = points.map((point) => point?.z);
  if (!points.length || ![...xs, ...zs].every(Number.isFinite)) return null;
  const x_min = Math.min(...xs), z_min = Math.min(...zs);
  return {x_min, z_min, width: Math.max(...xs) - x_min, height: Math.max(...zs) - z_min};
}

// Zoom to selection (F or the toolbar button); without a selection the tool notice says so
function mapPropertiesZoom() {
  const item = mapPropertiesItem(), box = item ? mapPropertiesBox(item) : null;
  if (!item) {
    window.ServerManMapTools.message(window.ServerManMapWording.selectFirst);
    return;
  }
  if (box) window.ServerManMapViewportUi.view().focus(box);
}

// The Delete key acts only on the item the admin is looking at: from the map, the page body, the selected item's
// list row or a Properties control outside text entry; never from another item's row (Product Owner 16:14:33)
function mapPropertiesDeleteTarget(target) {
  const id = window.ServerManMapLayers.selected();
  if (target?.closest?.("input:not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable]")) return false;
  return [document.getElementById("map-viewport"), document.body].includes(target) ||
    Boolean(target?.closest?.("#map-properties-fields")) || Boolean(id && target?.closest?.("[data-map-object]")?.dataset.mapObject === id);
}

// F acts only on the map or with no control focused (WCAG 2.1.4); Escape and Delete act outside text entry.
// A drag cancel and a calibration Cancel take Escape first.
function mapPropertiesKey(event) {
  const viewport = document.getElementById("map-viewport");
  if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey || document.querySelector("dialog") ||
      viewport.classList.contains("map-panning")) return;
  if (event.key === "Escape" && !event.target?.closest?.("input:not([type=checkbox]):not([type=radio]), textarea, select")) {
    window.ServerManMapLayers.clear();
  } else if (event.key.toLowerCase() === "f" && [viewport, document.body].includes(event.target)) {
    event.preventDefault();
    if (!document.getElementById("map-zoom-selection").disabled) mapPropertiesZoom();
  } else if (event.key === "Delete" && mapPropertiesDeleteTarget(event.target)) {
    event.preventDefault();
    mapPropertiesDelete();
  }
}

// Wire the fields, Delete, the zoom button and the keys once.
function mapPropertiesInit(model) {
  const state = mapPropertiesState, words = window.ServerManMapWording;
  if (state.model) return;
  state.model = model;
  for (const [id, text] of [["map-properties-name-label", words.objectName], ["map-properties-centre-x-label", words.centreX],
    ["map-properties-centre-z-label", words.centreZ], ["map-properties-radius-label", words.objectRadius],
    ["map-properties-delete", words.deleteObject], ["map-zoom-selection", words.zoomSelection]]) {
    document.getElementById(id).textContent = text;
  }
  document.getElementById("map-zoom-selection").addEventListener("click", mapPropertiesZoom);
  const field = (key) => document.getElementById(mapPropertiesFields[key]);
  field("name").addEventListener("change", () => mapPropertiesCommit(field("name"), null, () => ({name: field("name").value})));
  field("x").addEventListener("change", () => mapPropertiesCentre(field("x"), "x"));
  field("z").addEventListener("change", () => mapPropertiesCentre(field("z"), "z"));
  field("radius").addEventListener("change", () => mapPropertiesRadius(field("radius")));
  document.getElementById("map-properties-delete").addEventListener("click", mapPropertiesDelete);
  document.addEventListener("keydown", mapPropertiesKey);
  window.ServerManMapEncounterProperties.init(model);
  mapPropertiesRender();
}

// The model owns the undo actions; the layers own the selection.
window.ServerManMapProperties = Object.freeze({init: mapPropertiesInit, render: mapPropertiesRender, zoom: mapPropertiesZoom,
  box: mapPropertiesBox, delete: mapPropertiesDelete, item: mapPropertiesItem});
