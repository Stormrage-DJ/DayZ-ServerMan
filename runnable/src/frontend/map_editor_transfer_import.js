// The flow of Import plan and Copy from another profile (9.1, 9.2): settle, the source read with both previews in
// the host, the compatibility dialog, then one revision-guarded save and one undo step (D7). Architect 2026-10-17
// 17:10:42 in evidence 9.1, sections 4 and 5: the pause holds from the commit through the reload to the model
// replace. kind is "import" or "copy"; its words are in ServerManMapTransferWords.kinds.
"use strict";

// The bridge reasons whose own text shows unchanged (Product Owner texts of evidence 10.1, PO-91-2)
const mapTransferImportShown = Object.freeze(["mission_path_changed", "plan_limit", "confirmation_required",
  "invalid_document"]);

// The words of one refusal, without the "Not imported:" lead
function mapTransferImportWords(error) {
  const words = window.ServerManMapTransferWords, failure = error.result?.error, details = failure?.details ?? {};
  if (details.reason === "import_file_refused") return words.file(details.kind, details.file_name ?? "");
  if (details.reason === "import_selection_unknown") return words.selectionUnknown;
  // The destination text has a CLI sentence; the source text of a copy shows unchanged (PO-91-1, PO-91-2)
  if (details.reason === "no_valid_plan") return /^The source/.test(failure.message) ? failure.message : words.noValidPlan;
  if (mapTransferImportShown.includes(details.reason) && failure.message) return failure.message;
  return error.result ? window.ServerManMapWording.error(error.result) : error.message;
}

// The reload path of a stale plan, as the adoption refusal takes it: the stale text, then Reload plan has the focus
function mapTransferImportStale(kind) {
  window.ServerManMapAutosave.failed(window.ServerManMapTransferWords.kinds[kind].stale, true);
  document.getElementById("map-save-reload").focus();
}

// Show a refusal in the slot and on the map; a recovery block also offers Check again (8.18). lead: the
// "Not imported:" form of a refused commit.
function mapTransferImportRefused(error, lead, kind) {
  const words = window.ServerManMapTransferWords.kinds[kind], publish = window.ServerManMapPublishWords;
  const status = window.ServerManMapPublishStatus, failure = error.result?.error;
  if (failure?.code === "REVISION_CONFLICT" && failure.details?.reason === "stale_revision") return mapTransferImportStale(kind);
  if (publish.blocked(error)) {
    status.show(publish.refusal(error, "import"), "error", {notice: "recovery", announce: true});
    return status.checkable(true);
  }
  const reason = mapTransferImportWords(error), bare = failure?.details?.reason === "import_selection_unknown";
  status.show(lead && !bare ? words.refused(reason) : reason, "error", {notice: "error", announce: true});
}

// One host read of the file and its previews. A file that is not a valid plan opens the refused dialog with its
// problems; another refusal shows its text and ends the flow (null).
async function mapTransferImportRead(call, kind) {
  try {
    return await call();
  } catch (error) {
    const failure = error.result?.error, details = failure?.details ?? {};
    if (details.reason === "invalid_document" && typeof details.selection_id === "string") {
      return {refused: true, message: failure.message, problems: details.problems ?? [], selection_id: details.selection_id};
    }
    mapTransferImportRefused(error, false, kind);
    return null;
  }
}

// The pause of the commit: the model is read only, autosave and the bounds save stop, the pause text shows on the
// map (XFER-9). The end shows the save state again.
function mapTransferImportPause(paused, kind) {
  const editor = window.ServerManMapTransfer.editor(), autosave = window.ServerManMapAutosave;
  editor.model.pause(paused);
  window.ServerManMapBounds.pause(paused);
  window.ServerManMapTools.pause(paused ? window.ServerManMapTransferWords.kinds[kind].paused : "");
  if (paused) {
    autosave.pause();
    window.ServerManMapCalibration.cancel();
  } else if (editor.loaded && !editor.closing) autosave.restore({...editor.loaded, ...editor.model.status()});
}

// The saved import becomes one named undo step when the reload has the commit revision and the same association
function mapTransferImportReplace(saved, loaded, kind) {
  const editor = window.ServerManMapTransfer.editor(), current = editor.model.status().association ?? {};
  const value = loaded?.value, same = (name) => value?.association?.[name] === current[name];
  if (loaded?.stale || value?.revision !== saved.revision ||
      !["profile_id", "mission_root", "mission_key", "runtime_profile_key"].every(same)) return false;
  editor.loaded = structuredClone(value);
  editor.model.replace(window.ServerManMapTransferWords.kinds[kind].undo, value);
  return true;
}

// Select the imported objects and fit the view to them: all objects for Replace, the top block for Merge (section
// 5, item 8). The window has one selection, so the first imported object is selected.
function mapTransferImportShow(mode, report) {
  const objects = window.ServerManMapTransfer.editor().model.snapshot()?.objects ?? [];
  const imported = mode === "replace" ? objects : objects.slice(0, (report.object_count ?? 0) - (report.excluded_count ?? 0));
  const boxes = imported.map((item) => window.ServerManMapProperties.box(item)).filter(Boolean);
  window.ServerManMapLayers.select(imported[0]?.id ?? null);
  if (!boxes.length) return;
  const x_min = Math.min(...boxes.map((box) => box.x_min)), z_min = Math.min(...boxes.map((box) => box.z_min));
  const x_max = Math.max(...boxes.map((box) => box.x_min + box.width)), z_max = Math.max(...boxes.map((box) => box.z_min + box.height));
  window.ServerManMapViewportUi.view().focus({x_min, z_min, width: x_max - x_min, height: z_max - z_min});
}

