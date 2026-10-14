// Render ordered draft rows with explicit visibility, lock and priority controls.
"use strict";

let mapLayersModel = null, mapLayersSelected = null;

// Summaries describe stored settings; they do not estimate effective tiers or mission capacity.
function mapLayerSummary(item) {
  const words = window.ServerManMapWording;
  const type = words.objectTypes[item.kind];
  // An encounter has a loot summary only when its stored linked loot is enabled.
  const loot = item.kind === "encounter" ? (item.linked_loot.enabled ? item.linked_loot : null) : item;
  const summary = loot ? `${words.tier}: ${loot.tier ?? words.inherit}; ${words.zoneCapacity}: ${words.capacityPresets[loot.abundance]}` : "";
  return `${type}${item.kind === "encounter" ? ` (${item.provider})` : ""}${summary ? `; ${summary}` : ""}`;
}

// The map and the list share one selection; the vector layer shows it.
function mapLayersSelect(id) {
  mapLayersSelected = id;
  mapLayersRender();
  window.ServerManMapProperties?.render();
  window.ServerManMapViewportUi.redraw();
}

// All row content is text; listeners retain only stable IDs, never mutable object references.
function mapLayersRender() {
  if (!mapLayersModel) return;
  const model = mapLayersModel, plan = model.snapshot(), words = window.ServerManMapWording;
  const status = model.status();
  // History availability follows the shared model, including retained null-before creation.
  document.getElementById("map-undo").disabled = !status.writable || !status.history.undo || Boolean(document.querySelector("dialog"));
  document.getElementById("map-redo").disabled = !status.writable || !status.history.redo || Boolean(document.querySelector("dialog"));
  const container = document.getElementById("map-list");
  const active = document.activeElement?.dataset.mapControl;
  // Incomplete display fixtures stay intact and never become invented editable objects.
  container.replaceChildren();
  if (!status.object_list_available || !plan.objects.length) {
    const empty = document.createElement("p");
    empty.id = "map-layers-empty";
    empty.textContent = !plan ? words.noObjectPlan : (status.object_list_available ? words.noObjects : words.objectListUnavailable);
    container.append(empty);
    mapLayersSelected = null;
    return;
  }
  if (!plan.objects.some((item) => item.id === mapLayersSelected)) mapLayersSelected = null;
  // Render rows in actual top-first precedence, independent of hidden and locked flags.
  const list = document.createElement("ol");
  list.setAttribute("aria-label", words.objectList);
  plan.objects.forEach((item, index) => {
    const row = document.createElement("li");
    row.dataset.mapObject = item.id;
    row.className = "map-layer";
    row.classList.toggle("map-layer-selected", item.id === mapLayersSelected);
    // Each control targets a stable ID and reports a refused action visibly.
    const button = (name, label, run) => {
      const control = document.createElement("button");
      control.type = "button";
      control.className = "button";
      control.textContent = label;
      control.dataset.mapControl = `${item.id}:${name}`;
      control.setAttribute("aria-label", `${label}: ${item.name}`);
      control.disabled = !model.editable();
      control.addEventListener("click", () => {
        try { run(); } catch (error) { window.ServerManMapLayout.status(error.message, {notice: "error", announce: true}); }
      });
      row.append(control);
      return control;
    };
    const select = button("select", item.name, () => mapLayersSelect(item.id));
    // Selection can target a hidden or locked item without changing its content.
    select.setAttribute("aria-label", `${words.selectObject}: ${item.name}`);
    select.disabled = !status.enabled;
    select.setAttribute("aria-pressed", String(item.id === mapLayersSelected));
    const summary = document.createElement("p");
    summary.textContent = mapLayerSummary(item);
    row.append(summary);
    // Visibility and lock are explicit flags, rather than mission exclusion or priority.
    const visible = button("visible", words.visible, () => model.visible(item.id));
    visible.setAttribute("aria-pressed", String(item.visible));
    const locked = button("locked", words.locked, () => model.lock(item.id));
    locked.setAttribute("aria-pressed", String(item.locked));
    // Priority boundaries disable their corresponding controls without creating no-op actions.
    const up = button("up", words.moveUp, () => model.move(item.id, -1));
    const down = button("down", words.moveDown, () => model.move(item.id, 1));
    up.disabled ||= index === 0;
    down.disabled ||= index === plan.objects.length - 1;
    list.append(row);
  });
  container.append(list);
  // Redrawing a changed row retains focus on the same stable-ID control.
  if (active) [...container.querySelectorAll("button")].find((button) => button.dataset.mapControl === active)?.focus();
}

// Selection stays local and separate from object visibility and precedence.
window.ServerManMapLayers = Object.freeze({
  init: (model) => {
    // Buttons and keyboard shortcuts traverse the same shared history.
    mapLayersModel = model;
    for (const [id, operation] of [["map-undo", "undo"], ["map-redo", "redo"]]) {
      document.getElementById(id).textContent = window.ServerManMapWording[operation];
      document.getElementById(id).addEventListener("click", () => {
        model[operation]().catch((error) => window.ServerManMapLayout.status(error.message, {notice: "error", announce: true}));
      });
    }
    mapLayersRender();
  },
  render: mapLayersRender,
  reset: () => mapLayersSelect(null),
  selected: () => mapLayersSelected,
  select: mapLayersSelect,
  clear: () => mapLayersSelect(null),
});
