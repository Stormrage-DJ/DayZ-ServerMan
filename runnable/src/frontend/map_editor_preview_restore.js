// The Restore preview of both modes (8.10): the 8.8 answer in the UI-UX section order. Backend texts show unchanged.
"use strict";

// What the restore writes: the change that it takes back with its time, or the date of the original
function mapPreviewRestoreWrites(mode, answer) {
  const words = window.ServerManMapPreviewWords;
  if (mode === "previous") return answer.replaces ? words.previousWrites(answer.replaces.operation_kind, answer.replaces.applied_at) : null;
  return answer.original ? words.originalWrites(answer.original.captured_at) : null;
}

// Open the restore preview of one mode ("original" or "previous"). options: {opener}. Resolves {fingerprint, mode},
// {again: true} or null; submits nothing.
function mapPreviewRestoreOpen(mode, answer, options = {}) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapPreviewWords, dom = window.ServerManMapDom;
  const refused = answer.refused === true, original = mode === "original" ? answer.original : null;
  const list = (label, rows) => dialog.list(label, rows, refused);
  const external = answer.external_changes ?? [], coordinated = answer.coordinated_changes ?? [];
  const writes = mapPreviewRestoreWrites(mode, answer);
  const profiles = (answer.other_profiles ?? []).map((profile) =>
    profile.display_name ?? words.deletedProfile(profile.profile_id));
  const sections = [
    // The family refusal of Restore original is one of the refusals; its text is not repeated
    dialog.section(words.headings.restoreRefusals, [...(answer.refusals ?? []).map((item) => item.message),
      ...(answer.family?.verified === false && answer.family.text &&
        !(answer.refusals ?? []).some((item) => item.message === answer.family.text) ? [answer.family.text] : [])]
      .map((message) => dialog.notice(message, "error"))),
    // Restore original: the shared-original text states the other profiles; previous state has its intro line
    dialog.section(words.headings.restoreNotices, [
      dialog.notice(original?.shared ? original.text : null, "warning"),
      profiles.length && mode === "previous" ? dialog.notice(words.others.previous, "warning") : null,
      list(null, profiles), dialog.notice(original?.kept_changes ?? null, "warning")]),
    dialog.section(words.headings.writes, [writes ? dom.element("p", writes) : null,
      list(words.restoreAffected, (answer.affected_files ?? []).map((item) => item.path))]),
    // The backup lines are never collapsed
    dialog.section(words.headings.overwritten, [
      ...external.filter((item) => item.overwritten && item.text).map((item) => dom.element("p", item.text)),
      list(words.restoreCoordinated, coordinated.filter((item) => item.overwritten).map((item) => item.path))]),
    dialog.section(words.headings.kept, [list(null, external.filter((item) => !item.overwritten).map((item) =>
      words.notOverwritten(item.path)))]),
  ];
  return dialog.open({kind: "restore", title: words.titles[mode], sections, refused, confirm: words.confirm[mode],
    opener: options.opener, count: () => 0, first: () => null,
    value: () => ({fingerprint: answer.fingerprint, mode})});
}

// The editor opens a restore preview with a fresh answer of its mode (8.18)
window.ServerManMapPreviewRestore = Object.freeze({open: mapPreviewRestoreOpen});
