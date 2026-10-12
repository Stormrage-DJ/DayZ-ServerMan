// Editor labels and reasons stay separate from rendering and host calls.
"use strict";

// The stored reason is a key; operators see an action they can take.
const mapReadOnlyReasons = Object.freeze({
  invalid_saved_data: "Read only: the saved plan is invalid. Review the saved data before editing.",
  mission_path_changed: "Read only: the mission path changed. Reload the active mission before editing.",
  returned_to_earlier_path: "Read only: this mission returned to an earlier path. Adoption is required.",
});

// Plan conflicts require the editor's load action, without main-shell navigation.
function mapBridgeError(result) {
  if (result?.error?.code === "REVISION_CONFLICT" && result.error.details?.reason === "stale_revision") {
    return "The map plan changed. Reload the plan before saving again.";
  }
  // Other failures retain the shared error and diagnostic wording.
  return window.ServerManOperationMessages.bridgeError(result, "The map plan could not be loaded.");
}

// Public editor wording is independent of the main shell's page state.
window.ServerManMapWording = Object.freeze({
  loading: "Loading map plan", loaded: "Map plan loaded", unsaved: "Not saved yet",
  boundsRequired: "No map plan yet. Enter and confirm the mission bounds before editing.",
  failed: "The map plan could not be loaded.", closing: "Closing", unknownTerrain: "Unknown terrain",
  unavailable: "Not available", noTool: "No tool active", yes: "Yes", no: "No",
  toolsUnavailable: "Drawing tools are not available yet.",
  propertiesUnavailable: "Property editing is not available yet.",
  layersUnavailable: "Map layers are not available yet.",
  showControls: "Show map controls", hideControls: "Hide map controls",
  fitMap: "Fit map", coordinateMode: "Coordinates", grid: "Grid", native: "Native", both: "Both",
  plainTerrain: "Plain terrain view", unknownBounds: "Terrain bounds are not available.",
  unavailableViewport: "The map viewport is not available.",
  conversionFailure: "These bounds cannot be converted reliably between native and grid coordinates at the application's numeric precision.",
  conversionActions: "Map actions that require this conversion are unavailable. Your loaded bounds and saved geometry are unchanged.",
  navigationHelp: "Wheel to zoom; middle drag or Space+drag to pan. Native X/Z and distances are in metres.",
  boundsTitle: "Terrain bounds", boundsManualSave: "Confirm and save manual bounds", boundsDetectedSave: "Confirm detected bounds",
  boundsGuidance: "Use the actual terrain bounds in native metres. Saving keeps every placement at its native position.",
  boundsDetected: "Detected bounds", boundsNoDetection: "Detected bounds are not available. Enter the actual bounds manually.",
  boundsConfirmed: "Bounds confirmed", boundsUnconfirmed: "Bounds not confirmed", boundsMission: "mission",
  boundsOriginInvalid: "X and Z minimum must be finite numbers from -1000000 to 1000000 metres.",
  boundsExtentInvalid: "Width and height must be finite numbers above 0 and at most 100000 metres.",
  boundsReviewTitle: "Review placements", boundsPrevious: "Previous bounds", boundsProposed: "Proposed bounds",
  boundsReviewMessage: "Review every placement against the proposed bounds. Native coordinates stay unchanged. Invalid placements will refuse the save.",
  boundsCancel: "Cancel", boundsReviewSave: "I reviewed placements; save bounds",
  boundsSaving: "Saving bounds", boundsSaved: "Bounds saved", boundsNotSaved: "Bounds not saved",
  boundsResultUnknown: "The save result could not be confirmed. Reload the plan before saving again.",
  boundsReviewHistory: "I reviewed placements; change draft bounds",
  boundsHistoryReviewMessage: "Review every placement against the draft bounds. Native coordinates stay unchanged. This changes the draft only; it does not save.",
  localUnsaved: "Current draft has unsaved changes", savedSnapshot: "Bounds saved; current draft has unsaved changes",
  localNoPlan: "No local draft. The saved plan is unchanged.",
  objectTypes: Object.freeze({loot_circle: "Loot circle", loot_polygon: "Loot polygon", encounter: "Encounter"}),
  capacityPresets: Object.freeze({sparse: "Sparse -25%", normal: "Normal 0%", rich: "Rich +50%", very_rich: "Very Rich +100%"}),
  tier: "Tier", inherit: "Inherit", zoneCapacity: "Zone capacity", visible: "Visible", locked: "Locked",
  moveUp: "Move up", moveDown: "Move down", objectList: "Objects in priority order",
  undo: "Undo", redo: "Redo", selectObject: "Select object",
  noObjects: "No objects in this plan.", noObjectPlan: "Enter and confirm bounds to create a plan.",
  objectListUnavailable: "The object list is unavailable.",
  invalidName: "Object names need 1 to 80 characters without control characters.",
  invalidKind: "This object kind is not supported.", invalidIdentifier: "Object IDs must stay valid, stable and unique.",
  invalidAction: "This draft action is not valid.", objectLocked: "Unlock the object before editing its properties or geometry.",
  propertyAction: "Edit properties", flagAction: "Change visibility or lock", orderAction: "Change priority", boundsAction: "Confirm terrain bounds",
  // Local plan conflicts and shared diagnostics remain separate wording cases.
  error: mapBridgeError,
  // Only the accepted read-only keys receive specialized editor guidance.
  readOnly: (reason) => mapReadOnlyReasons[reason] || "Read only: this plan cannot be edited.",
});
