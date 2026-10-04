"""Section registry contract: one registration per section drives the whole shell dispatch."""
from __future__ import annotations

import re
import unittest

try:
    from tests.ui_harness_support import EDGE, FRONTEND, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, FRONTEND, run_shell_harness

from dayz_serverman.host.assets import compose_shell_html


SECTIONS = ("overview", "profiles", "configuration", "tweaks", "mods", "backups", "logs", "settings")

# Replaces every page module with a recorder, then drives the shell through the registry
HARNESS = r"""
const calls = [];
const page = (name, extra = {}) => { window[name] = {
  open: (...args) => { calls.push(`${name}.open(${args.map((item) => item === null ? "null"
    : typeof item === "object" ? "snapshot" : item).join(",")})`); },
  operationFinished: (operation) => { calls.push(`${name}.operation`);
    return operation.claimedBy === name; }, ...extra }; };
["ServerManOverview", "ServerManProfiles", "ServerManConfiguration", "ServerManTweaks",
  "ServerManMods", "ServerManBackups", "ServerManLogs", "ServerManSettings", "ServerManRestore",
  "ServerManMigration", "ServerManProfileRestore"].forEach((name) => page(name));
window.ServerManOverview.track = (id) => calls.push(`track(${id})`);
window.ServerManLogs.refresh = () => {};
window.ServerManOverviewStatus = {refresh: async () => {}};
window.ServerManUpdateStatus = {poll: async () => {}};
window.ServerManProfileContext = {initialize: async () => ({success: true}), selectedId: () => "alpha",
  profiles: () => profiles};
let events = [];
const operations = new Map();
window.pywebview = {api: {
  get_application_snapshot: async () => ok(snapshot()),
  read_operation_events: async () => { const batch = events; events = [];
    return ok({session_id: "harness", next_cursor: 1, events: batch}); },
  get_operation: async (id) => ok(operations.get(id)),
}};
const expected = {
  overview: ["Overview", "Server state, lifecycle controls, and the next safe operator action.",
    "ServerManOverview.open(null)", "ServerManOverview.open(snapshot)", true],
  profiles: ["Profiles", "Create and maintain complete DayZ server launch profiles.",
    "ServerManProfiles.open(alpha)", "ServerManProfiles.open(alpha)", true],
  configuration: ["Configuration", "Edit the selected server's core configuration with guided controls.",
    "ServerManConfiguration.open()", "ServerManConfiguration.open()", true],
  tweaks: ["Tweaks", "Fine-tune the selected server with compact, map-aware controls.",
    "ServerManTweaks.open()", "ServerManTweaks.open()", true],
  mods: ["Mods", "Download, update, and apply mods for the selected server.",
    "ServerManMods.open()", "ServerManMods.open()", true],
  backups: ["Backups", "Create and restore verified server backups.",
    "ServerManBackups.open()", "ServerManBackups.open()", true],
  logs: ["Logs", "Inspect manager activity and captured DayZ server output.",
    "ServerManLogs.open()", "ServerManLogs.open()", false],
  settings: ["Settings", "Configure portable application paths and SteamCMD authentication.",
    "ServerManSettings.open(null)", "ServerManSettings.open(snapshot)", false],
};
const handlers = {
  overview: ["ServerManOverview"], profiles: ["ServerManProfiles"],
  configuration: ["ServerManConfiguration"], tweaks: ["ServerManTweaks"], mods: ["ServerManMods"],
  backups: ["ServerManRestore", "ServerManBackups"], logs: [],
  settings: ["ServerManMigration", "ServerManSettings"],
};
const navigation = [...document.querySelectorAll(".nav-item")].map((item) => item.dataset.section);
check(JSON.stringify(window.ServerManSections.ids()) === JSON.stringify(navigation),
  "registry order differs from the navigation");
check(Object.isFrozen(window.ServerManSections), "registry is not frozen");
let refused = false;
try { window.ServerManSections.register("mods", {title: "x", description: "x", open: () => {}}); }
catch (_error) { refused = true; }
check(refused, "a second registration of a section was accepted");
shellState.hostReady = true; shellState.sessionId = "harness";
for (const id of navigation) {
  const [title, description, commitCall, snapshotCall, reopens] = expected[id];
  // Section commit: heading from the registry and one open call.
  calls.length = 0; commitSection(id);
  check(document.getElementById("page-title").textContent === title, `${id}: title`);
  check(document.getElementById("page-description").textContent === description, `${id}: description`);
  check(JSON.stringify(calls) === JSON.stringify([commitCall]), `${id}: commit opened ${calls}`);
  check(document.querySelector(".nav-item[aria-current]").dataset.section === id, `${id}: navigation mark`);
  // Snapshot load: only a section that needs the snapshot receives it.
  calls.length = 0; await loadSnapshot();
  check(JSON.stringify(calls) === JSON.stringify([snapshotCall]), `${id}: snapshot opened ${calls}`);
  // Profile change: the same six sections reopen as before.
  calls.length = 0; document.dispatchEvent(new CustomEvent("serverman:profile-change"));
  check(JSON.stringify(calls) === JSON.stringify(reopens ? [commitCall] : []),
    `${id}: profile change gave ${calls}`);
  // Operation events: the background handler first, then the handlers of the visible section in order.
  calls.length = 0;
  const unclaimed = window.ServerManSections.operationFinished(id, {operation_id: "nobody"});
  check(!unclaimed, `${id}: an unclaimed operation was reported as handled`);
  check(JSON.stringify(calls) === JSON.stringify(["ServerManProfileRestore.operation",
    ...handlers[id].map((name) => `${name}.operation`)]), `${id}: handler order ${calls}`);
  for (const name of handlers[id]) {
    check(window.ServerManSections.operationFinished(id, {operation_id: "one", claimedBy: name}),
      `${id}: ${name} did not consume its operation`);
  }
  calls.length = 0;
  check(window.ServerManSections.operationFinished(id, {operation_id: "zip",
    claimedBy: "ServerManProfileRestore"}), `${id}: the ZIP restore did not report in the background`);
  // No handler is skipped: the visible section still sees an operation that a background handler consumed.
  check(JSON.stringify(calls) === JSON.stringify(["ServerManProfileRestore.operation",
    ...handlers[id].map((name) => `${name}.operation`)]), `${id}: handlers after a background claim ${calls}`);
}
// The poll loop hands each named operation to the registry.
commitSection("mods"); calls.length = 0;
operations.set("polled", record("polled", "SAVE_STEAM_SETTINGS", "RUNNING",
  {progress_phase: "running", claimedBy: "ServerManMods"}));
events = [{operation_id: "polled"}];
await pollEvents();
check(calls.includes("ServerManMods.operation"), `poll did not reach the visible section: ${calls}`);
// The unsaved-edit rule of a snapshot load is unchanged.
window.ServerManTransitions.setDirty("settings-paths", true);
for (const [id, kept] of [["configuration", true], ["profiles", true], ["settings", true], ["mods", false]]) {
  shellState.section = id; window.ServerManWorkspace.activate(id); calls.length = 0;
  await loadSnapshot();
  check((calls.length === 0) === kept, `${id}: unsaved edits rule gave ${calls}`);
}
window.ServerManTransitions.setDirty("settings-paths", false);
// One registration adds a complete section.
window.ServerManSections.register("extra", {title: "Extra", description: "One more section.",
  reopensOnProfileChange: true, open: () => calls.push("extra.open"),
  operationFinished: () => { calls.push("extra.operation"); return true; }});
calls.length = 0; commitSection("extra");
check(document.getElementById("page-title").textContent === "Extra", "new section title");
document.dispatchEvent(new CustomEvent("serverman:profile-change"));
operations.set("extra-op", record("extra-op", "SAVE_SETTINGS", "RUNNING", {progress_phase: "running"}));
events = [{operation_id: "extra-op"}];
await pollEvents();
check(JSON.stringify(calls.filter((item) => item.startsWith("extra")))
  === JSON.stringify(["extra.open", "extra.open", "extra.operation"]), `new section calls ${calls}`);
shellState.hostReady = false;
"""


