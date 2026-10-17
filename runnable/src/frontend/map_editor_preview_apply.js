// The Apply preview (8.10): the sections of the 8.6 answer in the UI-UX order, the external-change and encounter
// choices without a preselection, and the reviewed fingerprint with the choices. Backend texts show unchanged.
"use strict";

// The refusals and the 6.11 family text once: a refusal that carries the family text is not repeated
function mapPreviewApplyRefusals(refusals, family) {
  const dialog = window.ServerManMapPreviewDialog, messages = (refusals ?? []).map((item) => item.message);
  if (family && family.verified === false && family.text && !messages.includes(family.text)) messages.push(family.text);
  return messages.map((message) => dialog.notice(message, "error"));
}

// The other profiles (A4) with their names; a deleted profile has no display name
function mapPreviewApplyProfiles(intro, profiles, collapsed) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapPreviewWords;
  if (!profiles?.length) return [];
  return [intro ? dialog.notice(intro, "warning") : null, dialog.list(null, profiles.map((profile) =>
    profile.display_name ?? words.deletedProfile(profile.profile_id)), collapsed)];
}

// The 7.3 reason of an unavailable encounter: from the plan object when the editor knows it, else from its provider
function mapPreviewApplyReason(entry, object, capabilities) {
  const encounters = window.ServerManMapEncounters;
  return object ? encounters.status(object, capabilities).reason : encounters.provider(capabilities, entry.provider).reason;
}

// One radio with its label; stacked, never checked at the start (R17: an explicit choice)
function mapPreviewApplyRadio(name, value, label) {
  const dom = window.ServerManMapDom, row = dom.element("label", "", {class: "check-row"});
  const input = dom.element("input", "", {type: "radio", name, value});
  row.append(input, dom.element("span", label));
  return {row, input};
}

// One fieldset for each external change: the path, Keep the change with its refusal text, and Replace the file
function mapPreviewApplyFile(change, noteId) {
  const dom = window.ServerManMapDom, words = window.ServerManMapPreviewWords;
  const fieldset = dom.element("fieldset"), name = window.ServerManMapPreviewDialog.id("map-preview-file");
  const keep = mapPreviewApplyRadio(name, "incorporate", words.keep);
  const replace = mapPreviewApplyRadio(name, "replace", words.replace);
  fieldset.append(dom.element("legend", change.path), keep.row);
  if (change.incorporate?.available === false) {
    keep.input.disabled = true;
    const message = dom.element("p", change.incorporate.message ?? "",
      {id: `${name}-refusal`, class: "map-preview-refusal"});
    fieldset.append(message);
    fieldset.setAttribute("aria-describedby", message.id);
  } else if (change.exists_now === true && noteId) {
    // The Replace the file note applies only to a file that exists now (Product Owner 15:57:10, decision 10)
    fieldset.setAttribute("aria-describedby", noteId);
  }
  fieldset.append(replace.row);
  return {fieldset, path: change.path, inputs: [keep.input, replace.input]};
}

// One checkbox for each unavailable encounter that needs a choice, with its reason and the linked-loot line
function mapPreviewApplyEncounter(entry, object, capabilities) {
  const dom = window.ServerManMapDom, words = window.ServerManMapPreviewWords;
  const reasonId = window.ServerManMapPreviewDialog.id("map-preview-encounter"), box = dom.element("div");
  const row = dom.element("label", "", {class: "check-row"}), input = dom.element("input", "", {type: "checkbox"});
  row.append(input, dom.element("span", words.exclude(entry.name ?? entry.id)));
  const lines = [mapPreviewApplyReason(entry, object, capabilities), entry.linked_loot ? words.linkedLoot : ""]
    .filter(Boolean).map((text, index) => dom.element("p", text, {id: `${reasonId}-${index}`, class: "map-preview-reason"}));
  if (lines.length) input.setAttribute("aria-describedby", lines.map((line) => line.id).join(" "));
  box.append(row, ...lines);
  return {box, id: entry.id, input};
}

