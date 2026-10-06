"""Headless Edge checks that the Overview fits 1150 x 720 without scrolling (D17) and that no page scrolls because of
the screen-reader announcers (QF-058)."""
from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

# `--dump-dom` keeps a window frame of 24 x 92 px inside the window size, so this window gives a 1150 x 720 viewport
# (the customer's 1166 x 753 window less its title bar). The harness checks the viewport before it measures.
FIT_WINDOW = "1174,812"

# Worst cases of the task 8.1 stress renders, with today's operator texts: long profile names, the longest block
# sentence, failed checks with their reasons, a skipped schedule run, the D11 notice, and a result in the bar.
SCENARIOS = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
profiles[0].display_name = "test20261001"; profiles[1].display_name = "Chernarus test";
const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60000).toISOString();
const build = (extra = {}) => ({state: "CURRENT", reason: null, ownership: "STEAM_CLIENT", installed_build: 24570360,
  installed_branch: "public", target_build: null, available_build: 24570360, available_time: 1786528820,
  check_state: "OK", checked_at: minutesAgo(4), last_success_at: minutesAgo(4), error_code: null, checking: false,
  waiting: false, revision: 1, ...extra});
const mods = (extra = {}) => ({check_state: "OK", checked_at: minutesAgo(4), last_success_at: minutesAgo(4),
  error_code: null, update_count: 0, pending_apply_count: 0, ...extra});
const backups = (profileId) => ok({profile_id: profileId, profile_revision: 3, settings_revision: 2,
  backups: [{backup_id: "b1", created_at: "2026-10-04T02:00:00Z", entry_count: 412, total_size: 1288490188,
    restore_compatibility: "COMPATIBLE", restore_compatibility_reason: null}], diagnostics: [], legacy_backups: [],
  destination_kind: "portable", runtime_profile: "serverman\\alpha\\profile"});
