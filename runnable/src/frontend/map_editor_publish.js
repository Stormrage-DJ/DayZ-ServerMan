// Run Apply and Restore from the editor (8.18). This module owns the header controls, the save settle and the 8.10
// preview. It also owns the submission, the pause and the own operation in the status area. Rules: Architect
// 2026-10-15 15:46:42, rulings 3 and 6; UI-UX Designer 2026-10-15 15:58:12, sections 1, 2 and 7.
"use strict";

// editor: the app state; reading: a settle, a preview read or a dialog runs, so the openers ignore more activations;
// own: the submitted operation {id, mode} until its end; changes: counts the ends and unknown changes, so a preview
// that was read before one opens stale.
const mapPublishState = {editor: null, reading: false, own: null, changes: 0};
// The busy-marked openers and their labels in the publication words
const mapPublishOpeners = Object.freeze([["map-apply", "apply"], ["map-restore", "restore"],
  ["map-restore-previous", "previous"], ["map-restore-original", "original"]]);

// One element of the header group or the status area
function mapPublishElement(id) {
  return document.getElementById(id);
}

// The family reason describes Apply and Restore original while it shows (UI-UX section 1). A busy lock keeps
// its reason behind it, and removes only that reason at its end.
function mapPublishFamily() {
  const shown = !mapPublishElement("map-family-reason").hidden;
  for (const id of ["map-apply", "map-restore-original"]) {
    const control = mapPublishElement(id), busy = (control.getAttribute("aria-describedby") || "").includes("busy-reason");
    const ids = [...(shown ? ["map-family-reason"] : []), ...(busy ? ["busy-reason"] : [])];
    if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
    else control.removeAttribute("aria-describedby");
  }
}

// Open or close the mode group of Restore. focus: give the focus back to Restore.
function mapPublishModes(open, focus = false) {
  const restore = mapPublishElement("map-restore");
  mapPublishElement("map-restore-modes").hidden = !open;
  restore.setAttribute("aria-expanded", String(open));
  if (focus) restore.focus();
}

// The pause lasts from the submission to the end (UI-UX section 7). The model shows its read-only state, and the
// pause stops autosave, the bounds save and the calibration. The end shows the save state again.
function mapPublishPause(mode, paused) {
  const editor = mapPublishState.editor, autosave = window.ServerManMapAutosave;
  editor.model.pause(paused);
  window.ServerManMapBounds.pause(paused);
  window.ServerManMapTools.pause(paused ? window.ServerManMapPublishWords.pausedText(mode) : "");
  if (paused) {
    autosave.pause();
    window.ServerManMapCalibration.cancel();
  } else if (editor.loaded && !editor.closing) autosave.restore({...editor.loaded, ...editor.model.status()});
}

// A synchronous refusal: its text in the slot and on the map; a recovery block also offers Check again
function mapPublishRefused(error, mode) {
  const words = window.ServerManMapPublishWords, status = window.ServerManMapPublishStatus, blocked = words.blocked(error);
  status.show(words.refusal(error, mode), "error", {notice: blocked ? "recovery" : "error", announce: true});
  if (blocked) status.checkable(true);
}

// Submit the reviewed preview; the editor pauses first, so no save can make the Apply stale (ruling 6)
async function mapPublishSubmit(mode, reviewed) {
  const state = mapPublishState, bridge = window.ServerManMapBridge, words = window.ServerManMapPublishWords;
  mapPublishPause(mode, true);
  let handle = null;
  try {
    handle = mode === "apply" ? await bridge.apply(reviewed.fingerprint, reviewed.choices) :
      await bridge.restore(mode, reviewed.fingerprint);
  } catch (error) {
    mapPublishPause(mode, false);
    mapPublishRefused(error, mode);
    return;
  }
  if (typeof handle?.operation_id !== "string") {
    mapPublishPause(mode, false);
    mapPublishRefused(new Error(window.ServerManMapWording.invalidAnswer), mode);
    return;
  }
  state.own = {id: handle.operation_id, mode};
  window.ServerManMapPublishStatus.show(words.progress({kind: words.kinds[mode], state: handle.state ?? "QUEUED"}),
    "muted", {announce: true});
  await window.ServerManMapFollow.adopt(handle.operation_id);
}

// The saved plan object of an ID, for the names of the Apply preview (8.10)
function mapPublishObject(id) {
  return mapPublishState.editor.model.snapshot()?.objects?.find((item) => item.id === id) ?? null;
}

