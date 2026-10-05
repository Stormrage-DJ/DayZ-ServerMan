"""Headless Edge checks of Settings: the update-check switch and the "In use" marks of the locations."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, FRONTEND, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, FRONTEND, run_shell_harness


# Fake answers of Settings: the stored switch, a settable save answer, and a folder picker
HEAD = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const region = () => byId("content-region");
const page = {automatic: false, saved: [], saveAnswer: null, statusReads: 0};
window.pywebview.api.get_ui_preferences = async () => ok({selected_profile_id: "alpha",
  backup_after_stop_profiles: [], automatic_update_checks: page.automatic});
window.pywebview.api.save_automatic_update_checks = async (enabled) => { page.saved.push(enabled);
  if (page.saveAnswer) return page.saveAnswer;
  page.automatic = enabled; return ok({automatic_update_checks: enabled}); };
window.pywebview.api.get_update_status = async () => { page.statusReads += 1; return ok(host.updates); };
window.pywebview.api.select_settings_path = async (role) => ok({cancelled: false, role, status: "READY",
  path: "E:\\New server", resolved_paths: {dayz_executable: "E:\\New server\\DayZServer_x64.exe"}});
host.updates = {...host.updates, mods: {...host.updates.mods, check_state: "STALE",
  last_success_at: "2026-10-02T07:12:44.000Z"}};
"""

# Specification 5.1 section 5: the switch saves at once, outside the unsaved-change guard
SWITCH = HEAD + r"""
await start("settings"); await wait(80);
same(byId("page-description").textContent, "Application folders and update checks.", "description");
same([...region().querySelectorAll(":scope > .panel > h2")].map((node) => node.textContent).join("|"),
  "Application locations|Update checks|Legacy manager import", "panel order");
const box = () => byId("automatic-update-checks");
check(box().type === "checkbox" && box().getAttribute("role") === "switch" && !box().checked, "switch from the stored value");
same(box().closest("label").textContent, "Check Steam for mod and server updates automatically", "switch label");
const help = byId(box().getAttribute("aria-describedby")).textContent;
check(help.includes("Only Workshop item numbers are sent.") && help.includes("every 30 minutes") && help.includes("every 6 hours")
  && help.includes("SteamCMD signs in anonymously") && help.includes("Your Steam account is not used"), `help: ${help}`);
const feedback = () => byId("automatic-update-checks-feedback");
check(feedback().getAttribute("role") === "status" && feedback().textContent === "", "feedback line");
// With the switch off, the update wording names the switch.
same(window.ServerManUpdateStatus.summary().text, "Could not check: automatic checks are off", "wording while off");
// Turning it on saves at once, locks the box during the call, and reads the update state again.
let release;
page.saveAnswer = new Promise((resolve) => { release = resolve; });
const reads = page.statusReads;
box().click(); await wait(10);
check(box().disabled && box().checked && JSON.stringify(page.saved) === "[true]", "save call or lock");
page.automatic = true; release(ok({automatic_update_checks: true})); await wait(60);
check(!box().disabled && box().checked, "the switch after the save");
same(feedback().textContent, "Saved. Automatic checks are on.", "result line");
check(window.ServerManUpdateStatus.automaticChecks() === true && page.statusReads === reads + 1,
  "the update state did not take the new value or was not read again");
same(window.ServerManUpdateStatus.summary().text, "Could not check: the last check is too old", "wording while on");
// The switch is no part of "Save locations": nothing is unsaved, and the guard does not ask.
check(!window.ServerManTransitions.hasUnsavedChanges() && byId("save-path-settings").disabled
  && region().querySelector(".unsaved-indicator").textContent === "Settings are unchanged", "the switch set the unsaved mark");
commitSection("overview"); await wait(60);
check(!document.querySelector(".unsaved-guard") && shellState.section === "overview", "the guard asked about the switch");
check(byId("overview-updates").querySelector(".overview-updates-switch").hidden, "Overview still says that checks are off");
// A failed save puts the old value back and says so.
commitSection("settings"); await wait(80);
page.saveAnswer = fail("STORAGE_FAILURE", "x");
box().click(); await wait(40);
check(box().checked && !box().disabled && window.ServerManUpdateStatus.automaticChecks() === true, "failed save");
same(feedback().textContent, "The setting could not be saved.", "failure line");
page.saveAnswer = null;
box().click(); await wait(40);
same(feedback().textContent, "Saved. Automatic checks are off.", "result line when off");
// A redraw of the locations form rebuilds the panel from the stored value.
document.querySelector("[data-settings-browse='dayz_root']").click(); await wait(60);
check(!box().checked && byId("automatic-update-checks-feedback").textContent === "", "panel after a redraw");
"""

