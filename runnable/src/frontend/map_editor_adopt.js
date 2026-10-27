// Offer the adoption of a moved, copied or restored mission, show its preview and submit the reviewed fingerprint (10.16).
// The action shows only when the load answer has `adoption` (Architect 2026-10-16 14:33:32, UX-2); the editor never
// infers it from the read-only reason. UI-UX Designer 2026-10-16 14:21:59 in evidence 10.1 sets the states and focus.
"use strict";

// value: the last load answer. enabled: false while the editor loads or closes. editor: the app state.
// reading: the flow runs (the read, the dialog and the submission). waiting: the preview reads.
// own: the submitted operation {id, success, paused} until its end.
// focus: a successful adoption owes the focus to the map after the next load.
const mapAdoptState = {value: null, enabled: true, editor: null, reading: false, waiting: false, own: null, focus: false};
// The lane states in which an operation waits for or holds the lane
const mapAdoptActive = Object.freeze(["ACCEPTED", "QUEUED", "RUNNING", "CANCELLING"]);

// One element of the document
function mapAdoptElement(id) {
  return document.getElementById(id);
}

// The action shows for a load answer with a case, while the editor is neither loading nor closing. While the preview
// reads, it is aria-disabled with ADOPT-2. The lane lock of busy_controls.js keeps its own reason. This state is set
// again at each operation change, because the unlock cannot tell it from its own (architecture.md, 8.19).
function mapAdoptShow() {
  const state = mapAdoptState, button = mapAdoptElement("map-adopt");
  button.hidden = !state.enabled || !state.value?.adoption;
  const ids = (button.getAttribute("aria-describedby") || "").split(" ").filter((id) => id && id !== "map-adopt-reason");
  if (state.waiting) ids.splice(1, 0, "map-adopt-reason");
  button.setAttribute("aria-describedby", ids.join(" "));
  if (state.waiting) button.setAttribute("aria-disabled", "true");
  else if (!("busyLocked" in button.dataset)) button.removeAttribute("aria-disabled");
}

// Refusals that need a reload: the profile, the mission or the saved plan changed since the editor loaded
const mapAdoptReloadCodes = Object.freeze(["REVISION_CONFLICT", "PROFILE_CONTEXT_MISMATCH", "NOT_FOUND"]);

// A refusal of a call: its text in the slot and on the map; a recovery block also offers Check again (8.18).
// A refusal that needs a reload shows ADOPT-33 through the autosave failure path, and Reload plan takes the focus.
function mapAdoptRefused(error) {
  const words = window.ServerManMapPublishWords, blocked = words.blocked(error);
  if (mapAdoptReloadCodes.includes(error.result?.error?.code) && !blocked) {
    mapAdoptState.value = null;
    mapAdoptShow();
    window.ServerManMapAutosave.failed(window.ServerManMapPreviewWords.adoptReload(error.message), true);
    mapAdoptElement("map-save-reload").focus();
    return;
  }
  window.ServerManMapPublishStatus.show(words.refusal(error, "adopt"), "error", {notice: blocked ? "recovery" : "error", announce: true});
  if (blocked) window.ServerManMapPublishStatus.checkable(true);
}

// Submit the reviewed fingerprint; the answer is an operation handle that the follower tracks
async function mapAdoptSubmit(reviewed, answer) {
  const state = mapAdoptState, words = window.ServerManMapPublishWords;
  let handle = null;
  // An editable plan pauses from the submission to the end, as for Apply (UI-UX 2026-10-17 13:06:47, R2)
  const paused = !state.editor.loaded?.read_only;
  if (paused) window.ServerManMapPublish.pause("adopt", true);
  try {
    handle = await window.ServerManMapBridge.adopt(reviewed.fingerprint);
  } catch (error) {
    if (paused) window.ServerManMapPublish.pause("adopt", false);
    return mapAdoptRefused(error);
  }
  if (typeof handle?.operation_id !== "string") {
    if (paused) window.ServerManMapPublish.pause("adopt", false);
    return mapAdoptRefused(new Error(window.ServerManMapWording.invalidAnswer));
  }
  state.own = {id: handle.operation_id, success: answer.success, paused};
  window.ServerManMapPublishStatus.show(words.progress({kind: words.kinds.adopt, state: handle.state ?? "QUEUED"}), "muted", {announce: true});
  await window.ServerManMapFollow.adopt(handle.operation_id);
}

