// Main-window map editor action, bound to the current profile and mission load.
"use strict";

// Keep load identity and action feedback outside the Tweaks render tree.
const mapEditorEntryState = {
  profileId: null, generation: 0, workspace: null, loaded: false, reason: "", feedback: "", call: 0,
};
const mapEditorEntryTexts = window.ServerManOperationLabels.mapEditorEntry;

// Refuse activation unless the selected profile has a current loaded mission.
function mapEditorEntryReason() {
  const selected = window.ServerManProfileContext.selectedId();
  if (!selected) return mapEditorEntryTexts.noProfile;
  if (selected !== mapEditorEntryState.profileId
      || !window.ServerManWorkspace.isActive(mapEditorEntryState.workspace)) return mapEditorEntryTexts.reading;
  return mapEditorEntryState.loaded ? "" : mapEditorEntryState.reason || mapEditorEntryTexts.reading;
}

// Refresh only the mounted entry; Tweaks controls and drafts stay in place.
function refreshMapEditorEntry() {
  const slot = document.getElementById("map-editor-entry");
  if (slot) renderMapEditorEntryAction(slot);
}

// Start a fresh mission read and invalidate every older load or action answer.
function beginMapEditorEntryLoad(profileId, workspace) {
  if (profileId !== mapEditorEntryState.profileId) mapEditorEntryState.feedback = "";
  Object.assign(mapEditorEntryState, { profileId, workspace, loaded: false, reason: "" });
  return Object.freeze({ profileId, generation: ++mapEditorEntryState.generation });
}

// Adopt only the current page's economy answer and its matching profile identity.
function loadedMapEditorEntry(token, result) {
  if (token.generation !== mapEditorEntryState.generation
      || token.profileId !== window.ServerManProfileContext.selectedId()
      || !window.ServerManWorkspace.isActive(mapEditorEntryState.workspace)) return;
  mapEditorEntryState.loaded = Boolean(result?.success && result.value?.profile_id === token.profileId);
  mapEditorEntryState.reason = mapEditorEntryState.loaded ? ""
    : window.ServerManOperationMessages.bridgeError(result, mapEditorEntryTexts.missionFailed);
  refreshMapEditorEntry();
}

// Open or focus through the existing host method without submitting a file operation.
async function openMapEditorEntry(button) {
  if (!document.contains(button) || mapEditorEntryReason()) return;
  const profileId = mapEditorEntryState.profileId;
  const generation = mapEditorEntryState.generation;
  const call = ++mapEditorEntryState.call;
  let result;
  try { result = await window.pywebview.api.open_map_editor(profileId); }
  catch (error) { result = { error: { message: error instanceof Error ? error.message : "" } }; }
  if (generation !== mapEditorEntryState.generation || call !== mapEditorEntryState.call
      || profileId !== window.ServerManProfileContext.selectedId()) return;
  mapEditorEntryState.feedback = result?.success
    ? result.value?.focused === true ? mapEditorEntryTexts.focused : ""
    : window.ServerManOperationMessages.bridgeError(result, mapEditorEntryTexts.failed);
  refreshMapEditorEntry();
}

// Render an accessible action and its visible reason using text nodes only.
function renderMapEditorEntryAction(slot) {
  const element = window.ServerManUi.element;
  const button = element("button", "button", mapEditorEntryTexts.open);
  button.id = "open-map-editor"; button.type = "button";
  const reason = mapEditorEntryReason();
  button.setAttribute("aria-disabled", String(Boolean(reason)));
  const line = element("p", "", reason || mapEditorEntryState.feedback || mapEditorEntryTexts.separate);
  line.id = "map-editor-entry-reason"; line.setAttribute("role", "status");
  if (reason) button.setAttribute("aria-describedby", line.id);
  button.addEventListener("click", () => { void openMapEditorEntry(button); });
  slot.replaceChildren(button, line);
}

// A profile change drops old mission bindings and action feedback before the page reloads.
document.addEventListener("serverman:profile-change", () => {
  mapEditorEntryState.generation += 1;
  Object.assign(mapEditorEntryState, { profileId: null, loaded: false, reason: "", feedback: "" });
  refreshMapEditorEntry();
});

// Publish only the integration points used by the Tweaks workspace and renderer.
window.ServerManMapEditorEntry = Object.freeze({
  beginLoad: beginMapEditorEntryLoad, loaded: loadedMapEditorEntry, render: renderMapEditorEntryAction,
});