const schedule = (extra) => async (id) => ok({profile_id: id, enabled: true, hour: 4, minute: 0, ...extra});
const SCENES = {
  stopped: () => { host.updates = {mods: mods(), server_build: build(), checking: false, revision: 1};
    Object.assign(window.pywebview.api, {list_backups: async (id) => backups(id),
      get_lifecycle_schedule: schedule({enabled: false, action: null, next_run_local: null, last_status: null})}); },
  busy: () => {
    // D18: the widest player count, with its "Full" mark, in every running case
    host.status = {state: "RUNNING_MANAGED", process_id: 18244, diagnostic_code: null, readiness: "READY",
      query_port: 27016, profile_id: "alpha", started_at: minutesAgo(192), players: 128, max_players: 128};
    host.updates = {mods: mods({update_count: 2, pending_apply_count: 1}), checking: false, revision: 1,
      server_build: build({state: "UPDATE_AVAILABLE", available_build: 24600000, available_time: 1791100000})};
    Object.assign(window.pywebview.api, {list_backups: async (id) => backups(id),
      get_ui_preferences: async () => ok({selected_profile_id: "alpha", backup_after_stop_profiles: ["alpha"],
        automatic_update_checks: true}),
      get_lifecycle_schedule: schedule({action: "restart", next_run_local: "2026-10-06T04:00",
        last_status: "SKIPPED_NOT_RUNNING"})});
  },
  errors: () => {
    host.status = {state: "UNKNOWN", process_id: null, diagnostic_code: "PROCESS_AMBIGUOUS", readiness: null,
      query_port: null, profile_id: null, started_at: null};
    host.updates = {mods: mods({check_state: "FAILED", error_code: "TIMEOUT", last_success_at: minutesAgo(1560)}),
      server_build: build({state: "COULD_NOT_CHECK", reason: "FAILED", check_state: "FAILED",
        error_code: "STEAMCMD_EXIT_UNPROVEN", last_success_at: minutesAgo(1560)}), checking: false, revision: 1};
    Object.assign(window.pywebview.api, {
      get_application_snapshot: async () => ok({...snapshot(), mutation_block: "interrupted backup restore"}),
      list_backups: async () => fail("IO_ERROR", "unreadable"),
      get_lifecycle_schedule: schedule({action: "stop", hour: 23, minute: 30, next_run_local: "2026-10-05T23:30",
        last_status: "QUEUE_FAILED"})});
  },
};
SCENES.busy2 = () => { SCENES.busy(); host.status = {...host.status, profile_id: "bravo"}; };
"""


# Results waiting in the operation bar: one row, or two rows (a backup and a failed mod update)
BAR1 = ('await push(record("op-bar", "CREATE_BACKUP", "SUCCEEDED", {finished_at: "2026-10-05T03:30:00.000+00:00",'
        ' progress_percent: 100, target_profile_id: "alpha"})); await wait(100);')
BAR2 = BAR1 + ('await push(record("op-bar2", "UPDATE_MODS", "FAILED", {finished_at: "2026-10-05T03:40:00.000+00:00",'
               ' progress_percent: 40, terminal_error: {code: "STEAMCMD_FAILED", message: "x", retryable: false}}));'
               ' await wait(100);')
LONG47 = "Chernarus PvE Hardcore - Season 4 (weekly wipe)"
LONG100 = "Chernarus PvE Hardcore Community Server - Season 4 - weekly wipe on Fridays - EU West (Frankfurt) #1"[:100]
SKIPPED = ('window.pywebview.api.get_lifecycle_schedule = schedule({action: "restart", hour: 23, minute: 30,'
           ' next_run_local: "2026-10-05T23:30", last_status: "SKIPPED_NOT_RUNNING"});')
# D11 together with errors: running under the other profile, not responding, mods failed with every pending part,
# a paused server build check, an unreadable backup history and a skipped schedule run (QA 8.3)
D11_ERRORS = ('host.status = {...host.status, readiness: "UNRESPONSIVE", diagnostic_code: "INVENTORY_INCOMPLETE"};'
              ' host.updates.mods = {...host.updates.mods, check_state: "FAILED", error_code: "TIMEOUT", update_count: 2,'
              ' pending_apply_count: 1, not_downloaded_count: 1};'
              ' host.updates.server_build = {...host.updates.server_build, paused: true};'
              ' window.pywebview.api.list_backups = async () => fail("IO_ERROR", "unreadable");'
              ' window.pywebview.api.get_lifecycle_schedule = schedule({action: "restart", next_run_local:'
              ' "2026-10-06T04:00", last_status: "SKIPPED_NOT_RUNNING"});')
# D11 with errors while the server answers: the widest count and "Full" beside a process note (D18)
D11_ERRORS_COUNT = D11_ERRORS + ' host.status = {...host.status, readiness: "READY"};'
NAMES = 'profiles[0].display_name = {0!r}; profiles[1].display_name = {1!r};'.replace("!r", "")
SERVER_TEXTS = ["Recovery required", "More than one matching DayZ process is running.", "Steam did not answer in time."]

# name: (scene, script after the scene, results in the bar, texts that must be on the page). The first five are the
# 8.1 stress renders; the others are QA's 8.3 worst cases (QF-059).
FIT_CASES = {
    "stopped": ("stopped", "", "", ["Stopped", "No mod updates available", "DayZ server is up to date.",
                                    "Restore ready", "Set schedule"]),
    "busy_bar": ("busy", "", BAR1, ["Ready", "Players 128 / 128", "Running for 3 h 12 min", "2 mod updates available",
                                    "belongs to your Steam library", "was skipped because this server", "Backup created."]),
    "errors": ("errors", "", "", [*SERVER_TEXTS, "SteamCMD did not close after the check.",
                                  "Backup history could not be read.", "could not be queued"]),
    "busy2_bar": ("busy2", "", BAR1, ["The running server was started with Chernarus test", "Backup created."]),
    "errors_bar": ("errors", "", BAR1, [*SERVER_TEXTS, "Backup created."]),
    "errors_bar_skip": ("errors", SKIPPED, BAR1, [*SERVER_TEXTS, "was skipped because this server was not running"]),
    "errors_bar_stale_paused": ("errors", 'host.updates.server_build = {...host.updates.server_build, reason: "STALE",'
                                ' check_state: "STALE", error_code: null, paused: true};', BAR1,
                                [*SERVER_TEXTS, "the last check is too old", "Server build checks are paused"]),
    "errors_bar2": ("errors", "", BAR2, [*SERVER_TEXTS, "Backup created."]),
    "busy2_bar_name47": ("busy2", NAMES.format(f'"{LONG47}"', f'"{LONG47} B"'), BAR1,
                         [f"The running server was started with {LONG47} B", "Running for 3 h"]),
    "busy2_bar_name100": ("busy2", NAMES.format(f'"{LONG100}"', f'"{LONG100}"'), BAR1,
                          [f"The running server was started with {LONG100}"]),
    "d11_errors_bar": ("busy2", D11_ERRORS, BAR1, ["The running server was started with Chernarus test",
                                                   "Not responding", "1 not downloaded", "Server build checks are paused",
                                                   "Backup history could not be read.", "was skipped because"]),
    "d11_errors_bar2": ("busy2", D11_ERRORS, BAR2, ["The running server was started with Chernarus test",
                                                    "Not responding", "1 not downloaded", "Server build checks are paused",
                                                    "Players not known"]),
    # D18 extension: a matched server outside the manager with the widest count, under its long state label
    "external_bar2_players": ("busy", 'host.status = {...host.status, state: "RUNNING_EXTERNAL", readiness: null,'
                              ' profile_id: null, started_at: null};', BAR2,
                              ["Running outside the manager", "Players 128 / 128", "Full"]),
    "d11_errors_bar2_players": ("busy2", D11_ERRORS_COUNT, BAR2, ["The running server was started with Chernarus test",
                                                                  "Players 128 / 128", "Full", "1 not downloaded",
                                                                  "Server build checks are paused"]),
}
# QF-059, QF-081: at least 40 px (about two text lines) stay free under the content, so a font that wraps differently
# (the hosted runner) still fits; the content bottom includes the bottom padding of main
FIT_MARGIN_BOTTOM = 680


def fit_body(case: str) -> str:
    """Return a harness body that draws one case and measures the page against the viewport."""
    scene, script, bar, parts = FIT_CASES[case]
    return (SCENARIOS + f"SCENES.{scene}(); {script}\nawait start(); await wait(400);\n{bar}\n"
            f"const parts = {json.dumps(parts)}; const limit = {FIT_MARGIN_BOTTOM};\n") + r"""
