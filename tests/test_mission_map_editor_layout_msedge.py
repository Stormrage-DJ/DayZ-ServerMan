"""Measure the composed editor layout and truthful status in isolated headless Edge."""

from __future__ import annotations

import unittest

from tests.ui_harness_support import EDGE, run_editor_harness


# Cover accepted normal, minimum and maximized outer sizes with the real document.
SIZES = ("1280,800", "960,600", "1920,1080")

# Measure visible containment rather than accepting hidden document overflow.
GEOMETRY = r'''
const rect = (id) => document.getElementById(id).getBoundingClientRect();
const fits = () => {
  check(document.documentElement.scrollWidth <= innerWidth, "horizontal page overflow");
  check(document.documentElement.scrollHeight <= innerHeight, "vertical page overflow");
  for (const id of ["map-identity", "map-status-area", "map-panel", "map-viewport"]) {
    const area = rect(id);
    check(area.left >= 0 && area.top >= 0 && area.right <= innerWidth + 1 &&
      area.bottom <= innerHeight + 1, `visible region ${id}`);
  }
  const map = rect("map-viewport");
  check(map.width >= innerWidth * .60 && map.height >= innerHeight * .50, "large central map");
  const terrain = document.getElementById("map-terrain-surface");
  if (terrain) {
    const area = terrain.getBoundingClientRect();
    check(area.width > 0 && area.height > 0, "actual bounded terrain rectangle");
  } else {
    check(document.getElementById("map-view-label").textContent.includes("not available"), "truthful unknown bounds");
    check(document.getElementById("map-fit").disabled, "no invented fit bounds");
  }
  check(!document.querySelector("[style]"), "no inline style attributes");
};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorLayoutEdgeTests(unittest.TestCase):
    """Use synthetic identities and API answers without any real application data."""

    def test_sizes_collapse_reload_and_long_safe_identity(self) -> None:
        """All three sizes retain identity and gain map space when controls collapse."""
        for size in SIZES:
            with self.subTest(size=size):
                self.assertEqual(run_editor_harness(GEOMETRY + r'''
await window.ServerManMapEditor.load(); await wait();
const panel = document.getElementById("map-panel");
const toggle = document.getElementById("map-panel-toggle");
check(panel.open, "controls start expanded"); fits();
const expandedWidth = rect("map-viewport").width;
toggle.focus(); toggle.click(); await wait();
check(!panel.open && toggle.getAttribute("aria-expanded") === "false", "collapsed disclosure");
check(toggle.getAttribute("aria-controls") === "map-panel-content", "disclosure target");
check(document.activeElement === toggle, "toggle retains keyboard focus");
check(getComputedStyle(toggle).outlineStyle !== "none", "visible keyboard focus");
check(rect("map-viewport").width > expandedWidth + 200, "collapse enlarges map"); fits();
await window.ServerManMapEditor.load(); await wait();
check(!panel.open && toggle.getAttribute("aria-expanded") === "false", "collapse survives load");
toggle.focus(); toggle.click(); await wait();
await window.ServerManMapEditor.load(); await wait();
check(panel.open && toggle.getAttribute("aria-expanded") === "true", "expanded state survives load");
const longName = "<img src=x onerror=alert(1)> & " + "Long safe identity ".repeat(80);
editorAnswer = {success: true, value: {...editorValue,
  profile: {...editorValue.profile, display_name: longName}, mission_name: longName,
  capabilities: {terrain_id: longName}, plan: {terrain: {terrain_id: longName}}}};
await window.ServerManMapEditor.load(); await wait();
for (const id of ["map-profile", "map-mission", "map-terrain"]) {
  check(document.getElementById(id).textContent === longName, `safe identity ${id}`);
  check(document.getElementById(id).title === longName, `full identity tooltip ${id}`);
}
check(!document.querySelector("#map-identity img"), "identity is text only"); fits();
check(editorCalls.every((call) => call === "load"), "display changes never save");
''', window_size=size), "PASS")

    def test_status_slots_backend_draft_states_and_announcements(self) -> None:
        """Display backend booleans and unavailable values without inventing Saved or coordinates."""
        self.assertEqual(run_editor_harness(r'''
await window.ServerManMapEditor.load(); await wait();
const text = (id) => document.getElementById(id).textContent;
const status = document.getElementById("map-status");
const live = document.getElementById("map-announcement");
check(text("map-coordinates") === "Not available", "coordinates unavailable");
check(text("map-tool-status") === "No tool active", "no invented current tool");
check(getComputedStyle(document.getElementById("map-coordinates")).fontVariantNumeric === "tabular-nums",
  "stable numeric coordinates slot");
check(!status.hasAttribute("aria-live") && live.getAttribute("aria-live") === "polite", "separate polite region");
check(getComputedStyle(status).userSelect !== "none", "status is selectable");
check(getComputedStyle(document.getElementById("map-profile")).userSelect !== "none", "identity is selectable");
for (const [value, expected] of [[false, "No"], [true, "Yes"], [null, "Not available"], [undefined, "Not available"]]) {
  editorAnswer = {success: true, value: {...editorValue, state: "VALID", draft_changes: value}};
  await window.ServerManMapEditor.load();
  check(text("map-draft-changes") === expected, `backend draft changes ${value}`);
  check(text("map-status") === "Map plan loaded", "load is not Saved");
  check(live.textContent === text("map-status"), "load announcement");
}
for (const reason of ["invalid_saved_data", "mission_path_changed", "returned_to_earlier_path"]) {
  editorAnswer = {success: true, value: {...editorValue, read_only: true,
    read_only_reason: reason, draft_changes: true}};
  await window.ServerManMapEditor.load();
  check(text("map-status").startsWith("Read only:"), `read-only ${reason}`);
  check(text("map-draft-changes") === "Yes", "read-only backend draft retained");
}
editorAnswer = {success: false, error: {code: "NOT_FOUND", message: "The profile no longer exists."}};
await window.ServerManMapEditor.load();
check(text("map-draft-changes") === "Not available", "failed load has no guessed draft state");
check(live.textContent === text("map-status"), "failure announcement");
check(editorCalls.every((call) => call === "load"), "no implicit save");
'''), "PASS")

    def test_stale_status_close_and_release_keep_layout_state(self) -> None:
        """A stale answer cannot replace backend draft state or Closing after collapse."""
        self.assertEqual(run_editor_harness(GEOMETRY + r'''
