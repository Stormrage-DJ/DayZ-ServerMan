// Settle a pending save, and refresh the loaded state after a map-affecting operation ends (8.18). The refresh
// adopts the non-plan fields and keeps unsaved work and the undo history (Architect 2026-10-15 15:46:42, ruling 6).
"use strict";

// The loaded fields that a refresh adopts; the plan, the revision and the association stay with the model
const mapRefreshFields = Object.freeze(["draft_changes", "capabilities", "read_only", "read_only_reason", "detail",
  "evaluation", "interrupted_evidence", "adoption"]);
// editor: the app state ({loaded, loading, closing, model}); running: a refresh runs; again: one more is due.
const mapRefreshState = {editor: null, running: false, again: false};

// Wait for a running plan or bounds save, then save the unsaved draft at once, without the 800 ms wait, as the
// close does (5.4). True: the saved plan equals the draft. A failed or blocked save leaves it unsaved.
async function mapRefreshSettle() {
  const model = mapRefreshState.editor.model, save = window.ServerManMapPlanSave;
  const idle = async () => {
    while (save.busy() || window.ServerManMapBounds.busy()) await new Promise((resolve) => setTimeout(resolve, 50));
  };
  await idle();
  const status = model.status();
  if (status.dirty && status.plan && status.writable && !window.ServerManMapAutosave.blocked()) {
    await save.run();
    await idle();
  }
  const after = model.status();
  return !(after.dirty && after.plan);
}

// Adopt a loaded answer. The same saved revision keeps the draft and the history. Another revision with unsaved
// work shows the stale-revision guidance with Reload plan. Another revision without unsaved work loads again.
function mapRefreshAdopt(value) {
  const editor = mapRefreshState.editor, status = editor.model.status(), words = window.ServerManMapWording;
  if ((value.revision ?? null) !== (status.revision ?? null)) {
    if (status.dirty && status.plan) window.ServerManMapAutosave.failed(words.reloadStale, true, words.savedPlanChanged);
    else void mapRefreshReload();
    return;
  }
  const fields = Object.fromEntries(mapRefreshFields.map((name) => [name, value[name] ?? null]));
  editor.loaded = {...editor.loaded, ...fields};
  editor.model.refresh(fields);
  window.ServerManMapLayout.identity(editor.loaded);
  document.getElementById("map-editor").dataset.readOnly = String(editor.loaded.read_only);
  window.ServerManMapSetAside.load(editor.loaded);
  window.ServerManMapAdopt.load(editor.loaded);
  window.ServerManMapPublish.family();
  // The tier layer of the mission files is read again (ruling 6)
  window.ServerManMapTiers.load(editor.loaded);
  window.ServerManMapAutosave.refresh();
}

// Another saved revision without unsaved work loads again. The map's info notice then says so, announced once
// (Product Owner 2026-10-16 15:50:59 in evidence 8.18). A read-only plan stays silent; a load detail stays in the
// status, and the notice still shows (Product Owner 16:25:27).
async function mapRefreshReload() {
  const editor = mapRefreshState.editor, words = window.ServerManMapPublishWords;
  await window.ServerManMapEditor.load();
  if (!editor.loaded || editor.loading || editor.closing || editor.loaded.read_only) return;
  if (!editor.loaded.detail) return void window.ServerManMapLayout.status(words.reloaded, {notice: "info", announce: true});
  const board = document.getElementById("map-save-notice");
  board.textContent = words.reloaded;
  board.classList.remove("map-save-error", "map-save-success");
  document.getElementById("map-announcement").textContent = words.reloaded;
}

// Settle, load again and adopt. A save during the load reads once more. Inspect drops the answers of jobs that
// started before the end and reads again (4.6, A181 test 15).
async function mapRefreshRun() {
  const state = mapRefreshState, editor = state.editor;
  if (state.running) {
    state.again = true;
    return;
  }
  if (!editor?.loaded || editor.loading || editor.closing) return;
  state.running = true;
  try {
    await mapRefreshSettle();
    const revision = editor.model.status().revision;
    const answer = await window.ServerManMapBridge.reload();
    if (answer.stale || !editor.loaded || editor.loading || editor.closing) return;
    if (editor.model.status().revision !== revision) state.again = true;
    else mapRefreshAdopt(answer.value);
  } catch (_error) {
    // A failed read keeps the loaded state; the next end of a map-affecting operation reads again
  } finally {
    state.running = false;
    window.ServerManMapInspectRead.outcome();
    if (state.again) {
      state.again = false;
      void mapRefreshRun();
    }
  }
}

// The app hands over its state once; the publication flow settles before the Apply preview
window.ServerManMapRefresh = Object.freeze({
  init: (editor) => { mapRefreshState.editor = editor; },
  settle: mapRefreshSettle, run: mapRefreshRun,
});
