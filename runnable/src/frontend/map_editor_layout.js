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

// Keep status text separate from polite announcements and future coordinates.
function mapLayoutStatus(text) {
  mapLayoutText("map-status", text);
  mapLayoutText("map-announcement", text);
}

// Show only the backend's last-applied comparison, never a browser estimate.
function mapLayoutDraft(value) {
  const words = window.ServerManMapWording;
  const text = value === true ? words.yes : (value === false ? words.no : words.unavailable);
  mapLayoutText("map-draft-changes", text);
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
    mapLayoutText("map-tools-empty", words.toolsUnavailable);
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
  draftChanges: mapLayoutDraft,
});
