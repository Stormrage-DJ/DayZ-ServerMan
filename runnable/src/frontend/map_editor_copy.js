// The flow of Copy from another profile (9.2): the picker, the previews of the chosen source, then the shared
// import flow (compatibility dialog, one revision-guarded save and one undo step). The page names only the source
// profile; the host binds the destination, and the source plan is only read (Architect 2026-10-17 17:10:42).
"use strict";

// The preview of the chosen source runs in the picker. A refusal shows in its status line, and the admin can
// choose another profile (PO-91-1 a). A success keeps the answer for the compatibility dialog.
async function mapCopyCheck(value) {
  const imported = window.ServerManMapTransferImport, publish = window.ServerManMapPublishWords;
  value.changes = window.ServerManMapPublish.changes();
  try {
    value.answer = await window.ServerManMapBridge.previewCopy(value.profile.profile_id);
    return null;
  } catch (error) {
    return publish.blocked(error) ? publish.refusal(error, "import") : imported.words(error);
  }
}

// Read the list and run the picker again while the admin asks to preview again. The answer names the source
// profile and mission for the dialog; a cancel gives {cancelled: true}; a refused list gives null.
async function mapCopyPick(opener) {
  for (;;) {
    let listed;
    try {
      listed = await window.ServerManMapBridge.copySources();
    } catch (error) {
      window.ServerManMapTransferImport.refused(error, false, "copy");
      return null;
    }
    const result = await window.ServerManMapCopySource.open(listed.profiles ?? [], {opener, check: mapCopyCheck});
    if (!result) return {cancelled: true};
    if (result.again) continue;
    const profile = result.profile;
    return {source_profile_id: profile.profile_id, source: {name: profile.display_name || profile.profile_id,
      mission: profile.mission_root}, previews: result.answer.previews, changes: result.changes};
  }
}

// Run Copy from another profile. Preview again reads both previews again for the same source.
function mapCopyRun(opener) {
  return window.ServerManMapTransferImport.flow("copy", opener, {
    first: () => mapCopyPick(opener),
    again: async (answer) => {
      const read = await window.ServerManMapTransferImport.read(
        () => window.ServerManMapBridge.previewCopy(answer.source_profile_id), "copy");
      return read && {...answer, previews: read.previews, changes: undefined};
    }});
}

// The Copy from another profile button starts the flow
window.ServerManMapCopy = Object.freeze({run: mapCopyRun});
