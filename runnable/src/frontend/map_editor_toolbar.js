// Organize existing controls into menus, editing groups and current-tool context.
"use strict";

const mapToolbarState = {started: false, tool: null};

// Close the View surface without changing any panel or map state.
function mapToolbarClose(focus = true) {
  const menu = document.getElementById("map-view-menu"), opener = document.getElementById("map-show-view");
  if (!menu || menu.hidden) return;
  menu.hidden = true; opener.setAttribute("aria-expanded", "false");
  if (focus) opener.focus();
}

// Panel close returns focus to the visible menu opener when its command is hidden.
function mapToolbarFocus(region) {
  const command = document.getElementById(`map-show-${region}`);
  (command.closest("[hidden]") ? document.getElementById("map-show-view") : command).focus();
}

// This is a disclosure with ordinary buttons and a native select, not an ARIA application menu.
function mapToolbarOpen() {
  const menu = document.getElementById("map-view-menu"), opener = document.getElementById("map-show-view");
  if (!menu.hidden) { mapToolbarClose(); return; }
  if (document.querySelector("dialog")) return;
  window.ServerManMapSetup.close(false);
  menu.hidden = false; opener.setAttribute("aria-expanded", "true");
  document.getElementById("map-show-navigation").focus();
}

// Build a named editing group while preserving the original button instances and listeners.
function mapToolbarGroup(label, nodes) {
  const make = window.ServerManMapDom.element;
  const group = make("div", "", {class: "map-edit-group", role: "group", "aria-label": label});
  const caption = make("span", label, {class: "map-edit-caption", "aria-hidden": "true"});
  const controls = make("div", "", {class: "map-edit-controls"});
  controls.append(...nodes); group.append(caption, controls); return group;
}

// Keep a stable polygon context slot before the first point; adding points does not move the map.
function mapToolbarRefresh() {
  if (!mapToolbarState.started) return;
  const get = (id) => document.getElementById(id), tool = mapToolbarState.tool;
  const count = window.ServerManMapTools?.draft().length ?? 0;
  get("map-tool-context").hidden = !["circle", "encounter"].includes(tool);
  get("map-drawing-actions").hidden = !count && tool !== "polygon";
  get("map-outline-actions").hidden = !count;
  get("map-polygon-start").hidden = Boolean(count);
  get("map-outline-status").title = get("map-outline-status").textContent;
  get("map-encounter-provider-reason").title = get("map-encounter-provider-reason").textContent;
  window.ServerManMapIcons.toolbar();
}

// Relocate view commands and build two clear bands after the workspace has supplied its original controls.
function mapToolbarInit() {
  const state = mapToolbarState;
  if (state.started) return;
  state.started = true;
  const get = (id) => document.getElementById(id), make = window.ServerManMapDom.element;
  const actions = get("map-region-actions"), plan = get("map-show-plan"), setup = get("map-show-setup");
  const view = make("button", "View ▾", {id: "map-show-view", type: "button", class: "button",
    "aria-expanded": "false", "aria-controls": "map-view-menu"});
  const menu = make("section", "", {id: "map-view-menu", hidden: "", "aria-label": "View options"});
  const panels = make("div", "", {class: "map-view-section"});
  panels.append(make("h2", "Panels"));
  for (const key of ["navigation", "inspector", "report"]) {
    const button = get(`map-show-${key}`);
    button.addEventListener("click", () => mapToolbarClose(false), true); panels.append(button);
  }
  const camera = make("div", "", {class: "map-view-section"}); camera.append(make("h2", "Map view"));
  for (const id of ["map-fit", "map-zoom-selection", "map-full-screen"]) {
    const button = get(id);
    button.addEventListener("click", () => mapToolbarClose(), true); camera.append(button);
  }
  const coordinates = make("div", "", {class: "map-view-coordinates"});
  coordinates.append(get("map-coordinate-mode-label"), get("map-coordinate-mode")); camera.append(coordinates);
  menu.append(panels, camera);
  plan.textContent = "Plan ▾"; plan.setAttribute("aria-label", "Plan file");
  actions.replaceChildren(plan, view, setup, get("map-tools-bounds-help"), get("map-layout-notice"), get("map-publication"), menu);
  actions.setAttribute("aria-label", "Editor menus");
  const buttons = get("map-tool-buttons");
  buttons.replaceChildren(mapToolbarGroup("Select", [get("map-tool-select")]),
    mapToolbarGroup("Create", [get("map-tool-circle"), get("map-tool-polygon"), get("map-tool-encounter")]),
    mapToolbarGroup("Inspect", [get("map-tool-inspect")]));
  const history = get("map-history-controls");
  const historyGroup = mapToolbarGroup("History", [get("map-undo"), get("map-redo")]);
  history.replaceChildren(historyGroup); get("map-primary-tools").replaceChildren(buttons, history);
  const start = make("p", "Polygon: click the map to add points", {id: "map-polygon-start"});
  get("map-drawing-actions").prepend(start);
  get("map-tool-message").classList.add("map-toolbar-feedback");
  get("map-toolbar").append(get("map-tool-message"));
  const boundsReason = get("map-tools-bounds-help").querySelector("p");
  boundsReason.id = "map-toolbar-bounds-reason"; boundsReason.classList.add("map-toolbar-feedback");
  get("map-tools-bounds-jump").setAttribute("aria-describedby", boundsReason.id);
  view.addEventListener("click", mapToolbarOpen);
  for (const button of [plan, setup, ...get("map-publication").querySelectorAll("button")]) {
    button.addEventListener("click", () => mapToolbarClose(false), true);
  }
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#map-view-menu, #map-show-view")) mapToolbarClose(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault(); event.stopImmediatePropagation(); mapToolbarClose();
    }
  }, true);
  menu.addEventListener("focusout", () => queueMicrotask(() => {
    if (!menu.contains(document.activeElement) && document.activeElement !== view) mapToolbarClose(false);
  }));
  mapToolbarRefresh();
}

window.ServerManMapToolbar = Object.freeze({init: mapToolbarInit, close: mapToolbarClose, focus: mapToolbarFocus,
  refresh: mapToolbarRefresh, context: (tool) => { mapToolbarState.tool = tool; mapToolbarRefresh(); }});
