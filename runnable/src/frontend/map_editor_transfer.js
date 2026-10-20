// Export plan, Import plan and Copy from another profile (9.1, 9.2): the "Plan file" panel section, the footer
// Export plan link, the export of the shown draft and the openers of the import and the copy. Rules: UI-UX Designer
// 2026-10-17 17:08:16 and Architect 17:10:42 in evidence 9.1; the Architect entry wins where they differ.
"use strict";

// editor: the app state; busy: the lane holds a map operation; reminder: the placement reminder {text,
// generation} until the next change of the draft history
const mapTransferState = {editor: null, busy: false, reminder: null};

// One element of the document
function mapTransferElement(id) {
  return document.getElementById(id);
}

// Why Import is not available now, or "" (XFER-4, in the order of the Product Owner table)
function mapTransferImportReason() {
  const editor = mapTransferState.editor, reasons = window.ServerManMapTransferWords.reasons;
  const status = editor?.model?.status();
  if (!editor?.loaded || editor.loading || editor.closing || !status) return reasons.loading;
  if (status.read_only) return reasons.readOnly;
  if (window.ServerManMapAutosave.blocked()) return reasons.conflict;
  if (mapTransferState.busy) return reasons.busy;
  if (window.ServerManMapPublish.busy() || window.ServerManMapPreviewDialog.isOpen()) return reasons.reading;
  // The same gate as the placement tools: the bounds of the draft are confirmed (Architect section 7)
  return status.plan?.terrain?.bounds_confirmed === true ? "" : reasons.bounds;
}

// Why Export is not available now, or "": it needs a loaded plan and no other file dialog or read. The lane lock
// and an own Apply do not stop it, because it writes no game file (UI-UX section 2).
function mapTransferExportReason() {
  const editor = mapTransferState.editor, words = window.ServerManMapTransferWords;
  if (!editor?.loaded || editor.loading || editor.closing || !editor.model?.snapshot()) return words.exportLoading;
  return window.ServerManMapPublish.reading() || window.ServerManMapPreviewDialog.isOpen() ? words.reasons.reading : "";
}

// Gate one opener: aria-disabled with its reason, never disabled, so the keyboard reaches the reason
function mapTransferGate(button, reasonId, reason, kept = []) {
  mapTransferElement(reasonId).textContent = reason;
  const ids = [...kept, ...(reason ? [reasonId] : [])];
  if (ids.length) button.setAttribute("aria-describedby", ids.join(" "));
  else button.removeAttribute("aria-describedby");
  if (reason) button.setAttribute("aria-disabled", "true");
  else button.removeAttribute("aria-disabled");
}

// Show the state of the three openers; the footer link keeps the save notice as its first description
function mapTransferRender() {
  if (!mapTransferState.editor) return;
  const exporting = mapTransferExportReason();
  mapTransferGate(mapTransferElement("map-export"), "map-export-reason", exporting);
  mapTransferGate(mapTransferElement("map-plan-export"), "map-export-reason", exporting, ["map-save-notice"]);
  // Import and copy share one reason: the same gates hold for both
  for (const id of ["map-import", "map-copy"]) mapTransferGate(mapTransferElement(id), "map-import-reason", mapTransferImportReason());
}

// Give the focus back to the opener, or to the map when the opener is gone or hidden
function mapTransferFocus(opener) {
  (opener?.closest("#map-setup-parking") ? mapTransferElement("map-show-plan") :
    opener?.isConnected && !opener.closest("[hidden]") ? opener : mapTransferElement("map-viewport")).focus();
}

