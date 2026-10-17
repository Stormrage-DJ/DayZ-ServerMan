// Editor labels and reasons stay separate from rendering and host calls.
"use strict";

// The stored reason is a key; operators see an action they can take.
const mapReadOnlyReasons = Object.freeze({
  invalid_saved_data: "Read only: the saved map plan is not valid. Set aside invalid plan keeps it under a dated name and starts a new, empty plan.",
  // ADOPT-4, ADOPT-5 and ADOPT-6 (Product Owner 2026-10-16 14:26:38 and 14:35:24 in evidence 10.1); the plan of ADOPT-6
  // stays editable, so its text begins "Apply is not possible"
  mission_path_changed: "Read only: the mission path changed. The map plan shown belongs to an earlier path. Adopt the mission to use its map editor records for the new path.",
  returned_to_earlier_path: "Read only: this mission returned to an earlier path. Adopt the mission to use its map editor records again.",
  adoption_required: "Apply is not possible: a restore removed the last Apply record of this profile for this mission. Adopt the mission to continue.",
  // The profile settings name another mission folder or runtime profile folder than the saved plan (D2 23:12:53)
  association_mismatch: "Read only: the saved map plan does not match the mission folder or runtime profile folder of this profile. Correct the profile settings, then load the plan again.",
});

// Reasons of a changed profile or mission; the caller adds the Reload plan sentence.
const mapContextReasons = Object.freeze({
  association_mismatch: "This profile now uses a different mission from the one in this map editor.",
  mission_path_changed: "The mission path of this profile changed.",
  returned_to_earlier_path: "This mission returned to an earlier path.",
  // The ADOPT-6 Apply refusal (Product Owner 2026-10-16 14:26:38 in evidence 10.1)
  adoption_required: "A restore removed the last Apply record of this profile for this mission. Adopt the mission before you apply.",
  other: "The profile or its mission changed.",
});
// Plan states with editor wording; the backend detail of the same state has the same text (Architect 15:44:32)
const mapPlanDetails = Object.freeze({OTHER_ASSOCIATION: "The saved plan belongs to another profile or mission."});

// Plan conflicts require the editor's load action, without main-shell navigation.
function mapBridgeError(result) {
  if (result?.error?.code === "REVISION_CONFLICT" && result.error.details?.reason === "stale_revision") {
    return "The map plan changed. Reload the plan before saving again.";
  }
  // A changed profile or mission has editor wording; the shared text of this code names a backup (QA-5.3-F2)
  if (result?.error?.code === "PROFILE_CONTEXT_MISMATCH") return mapContextReasons[result.error.details?.reason] ?? mapContextReasons.other;
  // Other failures retain the shared error and diagnostic wording.
  return window.ServerManOperationMessages.bridgeError(result, "The map plan could not be loaded.");
}

// Tier-layer reasons: capability reasons of the mission and refusals of the tier read.
const mapTierReasons = Object.freeze({
  areaflags_missing: "This mission has no areaflags.map, so its original tiers cannot be shown.",
  mission_file_unreadable: "A mission file could not be read.",
  tier_header_invalid: "The tier map of this mission is not valid.",
  limits_missing: "This mission has no cfglimitsdefinition.xml, so its tier names are not known.",
  limits_invalid: "The value flags in cfglimitsdefinition.xml are not valid.",
  tier_unknown: "This tier is not in the tier map of this mission.",
  over_budget: "The original tiers of this mission are too large to show on the map. Inspect still shows the tier of each point.",
});

// Image reasons: availability of the stored image and refusals of a chosen image.
const mapImageReasons = Object.freeze({
  missing: "The map image file is missing.", replaced: "The map image file was replaced by a different file.",
  unreadable: "The map image file could not be read.", not_a_file: "The chosen path is not a file.",
  reparse_point: "The chosen path is a link. Choose the image file itself.",
  unsupported_image_type: "This file is not a PNG or JPEG image that the map editor can show. Choose a PNG or JPEG file.",
  selection_unknown: "The chosen image is no longer known.", selection_mismatch: "The chosen image is no longer known.",
  selection_changed: "The chosen image changed after it was chosen.", part_unknown: "The image load failed.",
});

// Budget texts give the measured size against the accepted A9 limits.
function mapDisplayReason(kind, details) {
  const reason = details?.reason;
  if (reason === "over_budget" && kind === "image") {
    const count = (value) => Number(value).toLocaleString("en-US");
    return details.unit === "pixels" ?
      `This image is ${count(details.width)} × ${count(details.height)} pixels. The map editor shows images up to 8,192 pixels on each side. Make the image smaller and choose it again.` :
      `This image is ${(details.size / 1048576).toFixed(1)} MiB. The map editor shows images up to 32 MiB. Save a smaller JPEG or PNG file and choose it again.`;
  }
  return (kind === "tier" ? mapTierReasons : mapImageReasons)[reason] ?? null;
}

