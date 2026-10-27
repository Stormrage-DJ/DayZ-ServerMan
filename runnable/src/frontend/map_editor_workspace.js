// Own local panel arrangement and expose explicit changes to the preference adapter.
"use strict";

const mapWorkspaceState = {started: false, applying: false, active: null, reveal: {},
  preferred: {navigation_width: 240, inspector_width: 380, report_height: 240,
    navigation_open: true, inspector_open: true, report_open: false}};

// Compact windows retain one auxiliary region while keeping the map usable.
function mapWorkspaceVisible(region) {
  const state = mapWorkspaceState;
  return innerWidth < 1200 ? state.active === region : Boolean(state.preferred[`${region}_open`] || state.reveal[region]);
}

// Bounds are measured from the actual workspace, not the outer native window.
function mapWorkspaceLimits(key) {
  const area = document.getElementById("map-workspace"), pref = mapWorkspaceState.preferred;
  const compact = innerWidth < 1200;
  if (key === "report_height") return [120, Math.max(120, Math.min(600, area.clientHeight * .4, area.clientHeight - 250))];
  const navigation = key === "navigation_width", other = navigation ? "inspector" : "navigation";
  const used = !compact && mapWorkspaceVisible(other) ? pref[`${other}_width`] + 8 : 0;
  return [navigation ? 200 : 320, Math.max(navigation ? 200 : 320,
    Math.min(navigation ? 480 : 640, area.clientWidth - used - 336))];
}

// A scoped stylesheet keeps dimensions dynamic without putting style attributes on controls.
function mapWorkspaceDraw() {
  const state = mapWorkspaceState, get = (id) => document.getElementById(id);
  if (!state.started) return;
  const visible = Object.fromEntries(["navigation", "inspector", "report"].map((key) => [key, mapWorkspaceVisible(key)]));
  const size = (key) => { const [min, max] = mapWorkspaceLimits(key); return Math.max(min, Math.min(max, state.preferred[key])); };
  get("map-panel").open = visible.navigation;
  get("map-panel").hidden = !visible.navigation;
  get("map-inspector").hidden = !visible.inspector;
  get("map-reports").hidden = !visible.report;
  for (const key of Object.keys(visible)) get(`map-show-${key}`).setAttribute("aria-expanded", String(visible[key]));
  get("map-workspace-sizing").textContent = `#map-workspace {grid-template-columns: ${visible.navigation ? size("navigation_width") : 0}px minmax(0,1fr) ${visible.inspector ? size("inspector_width") : 0}px; grid-template-rows: minmax(0,1fr) ${visible.report ? size("report_height") : 0}px; row-gap:${visible.report ? 8 : 0}px;}`;
  // A selection can reveal properties during a click; update transforms before the next gesture.
  const view = window.ServerManMapViewportUi?.view(), frame = view?.snapshot(), viewport = get("map-viewport");
  if (frame && (frame.width !== viewport.clientWidth || frame.height !== viewport.clientHeight)) {
    view.resize(viewport.clientWidth, viewport.clientHeight); window.ServerManMapViewportUi.redraw();
  }
  window.ServerManMapWorkspaceResize?.refresh();
}

// Only explicit commits enter persistence; preview, responsive changes and selection do not.
function mapWorkspaceChange(patch, persist = true) {
  Object.assign(mapWorkspaceState.preferred, patch);
  mapWorkspaceDraw();
  if (persist) window.ServerManMapWorkspacePreferences?.changed(patch);
}

// Reveal a region without moving focus for selection-driven inspector updates.
function mapWorkspaceOpen(region, explicit = false) {
  const state = mapWorkspaceState;
  if (explicit) {
    state.reveal[region] = false;
    mapWorkspaceChange({[`${region}_open`]: true, last_explicit_opened_region: region});
  } else state.reveal[region] = true;
  if (innerWidth < 1200) state.active = region;
  mapWorkspaceDraw();
  if (explicit) document.getElementById(region === "navigation" ? "map-panel-toggle" : `map-${region === "report" ? "reports" : region}-title`)?.focus();
}

// Closing returns focus before the region disappears and never changes selected objects.
function mapWorkspaceToggle(region) {
  if (!mapWorkspaceVisible(region)) return mapWorkspaceOpen(region, true);
  window.ServerManMapToolbar.focus(region);
  mapWorkspaceState.reveal[region] = false;
  if (mapWorkspaceState.active === region) mapWorkspaceState.active = null;
  mapWorkspaceChange({[`${region}_open`]: false});
}

// Initialize before existing controllers bind to their unique original controls.
function mapWorkspaceInit() {
  const state = mapWorkspaceState;
  if (state.started) return;
  window.ServerManMapWorkspaceDom.build(); window.ServerManMapToolbar.init(); state.started = true;
  if (innerWidth < 1200) Object.assign(state.preferred, {navigation_open: false, inspector_open: false});
  const sheet = document.createElement("style"); sheet.id = "map-workspace-sizing"; document.head.append(sheet);
  document.getElementById("map-panel").addEventListener("toggle", () => {
    if (!document.getElementById("map-panel").open && mapWorkspaceVisible("navigation")) mapWorkspaceToggle("navigation");
  });
  new ResizeObserver(mapWorkspaceDraw).observe(document.getElementById("map-workspace"));
  window.addEventListener("resize", () => {
    if (innerWidth < 1200 && !state.active) state.active = [state.preferred.last_explicit_opened_region,
      "navigation", "inspector", "report"].find((key) => state.preferred[`${key}_open`]) ?? null;
    mapWorkspaceDraw();
  });
  window.ServerManMapWorkspaceResize.init(); mapWorkspaceDraw();
  window.ServerManMapWorkspacePreferences.init();
  window.ServerManMapSetup.init();
  window.ServerManMapReports.init();
}

window.ServerManMapWorkspace = Object.freeze({init: mapWorkspaceInit, open: mapWorkspaceOpen, toggle: mapWorkspaceToggle,
  visible: mapWorkspaceVisible, draw: mapWorkspaceDraw, limits: mapWorkspaceLimits, change: mapWorkspaceChange,
  snapshot: () => ({...mapWorkspaceState.preferred}),
  restore: (patch) => {
    Object.assign(mapWorkspaceState.preferred, patch);
    const pref = mapWorkspaceState.preferred;
    if (innerWidth < 1200) mapWorkspaceState.active = [pref.last_explicit_opened_region, "navigation", "inspector", "report"]
      .find((key) => pref[`${key}_open`]) ?? null;
    mapWorkspaceDraw();
  }});
