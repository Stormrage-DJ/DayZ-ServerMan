// Place existing controls into independent workspace regions without cloning their listeners.
"use strict";

// Build one region with a stable heading and independently scrolling body.
function mapWorkspaceRegion(id, title) {
  const make = window.ServerManMapDom.element;
  const region = make("section", "", {id, class: "map-workspace-region", "aria-labelledby": `${id}-title`});
  const header = make("header", "", {class: "map-region-heading"});
  const heading = make("h2", title, {id: `${id}-title`, tabindex: "-1"});
  const close = make("button", "Close", {type: "button", class: "button", "aria-label": `Close ${title}`});
  const body = make("div", "", {id: `${id}-body`, class: "map-region-body"});
  close.addEventListener("click", () => window.ServerManMapWorkspace.toggle(id === "map-inspector" ? "inspector" : "report"));
  header.append(heading, close); region.append(header, body);
  return region;
}

// Keep setup controls parked in the document until the modal or calibration needs them.
function mapWorkspaceBuild() {
  const get = (id) => document.getElementById(id), make = window.ServerManMapDom.element;
  const main = get("map-editor"), workspace = get("map-workspace"), toolbar = get("map-toolbar");
  const strip = make("nav", "", {id: "map-edit-bar", "aria-label": "Editor controls"});
  const actions = make("div", "", {id: "map-region-actions"});
  for (const [key, title] of [["navigation", "Objects & layers"], ["inspector", "Properties"], ["report", "Reports"],
    ["setup", "Map setup"], ["plan", "Plan file"]]) {
    const target = key === "navigation" ? "map-panel" : key === "report" ? "map-reports" : `map-${key}`;
    const button = make("button", title, {id: `map-show-${key}`, type: "button", class: "button", "aria-expanded": "false", "aria-controls": target});
    button.addEventListener("click", () => {
      if (["setup", "plan"].includes(key)) window.ServerManMapSetup?.open(key);
      else window.ServerManMapWorkspace.toggle(key);
    });
    actions.append(button);
  }
  strip.append(actions, toolbar); main.insertBefore(strip, workspace);
  const context = make("div", "", {id: "map-tool-context"});
  const drawing = make("div", "", {id: "map-drawing-actions"});
  const primary = make("div", "", {id: "map-primary-tools"});
  for (const id of ["map-fit", "map-zoom-selection", "map-full-screen", "map-tool-buttons", "map-history-controls"]) primary.append(get(id));
  for (const id of ["map-coordinate-mode-label", "map-coordinate-mode", "map-circle-radius-label", "map-circle-radius",
    "map-encounter-provider-label", "map-encounter-provider", "map-encounter-provider-reason"]) context.append(get(id));
  for (const id of ["map-outline-status", "map-outline-actions", "map-tool-message", "map-tools-bounds-help"]) drawing.append(get(id));
  get("map-navigation-help").hidden = true;
  const navigationHelp = get("map-navigation-help");
  toolbar.replaceChildren(primary, context, drawing, navigationHelp);
  const inspector = mapWorkspaceRegion("map-inspector", "Selected object");
  workspace.append(inspector);
  get("map-inspector-body").append(get("map-properties-title").closest("section"));
  const report = mapWorkspaceRegion("map-reports", "Reports"); workspace.append(report);
  get("map-reports-body").append(get("map-tier-capacity"), get("map-inspect"));
  const parking = make("div", "", {id: "map-setup-parking", hidden: ""}); main.append(parking);
  for (const id of ["map-bounds-title", "map-image-title", "map-plan-file-title"]) parking.append(get(id).closest("section"));
  get("map-panel-toggle").textContent = "Objects & layers";
  const nav = get("map-panel-content");
  const tiers = get("map-tiers-title").closest("section"), global = get("map-global-capacity-title").closest("section");
  const objects = get("map-layers-title").closest("section");
  nav.replaceChildren(objects, tiers, global);
  const settings = make("details"), settingsTitle = make("summary", "Plan settings");
  global.before(settings); settings.append(settingsTitle, global);
  const calibration = make("section", "", {id: "map-calibration-workspace", hidden: "", "aria-label": "Map calibration"});
  calibration.append(get("map-calibration")); main.append(calibration);
  const note = make("p", "", {id: "map-layout-notice", role: "status"}); actions.append(note);
}

// Only the chosen drawing mode exposes its extra controls in the compact toolbar.
function mapWorkspaceToolContext(tool) {
  window.ServerManMapToolbar.context(tool);
  for (const id of ["map-circle-radius", "map-circle-radius-label"]) document.getElementById(id).hidden = tool !== "circle";
  for (const id of ["map-encounter-provider", "map-encounter-provider-label", "map-encounter-provider-reason"]) document.getElementById(id).hidden = tool !== "encounter";
}

window.ServerManMapWorkspaceDom = Object.freeze({build: mapWorkspaceBuild, context: mapWorkspaceToolContext});