check(window.innerWidth === 1150 && window.innerHeight === 720,
  `viewport ${window.innerWidth} x ${window.innerHeight}: the window frame of the harness changed`);
check(document.getElementById("overview-schedule").querySelector(".schedule-summary").textContent !== "Checking…",
  "the schedule row did not load");
// Every part of the worst case is on the page, so the measure covers it.
const text = document.body.textContent;
for (const part of parts) check(text.includes(part), `missing: ${part}`);
const over = [...document.querySelectorAll("#content-region *")].filter((node) => node.getBoundingClientRect().bottom > 720.5);
same(document.documentElement.scrollHeight <= window.innerHeight, true,
  `scrollHeight ${document.documentElement.scrollHeight}; below the fold: ${over.slice(0, 4).map((node) => node.className || node.tagName)}`);
const bottom = Math.round(document.getElementById("content-region").getBoundingClientRect().bottom
  + parseFloat(getComputedStyle(document.querySelector("main")).paddingBottom));
check(bottom <= limit, `content bottom ${bottom}: less than 40 px free under the content`);
// D18: the player count shares the state row, so the strip is as high without it; the closed panel takes no space.
const strip = document.getElementById("overview-server");
const count = [document.getElementById("overview-players-toggle"), document.getElementById("overview-players-full"),
  strip.querySelector(".overview-players-unknown")].filter(Boolean);
if (count.length) {
  const withCount = strip.getBoundingClientRect().height;
  check(document.getElementById("overview-players")?.hidden !== false, "the names panel is open");
  const shown = count.map((node) => node.hidden);
  count.forEach((node) => { node.hidden = true; });
  // An outside server showed "Players not known" before the D18 extension, so that is its baseline.
  const outside = strip.querySelector(".overview-state").textContent === "Running outside the manager";
  const baseline = Object.assign(document.createElement("p"), {className: "overview-players-unknown",
    textContent: "Players not known"});
  if (outside) strip.querySelector(".overview-state-row").append(baseline);
  const without = strip.getBoundingClientRect().height;
  baseline.remove();
  count.forEach((node, index) => { node.hidden = shown[index]; });
  // Below one pixel: the 20 px link and the 13 px text line differ by a fraction of a pixel when they wrap
  check(Math.abs(withCount - without) < 1, `the player count adds ${withCount - without} px to the server strip`);
}
"""


# Every page: the two announcers after the shell add no scroll range (QF-058)
ANNOUNCERS = r"""
await start();
const sections = window.ServerManSections.ids();
const announcers = [document.getElementById("operation-announcer"), document.getElementById("operation-alert")];
for (const section of sections) {
  commitSection(section); await wait(150);
  announcers.forEach((node) => { node.textContent = "A long announcement that the screen reader speaks."; });
  const shown = document.documentElement.scrollHeight;
  announcers.forEach((node) => { node.hidden = true; });
  const without = document.documentElement.scrollHeight;
  announcers.forEach((node) => { node.hidden = false; node.textContent = ""; });
  check(shown === without, `${section}: the announcers add ${shown - without} px of scroll`);
}
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewFitDynamicTests(unittest.TestCase):
    """D17 and QF-059: the Overview fits 1150 x 720 with a margin in every worst case; QF-058."""

    def test_overview_has_no_vertical_scroll_at_1150_by_720(self) -> None:
        """The 8.1 stress renders and QA's 8.3 worst cases: no scroll, and at least 40 px free (QF-059, QF-081)."""
        for case in FIT_CASES:
            with self.subTest(case=case):
                self.assertEqual(run_shell_harness(fit_body(case), window_size=FIT_WINDOW, budget=8000), "PASS")

    def test_announcers_add_no_scroll_on_any_page(self) -> None:
        """The visually hidden announcers stay inside the page on every section."""
        self.assertEqual(run_shell_harness(ANNOUNCERS, window_size=FIT_WINDOW, budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
