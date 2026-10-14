// Route current shared history keys while text entry and modal dialogs retain ownership.
"use strict";

let mapKeysStarted = false;

// This leaf binds history only; later tools and save leaves grow their accepted surfaces.
function mapKeysInit(model) {
  if (mapKeysStarted) return;
  mapKeysStarted = true;
  // Modal and previously consumed gestures keep their existing input ownership.
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.metaKey || document.querySelector("dialog")) return;
    const target = event.target instanceof Element ? event.target : document.activeElement;
    // Text, select and editable elements retain their native undo behavior.
    if (target?.closest('textarea, select, [contenteditable]:not([contenteditable="false"]), input:not([type="range"]):not([type="checkbox"]):not([type="radio"])')) return;
    const viewport = document.getElementById("map-viewport");
    // Active panning cannot accidentally traverse the draft history.
    if (viewport.classList.contains("map-panning")) return;
    const key = event.key.toLowerCase();
    if (!event.ctrlKey || !["z", "y"].includes(key) || (key === "y" && event.shiftKey)) return;
    event.preventDefault();
    // Current history shortcuts change only the draft and never dispatch a save.
    const result = key === "y" || event.shiftKey ? model.redo() : model.undo();
    result.catch((error) => window.ServerManMapLayout.status(error.message, {notice: "error", announce: true}));
  });
}

// No autosave, Apply, drawing shortcut or native full-screen call is introduced here.
window.ServerManMapKeys = Object.freeze({init: mapKeysInit});