// The external changes: choices, or a plain list when the answer is refused
function mapPreviewApplyExternal(answer, files, collapsed) {
  const dom = window.ServerManMapDom, words = window.ServerManMapPreviewWords;
  const changes = (answer.choices?.external_changes ?? []).map((path) =>
    (answer.external_changes ?? []).find((item) => item.path === path) ?? {path});
  if (answer.refused) return [window.ServerManMapPreviewDialog.list(null, changes.map((item) => item.path), collapsed)];
  if (!changes.length) return [];
  const noted = changes.some((item) => item.exists_now === true && item.incorporate?.available !== false);
  const note = noted ? dom.element("p", words.replaceNote, {id: window.ServerManMapPreviewDialog.id("map-preview-note")}) : null;
  files.push(...changes.map((change) => mapPreviewApplyFile(change, note?.id)));
  return [dom.element("p", words.externalIntro), note, ...files.map((item) => item.fieldset)];
}

// The unavailable encounters that need a choice: checkboxes, or a plain list when the answer is refused
function mapPreviewApplyEncounters(answer, encounters, objectOf, collapsed) {
  const entries = (answer.choices?.unavailable_encounters ?? []).map((id) =>
    (answer.encounters?.unavailable ?? []).find((item) => item.id === id) ?? {id});
  if (answer.refused) {
    return [window.ServerManMapPreviewDialog.list(null, entries.map((item) => item.name ?? item.id), collapsed)];
  }
  if (!entries.length) return [];
  encounters.push(...entries.map((entry) => mapPreviewApplyEncounter(entry, objectOf(entry.id), answer.capabilities)));
  return [window.ServerManMapDom.element("p", window.ServerManMapPreviewWords.encounterIntro),
    ...encounters.map((item) => item.box)];
}

// One report text, then a collapsed list without a label that the text names; the list needs rows (PO-2)
function mapPreviewApplyMechanism(text, rows) {
  if (!text) return [];
  const dom = window.ServerManMapDom, line = dom.element("p", text, {id: window.ServerManMapPreviewDialog.id("map-preview-text")});
  return [line, window.ServerManMapPreviewDialog.list(null, rows, true, line.id)];
}

// The loot report (UI-UX 2026-10-15 17:59:02, change 4; Product Owner 18:04:08). First the explanation. Then each
// mechanism text with its groups or categories, and then three labelled lists. Every list is always collapsed.
function mapPreviewApplyLoot(loot) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapPreviewWords, dom = window.ServerManMapDom;
  if (!loot) return [];
  const mechanisms = loot.mechanisms ?? {}, fixed = mechanisms.fixed_dispatch?.groups ?? [];
  const shared = mechanisms.shared_category_capacity?.groups ?? [], supply = mechanisms.missing_supply;
  const missing = Object.keys(supply?.categories ?? {});
  // Each pair of name and reason once, in answer order; the kept texts show only in these rows
  const kept = new Map((loot.kept ?? []).map((item) => [JSON.stringify([item.name, item.reason]), `${item.name}: ${item.text}`]));
  return [loot.explanation ? dom.element("p", loot.explanation) : null,
    fixed.length ? mapPreviewApplyMechanism(mechanisms.fixed_dispatch.text, fixed) : [],
    shared.length ? mapPreviewApplyMechanism(mechanisms.shared_category_capacity.text,
      shared.map((item) => `${item.source}: ${(item.categories ?? []).join(", ")}`)) : [],
    // Unknown supply (categories null) has its own text and no list
    supply && supply.categories === null ? dom.element("p", supply.text) : [],
    missing.length ? mapPreviewApplyMechanism(supply.text, missing) : [],
    dialog.list(words.lootUnavailable, (loot.unavailable ?? []).map((item) => `${item.name ?? item.id}: ${item.text}`), true),
    dialog.list(words.lootCapacity, (loot.capacity ?? []).filter((entry) => entry.class === "changed").map((entry) =>
      [`${entry.source}: ${window.ServerManMapInspectWording.groupCapacity(entry.group)}`, entry.bound_text]
        .filter(Boolean).join(" ")), true),
    dialog.list(words.lootKept, [...kept.values()], true)];
}

