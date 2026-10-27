"""Run editor identity, refusal, focus and stale-answer contracts in headless Edge."""

from __future__ import annotations

import unittest

from tests.ui_harness_support import EDGE, run_editor_harness


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorEdgeTests(unittest.TestCase):
    """Use only fake editor answers and isolated browser profiles."""

    def test_identity_unsaved_read_only_and_error_without_implicit_save(self) -> None:
        """Loads retain identity and show distinct unsaved, read-only and failure states."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
check(document.getElementById("map-profile").textContent === "Alpha", "profile identity");
check(document.getElementById("map-mission").textContent === "dayzOffline.pripyat", "mission identity");
check(document.getElementById("map-terrain").textContent === "pripyat", "terrain identity");
check(document.getElementById("map-status").textContent === "Not saved yet", "unsaved state");
check(document.activeElement.id === "map-viewport", "map focus at load");
for (const reason of ["invalid_saved_data", "mission_path_changed", "returned_to_earlier_path"]) {
  editorAnswer = {success: true, value: {...editorValue, read_only: true, read_only_reason: reason}};
  await window.ServerManMapEditor.load();
  check(document.getElementById("map-status").textContent.startsWith("Read only:"), reason);
  check(document.getElementById("map-profile").textContent === "Alpha", "read-only identity");
}
editorAnswer = {success: true, value: {...editorValue, plan: null}};
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent.includes("confirm"), "missing bounds");
editorAnswer = {success: true, value: {...editorValue, state: "VALID", detail: "Editor records changed. Reload the plan."}};
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent.includes("Reload the plan"), "record freshness guidance");
editorAnswer = {success: false, error: {code: "NOT_FOUND", message: "The profile no longer exists.", retryable: false}};
await window.ServerManMapEditor.load();
check(document.body.dataset.mapReady === "false", "failure state");
check(document.getElementById("map-status").textContent.includes("profile"), "failure guidance");
check(editorCalls.every((call) => call === "load"), "no implicit save");
'''), "PASS")

    def test_stale_reads_close_promise_and_release(self) -> None:
        """A late load cannot overwrite a newer identity or the closing state."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
let finish;
window.pywebview.api.load_plan = () => new Promise((resolve) => { finish = resolve; });
const older = window.ServerManMapEditor.load();
window.pywebview.api.load_plan = async () => ({success: true, value: {
  ...editorValue, profile: {...editorValue.profile, display_name: "New identity"}}});
await window.ServerManMapEditor.load();
finish({success: true, value: editorValue}); await older;
check(document.getElementById("map-profile").textContent === "New identity", "late answer dropped");
const settle = window.ServerManMapEditor.settleForClose("application");
check(settle instanceof Promise, "close always returns promise");
check(await settle === "close", "pre-edit close answer");
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent === "Closing", "closing blocks load");
window.ServerManMapEditor.releaseClose();
check(document.getElementById("map-status").textContent === "Not saved yet", "release restores status");
window.pywebview.api.load_plan = () => new Promise((resolve) => { finish = resolve; });
const pending = window.ServerManMapEditor.load();
await window.ServerManMapEditor.settleForClose("editor");
finish({success: true, value: editorValue}); await pending;
check(document.getElementById("map-status").textContent === "Closing", "late load cannot undo close");
'''), "PASS")

    def test_bridge_save_guards_and_connection_errors(self) -> None:
        """Explicit saves retain their guards and broken host answers fail visibly."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
const plan = {background: {path: null}};
await window.ServerManMapBridge.save(plan, null, null);
const call = editorCalls.find((value) => Array.isArray(value));
check(call[1] === plan && call[2] === null && call[3] === null, "save arguments unchanged");
window.pywebview.api.load_plan = async () => ({});
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent.includes("invalid answer"), "malformed envelope");
delete window.pywebview.api.load_plan;
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent.includes("not connected"), "missing API refusal");
'''), "PASS")

    def test_stale_revision_keeps_editor_reload_guidance_and_shell_wording(self) -> None:
        """Plan conflicts offer editor reload while other errors keep shared wording."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
const plan = {background: {path: null}};
const failure = {success: false, error: {code: "REVISION_CONFLICT", message: "Reload the plan.",
  retryable: true, details: {reason: "stale_revision"}}};
window.pywebview.api.save_plan = async (...args) => {
  check(args[0] === plan && args[1] === 5 && args[2] === "token", "save guards unchanged");
  return failure;
};
let caught;
try { await window.ServerManMapBridge.save(plan, 5, "token"); } catch (error) { caught = error; }
check(caught?.result === failure, "structured refusal preserved");
check(caught?.message === "The map plan changed. Reload the plan before saving again.", "editor reload guidance");
check(!caught.message.includes("another page"), "no shell navigation advice");
const shellText = window.ServerManOperationMessages.bridgeError(failure);
check(shellText.includes("another page"), "shared shell wording unchanged");
for (const other of [
  {success: false, error: {...failure.error, details: {reason: "invalid_saved_data"}}},
  {success: false, error: {code: "INVALID_REQUEST", message: "Correct the plan.", details: {reason: "stale_revision"}}}
]) {
  check(window.ServerManMapWording.error(other) === window.ServerManOperationMessages.bridgeError(
    other, "The map plan could not be loaded."), "other failures reuse shared formatter");
}
// Reload is already an editor action and makes no implicit save or shell navigation.
await window.ServerManMapEditor.load();
check(document.body.dataset.mapReady === "true", "editor load action remains available");
'''), "PASS")

    def test_dom_text_modal_focus_trap_and_restore(self) -> None:
        """Modal text is safe and its keyboard focus stays within its choices."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
const viewport = document.getElementById("map-viewport"); viewport.focus();
const dialogPromise = window.ServerManMapDom.dialog("Review", "<img src=x>", [
  {label: "Discard changes", value: "close", danger: true}, {label: "Keep editing", value: "cancel"}]);
const dialog = document.querySelector("dialog");
const buttons = [...dialog.querySelectorAll("button")];
check(!dialog.querySelector("img"), "message is text only");
check(document.activeElement === buttons[1], "keep editing gets focus");
buttons[1].dispatchEvent(new KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true}));
check(document.activeElement === buttons[0], "forward focus wraps");
buttons[0].dispatchEvent(new KeyboardEvent("keydown", {key: "Tab", shiftKey: true, bubbles: true, cancelable: true}));
check(document.activeElement === buttons[1], "backward focus wraps");
check(buttons[0].className === "button-danger", "discard danger style");
buttons[1].click(); check(await dialogPromise === "cancel", "cancel answer");
check(document.activeElement === viewport && !document.querySelector("dialog"), "focus restored");
'''), "PASS")
