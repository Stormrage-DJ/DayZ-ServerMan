// The picker of Copy from another profile (9.2): one radio for each profile, none chosen, a disabled radio with its
// reason for this profile and for a profile without a mission link. UI-UX Designer 2026-10-17 17:08:16 in evidence
// 9.1, section 4; the texts are the Product Owner texts of 17:14:17 (PO-91-1 a: no plan state before the choice).
"use strict";

// One profile: its radio, and below it the description (the mission, or the reason of a disabled profile)
function mapCopySourceRow(profile, group) {
  const dialog = window.ServerManMapPreviewDialog, copy = window.ServerManMapTransferWords.kinds.copy;
  const control = window.ServerManMapPreviewControls.radio(group, profile.profile_id, profile.display_name || profile.profile_id);
  const reason = profile.disabled ? copy.disabled[profile.disabled] : copy.mission(profile.mission_root);
  const note = window.ServerManMapDom.element("p", reason, {id: dialog.id("map-copy-source-note"), class: "map-preview-reason"});
  control.input.setAttribute("aria-describedby", note.id);
  control.input.disabled = Boolean(profile.disabled);
  return {profile, input: control.input, nodes: [control.row, note]};
}

// Open the picker for the profiles of the host. options: {opener, check}. check(value) previews the chosen source
// and returns a refusal text, which keeps the picker open, or null. The picker resolves {profile}, with the data
// that check added, {again: true} (the list is old) or null (Cancel). With no other profile it shows the empty
// text and Close only.
function mapCopySourceOpen(profiles, options = {}) {
  const dialog = window.ServerManMapPreviewDialog, dom = window.ServerManMapDom;
  const copy = window.ServerManMapTransferWords.kinds.copy, group = dialog.id("map-copy-source");
  const others = profiles.filter((profile) => profile.disabled !== "self");
  const rows = others.length ? profiles.map((profile) => mapCopySourceRow(profile, group)) : [];
  const fieldset = dom.element("fieldset");
  fieldset.append(dom.element("legend", copy.pickerLegend), ...rows.flatMap((row) => row.nodes));
  const chosen = () => rows.find((row) => row.input.checked) ?? null;
  return dialog.open({kind: "copy-source", title: copy.pickerTitle, refused: !others.length, confirm: copy.pickerConfirm,
    sections: others.length ? [dom.element("p", copy.pickerIntro), fieldset] : [dialog.notice(copy.empty)],
    opener: options.opener?.closest("#map-setup-parking") ? document.getElementById("map-show-plan") : options.opener, remaining: () => copy.pickerRemaining, stale: copy.pickerStale,
    count: () => chosen() ? 0 : 1, first: () => rows.find((row) => !row.input.disabled)?.input ?? null,
    checking: copy.checking, check: options.check, value: () => ({profile: chosen().profile})});
}

// The copy flow opens the picker with the profiles of the host
window.ServerManMapCopySource = Object.freeze({open: mapCopySourceOpen});