class SectionRegistryStaticTests(unittest.TestCase):
    """The registry is the only place that names the section modules."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the registry, the shell loop, and the composed document once."""
        cls.registry = (FRONTEND / "sections.js").read_text(encoding="utf-8")
        cls.app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        cls.composed = compose_shell_html(FRONTEND)

    def test_every_section_is_registered_exactly_once(self) -> None:
        """Each navigation section has one registration, in navigation order."""
        registered = re.findall(r'^registerSection\("([a-z]+)"', self.registry, re.MULTILINE)
        self.assertEqual(tuple(registered), SECTIONS)
        document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertEqual(tuple(re.findall(r'data-section="([a-z]+)"', document)), SECTIONS)

    def test_shell_loop_names_no_section_module_for_dispatch(self) -> None:
        """The shell loop opens and notifies sections only through the registry."""
        for module in ("ServerManOverview.open", "ServerManProfiles", "ServerManConfiguration",
                       "ServerManTweaks", "ServerManBackups", "ServerManMods", "ServerManSettings",
                       "ServerManMigration", "ServerManRestore", "ServerManLogs.open",
                       "operationFinished(operation.value)"):
            self.assertNotIn(module, self.app)
        for call in ("ServerManSections.get(shellState.section)",
                     "ServerManSections.open(shellState.section, result.value)",
                     "ServerManSections.open(section)",
                     "ServerManSections.profileChanged(shellState.section)",
                     "ServerManSections.operationFinished(shellState.section, operation.value)"):
            self.assertIn(call, self.app)

    def test_registry_is_composed_after_the_pages_and_before_the_shell_loop(self) -> None:
        """The composed document loads the registry between the page modules and the shell loop."""
        registry = self.composed.index("window.ServerManSections = Object.freeze(")
        self.assertLess(self.composed.index("window.ServerManLogs = "), registry)
        self.assertLess(registry, self.composed.index("const shellState = {"))
        self.assertTrue(self.registry.startswith("// "))
        self.assertIn('"use strict";', self.registry)
        self.assertNotIn("innerHTML", self.registry)
        self.assertLessEqual(len(self.registry.splitlines()), 300)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SectionRegistryDynamicTests(unittest.TestCase):
    """Every section opens, follows a profile change, and consumes its operation events."""

    def test_sections_behave_as_before_and_one_registration_adds_a_section(self) -> None:
        """Drive commit, snapshot load, profile change, and operation events for each section."""
        self.assertEqual(run_shell_harness(HARNESS), "PASS")


if __name__ == "__main__":
    unittest.main()