// A refused display read uses its closed reason, else the shared bridge wording.
function mapDisplayError(kind, error) {
  return mapDisplayReason(kind, error?.result?.error?.details) ?? error?.message ?? mapInvalidAnswer;
}

// The one text of an answer that the editor cannot read (bridge, display reads, Apply and Restore)
const mapInvalidAnswer = "The map editor received an invalid answer.";
// Tool names of the panel and the footer; no tool shows "No tool active"
const mapToolNames = Object.freeze({select: "Select", circle: "Circle", polygon: "Polygon", encounter: "Encounter", inspect: "Inspect"});

// Public editor wording is independent of the main shell's page state.
window.ServerManMapWording = Object.freeze({
  // Status of the case copy with an editable plan (Product Owner 2026-10-17 13:41:00 in evidence 10.31)
  adoptCopy: "Adoption is needed: this mission is a copy of another mission path. Adopt the mission to continue.",
  loading: "Loading map plan", loaded: "Map plan loaded", unsaved: "Not saved yet",
  boundsRequired: "No map plan yet. Enter and confirm the mission bounds before editing.",
  failed: "The map plan could not be loaded.", closing: "Closing", unknownTerrain: "Unknown terrain", invalidAnswer: mapInvalidAnswer,
  unavailable: "Not available", noTool: "No tool active", yes: "Yes", no: "No",
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
  originalTiers: "Original tiers", tierLegend: "Original tier legend", tiersReading: "Reading original tiers",
  tiersNone: "This mission has no tier that its tier map can hold.", tiersHidden: "Original tiers are hidden.",
  tiersChanged: "The tier map changed while it was read. Load the plan again.",
  tierFailed: (tier, reason) => `${tier}: ${reason}`,
  imageTitle: "Map image", chooseImage: "Choose image", chooseAgain: "Choose the image again", dimImage: "Dim image",
  noImage: "No map image. The plain terrain is shown.", imageLoading: "Loading the map image",
  imageShown: (name) => `Map image: ${name}`, imageAction: "Change map image", calibrateAction: "Calibrate map image",
  imageKept: "The grid, overlays and objects stay in place.",
  calibrate: "Calibrate with three points",
  calibrationGuidance: "Choose three points far apart and not on one line, such as grid crossings or map corners.",
  calibrationPoint: (index) => `Point ${index}`, calibrationPick: "Pick on map", calibrationPicking: "Click the map image",
  calibrationPixel: (x, y) => `Image pixel ${x.toFixed(1)}, ${y.toFixed(1)}`, calibrationNoPixel: "Image pixel not picked",
  calibrationNext: (index) => `Click the map image for point ${index}, then type its coordinates.`,
  calibrationEnter: "Type the coordinates of each point.",
  calibrationCollinear: "The three image points are on one line. Choose points far apart and not on one line.",
  calibrationSingular: "These points do not give a usable calibration. Choose points far apart.",
  calibrationReady: "The preview shows the image with these points. Accept stores them.",
  calibrationFitted: "The preview fits the image to the terrain bounds. Accept stores it.",
  calibrationSkew: "These points skew or stretch the image. Check the clicked places and the typed coordinates, or choose points farther apart.",
  calibrationMirror: "These points mirror the image. Check that X and Z are not swapped.",
  calibrationAccept: "Accept", calibrationFit: "Fit to terrain bounds", calibrationCancel: "Cancel",
  circleTool: "Circle", polygonTool: "Polygon", circleRadius: "Circle radius (m)", finish: "Finish", discardOutline: "Discard outline",
  toolStatus: (tool, count) => (mapToolNames[tool] ?? "No tool active") +
    (count ? `; outline ${count} ${count === 1 ? "point" : "points"}` : ""),
  // The footer slot is one line of 12ch; its title holds the full toolStatus text (QA-4.4-F8)
  toolStatusShort: (tool, count) => (mapToolNames[tool] ?? "No tool active") +
    (count ? ` · ${count} pt` : ""),
  outlinePoints: (count, crossing) => `Unfinished outline: ${count} ${count === 1 ? "point" : "points"}` +
    (crossing ? `. ${crossing} ${crossing === 1 ? "edge crosses or touches" : "edges cross or touch"}.` : ""),
  radiusInvalid: (limit) => `Enter a radius from 1 to ${limit} m. New circles keep the previous radius.`,
  outsideCircle: "This point is outside the terrain bounds. Click inside the bounds to place a circle.",
  outsidePoint: "This point is outside the terrain bounds. Click inside the bounds to add an outline point.",
  pointTooClose: "This point is too close to another point of the outline.",
  outlineNeedsPoints: "An outline needs 3 points",
  outlineCrosses: "The outline crosses or touches itself. Press Backspace on the map to remove the last point until no edges cross or touch, or choose Discard outline.",
  outlineDiscarded: "Outline discarded.",
  outlineArea: "The outline encloses less than 1 m².",
  circleAction: "Add circle", outlineAction: "Add outline", saving: "Saving", saved: "Saved",
  // Selection and geometry editing (4.5)
  selectTool: "Select", objectFallback: "Object", zoomSelection: "Zoom to selection", selectFirst: "Select an item first",
  noSelection: "No item selected. Shift+click an item on the map, use Select, or choose it in the list.",
  objectName: "Name", centreX: "Centre X (m)", centreZ: "Centre Z (m)", objectRadius: "Radius (m)",
  centreInvalid: (bounds) => `Enter a centre inside the terrain bounds: X from ${bounds?.x_min} to ${bounds?.x_min + bounds?.width} m, ` +
    `Z from ${bounds?.z_min} to ${bounds?.z_min + bounds?.height} m. The circle stays where it is.`,
  deleteObject: "Delete", deleteAction: "Delete object", deleted: (name) => `${name} deleted. Undo brings it back.`, radiusEditInvalid: (limit) => `Enter a radius from 1 to ${limit} m. The circle keeps its radius.`,
  lockedNote: "Locked. Unlock it in the list to change it.", hiddenNote: "Hidden on the map. It still counts in the mission settings.",
  moveAction: "Move object", resizeAction: "Resize circle", vertexAction: "Move outline point",
  dragOutside: "The item would leave the terrain bounds.", dragCrosses: "The outline would cross or touch itself.",
  dragArea: "The outline would enclose less than 1 m².", dragClose: "Two points of the outline would be too close.",
  dragWarning: (reason) => `${reason} Move back, or press Escape to cancel.`,
  dragRefused: (reason) => `${reason} The item did not change.`,
  // Save Status and Close Dialogs: a save waits for an apply or restore that holds the association lock
  savingDelayed: "Saving takes longer than usual. An apply or restore of this mission can delay it.", reloadPlan: "Reload plan",
  updating: "Updating", saveUnconfirmed: "The save result could not be confirmed.",
  // Plan-save refusals that offer Reload plan name the loss of the draft, then Export plan (UI-UX 2026-10-13 00:01:25, item 4; XFER-13)
  reloadStale: "Not saved: The saved map plan changed. Reload plan loads it and discards your unsaved changes. To keep your unsaved changes, choose Export plan first.",
  reloadInvalid: "Not saved: The saved map plan is not valid. Reload plan shows it read only and discards your unsaved changes. To keep your unsaved changes, choose Export plan first.",
  savedPlanChanged: "The saved map plan changed.", savedPlanInvalid: "The saved map plan is not valid.",
  reloadReason: (reason) => `Not saved: ${reason} Reload plan discards your unsaved changes. To keep your unsaved changes, choose Export plan first.`,
  notSaved: (reason) => `Not saved: ${reason}`,
  // Set aside of invalid saved data (5.3): the file is renamed, never deleted
  setAside: "Set aside invalid plan", setAsideTitle: "Set aside the invalid plan?", setAsideCancel: "Cancel",
  // UI-UX 2026-10-13 15:19:47: "not valid" in sentences; "invalid" only where the texts name the button
  setAsideMessage: "The plan file stays in its folder under a dated name. Nothing is deleted, and mission files do not change. The editor then starts a new, empty plan. The new plan does not contain the objects of the invalid plan.",
  setAsideConfirm: "Set aside", settingAside: "Setting aside the invalid plan", setAsideOpen: "A new, empty plan is open.",
  setAsideDone: (name) => `The invalid plan was kept as ${name} in its folder.`, notSetAside: (reason) => `Not set aside: ${reason}`,
  setAsideNotInvalid: "Not set aside: The saved map plan is valid now, or it is no longer there. Reload plan shows its current state.",
  setAsideReload: (reason) => `Not set aside: ${reason} Reload plan shows its current state.`,
  outlineReturned: (reason) => `The outline was not saved: ${reason}. It is back in the drawing. Correct it and choose Finish.`,
  // Local plan conflicts and shared diagnostics remain separate wording cases.
  error: mapBridgeError, displayReason: mapDisplayReason, displayError: mapDisplayError,
  // Only the accepted read-only keys receive specialized editor guidance.
  readOnly: (reason) => mapReadOnlyReasons[reason] || "Read only: this plan cannot be edited.",
  // The load detail: editor wording for a known plan state, else the backend detail
  detail: (value) => mapPlanDetails[value.state] ?? value.detail,
});
