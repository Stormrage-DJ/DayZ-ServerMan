// Render safe identity and status while panel collapse stays local to this window.
"use strict";

// Initialize only once so repeated loads never reset the user's panel choice.
let mapLayoutStarted = false;

// Backend strings become selectable text, with the full identity in its tooltip.
function mapLayoutText(id, text, identity = false) {
  const element = document.getElementById(id);
  element.textContent = text;
  if (identity) element.title = text;
}

// Mirror native disclosure state without storing it in the mission or plan.
function mapPanelState() {
  const panel = document.getElementById("map-panel");
  const toggle = document.getElementById("map-panel-toggle");
  toggle.setAttribute("aria-expanded", String(panel.open));
  toggle.setAttribute("aria-label", panel.open ? window.ServerManMapWording.hideControls : window.ServerManMapWording.showControls);
}

// The one writer of the plan state (UI-UX 2026-10-13 00:01:25): the full text and its title in the one-line footer,
// the full text on the map for a notice ("info", "success" or "error"), and the live region only when the caller asks.
function mapLayoutStatus(text, {notice = null, announce = false} = {}) {
  mapLayoutText("map-status", text, true);
  const board = document.getElementById("map-save-notice");
  board.textContent = notice ? text : "";
  board.classList.toggle("map-save-error", notice === "error");
  board.classList.toggle("map-save-success", notice === "success");
  if (announce) mapLayoutText("map-announcement", text);
}

// Show only the backend's last-applied comparison, never a browser estimate; "updating" while a save waits or runs.
function mapLayoutDraft(value) {
  const words = window.ServerManMapWording;
  const text = value === true ? words.yes : (value === false ? words.no : (value === "updating" ? words.updating : words.unavailable));
  mapLayoutText("map-draft-changes", text);
  document.getElementById("map-draft-changes").classList.toggle("map-draft-updating", value === "updating");
}

// Expose the current layout responsibilities without future editing controls.
window.ServerManMapLayout = Object.freeze({
  // Native details provides keyboard activation and retains focus on its summary.
  init: () => {
    if (mapLayoutStarted) return;
    mapLayoutStarted = true;
    const words = window.ServerManMapWording;
    document.getElementById("map-panel").addEventListener("toggle", mapPanelState);
    mapPanelState();
    // Empty states name unavailable capabilities instead of pretending they exist.
    mapLayoutText("map-properties-empty", words.propertiesUnavailable);
    mapLayoutText("map-layers-empty", words.layersUnavailable);
    mapLayoutText("map-coordinates", words.unavailable);
    mapLayoutText("map-tool-status", words.noTool);
  },
  // Identity belongs to this profile-bound answer and survives panel collapse.
  identity: (value) => {
    mapLayoutText("map-profile", value.profile.display_name, true);
    mapLayoutText("map-mission", value.mission_name, true);
    mapLayoutText("map-terrain", value.plan?.terrain?.terrain_id || value.capabilities?.terrain_id || window.ServerManMapWording.unknownTerrain, true);
    document.title = `Map editor - ${value.profile.display_name}`;
  },
  status: mapLayoutStatus,
  // Announce the current plan state again, for example after a cancelled close
  announce: () => mapLayoutText("map-announcement", document.getElementById("map-status").textContent),
  draftChanges: mapLayoutDraft,
});
