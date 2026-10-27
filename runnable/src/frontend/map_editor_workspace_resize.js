// Own cancellable pointer and keyboard resize gestures independently of map input.
"use strict";

const mapWorkspaceResizeState = {gesture: null, controls: new Map()};

// Cancel restores the prior preferred dimension without committing a preference.
function mapWorkspaceResizeEnd(commit) {
  const state = mapWorkspaceResizeState, gesture = state.gesture;
  if (!gesture) return;
  state.gesture = null;
  const workspace = window.ServerManMapWorkspace;
  workspace.change({[gesture.key]: commit ? workspace.snapshot()[gesture.key] : gesture.before}, commit);
  if (gesture.pointer !== undefined && gesture.control.hasPointerCapture(gesture.pointer)) gesture.control.releasePointerCapture(gesture.pointer);
}

// Preview uses the same limits for every input device and never writes preferences.
function mapWorkspaceResizePreview(key, value) {
  const [min, max] = window.ServerManMapWorkspace.limits(key);
  window.ServerManMapWorkspace.change({[key]: Math.round(Math.max(min, Math.min(max, value)))}, false);
}

// Reflect effective limits for assistive technology without moving focused separators.
function mapWorkspaceResizeRefresh() {
  for (const [key, control] of mapWorkspaceResizeState.controls) {
    const [min, max] = window.ServerManMapWorkspace.limits(key);
    control.setAttribute("aria-valuemin", String(Math.round(min)));
    control.setAttribute("aria-valuemax", String(Math.round(max)));
    control.setAttribute("aria-valuenow", String(Math.round(Math.min(max, window.ServerManMapWorkspace.snapshot()[key]))));
  }
}

// Pointer capture isolates dragging from the map; all cancellation paths roll back.
function mapWorkspaceResizeAttach(key, region, label, vertical) {
  const control = window.ServerManMapDom.element("div", "", {role: "separator", tabindex: "0", class: "map-resize-handle",
    "aria-label": `Resize ${label}`, "aria-orientation": vertical ? "horizontal" : "vertical"});
  document.getElementById(region).append(control); mapWorkspaceResizeState.controls.set(key, control);
  control.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); control.focus();
    const before = window.ServerManMapWorkspace.snapshot()[key];
    mapWorkspaceResizeState.gesture = {key, before, origin: vertical ? event.clientY : event.clientX,
      pointer: event.pointerId, control}; control.setPointerCapture(event.pointerId);
  });
  control.addEventListener("pointermove", (event) => {
    const gesture = mapWorkspaceResizeState.gesture;
    if (!gesture || gesture.control !== control || gesture.pointer !== event.pointerId) return;
    event.preventDefault(); event.stopPropagation();
    const delta = (vertical ? event.clientY : event.clientX) - gesture.origin;
    mapWorkspaceResizePreview(key, gesture.before + delta * (key === "navigation_width" ? 1 : -1));
  });
  control.addEventListener("pointerup", () => mapWorkspaceResizeEnd(true));
  for (const type of ["pointercancel", "lostpointercapture"]) control.addEventListener(type, () => mapWorkspaceResizeEnd(false));
  control.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); mapWorkspaceResizeEnd(false); return; }
    const arrows = vertical ? ["ArrowDown", "ArrowUp"] : ["ArrowLeft", "ArrowRight"];
    if (![...arrows, "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const value = window.ServerManMapWorkspace.snapshot()[key];
    if (!mapWorkspaceResizeState.gesture) mapWorkspaceResizeState.gesture = {key, before: value, control};
    const [min, max] = window.ServerManMapWorkspace.limits(key);
    const change = (event.shiftKey ? 1 : 10) * (event.key === arrows[0] ? -1 : 1);
    mapWorkspaceResizePreview(key, event.key === "Home" ? min : event.key === "End" ? max : value + change);
  });
  control.addEventListener("keyup", (event) => { if (event.key !== "Escape") mapWorkspaceResizeEnd(true); });
  control.addEventListener("blur", () => mapWorkspaceResizeEnd(true));
}

window.ServerManMapWorkspaceResize = Object.freeze({refresh: mapWorkspaceResizeRefresh,
  init: () => {
    mapWorkspaceResizeAttach("navigation_width", "map-panel", "objects and layers", false);
    mapWorkspaceResizeAttach("inspector_width", "map-inspector", "properties", false);
    mapWorkspaceResizeAttach("report_height", "map-reports", "reports", true);
    window.addEventListener("blur", () => mapWorkspaceResizeEnd(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && mapWorkspaceResizeState.gesture) {
        event.preventDefault(); event.stopImmediatePropagation(); mapWorkspaceResizeEnd(false);
      }
    }, true);
  }});
