// Bind viewport navigation while leaving normal controls and activation untouched.
"use strict";

// Wheel modes convert through this viewport's actual CSS line and page dimensions.
function mapViewportWheelPixels(event, element) {
  if (event.deltaMode === 1) return event.deltaY * parseFloat(getComputedStyle(element).lineHeight);
  return event.deltaMode === 2 ? event.deltaY * element.clientHeight : event.deltaY;
}

// Attach one window's input lifecycle and report cursor positions in CSS pixels.
function mapViewportInput(element, view, cursor, allowed) {
  let space = false;
  let drag = null;
  let suppressClick = false;
  const controls = (event) => event.target.closest("input, textarea, select, button, [contenteditable], dialog");
  const position = (event) => {
    const rectangle = element.getBoundingClientRect();
    return {x: event.clientX - rectangle.left - element.clientLeft,
      y: event.clientY - rectangle.top - element.clientTop};
  };

  // Release only our active pointer and clear temporary Space-pan state.
  const finish = () => {
    const pointer = drag?.pointer;
    drag = null;
    if (pointer !== undefined && element.hasPointerCapture(pointer)) element.releasePointerCapture(pointer);
    element.classList.remove("map-panning");
    window.ServerManMapViewportUi?.redraw();
  };
  const reset = () => {
    space = false;
    suppressClick = false;
    finish();
    element.classList.remove("map-pan-ready");
  };

  // Wheel zoom requires map focus; focused text/select controls retain their own gestures.
  element.addEventListener("wheel", (event) => {
    if (!allowed() || controls(event) || document.activeElement !== element) return;
    if (view.zoom(position(event), mapViewportWheelPixels(event, element))) event.preventDefault();
  }, {passive: false});

  // Space is temporary panning only when the map itself owns keyboard focus.
  element.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.ctrlKey || event.altKey || event.metaKey ||
        controls(event) || document.activeElement !== element || !allowed()) return;
    space = true;
    element.classList.add("map-pan-ready");
    event.preventDefault();
  });
  window.addEventListener("keyup", (event) => { if (event.code === "Space") reset(); });
  window.addEventListener("blur", reset);
  element.addEventListener("blur", reset);

  // Middle drag and Space-left drag consume their gesture before placement handlers.
  element.addEventListener("pointerdown", (event) => {
    if (controls(event)) return;
    element.focus();
    if (!drag) suppressClick = false;
    if (!allowed() || !view.snapshot().bounds || drag ||
        !(event.button === 1 || (event.button === 0 && space))) return;
    // Navigation takes ownership without retaining a geometry preview from an earlier press.
    window.ServerManMapDrag?.cancel();
    drag = {pointer: event.pointerId, position: position(event)};
    suppressClick = true;
    element.setPointerCapture(event.pointerId);
    element.classList.add("map-panning");
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  // Captured pointer moves remain navigation outside the viewport without placing objects.
  element.addEventListener("pointermove", (event) => {
    const point = position(event);
    if (drag && event.pointerId === drag.pointer) {
      view.pan(point.x - drag.position.x, point.y - drag.position.y);
      drag.position = point;
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    cursor(point);
  }, true);
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
    element.addEventListener(type, (event) => {
      if (!drag || event.pointerId !== drag.pointer) return;
      finish();
      if (type !== "pointerup") suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }
  element.addEventListener("pointerleave", () => { if (!drag) cursor(null); });

  // The click following a pan cannot reach future drawing or selection handlers.
  const consumeClick = (event) => {
    if (!suppressClick) return;
    suppressClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  element.addEventListener("click", consumeClick, true);
  element.addEventListener("auxclick", consumeClick, true);
  return Object.freeze({reset});
}

// Input has no save, tool-change or geometry-placement responsibility.
window.ServerManMapViewportInput = Object.freeze({attach: mapViewportInput, wheelPixels: mapViewportWheelPixels});