// The preview read: a draft settles first, as for Apply. A failed save keeps the preview closed and shows its reason.
async function mapAdoptRead() {
  const status = window.ServerManMapPublishStatus, reason = window.ServerManMapAutosave.reason;
  if (!(await window.ServerManMapRefresh.settle())) {
    // ADOPT-30 replaces the reading line, with the save reason after it when there is one
    const text = window.ServerManMapPreviewWords.adoptNotOpened;
    status.show(reason() ? `${text} ${reason()}` : text, "error", {notice: "error", announce: true});
    return null;
  }
  try {
    return await window.ServerManMapBridge.previewAdoption();
  } catch (error) {
    mapAdoptRefused(error);
    return null;
  }
}

// Read, show and submit. Preview again reads again; Cancel and Close end. opener gets the focus back, else the map.
async function mapAdoptRun(opener) {
  const state = mapAdoptState, editor = state.editor, words = window.ServerManMapPreviewWords;
  const status = window.ServerManMapPublishStatus, button = mapAdoptElement("map-adopt");
  if (state.reading || state.own || !editor.loaded || editor.loading || editor.closing) return;
  state.reading = true;
  state.focus = false;
  try {
    for (;;) {
      state.waiting = true;
      mapAdoptShow();
      status.show(words.adoptReading, "muted", {announce: true});
      const changes = window.ServerManMapPublish.changes(), answer = await mapAdoptRead();
      state.waiting = false;
      mapAdoptShow();
      if (!answer || editor.closing) return;
      status.idle();
      const pending = window.ServerManMapPreviewAdopt.open(answer, {opener});
      // An operation that ended during the read makes this answer old
      if (changes !== window.ServerManMapPublish.changes()) window.ServerManMapPreviewDialog.stale();
      const result = await pending;
      // The control can hide while the dialog is open; then the map takes the focus
      if (opener === button && button.hidden) mapAdoptElement("map-viewport").focus();
      if (!result) return;
      if (!result.again) return void (await mapAdoptSubmit(result, answer));
    }
  } finally {
    state.reading = state.waiting = false;
    mapAdoptShow();
  }
}

// The end of the own adoption: its result in the slot, the ADOPT-8 line of the reviewed case after a success
function mapAdoptRecord(record) {
  const state = mapAdoptState, own = state.own, words = window.ServerManMapPublishWords;
  const status = window.ServerManMapPublishStatus;
  if (!own || record.operation_id !== own.id) return;
  if (mapAdoptActive.includes(record.state)) return status.show(words.progress(record));
  state.own = null;
  if (own.paused) window.ServerManMapPublish.pause("adopt", false);
  const result = words.result(record), look = {success: "done", failed: "error", recovery: "recovery"}[result.look] ?? "done";
  const done = result.look === "success";
  state.focus = done;
  const text = done && own.success ? `${result.text} ${own.success}` : result.text;
  status.show(text, look, {notice: look === "done" ? null : look, announce: true});
  if (record.state === "RECOVERY_REQUIRED") status.checkable(true);
}

// After a load that follows a successful adoption, the focus goes to the map when the action is gone
function mapAdoptLoaded() {
  const state = mapAdoptState;
  if (!state.focus || state.own) return;
  state.focus = false;
  if (mapAdoptElement("map-adopt").hidden) mapAdoptElement("map-viewport").focus();
}

// Wire the action once; the editor reports each load answer and its enabled state
window.ServerManMapAdopt = Object.freeze({
  init: (editor) => {
    const button = mapAdoptElement("map-adopt"), state = mapAdoptState;
    state.editor = editor;
    if (button.dataset.wired) return;
    button.dataset.wired = "true";
    button.textContent = window.ServerManMapPreviewWords.adopt;
    mapAdoptElement("map-adopt-reason").textContent = window.ServerManMapPreviewWords.adoptBusy;
    window.ServerManBusy.mark(button);
    button.addEventListener("click", (event) => {
      if (!window.ServerManMapPublish.busy()) void mapAdoptRun(event.currentTarget);
    });
    // After the busy helper, which listens first
    document.addEventListener("serverman:operation-change", mapAdoptShow);
  },
  load: (value) => { mapAdoptState.value = value; mapAdoptShow(); mapAdoptLoaded(); },
  enabled: (enabled) => { mapAdoptState.enabled = enabled; mapAdoptShow(); },
  run: mapAdoptRun, record: mapAdoptRecord, own: () => mapAdoptState.own?.id ?? null,
});