# "Active locations" of the old Overview: marks on the existing rows, no second list
MARKS = HEAD + r"""
await start("settings"); await wait(80);
const inUse = () => [...region().querySelectorAll(".settings-in-use")].map((node) => node.textContent).join("|");
const tags = () => [...region().querySelectorAll(".settings-in-use-tag")].map((node) =>
  node.closest(".backup-choice").dataset.backupChoice).join("|");
// Saved state: the selected backup choice carries the tag; no path is shown twice.
same(tags(), "portable", "tag on the saved destination");
same(inUse(), "", "lines without an unsaved change");
for (const path of ["D:\\Server", "D:\\Server\\DayZServer_x64.exe", "D:\\Manager\\backups"]) {
  same([...region().querySelectorAll("dd, code")].filter((node) => node.textContent === path).length
    + [...region().querySelectorAll("input")].filter((input) => input.value === path).length, 1,
    `times ${path} is shown`);
}
// A changed folder names the saved one under it, and under the path that follows it.
document.querySelector("[data-settings-browse='dayz_root']").click(); await wait(60);
same(byId("settings-dayz_root").value, "E:\\New server", "chosen folder");
same(inUse(), "In use until saved: D:\\Server|In use until saved: D:\\Server\\DayZServer_x64.exe", "lines after a change");
check(byId("settings-dayz_root").closest(".settings-path").querySelector(".settings-in-use"), "line place");
same(tags(), "portable", "tag after a folder change");
// Another backup choice: the tag goes, and the saved destination is named until the save.
byId("backup-mode-custom").click(); await wait(40);
same(tags(), "", "tag on an unsaved choice");
check(inUse().endsWith("|In use until saved: D:\\Manager\\backups"), `backup line: ${inUse()}`);
byId("backup-mode-portable").click(); await wait(40);
same(tags(), "portable", "tag after the choice returned");
// Overview no longer lists the locations.
window.ServerManTransitions.setDirty("settings-paths", false);
commitSection("overview"); await wait(80);
check(!region().textContent.includes("Active locations") && !region().textContent.includes("D:\\Manager\\backups"),
  "Overview still lists the locations");
"""


class SettingsUpdatesStaticTests(unittest.TestCase):
    """Source contracts of the switch module."""

    def test_switch_module_saves_through_the_named_method_only(self) -> None:
        """The module calls the named bridge method and never touches the unsaved-change guard."""
        source = (FRONTEND / "settings_updates.js").read_text(encoding="utf-8")
        self.assertIn("window.pywebview.api.save_automatic_update_checks(enabled)", source)
        self.assertIn("window.ServerManUpdateStatus.setAutomaticChecks(enabled)", source)
        self.assertNotIn("setDirty", source)
        self.assertNotIn("innerHTML", source)
        self.assertLessEqual(len(source.splitlines()), 300)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SettingsUpdatesDynamicTests(unittest.TestCase):
    """Decision D1 on Settings, and the place of the former "Active locations"."""

    def test_switch_saves_at_once_and_the_wording_follows(self) -> None:
        """Switch: stored value, save, result line, failure, wording, no unsaved mark."""
        self.assertEqual(run_shell_harness(SWITCH, window_size="1500,900", budget=12000), "PASS")

    def test_in_use_marks_replace_the_overview_list(self) -> None:
        """Tag on the saved backup choice; "In use until saved" under a changed path."""
        self.assertEqual(run_shell_harness(MARKS, window_size="1500,900", budget=10000), "PASS")


if __name__ == "__main__":
    unittest.main()
