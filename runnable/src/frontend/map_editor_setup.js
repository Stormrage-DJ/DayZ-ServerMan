// Mount setup controls only while active; calibration runs outside every modal.
"use strict";

const mapSetupState = {dialog: null, menu: null, returning: false, suspended: false, opener: null};

// Preserve original elements and listeners while removing inactive modal nodes entirely.
function mapSetupClose(focus = true) {
  const state = mapSetupState, parking = document.getElementById("map-setup-parking");
  for (const id of ["map-bounds-title", "map-image-title", "map-plan-file-title"]) parking.append(document.getElementById(id).closest("section"));
  if (state.dialog) { state.dialog.close(); state.dialog.remove(); state.dialog = null; }
  state.menu?.remove(); state.menu = null;
  for (const key of ["setup", "plan"]) document.getElementById(`map-show-${key}`).setAttribute("aria-expanded", "false");
  if (focus) (state.opener?.isConnected ? state.opener : document.getElementById("map-viewport")).focus();
  window.ServerManMapLayers?.render();
}

// Give bounds review and native transfer flows their existing modal ownership.
function mapSetupSuspend() {
  mapSetupState.suspended = true; mapSetupClose(false);
  queueMicrotask(mapSetupResume);
}

// Resume bounds setup after its review and save settle; never compete with another dialog.
function mapSetupResume() {
  const state = mapSetupState;
  if (!state.suspended || state.dialog || document.querySelector("dialog") ||
      document.getElementById("map-bounds-fields").disabled || mapEditorState.closing) return;
  state.suspended = false; mapSetupOpen("setup");
}

// The transfer popover contains existing actions and their refusal text.
function mapSetupPlan() {
  const state = mapSetupState, make = window.ServerManMapDom.element;
  const menu = make("section", "", {id: "map-plan", class: "map-plan-menu", "aria-label": "Plan file"});
  const close = make("button", "Close", {type: "button", class: "button"});
  close.addEventListener("click", () => mapSetupClose());
  menu.append(close, document.getElementById("map-plan-file-title").closest("section"));
  document.getElementById("map-region-actions").append(menu); state.menu = menu;
  menu.querySelector("#map-export").focus();
}

// Setup owns no draft: fields are moved, never copied or reset on open/close.
function mapSetupOpen(kind) {
  const state = mapSetupState, same = kind === "setup" ? state.dialog : state.menu;
  if (kind === "setup" && window.ServerManMapCalibration?.state().active) {
    document.getElementById("map-calibration-pick-0")?.focus(); return;
  }
  if (same) { mapSetupClose(); return; }
  if (document.querySelector("dialog") && !state.dialog) return;
  mapSetupClose(false); state.opener = document.getElementById(`map-show-${kind}`);
  state.opener.setAttribute("aria-expanded", "true");
  if (kind === "plan") { mapSetupPlan(); return; }
  const make = window.ServerManMapDom.element, dialog = make("dialog", "", {id: "map-setup", "aria-labelledby": "map-setup-title"});
  const heading = make("h2", "Map setup", {id: "map-setup-title"});
  const close = make("button", "Close", {type: "button", class: "button"});
  close.addEventListener("click", () => mapSetupClose());
  const header = make("header", "", {class: "map-region-heading"});
  const body = make("div", "", {class: "map-region-body"});
  header.append(heading, close);
  body.append(document.getElementById("map-bounds-title").closest("section"),
    document.getElementById("map-image-title").closest("section"));
  dialog.append(header, body);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); mapSetupClose(); });
  document.body.append(dialog); state.dialog = dialog; dialog.showModal(); close.focus();
  window.ServerManMapLayers?.render();
}

// Capture handoffs before original click handlers start their existing workflows.
function mapSetupInit() {
  for (const id of ["map-bounds-save", "map-bounds-detected"]) document.getElementById(id).addEventListener("click", () => {
    if (mapSetupState.dialog) mapSetupSuspend();
  }, true);
  document.getElementById("map-background-choose").addEventListener("click", (event) => {
    if (!mapSetupState.dialog) return;
    event.stopImmediatePropagation(); mapSetupClose(false);
    void window.ServerManMapBackground.choose().finally(() => { if (!mapEditorState.closing) mapSetupOpen("setup"); });
  }, true);
  document.getElementById("map-calibrate").addEventListener("click", () => {
    mapSetupState.returning = Boolean(mapSetupState.dialog);
    if (mapSetupState.dialog) mapSetupClose(false);
  }, true);
  for (const id of ["map-export", "map-import", "map-copy"]) document.getElementById(id).addEventListener("click", (event) => {
    if (event.currentTarget.getAttribute("aria-disabled") !== "true" && mapSetupState.menu) mapSetupClose(false);
  }, true);
  const calibration = document.getElementById("map-calibration");
  new MutationObserver(() => {
    document.getElementById("map-calibration-workspace").hidden = calibration.hidden;
    if (calibration.hidden && mapSetupState.returning) { mapSetupState.returning = false; mapSetupOpen("setup"); document.getElementById("map-calibrate").focus(); }
  }).observe(calibration, {attributes: true, attributeFilter: ["hidden"]});
  new MutationObserver(mapSetupResume).observe(document.body, {childList: true, subtree: true, attributes: true,
    attributeFilter: ["disabled"]});
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mapSetupState.menu) { event.preventDefault(); mapSetupClose(); }
  });
  document.addEventListener("pointerdown", (event) => {
    if (mapSetupState.menu && !event.target.closest("#map-plan, #map-show-plan")) mapSetupClose(false);
  });
}

window.ServerManMapSetup = Object.freeze({open: mapSetupOpen, close: mapSetupClose, init: mapSetupInit});