await window.ServerManMapEditor.load(); await wait();
let finish;
window.pywebview.api.load_plan = () => new Promise((resolve) => { finish = resolve; });
const older = window.ServerManMapEditor.load();
window.pywebview.api.load_plan = async () => ({success: true, value: {...editorValue, draft_changes: true}});
await window.ServerManMapEditor.load();
finish({success: true, value: {...editorValue, draft_changes: false}}); await older;
check(document.getElementById("map-draft-changes").textContent === "Yes", "drop stale draft status");
document.getElementById("map-panel-toggle").click(); await wait();
const settle = window.ServerManMapEditor.settleForClose("application");
check(settle instanceof Promise && await settle === "close", "close Promise unchanged");
check(document.getElementById("map-status").textContent === "Closing", "exact Closing state");
check(document.getElementById("map-announcement").textContent === "Closing", "close announced");
await window.ServerManMapEditor.load();
check(document.getElementById("map-status").textContent === "Closing", "load cannot undo close");
window.ServerManMapEditor.releaseClose();
check(document.getElementById("map-status").textContent === "Not saved yet", "release status unchanged");
check(!document.getElementById("map-panel").open, "close release keeps collapse"); fits();
'''), "PASS")

    def test_pending_reload_close_release_restores_loaded_comparison_and_busy(self) -> None:
        """Cancelled close restores the backend comparison and accessibility state at every size."""
        for size in SIZES:
            with self.subTest(size=size):
                self.assertEqual(run_editor_harness(GEOMETRY + r'''
await window.ServerManMapEditor.load(); await wait();
const panel = document.getElementById("map-panel");
panel.open = false; await wait();
const cases = [[true, "Yes"], [false, "No"], [null, "Not available"],
  [undefined, "Not available"], ["true", "Not available"]];
for (const [value, expected] of cases) {
  for (const rejected of [false, true]) {
    for (const lateAfterRelease of [false, true]) {
      const loaded = {...editorValue, state: "VALID", draft_changes: value,
        read_only: value === false || value === undefined, read_only_reason: "mission_path_changed"};
      if (value === undefined) delete loaded.draft_changes;
      window.pywebview.api.load_plan = async () => { editorCalls.push("load");
        return {success: true, value: loaded}; };
      await window.ServerManMapEditor.load();
      const text = (id) => document.getElementById(id).textContent;
      const status = text("map-status");
      const profile = text("map-profile");
      check(text("map-draft-changes") === expected, "loaded backend comparison");
      let finish;
      window.pywebview.api.load_plan = () => { editorCalls.push("load");
        return new Promise((resolve, reject) => { finish = rejected ? reject : resolve; }); };
      const pending = window.ServerManMapEditor.load();
      check(document.getElementById("map-editor").getAttribute("aria-busy") === "true", "pending load busy");
      const settle = window.ServerManMapEditor.settleForClose("application");
      check(settle instanceof Promise && await settle === "close", "close Promise retained");
      check(text("map-status") === "Closing", "exact pending close status");
      const complete = async () => {
        finish(rejected ? new Error("Late invalidated failure") : {success: true, value: {
          ...editorValue, draft_changes: !value, profile: {display_name: "Late identity"}}});
        await pending;
      };
      if (!lateAfterRelease) {
        await complete(); check(text("map-status") === "Closing", "invalidated answer cannot undo Closing");
      }
      const beforeRelease = editorCalls.length;
      window.ServerManMapEditor.releaseClose();
      if (lateAfterRelease) await complete();
      window.ServerManMapEditor.releaseClose();
      const actual = {draft: text("map-draft-changes"), busy: document.getElementById("map-editor").getAttribute("aria-busy")};
      check(actual.draft === expected && actual.busy === "false",
        `loaded restoration ${expected}: ${JSON.stringify(actual)}`);
      check(text("map-status") === status && text("map-announcement") === status, "restore loaded/read-only status");
      check(text("map-profile") === profile && !panel.open, "retain identity and local collapse");
      check(editorCalls.length === beforeRelease, "repeated loaded release makes no new request"); fits();
    }
  }
}
window.pywebview.api.load_plan = async () => ({success: false, error: {
  code: "NOT_FOUND", message: "The profile no longer exists."}});
await window.ServerManMapEditor.load();
await window.ServerManMapEditor.settleForClose("application");
window.pywebview.api.load_plan = async () => { editorCalls.push("load");
  return {success: true, value: {...editorValue, state: "VALID", draft_changes: true}}; };
const beforeReload = editorCalls.length;
window.ServerManMapEditor.releaseClose(); await wait();
check(editorCalls.length === beforeReload + 1, "no-loaded release still reloads");
check(document.getElementById("map-draft-changes").textContent === "Yes", "fresh reload comparison");
check(editorCalls.every((call) => call === "load"), "no close release saves");
''', window_size=size), "PASS")
