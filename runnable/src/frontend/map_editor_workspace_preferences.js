// Persist explicit layout patches independently of mission saves and recovery.
"use strict";

const mapWorkspacePreferencesState = {started: false, stamp: 0, pending: {}, active: false, timer: null, failed: false, loadFailed: false};

// Keep a failed preference write visible without disabling local editing.
function mapWorkspacePreferencesNotice(failed) {
  const state = mapWorkspacePreferencesState, note = document.getElementById("map-layout-notice");
  state.failed = failed; note.replaceChildren();
  if (!failed) return;
  note.append(document.createTextNode("Layout not saved. "));
  const retry = window.ServerManMapDom.element("button", "Retry", {type: "button", class: "button"});
  retry.addEventListener("click", mapWorkspacePreferencesFlush); note.append(retry);
}

// One request owns its captured patch; newer edits remain pending until that request settles.
async function mapWorkspacePreferencesFlush() {
  const state = mapWorkspacePreferencesState;
  clearTimeout(state.timer);
  if (state.active) return;
  if (!Object.keys(state.pending).length) {
    if (state.loadFailed) { state.started = false; await mapWorkspacePreferencesLoad(); }
    return;
  }
  const patch = state.pending; state.pending = {}; state.active = true;
  try {
    await window.ServerManMapBridge.saveLayout(patch);
    mapWorkspacePreferencesNotice(false);
  } catch (_error) {
    state.pending = {...patch, ...state.pending}; mapWorkspacePreferencesNotice(true);
  } finally {
    state.active = false;
    if (!state.failed && Object.keys(state.pending).length) void mapWorkspacePreferencesFlush();
  }
}

// Ignore a late read after any local interaction; stored dimensions remain preferred when clamped.
async function mapWorkspacePreferencesLoad() {
  const state = mapWorkspacePreferencesState;
  if (state.started) return;
  state.started = true;
  const stamp = state.stamp;
  try {
    const value = await window.ServerManMapBridge.getLayout();
    state.loadFailed = !["MISSING", "VALID"].includes(value.storage_status);
    if (stamp === 0 && stamp === state.stamp) window.ServerManMapWorkspace.restore(value.editor_layout ?? {});
    mapWorkspacePreferencesNotice(state.loadFailed);
  } catch (_error) { state.loadFailed = true; mapWorkspacePreferencesNotice(true); }
}

window.ServerManMapWorkspacePreferences = Object.freeze({
  init: () => {
    if (window.pywebview?.api) void mapWorkspacePreferencesLoad();
    else window.addEventListener("pywebviewready", mapWorkspacePreferencesLoad, {once: true});
    window.addEventListener("pagehide", () => { void mapWorkspacePreferencesFlush(); });
  },
  changed: (patch) => {
    const state = mapWorkspacePreferencesState; state.stamp += 1;
    Object.assign(state.pending, patch); clearTimeout(state.timer);
    state.timer = setTimeout(mapWorkspacePreferencesFlush, 250);
  },
  flush: mapWorkspacePreferencesFlush,
});
