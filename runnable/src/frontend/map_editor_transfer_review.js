// The compatibility dialog of Import plan and Copy from another profile (9.1, 9.2): both previews in one dialog, the Replace or Merge choice, and one
// checkbox for each cause that needs a confirmation. UI-UX Designer 2026-10-17 17:08:16 in evidence 9.1, section 5,
// sets the order and the focus; the texts are the Product Owner texts of 17:14:17 and the reused CLI texts.
"use strict";

// The name of an object in a list: its name, else its ID (D12)
function mapTransferReviewName(item) {
  return String(item?.name || item?.id || "");
}

// One line that a checkbox or a radio names in its description
function mapTransferReviewLine(text, kind = null) {
  const id = window.ServerManMapPreviewDialog.id("map-transfer-line");
  const node = kind ? window.ServerManMapPreviewDialog.notice(text, kind) : window.ServerManMapDom.element("p", text);
  node.id = id;
  return node;
}

// The Compatibility section: for a copy, the source profile and mission first. Then terrain, bounds and object
// count. Another terrain or other bounds add the warning and the placement checkbox directly after it.
function mapTransferReviewCompatibility(report, boxes, kind, source) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapTransferWords, dom = window.ServerManMapDom;
  const text = kind.lines;
  const lines = [source ? dom.element("p", kind.source(source.name, source.mission)) : null,
    dom.element("p", report.terrain_match ? words.cli.terrainSame :
      text.terrain(report.imported_terrain_id, report.destination_terrain_id)),
    dom.element("p", report.bounds_equal ? words.cli.boundsSame : text.bounds(report.bounds_difference)),
    dom.element("p", text.objects(report.object_count ?? 0))].filter(Boolean);
  if (report.placement_review) {
    const warning = mapTransferReviewLine(words.cli.placementReview, "warning");
    const box = window.ServerManMapPreviewControls.checkbox(words.placementBox, warning.id);
    boxes.push(box.input);
    lines.push(warning, box.row);
  }
  return dialog.section(words.cli.heading, lines);
}

// The description of one mode: the insertion, the global capacity (Merge: only when the values differ) and the
// result. Merge also lists the objects that get a new ID, collapsed. A mode that a plan limit refuses has its note.
function mapTransferReviewEffect(report, text) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapTransferWords, dom = window.ServerManMapDom;
  const capacity = report.global_abundance ?? {}, replace = report.insertion === "replace";
  const box = dom.element("div", "", {id: dialog.id("map-transfer-effect"), class: "map-preview-reason"});
  const remapped = (report.remapped ?? []).map((entry) => String(entry?.from ?? ""));
  box.append(...[report.refusal ? dialog.notice(words.modeRefused(report.refusal_text ?? report.refusal), "error") : null,
    dom.element("p", text.insertion(replace ? "replace" : "top")),
    replace ? dom.element("p", words.capacityReplace(capacity.imported ?? 0)) :
      (capacity.imported !== capacity.destination ?
        dom.element("p", text.capacity(capacity.destination ?? 0, capacity.imported ?? 0)) : null),
    dom.element("p", words.result(report.result_object_count ?? 0, report.result_vertex_count ?? 0)),
    replace ? null : dialog.list(words.newIds(remapped.length), remapped, true)].filter(Boolean));
  return box;
}

// The mode group: a fieldset with its legend and two radios, none checked; both effects show together
function mapTransferReviewModes(previews, radios, text) {
  const words = window.ServerManMapTransferWords, dom = window.ServerManMapDom;
  const fieldset = dom.element("fieldset"), name = window.ServerManMapPreviewDialog.id("map-transfer-mode");
  fieldset.append(dom.element("legend", words.legend));
  for (const mode of ["replace", "merge"]) {
    const control = window.ServerManMapPreviewControls.radio(name, mode, words.modes[mode]);
    const effect = mapTransferReviewEffect(previews[mode], text);
    control.input.setAttribute("aria-describedby", effect.id);
    // A plan-level limit disables only the radio of its mode, as the Keep the change radio of Apply
    control.input.disabled = Boolean(previews[mode].refusal);
    radios.push(control.input);
    fieldset.append(control.row, effect);
  }
  return fieldset;
}