// Apply settles a pending save first; a restore reads no plan. Preview again reads again; Cancel ends.
async function mapPublishActivate(mode, opener) {
  const state = mapPublishState, editor = state.editor, bridge = window.ServerManMapBridge;
  const words = window.ServerManMapPublishWords, status = window.ServerManMapPublishStatus;
  if (state.reading || state.own || !editor.loaded || editor.loading || editor.closing) return;
  state.reading = true;
  status.reading();
  try {
    for (;;) {
      // A failed or refused save keeps the preview closed: the 8.10 text, then the save reason (decision 9)
      if (mode === "apply" && !(await window.ServerManMapRefresh.settle())) {
        const reason = window.ServerManMapAutosave.reason();
        status.show(reason ? `${words.notOpened} ${reason}` : words.notOpened, "error", {notice: "error", announce: true});
        return;
      }
      const changes = state.changes;
      let answer;
      try {
        answer = mode === "apply" ? await bridge.previewApply() : await bridge.previewRestore(mode);
      } catch (error) {
        mapPublishRefused(error, mode);
        return;
      }
      if (editor.closing) return;
      // No text while the dialog is open, except a kept recovery text (UI-UX 2026-10-16 15:49:13, R1)
      status.idle();
      const pending = mode === "apply" ? window.ServerManMapPreviewApply.open(answer, {opener, objectOf: mapPublishObject,
        outline: window.ServerManMapTools.draft().length > 0}) : window.ServerManMapPreviewRestore.open(mode, answer, {opener});
      // An operation that ended during the read makes this answer old (UI-UX section 6)
      if (changes !== state.changes) window.ServerManMapPreviewDialog.stale();
      const result = await pending;
      if (!result) return;
      // The second button of a refused Apply dialog opens the adoption preview in its place (10.16)
      if (result.adopt) return void (await window.ServerManMapAdopt.run(opener));
      if (!result.again) return void (await mapPublishSubmit(mode, result));
      status.reading();
    }
  } finally {
    state.reading = false;
  }
}

// Each record of the lane: the own operation shows its progress, and its end ends the pause before the refresh
function mapPublishRecord(record) {
  const state = mapPublishState, own = state.own, words = window.ServerManMapPublishWords;
  const status = window.ServerManMapPublishStatus;
  // An adoption of this editor shows its own progress and end (10.16)
  window.ServerManMapAdopt.record(record);
  if (!own || record.operation_id !== own.id) return;
  if (["ACCEPTED", "QUEUED", "RUNNING", "CANCELLING"].includes(record.state)) {
    status.show(words.progress(record));
    return;
  }
  state.own = null;
  mapPublishPause(own.mode, false);
  const result = words.result(record), look = {success: "done", failed: "error", recovery: "recovery"}[result.look] ?? "done";
  status.show(result.text, look, {notice: look === "done" ? null : look, announce: true});
  if (record.state === "RECOVERY_REQUIRED") status.checkable(true);
}

// A map-affecting end from any origin, or an unknown change: an open preview is stale, and the editor refreshes
function mapPublishEnded(unknown) {
  const state = mapPublishState;
  state.changes += 1;
  window.ServerManMapPreviewDialog.stale();
  // The lane runs one map operation: an own Apply, restore or adoption
  const own = state.own?.id ?? window.ServerManMapAdopt.own();
  if (unknown && own) void window.ServerManMapFollow.adopt(own);
  void window.ServerManMapRefresh.run();
}

// Wire the header group once, mark the openers busy, and start the follower (UI-UX section 1)
function mapPublishInit(editor) {
  const state = mapPublishState, words = window.ServerManMapPublishWords;
  const wrapper = mapPublishElement("map-publication"), group = mapPublishElement("map-restore-modes");
  if (state.editor) return;
  state.editor = editor;
  for (const [id, label] of mapPublishOpeners) {
    mapPublishElement(id).textContent = words[label];
    window.ServerManBusy.mark(mapPublishElement(id));
  }
  mapPublishElement("map-apply").addEventListener("click", (event) => { void mapPublishActivate("apply", event.currentTarget); });
  mapPublishElement("map-restore").addEventListener("click", () => mapPublishModes(group.hidden));
  for (const mode of ["previous", "original"]) {
    mapPublishElement(`map-restore-${mode}`).addEventListener("click", () => {
      mapPublishModes(false, true);
      void mapPublishActivate(mode, mapPublishElement("map-restore"));
    });
  }
  // Escape closes the group and stops there, so the key map keeps the selection
  wrapper.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || group.hidden) return;
    event.preventDefault();
    event.stopPropagation();
    mapPublishModes(false, true);
  });
  wrapper.addEventListener("focusout", (event) => { if (!wrapper.contains(event.relatedTarget)) mapPublishModes(false); });
  window.addEventListener("pointerdown", (event) => {
    if (!group.hidden && !wrapper.contains(event.target)) mapPublishModes(false);
  }, true);
  window.ServerManMapPublishStatus.init();
  window.ServerManMapFollow.start({record: mapPublishRecord, ended: mapPublishEnded});
}

// The app starts the flow and updates the family description after each load
window.ServerManMapPublish = Object.freeze({init: mapPublishInit, family: mapPublishFamily,
  busy: () => mapPublishState.reading || Boolean(mapPublishState.own), changes: () => mapPublishState.changes,
  pause: mapPublishPause,
  // Export plan and Import plan share the reading guard, so two dialogs or reads never run together (9.1)
  reading: () => mapPublishState.reading, claim: () => !mapPublishState.reading && (mapPublishState.reading = true),
  release: () => { mapPublishState.reading = false; }});