// The text of a refused export: the D1 lead with each object, or the export path texts with the file name
function mapTransferExportRefusal(error) {
  const words = window.ServerManMapTransferWords, details = error.result?.error?.details ?? {};
  if (details.reason === "invalid_document") {
    const objects = mapTransferState.editor.model.snapshot()?.objects ?? [];
    const rows = (details.problems ?? []).map((problem) => {
      const item = objects.find((object) => object.id === problem?.object_id);
      return item ? words.row(item.name, problem.message) : String(problem?.message ?? "");
    });
    return [words.exportInvalid, ...rows].join("\n");
  }
  const texts = {protected: words.cli.protected, folder_or_link: words.cli.folderOrLink, not_written: words.cli.notWritten};
  if (texts[details.reason] && typeof details.file_name === "string") return words.path(texts[details.reason], details.file_name);
  return error.result ? window.ServerManMapWording.error(error.result) : error.message;
}

// Export the draft that the window shows; it changes no model, history or save state (UI-UX section 2)
async function mapTransferExport(opener) {
  const words = window.ServerManMapTransferWords, status = window.ServerManMapPublishStatus;
  if (mapTransferExportReason() || !window.ServerManMapPublish.claim()) return;
  mapTransferRender();
  status.show(words.exporting, "muted", {announce: true});
  try {
    const answer = await window.ServerManMapBridge.exportPlan(mapTransferState.editor.model.snapshot());
    if (answer?.cancelled === true) status.idle();
    else if (typeof answer?.file_name === "string") {
      // While autosave is stopped, the file is the way to keep the draft (XFER-12)
      const text = words.path(words.cli.written, answer.file_name);
      status.show(window.ServerManMapAutosave.blocked() ? `${text} ${words.stillUnsaved}` : text, "done",
        {notice: "success", announce: true});
    } else status.show(window.ServerManMapWording.invalidAnswer, "error", {notice: "error", announce: true});
  } catch (error) {
    status.show(mapTransferExportRefusal(error), "error", {notice: "error", announce: true});
  } finally {
    window.ServerManMapPublish.release();
    mapTransferRender();
    mapTransferFocus(opener);
  }
}

// Wire the openers once and follow the lane state; the flow of Import plan is in the import module
function mapTransferInit(editor) {
  const words = window.ServerManMapTransferWords;
  if (mapTransferState.editor) return;
  mapTransferState.editor = editor;
  mapTransferElement("map-plan-file-title").textContent = words.heading;
  mapTransferElement("map-export").textContent = words.exportLabel;
  mapTransferElement("map-import").textContent = words.importLabel;
  mapTransferElement("map-copy").textContent = words.copyLabel;
  mapTransferElement("map-plan-export").textContent = words.exportLink;
  for (const id of ["map-export", "map-plan-export"]) {
    mapTransferElement(id).addEventListener("click", (event) => { void mapTransferExport(event.currentTarget); });
  }
  mapTransferElement("map-import").addEventListener("click", (event) => {
    if (!mapTransferImportReason()) void window.ServerManMapTransferImport.run(event.currentTarget);
  });
  mapTransferElement("map-copy").addEventListener("click", (event) => {
    if (!mapTransferImportReason()) void window.ServerManMapCopy.run(event.currentTarget);
  });
  document.addEventListener("serverman:operation-change", (event) => {
    mapTransferState.busy = event.detail?.busy === true;
    mapTransferRender();
  });
  mapTransferRender();
}

// Each draft change ends the placement reminder of an import once the history moved on, and gates the openers
function mapTransferChanged(value) {
  const reminder = mapTransferState.reminder;
  if (reminder && value?.history?.generation !== reminder.generation) {
    mapTransferState.reminder = null;
    if (mapTransferElement("map-tool-message").textContent === reminder.text) window.ServerManMapTools.message("");
  }
  mapTransferRender();
}

// The app wires the openers and reports draft changes; the import module uses the state, the gate and the focus
window.ServerManMapTransfer = Object.freeze({init: mapTransferInit, changed: mapTransferChanged, render: mapTransferRender,
  importReason: mapTransferImportReason, focus: mapTransferFocus, editor: () => mapTransferState.editor,
  remind: (text) => {
    window.ServerManMapTools.message(text);
    mapTransferState.reminder = {text, generation: mapTransferState.editor.model.status().history.generation};
  }});