// Open the Apply preview of one answer. options: {opener, objectOf(id) (the saved plan object), outline (an
// unfinished outline at the activation)}. Resolves {fingerprint, choices}, {again: true} or null; submits nothing.
function mapPreviewApplyOpen(answer, options = {}) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapPreviewWords;
  const refused = answer.refused === true, objectOf = options.objectOf ?? (() => null);
  const list = (label, rows, always = false) => dialog.list(label, rows, always || refused);
  const name = (id) => objectOf(id)?.name ?? id, files = [], encounters = [];
  const objects = answer.objects ?? {}, global = objects.global ?? {};
  const names = (items) => (items ?? []).map((item) => item.name ?? item.id);
  const sections = [
    dialog.section(words.headings.refusals, mapPreviewApplyRefusals(answer.refusals, answer.capabilities?.family)),
    dialog.section(words.headings.notices, [
      answer.capabilities?.tier_writes?.reason === "bounds_not_confirmed" ? dialog.notice(words.bounds, "warning") : null,
      options.outline ? dialog.notice(words.outline, "warning") : null,
      mapPreviewApplyProfiles(words.others.apply, answer.other_profiles, refused)]),
    dialog.section(words.headings.external, mapPreviewApplyExternal(answer, files, refused)),
    dialog.section(words.headings.encounterChoices, mapPreviewApplyEncounters(answer, encounters, objectOf, refused)),
    dialog.section(words.headings.changes, [dialog.notice(objects.order_text, "warning"),
      Number.isInteger(global.current) ? window.ServerManMapDom.element("p", words.global(global.current, global.applied)) : null,
      objects.previous === false ? window.ServerManMapDom.element("p", words.noPrevious) : null,
      list(words.added, names(objects.added)), list(words.changed, names(objects.changed)),
      list(words.removed, names(objects.removed))]),
    dialog.section(words.headings.encounters, [
      ...(answer.infected?.notices ?? []).map((notice) => dialog.notice(notice.text, "warning")),
      list(words.written, (answer.encounters?.written ?? []).map(name)),
      // The members and names come from the answer alone (Architect 2026-10-15 18:04:57, A-1)
      list(words.excluded, names(answer.encounters?.excluded))]),
    dialog.section(words.headings.loot, mapPreviewApplyLoot(answer.loot)),
    dialog.section(words.headings.files, [dialog.notice(answer.capture?.text ?? null),
      list(words.affected, (answer.affected_files ?? []).map((item) => item.path)),
      list(words.coordinated, (answer.coordinated_changes ?? []).map((item) => item.path))]),
  ];
  const open = () => [...files.filter((item) => !item.inputs.some((input) => input.checked)).map((item) =>
    item.inputs.find((input) => !input.disabled)), ...encounters.filter((item) => !item.input.checked).map((item) => item.input)];
  // A refused answer with `capture.adoption` offers the adoption preview as a second button (UI-UX 14:21:59, section 3)
  return dialog.open({kind: "apply", title: words.titles.apply, sections, refused, confirm: words.confirm.apply,
    second: refused && answer.capture?.adoption ? words.adopt : null, opener: options.opener, count: () => open().length, first: () => open()[0] ?? null,
    value: () => ({fingerprint: answer.fingerprint, choices: {
      external_changes: Object.fromEntries(files.map((item) => [item.path, item.inputs.find((input) => input.checked).value])),
      unavailable_encounters: Object.fromEntries(encounters.map((item) => [item.id, "exclude"]))}})});
}

// The editor opens the Apply preview with a fresh answer (8.18)
window.ServerManMapPreviewApply = Object.freeze({open: mapPreviewApplyOpen});
