// Toggle native full screen from the toolbar button and the F11 key (Keyboard Map, R01).
"use strict";

// The host answers no state, so this window tracks the pressed state and flips it only on success.
const mapFullScreenState = {on: false, busy: false, started: false};
// Accepted labels of the Keyboard Map; native full screen has no window controls, so the label names the way out.
const mapFullScreenLabels = Object.freeze({enter: "Full screen (F11)", leave: "Exit full screen (F11)"});

// Show the label and the pressed state; the label is the accessible name as for the other toolbar buttons.
function mapFullScreenShow() {
  const button = document.getElementById("map-full-screen");
  const label = mapFullScreenState.on ? mapFullScreenLabels.leave : mapFullScreenLabels.enter;
  button.textContent = label;
  button.title = label;
  button.setAttribute("aria-pressed", String(mapFullScreenState.on));
  window.ServerManMapIcons.button(button, "fullscreen");
}

// One call at a time; a failure keeps the state and shows the host reason.
async function mapFullScreenToggle() {
  if (mapFullScreenState.busy) return;
  mapFullScreenState.busy = true;
  try {
    await window.ServerManMapBridge.toggleFullScreen();
    mapFullScreenState.on = !mapFullScreenState.on;
    mapFullScreenShow();
  } catch (error) {
    window.ServerManMapLayout.status(error.message, {notice: "error", announce: true});
  } finally {
    mapFullScreenState.busy = false;
  }
}

// F11 acts in text fields too, but not while a dialog is open; it never reaches the browser default.
function mapFullScreenKey(event) {
  if (event.key !== "F11" || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
  event.preventDefault();
  if (event.repeat || document.querySelector("dialog")) return;
  mapFullScreenToggle();
}

// Bind once; a repeated start must not add a second handler.
function mapFullScreenInit() {
  if (mapFullScreenState.started) return;
  mapFullScreenState.started = true;
  mapFullScreenShow();
  document.getElementById("map-full-screen").addEventListener("click", mapFullScreenToggle);
  document.addEventListener("keydown", mapFullScreenKey);
}

window.ServerManMapFullScreen = Object.freeze({init: mapFullScreenInit});