// Commit the reviewed choice under the pause, read the saved plan and replace the draft; the pause ends in finally
async function mapTransferImportSubmit(kind, answer, choice, opener) {
  const words = window.ServerManMapTransferWords, status = window.ServerManMapPublishStatus, bridge = window.ServerManMapBridge;
  const editor = window.ServerManMapTransfer.editor(), text = words.kinds[kind];
  let saved = null, replaced = false, failure = null;
  mapTransferImportPause(true, kind);
  status.show(text.working, "muted", {announce: true});
  try {
    saved = kind === "copy" ? await bridge.copyPlan(answer.source_profile_id, choice.mode, choice.revision, choice.confirmed) :
      await bridge.importPlan(answer.selection_id, choice.mode, choice.revision, choice.confirmed);
    replaced = mapTransferImportReplace(saved, await bridge.reload(), kind);
  } catch (error) {
    failure = error;
  } finally {
    mapTransferImportPause(false, kind);
  }
  if (editor.closing) return;
  if (!saved) return mapTransferImportRefused(failure, true, kind);
  // A saved plan whose reload does not match loads again; Undo and Redo then start from the saved plan
  if (!replaced) {
    await window.ServerManMapEditor.load();
    return window.ServerManMapLayout.status(window.ServerManMapPublishWords.reloaded, {notice: "info", announce: true});
  }
  const report = saved.report ?? {}, left = report.excluded_count ?? 0;
  mapTransferImportShow(choice.mode, report);
  const done = text.done(saved.revision);
  status.show(left ? `${done} ${words.leftOut(left)}` : done, "done", {notice: "success", announce: true});
  if (report.placement_review) window.ServerManMapTransfer.remind(words.placement);
  window.ServerManMapTransfer.focus(opener);
}

// Run the flow of one kind from its opener. reader: {first(), again(answer)}; each returns an answer, {cancelled:
// true} or null (the refusal is shown). Preview again reads both previews again for the same file or source.
async function mapTransferImportFlow(kind, opener, reader) {
  const text = window.ServerManMapTransferWords.kinds[kind], status = window.ServerManMapPublishStatus;
  const publish = window.ServerManMapPublish, editor = window.ServerManMapTransfer.editor();
  if (window.ServerManMapTransfer.importReason() || !publish.claim()) return;
  window.ServerManMapTransfer.render();
  try {
    // A pending save settles first; a failed or blocked save keeps the file dialog closed (XFER-10)
    if (!(await window.ServerManMapRefresh.settle()) || window.ServerManMapAutosave.blocked()) {
      const reason = window.ServerManMapAutosave.reason();
      return void status.show(reason ? `${text.notOpened} ${reason}` : text.notOpened, "error", {notice: "error", announce: true});
    }
    // An operation that ends during a read makes its previews old (UI-UX section 5, Stale)
    let changes = publish.changes(), answer = await reader.first();
    for (;;) {
      if (!answer || editor.closing) return;
      if (answer.cancelled === true) return void status.idle();
      // The previews must name the revision of the draft; another one means the saved plan changed
      if (!answer.refused && answer.previews?.replace?.destination_revision !== editor.model.status().revision) {
        return mapTransferImportStale(kind);
      }
      status.idle();
      const pending = window.ServerManMapTransferReview.open(answer, {opener, kind});
      if ((answer.changes ?? changes) !== publish.changes()) window.ServerManMapPreviewDialog.stale();
      const result = await pending;
      if (!result) return;
      if (!result.again) return void (await mapTransferImportSubmit(kind, answer, result, opener));
      status.show(text.checking, "muted", {announce: true});
      changes = publish.changes();
      answer = await reader.again(answer);
    }
  } finally {
    publish.release();
    window.ServerManMapTransfer.render();
    if (!opener?.isConnected || document.activeElement === document.body) window.ServerManMapTransfer.focus(opener);
  }
}

// Run Import plan: the native open dialog and both previews are one host call
function mapTransferImportRun(opener) {
  const bridge = window.ServerManMapBridge, status = window.ServerManMapPublishStatus;
  const checking = window.ServerManMapTransferWords.kinds.import.checking;
  return mapTransferImportFlow("import", opener, {
    first: () => { status.show(checking, "muted", {announce: true}); return mapTransferImportRead(() => bridge.selectImportFile(), "import"); },
    again: (answer) => mapTransferImportRead(() => bridge.previewImport(answer.selection_id), "import")});
}

// The transfer module starts the import from the Import plan button; the copy module brings its own reader
window.ServerManMapTransferImport = Object.freeze({run: mapTransferImportRun, flow: mapTransferImportFlow,
  read: mapTransferImportRead, refused: mapTransferImportRefused, words: mapTransferImportWords});