// A listed cause with its checkbox after the list, so a collapsed list never hides the box
function mapTransferReviewListed(heading, rows, label, boxes) {
  const dialog = window.ServerManMapPreviewDialog;
  if (!rows.length) return null;
  const list = dialog.list(null, rows);
  const box = window.ServerManMapPreviewControls.checkbox(label);
  boxes.push(box.input);
  const section = dialog.section(heading, [list, box.row]);
  box.input.setAttribute("aria-describedby", section.querySelector("h3").id);
  return section;
}

// The sections of a refused file: the bridge text and each failing object in a collapsed list (XFER-32)
function mapTransferReviewProblems(answer) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapTransferWords;
  const rows = (answer.problems ?? []).map((problem) =>
    problem?.object_id ? words.row(problem.object_id, problem.message) : String(problem?.message ?? ""));
  return [dialog.section(words.cli.heading, [dialog.notice(answer.message, "error"),
    dialog.list(words.problems(rows.length), rows, true)])];
}

// Open the dialog of one host answer. The answer is {selection_id, previews: {replace, merge}}, or for a copy
// {source: {name, mission}, previews}. A file that is not a valid plan gives {refused: true, message, problems}.
// options: {opener, kind: "import" (default) or "copy"}. The dialog submits nothing. It resolves {mode,
// confirmed, revision}, {again: true} or null.
function mapTransferReviewOpen(answer, options = {}) {
  const copy = options.kind === "copy", kind = window.ServerManMapTransferWords.kinds[copy ? "copy" : "import"];
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapTransferWords, dom = window.ServerManMapDom;
  const radios = [], boxes = [], previews = answer.previews ?? {}, report = previews.replace ?? {};
  let sections;
  if (answer.refused) sections = mapTransferReviewProblems(answer);
  else {
    const unavailable = (report.unavailable ?? []).map((item) => words.row(mapTransferReviewName(item), words.reason(item)));
    const excluded = (report.excluded ?? []).map((item) => item?.linked_loot?.enabled ?
      words.withLinkedLoot(mapTransferReviewName(item)) : mapTransferReviewName(item));
    const unmapped = (report.unmapped ?? []).map((item) => item?.id ? words.row(item.id, item.reason) : String(item?.reason ?? ""));
    const discarded = (report.discarded ?? []).length;
    sections = [mapTransferReviewCompatibility(report, boxes, kind, copy ? answer.source : null),
      mapTransferReviewModes(previews, radios, kind.lines),
      dialog.section(words.cli.unavailable, [dialog.list(null, unavailable)]),
      mapTransferReviewListed(words.cli.excluded, excluded, kind.leftOutBox(excluded.length), boxes),
      mapTransferReviewListed(words.cli.unmapped, unmapped, kind.unmappedBox(unmapped.length), boxes),
      discarded ? dom.element("p", kind.lines.discarded(discarded)) : null];
  }
  // Both modes refused by a plan limit: the answer is refused, with Close only
  const refused = answer.refused === true || (radios.length > 0 && radios.every((radio) => radio.disabled));
  const chosen = () => radios.find((radio) => radio.checked) ?? null;
  // The Compatibility section adds the placement box first, so the other boxes follow it
  const placement = !answer.refused && report.placement_review ? boxes[0] : null;
  // The open choices in focus order: the placement box, the mode group, then the left-out and unmapped boxes
  const open = () => [placement && !placement.checked ? placement : null,
    chosen() ? null : radios.find((radio) => !radio.disabled),
    ...boxes.filter((box) => box !== placement && !box.checked)].filter(Boolean);
  const title = copy ? kind.title(answer.source?.name ?? "") : kind.title;
  return dialog.open({kind: copy ? "copy" : "import", title, sections: [dom.element("p", kind.intro), ...sections],
    refused, confirm: kind.confirm, opener: options.opener, remaining: kind.remaining, stale: kind.previewStale,
    count: () => open().length, first: () => open()[0] ?? null,
    value: () => ({mode: chosen().value, revision: previews[chosen().value].destination_revision,
      confirmed: previews[chosen().value].confirmation_required === true && boxes.every((box) => box.checked)})});
}

// The import flow opens the dialog with the answer of the host
window.ServerManMapTransferReview = Object.freeze({open: mapTransferReviewOpen});
