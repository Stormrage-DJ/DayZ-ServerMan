// Bind geometry gestures and observe Control without taking shortcuts from native controls.
"use strict";

// Physical Control state is display-only; each press latches its own geometry operation.
const mapDragInputState = {control: false};

// Text entry, calibration and navigation keep their own modifier and pointer behavior.
function mapDragInputHeld() {
  const target = document.activeElement;
  return mapDragInputState.control && !target?.closest?.("input, textarea, select, [contenteditable], dialog") &&
    !document.querySelector("dialog") && !window.ServerManMapCalibration.state().active &&
    !document.getElementById("map-viewport").classList.contains("map-panning");
}

// Redraw handle visibility only when the observed physical modifier changes.
function mapDragInputControl(held) {
  if (mapDragInputState.control === held) return;
  mapDragInputState.control = held;
  window.ServerManMapViewportUi.redraw();
}

// Keep input lifecycle separate from geometry calculations and model commits.
function mapDragInputInit(element) {
  const state = mapDragState;
  // Observe modifiers before active-drag shortcut suppression; never consume idle native keys.
  window.addEventListener("keydown", (event) => {
    mapDragInputControl(event.key === "Control" || event.ctrlKey);
    if (!state.drag && event.key === "Escape") state.pending = null;
    if (!state.drag) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (event.key === "Escape") mapDragCancel();
  }, true);
  window.addEventListener("keyup", (event) => {
    mapDragInputControl(event.key !== "Control" && event.ctrlKey);
    // Pan releases Space in a later listener; restore handles after that navigation state settles.
    queueMicrotask(() => window.ServerManMapViewportUi.redraw());
  }, true);
  // Pointer modifiers recover from a key event missed while the window lacked focus.
  for (const type of ["pointerdown", "pointermove", "pointerup"]) {
    window.addEventListener(type, (event) => mapDragInputControl(event.ctrlKey), true);
  }
  document.addEventListener("focusin", (event) => {
    if (event.target.closest?.("input, textarea, select, [contenteditable], dialog")) mapDragCancel();
    window.ServerManMapViewportUi.redraw();
  });
  element.addEventListener("pointerdown", mapDragDown);
  element.addEventListener("pointermove", mapDragMove);
  element.addEventListener("pointerup", mapDragUp);
  for (const type of ["pointercancel", "lostpointercapture"]) {
    element.addEventListener(type, (event) => {
      if ((state.drag ?? state.pending)?.pointer === event.pointerId) mapDragCancel();
    });
  }
  // The viewport's own release commits first; an unrelated release cancels the outstanding press.
  window.addEventListener("pointerup", (event) => {
    if ((state.drag ?? state.pending)?.pointer === event.pointerId) mapDragCancel();
    window.ServerManMapViewportUi.redraw();
  });
  window.addEventListener("blur", () => { mapDragInputControl(false); mapDragCancel(); });
  // A drag or cancelled press must not place an object through its trailing click.
  element.addEventListener("click", (event) => {
    if (!state.suppress) return;
    state.suppress = false; event.preventDefault(); event.stopImmediatePropagation();
  }, true);
}

window.ServerManMapDragInput = Object.freeze({init: mapDragInputInit, held: mapDragInputHeld});
