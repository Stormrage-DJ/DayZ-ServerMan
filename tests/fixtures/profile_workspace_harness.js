const output = document.getElementById("result");
function check(value, message) { if (!value) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await new Promise((done) => setTimeout(done, 0)); }
function record(id, revision = 1) { return { revision, semantic_digest: "a".repeat(64), profile_id: id,
  display_name: id.toUpperCase(), server_executable: "DayZServer_x64.exe",
  server_config: "serverDZ.cfg", runtime_profile: null,
  mission_root: "mpmissions\\dayzOffline.test", game_port: 2302,
  mods: [{ directory: `@${id}`, launch_scope: "client",
    source: { kind: "workshop", workshop_id: id === "alpha" ? "1" : "2" } }],
  extra_arguments: ["-doLogs"] }; }
const alpha = record("alpha"); const bravo = record("bravo");
const host = { list: null, save: null, preview: null, remove: null };
window.pywebview = { api: {
  list_profiles: () => host.list.promise, save_profile: () => host.save.promise,
  delete_profile: () => host.remove.promise, preview_profile_command: () => host.preview.promise,
} };
window.ServerManUi = { renderHostError: () => {},
  setHostStatus: (text) => { document.getElementById("host").textContent = text; },
  clearHostStatus: () => { document.getElementById("host").textContent = ""; } };
window.shellState = { section: "profiles" };
// Supply the shared selector boundary used after ordinary save/delete mutations.
window.ServerManProfileContext = { select: () => {}, refreshAndSelect: async (preferred) => {
  const result = await window.pywebview.api.list_profiles();
  return result.success ? { success: true, value: { profiles: result.value,
    selected_profile_id: result.value.some(item => item.profile_id === preferred) ? preferred : result.value[0]?.profile_id || null } } : result;
} };
(async () => {
  window.ServerManWorkspace.activate("profiles"); host.list = deferred();
  const staleList = openProfilesWorkspace(); window.ServerManWorkspace.activate("overview");
  host.list.resolve({ success: true, value: [alpha, bravo] }); await staleList;
  check(document.getElementById("content-region").textContent === "sentinel", "stale list rendered");

  window.ServerManWorkspace.activate("profiles"); shellState.section = "profiles"; host.list = deferred();
  const initial = openProfilesWorkspace("alpha", true);
  host.list.resolve({ success: true, value: [alpha, bravo] }); await initial;
  check(profileState.selected.profile_id === "alpha", "initial preferred profile was not selected");

  host.preview = deferred(); const preview = previewProfileCommand(); renderProfileForm(bravo);
  host.preview.resolve({ success: true, value: { argv: ["stale-alpha"] } }); await preview;
  check(!document.getElementById("profile-feedback").textContent.includes("stale-alpha"),
    "stale preview crossed selection");

  renderProfileForm(alpha); host.save = deferred();
  const staleSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  renderProfileForm(bravo); host.save.resolve({ success: true, value: { operation_id: "stale-save" } });
  await staleSave; check(profileState.pending === null, "stale save crossed selection");

  renderProfileForm(bravo); host.save = deferred();
  const reverseSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  renderProfileForm(alpha); host.save.resolve({ success: true, value: { operation_id: "reverse-stale-save" } });
  await reverseSave; check(profileState.pending === null && profileState.selected.profile_id === "alpha",
    "reverse stale save crossed selection");

  renderProfileForm(bravo);
  host.save = deferred(); const currentForm = document.getElementById("profile-form");
  const activeSave = saveProfile({ preventDefault() {}, currentTarget: currentForm });
  host.save.resolve({ success: true, value: { operation_id: "save-bravo" } }); await activeSave;
  currentForm.querySelector('[name="display_name"]').value = "newer edit";
  currentForm.querySelector('[name="display_name"]').dispatchEvent(new Event("input", { bubbles: true }));
  check(profileOperationFinished({ operation_id: "save-bravo", state: "SUCCEEDED" }),
    "terminal save was not correlated");
  check(profileState.selected.profile_id === "bravo" && profileState.dirty,
    "terminal save discarded newer edits");
  check(document.getElementById("profile-feedback").textContent.includes("Newer profile edits"),
    "newer-edit outcome was not actionable");

  host.list = deferred(); const dirtyRefresh = openProfilesWorkspace("alpha", true);
  host.list.resolve({ success: true, value: [alpha, bravo] }); await dirtyRefresh;
  check(profileState.selected.profile_id === "bravo" && profileState.dirty,
    "refresh replaced dirty profile context");

  renderProfileForm(bravo); host.save = deferred();
  const cleanSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  host.save.resolve({ success: true, value: { operation_id: "clean-save" } }); await cleanSave;
  host.list = deferred(); profileOperationFinished({ operation_id: "clean-save", state: "SUCCEEDED" });
  host.list.resolve({ success: true, value: [alpha, record("bravo", 2)] }); await flush();
  check(profileState.selected.profile_id === "bravo" && profileState.selected.revision === 2,
    "successful save fell back to first profile");

  renderProfileForm(alpha); const deleteButton = [...document.querySelectorAll("button")]
    .find((item) => item.textContent === "Delete profile"); deleteButton.focus(); deleteButton.click();
  const dialog = document.getElementById("profile-delete-confirmation");
  check(dialog.getAttribute("aria-modal") === "true" && document.getElementById("background").inert,
    "delete dialog did not isolate background");
  const buttons = dialog.querySelectorAll("button"); buttons[1].focus();
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "delete dialog forward Tab escaped");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "delete dialog reverse Tab escaped");
  dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.getElementById("profile-delete-confirmation") && !document.getElementById("background").inert,
    "Escape did not cancel delete dialog");
  check(document.activeElement === deleteButton, "delete cancel did not restore focus");

  deleteButton.click(); host.remove = deferred();
  const deleting = confirmDeleteProfile(); host.remove.resolve({ success: true,
    value: { operation_id: "delete-alpha" } }); await deleting;
  host.list = deferred(); profileOperationFinished({ operation_id: "delete-alpha", state: "SUCCEEDED" });
  host.list.resolve({ success: true, value: [record("bravo", 2)] }); await flush();
  check(profileState.selected.profile_id === "bravo", "delete did not choose deterministic neighbor");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
