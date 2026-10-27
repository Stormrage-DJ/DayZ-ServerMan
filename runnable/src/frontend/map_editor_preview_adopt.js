// The adoption preview (10.16): the sections of the 10.13 answer on the 8.10 frame, in the UI-UX order. It has no
// choice, so the confirm button is available at once. Backend texts of the answer show unchanged.
"use strict";

// Open the adoption preview of one answer. options: {opener}. Resolves {fingerprint}, {again: true} or null; it
// submits nothing. A refused answer shows the refusals and what happened only, with Close.
function mapPreviewAdoptOpen(answer, options = {}) {
  const dialog = window.ServerManMapPreviewDialog, words = window.ServerManMapPreviewWords, dom = window.ServerManMapDom;
  const refused = answer.refused === true, headings = words.headings;
  // The other profiles of the source key follow ADOPT-13, as in Restore previous state
  const profiles = (answer.other_profiles ?? []).map((profile) => profile.display_name ?? words.deletedProfile(profile.profile_id));
  const sections = [
    dialog.section(headings.adoptRefusals, (answer.refusals ?? []).map((item) => dialog.notice(item.message, "error"))),
    dialog.section(headings.adoptHappened, [dialog.notice(answer.happened), dialog.notice(answer.other_profiles_text, "warning"),
      dialog.list(null, profiles, refused)]),
  ];
  // The records are a collapsed list above 5 rows; the unchanged lines are never collapsed
  if (!refused) {
    sections.push(
      dialog.section(headings.adoptWrites, [answer.intro ? dom.element("p", answer.intro) : null,
        dialog.list(words.adoptRecords, answer.records ?? [])]),
      dialog.section(headings.adoptUnchanged, (answer.unchanged ?? []).filter(Boolean).map((text) => dom.element("p", text))));
  }
  return dialog.open({kind: "adopt", title: words.titles.adopt, sections, refused, confirm: words.confirm.adopt,
    opener: options.opener, count: () => 0, first: () => null, value: () => ({fingerprint: answer.fingerprint})});
}

// The editor opens an adoption preview with a fresh answer (10.16)
window.ServerManMapPreviewAdopt = Object.freeze({open: mapPreviewAdoptOpen});
