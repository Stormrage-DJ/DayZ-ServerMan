"""Composed-shell checks of the profile-bound main-window map editor action."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, FRONTEND, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, FRONTEND, run_shell_harness


# Synthetic mission loads use the requested identity and exercise the real Tweaks workspace.
SETUP = r"""
const mission = (id, target) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, digest: "c".repeat(64), values: target === "events"
    ? {events: {}} : target === "starter_loadout" ? {items: []} : {}});
window.pywebview.api.load_configuration = async (id) => ok({profile_id: id, fields: []});
window.pywebview.api.load_mission_configuration = async (id, target) => mission(id, target);
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id,
  profile_revision: 3, settings_revision: 2, features: {
    medical_loot_zones: {enabled: false}}});
const calls = [];
window.pywebview.api.open_map_editor = async (id) => { calls.push(id); return ok({opened: true, focused: false}); };
const action = () => document.getElementById("open-map-editor");
const line = () => document.getElementById("map-editor-entry-reason");
const unavailable = (text) => {
  const button = action();
  check(button && button.getAttribute("aria-disabled") === "true", "action must be unavailable");
  check(!button.hasAttribute("disabled"), "unavailable action must remain focusable");
  check(button.getAttribute("aria-describedby") === line().id, "visible reason association");
  check(line().textContent === text, "reason: " + line().textContent);
  button.focus(); check(document.activeElement === button, "unavailable focus");
  button.click(); check(calls.length === 0, "unavailable activation called the host");
};
const selectDirectly = (id) => { profileContextState.selectedId = id; announceProfileSelection(); };
"""


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class MapEditorEntryEdgeTests(unittest.TestCase):
    """Use actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_current_profile_and_all_areas_preserve_unsaved_tweaks(self) -> None:
        """The entry precedes all area tabs and never discards or applies drafts."""
        self.run_case(r"""
await start("tweaks");
setTweakValue("gameplay", "retained", 42);
const before = tweakFingerprint([...tweaksState.drafts]);
for (const area of ["gameplay", "events", "population"]) {
  chooseTweakArea(area);
  check(action().classList.contains("button"), "normal button styling");
  check(action().getAttribute("aria-disabled") === "false", "loaded action enabled");
  check(action().textContent === "Open map editor", "action wording");
  check(line().textContent === "Opens a separate window.", "separate window wording");
  const slot = document.getElementById("map-editor-entry");
  check(slot.nextElementSibling.classList.contains("primary-tabs"), "entry above area tabs");
  check(document.getElementById("tweaks-actions"), "tweak actions retained");
  action().click(); await wait();
  check(calls.at(-1) === "alpha", "current profile argument");
  check(tweakFingerprint([...tweaksState.drafts]) === before && anyTweakDirty(), "draft retained");
}
window.ServerManOperationBar.reset([record("busy", "CREATE_BACKUP", "QUEUED")]);
check(action().getAttribute("aria-disabled") === "false", "nonmutating action stays available during work");
check(calls.length === 3, "one open per click");
""")

    def test_no_profile_and_missing_mission_show_accessible_safe_reasons(self) -> None:
        """Missing context and a real safe NOT_FOUND sentence explain refusal."""
        self.run_case(r"""
await start(); selectDirectly(null); await start("tweaks");
unavailable("Select a server profile first.");
window.pywebview.api.load_mission_configuration = async (id, target) => target === "economy"
  ? fail("NOT_FOUND", "The mission folder <img src=x> was not found.") : mission(id, target);
selectDirectly("alpha"); await wait(20);
unavailable("The mission folder <img src=x> was not found.");
check(!document.querySelector("#map-editor-entry img"), "host text cannot create markup");
for (const area of ["events", "population", "gameplay"]) {
  chooseTweakArea(area); unavailable("The mission folder <img src=x> was not found.");
}
""")

    def test_pending_load_and_old_snapshot_rerender_cannot_enable_action(self) -> None:
        """A pending current load invalidates the previously loaded mission immediately."""
        self.run_case(r"""
await start("tweaks");
let release;
window.pywebview.api.load_mission_configuration = async (id, target) => target === "economy"
  ? await new Promise((resolve) => { release = () => resolve(mission(id, target)); }) : mission(id, target);
const pending = window.ServerManTweaks.open();
unavailable("Reading the mission of this profile.");
check(tweaksState.snapshots.has("economy"), "old snapshots are still present for this regression");
renderTweaks(); unavailable("Reading the mission of this profile.");
release(); await pending;
check(action().getAttribute("aria-disabled") === "false", "current completed load enables action");
""")

    def test_focus_failure_rerender_and_retry_keep_operator_feedback(self) -> None:
        """Focus and refusal feedback survive all area renders; unsafe errors use the catalogue."""
        self.run_case(r"""
await start("tweaks");
window.pywebview.api.open_map_editor = async (id) => { calls.push(id); return ok({opened: false, focused: true}); };
action().click(); await wait();
check(line().textContent === "The map editor of this profile is already open.", "focus wording");
window.pywebview.api.open_map_editor = async () => fail("STORAGE_FAILURE", "The map editor folder could not be read.");
action().click(); await wait();
for (const area of ["events", "population", "gameplay"]) {
  chooseTweakArea(area); check(line().textContent === "The map editor folder could not be read.", "failure retained");
}
window.pywebview.api.open_map_editor = async () => fail("INTERNAL_FAILURE", "PRIVATE_INTERNAL_DETAIL");
action().click(); await wait();
check(!line().textContent.includes("PRIVATE_INTERNAL_DETAIL"), "internal details stay private");
window.pywebview.api.open_map_editor = async () => ok({opened: true, focused: false});
action().click(); await wait();
check(line().textContent === "Opens a separate window.", "success clears prior failure");
""")

    def test_profile_switch_drops_old_load_and_open_answers(self) -> None:
        """Selection changes clear feedback and reject old answers, including return to the same ID."""
        self.run_case(r"""
await start("tweaks");
window.pywebview.api.open_map_editor = async () => fail("NOT_FOUND", "The old editor failed.");
action().click(); await wait(); check(line().textContent === "The old editor failed.", "failure stored");
selectDirectly("bravo"); await wait(20);
check(line().textContent === "Opens a separate window.", "profile change clears stored failure");
selectDirectly("alpha"); await wait(20);
let openRelease;
window.pywebview.api.open_map_editor = async (id) => { calls.push(id);
  return await new Promise((resolve) => { openRelease = resolve; }); };
action().click();
const oldButton = action();
selectDirectly("bravo"); await wait(20);
oldButton.click(); check(calls.length === 1, "detached stale action refused");
selectDirectly("alpha"); await wait(20);
openRelease(fail("NOT_FOUND", "The old editor failed.")); await wait();
check(line().textContent === "Opens a separate window.", "late old open answer ignored");
let loadRelease;
window.pywebview.api.load_mission_configuration = async (id, target) => id === "alpha" && target === "economy"
  ? await new Promise((resolve) => { loadRelease = resolve; }) : mission(id, target);
const oldLoad = window.ServerManTweaks.open();
selectDirectly("bravo"); await wait(20);
loadRelease(fail("NOT_FOUND", "The old mission was not found.")); await oldLoad;
check(action().getAttribute("aria-disabled") === "false", "new profile remains enabled");
check(line().textContent === "Opens a separate window.", "old load answer ignored");
window.pywebview.api.open_map_editor = async (id) => { calls.push(id); return ok({opened: true}); };
action().click(); await wait(); check(calls.at(-1) === "bravo", "new current profile argument");
""")

    def test_mismatched_snapshot_and_failed_read_never_enable_action(self) -> None:
        """A successful envelope from another profile and a thrown read both remain unavailable."""
        self.run_case(r"""
window.pywebview.api.load_mission_configuration = async (id, target) => mission(target === "economy" ? "bravo" : id, target);
await start("tweaks"); unavailable("The mission of this profile could not be read.");
window.pywebview.api.load_mission_configuration = async () => { throw new Error("PRIVATE_READ_DETAIL"); };
await window.ServerManTweaks.open();
unavailable("Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.");
""")


class MapEditorEntryCompositionTests(unittest.TestCase):
    """Keep the entry offline and ordered before the Tweaks workspace integration."""

    def test_composition_and_nonmutating_boundary(self) -> None:
        """The composed shell embeds the module, and its only host action is opening the editor."""
        from dayz_serverman.host.assets import compose_shell_html
        document = compose_shell_html(FRONTEND)
        self.assertLess(document.index("const mapEditorEntryState"), document.index("const tweaksState"))
        source = (FRONTEND / "map_editor_entry.js").read_text(encoding="utf-8")
        for prohibited in ("ServerManBusy", "apply_configuration", "apply_mission_configuration",
                           "discardTweaks", "save_plan", "http://", "https://"):
            self.assertNotIn(prohibited, source)
        self.assertIn("pywebview.api.open_map_editor(profileId)", source)


if __name__ == "__main__":
    unittest.main()
