"""Headless Edge checks of the "DayZ server" row of the Overview status board (14.9, 14.10, D17)."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

# Fake answers: the server build object of `get_update_status`, and the recorded check requests
HEAD = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const requests = [];
const statusCalls = [];
const build = (extra = {}) => ({state: "CURRENT", reason: null, ownership: "STEAM_CLIENT", installed_build: 24570360,
  installed_branch: "public", target_build: null, available_build: 24570360, available_time: 1786528820,
  check_state: "OK", checked_at: "2026-10-04T20:14:39.000+00:00", last_success_at: "2026-10-04T20:14:39.000+00:00",
  error_code: null, checking: false, waiting: false, revision: 1, paused: false, ...extra});
const updates = (server) => ({mods: {check_state: "OK", checked_at: null, last_success_at: "2026-10-04T08:41:07.120Z",
  error_code: null, update_count: 0, pending_apply_count: 0}, server_build: server, checking: false, revision: 1});
host.updates = updates(build());
Object.assign(window.pywebview.api, {
  get_update_status: async (profileId) => { statusCalls.push(profileId);
    return ok(profileId === null ? {...host.updates, mods: null} : host.updates); },
  request_update_check: async (scope, force) => { requests.push([scope, force]);
    return ok({accepted: true, checking: false, server_build: {accepted: true, checking: false, waiting: false}}); },
});
const part = () => byId("overview-build");
const line = (name) => part().querySelector(`.overview-build-${name}`);
const land = async (server, revision) => { host.updates = updates(server(revision)); updateStatusState.readAt -= 31000;
  await window.ServerManUpdateStatus.poll(false); await wait(10); };
const shown = (name) => line(name).hidden ? "" : line(name).textContent;
// A failed check shows a short head and the reason under it (D17); other states show one line.
const lead = () => line("summary").textContent + (shown("reason") ? `|${shown("reason")}` : "");
const split = (text) => { const prefix = "Could not check the DayZ server build: ";
  return text.startsWith(prefix) ? `Could not check|${text[prefix.length].toUpperCase()}${text.slice(prefix.length + 1)}`
    : text; };
"""

STATES = HEAD + r"""
await start(); await wait(60);
check(!part().hidden && part().querySelector("h3").textContent === "DayZ server"
  && part().tagName === "LI", "server row");
same(line("summary").textContent, "DayZ server is up to date.", "current");
check(line("summary").classList.contains("status-normal"), "current tone");
check(line("detail").textContent.startsWith("Installed: build 24570360 · Steam: build 24570360, on Steam since ")
  && line("detail").textContent.includes(" · Last checked "), line("detail").textContent);
same(shown("advice") + shown("busy"), "", "no advice or busy line when current");
let revision = 2;
const cases = [
  [{state: "UPDATE_AVAILABLE", available_build: 24600000}, "DayZ server update available: build 24600000.", "warning",
    "This DayZ server folder belongs to your Steam library. Stop the server, then update “DayZ Server” through Steam."],
  [{state: "UPDATE_AVAILABLE", available_build: 1, ownership: "STEAMCMD"}, "Steam lists a different DayZ server build: 1.", "warning",
    "Stop the server, then update it with SteamCMD. DayZ-ServerMan does not update the server build yet."],
  [{state: "UPDATE_PENDING", reason: "TARGET_BUILD", target_build: 24600000, ownership: "UNKNOWN"},
    "Steam has a DayZ server update queued: build 24600000.", "warning",
    "Finish the update with the program that installed the server. Start the server after it has finished."],
  [{state: "UPDATE_PENDING", reason: "UPDATE_RUNNING"}, "Steam is updating the DayZ server.", "warning",
    "Let Steam finish the update. Start the server after it has finished."],
  [{state: "COULD_NOT_CHECK", reason: "FAILED", check_state: "FAILED", error_code: "TIMEOUT"},
    "Could not check the DayZ server build: SteamCMD did not answer in time.", "error", ""],
  [{state: "COULD_NOT_CHECK", reason: "FAILED", check_state: "FAILED", error_code: "STEAMCMD_EXIT_UNPROVEN"},
    "Could not check the DayZ server build: SteamCMD did not close after the check. Close SteamCMD, then restart DayZ-ServerMan.", "error", ""],
  [{state: "COULD_NOT_CHECK", reason: "FAILED", check_state: "FAILED", error_code: "LATER_CODE"},
    "Could not check the DayZ server build: the check failed.", "error", ""],
  [{state: "COULD_NOT_CHECK", reason: "STALE", check_state: "STALE"},
    "Could not check the DayZ server build: the last check is too old.", "neutral", ""],
  [{state: "COULD_NOT_CHECK", reason: "BRANCH_NOT_LISTED", installed_branch: "beta_x", available_build: null},
    "Could not check the DayZ server build: Steam shows the branch “beta_x” only after a sign-in.", "neutral", ""],
  [{state: "COULD_NOT_CHECK", reason: "STEAMCMD_NOT_CONFIGURED", check_state: "NEVER", last_success_at: null, available_build: null},
    "Could not check the DayZ server build: SteamCMD is not set up. Set its folder in Settings.", "neutral", ""],
  [{state: "UNKNOWN_INSTALLATION", reason: "NO_MANIFEST", installed_build: null, installed_branch: null, ownership: "UNKNOWN"},
    "The installed DayZ server build is not known: Steam has no installation record for this folder.", "neutral", ""],
];
for (const [extra, summary, tone, advice] of cases) {
  await land((value) => build({...extra, revision: value}), revision += 1);
  same(lead(), split(summary), "summary");
  same(window.ServerManServerBuild.lines(build(extra)).text, summary, "one-line text");
  check(line("summary").classList.contains(`status-${tone}`), `${summary} tone`);
  same(shown("advice"), advice, `${summary} advice`);
  for (const raw of ["UPDATE_", "STEAM_CLIENT", "STEAMCMD_", "TIMEOUT", "NO_MANIFEST", "LATER_CODE"]) {
    check(!part().textContent.includes(raw), `raw ${raw} in ${part().textContent}`);
  }
}
check(!line("detail").textContent.includes("Installed:"), "an unknown installation names no installed build");
await land((value) => build({installed_branch: "beta_x", revision: value}), revision += 1);
check(line("detail").textContent.startsWith("Installed: build 24570360 (branch beta_x)"), line("detail").textContent);
// The switch off names itself as the reason of an old or missing check.
window.ServerManUpdateStatus.setAutomaticChecks(false);
await land((value) => build({state: "COULD_NOT_CHECK", reason: "NEVER", check_state: "NEVER", last_success_at: null,
  revision: value}), revision += 1);
same(lead(), "Could not check|Automatic checks are off.", "switch off");
check(line("detail").textContent.endsWith("Not checked yet"), "never checked");
window.ServerManUpdateStatus.setAutomaticChecks(true);
// Busy and waiting lines; a running build check does not lock "Check now".
await land((value) => build({checking: true, revision: value}), revision += 1);
same(shown("busy"), "Checking the DayZ server build…", "checking line");
check(!byId("overview-check-now").disabled, "a build check locked Check now");
await land((value) => build({waiting: true, revision: value}), revision += 1);
same(shown("busy"), "The server build check waits until the current task has finished.", "waiting line");
// A poisoned guard: the reason with its action replaces the waiting line (QF-050).
const paused = "Server build checks are paused: an earlier SteamCMD run did not end cleanly. Restart DayZ-ServerMan to check again.";
await land((value) => build({paused: true, revision: value}), revision += 1);
same(shown("busy"), paused, "paused line");
await land((value) => build({paused: true, state: "COULD_NOT_CHECK", reason: "FAILED", check_state: "FAILED",
  error_code: "STEAMCMD_EXIT_UNPROVEN", revision: value}), revision += 1);
same(shown("busy"), "", "line 1 already names the unproven exit");
// "Check now" checks both; the Mods badge does not count the server build.
await land((value) => build({state: "UPDATE_AVAILABLE", available_build: 24600000, revision: value}), revision += 1);
same(window.ServerManUpdateStatus.badge().form, "none", "badge with a server update");
byId("overview-check-now").click(); await wait(40);
same(JSON.stringify(requests.at(-1)), JSON.stringify(["all", true]), "Check now scope");
// An older host without the part hides it.
await land(() => null, 0);
check(part().hidden, "part without a server build answer");
"""

NO_PROFILE = HEAD + r"""
profiles.length = 0;
await start(); await wait(80);
check(statusCalls.includes(null), `status read without a profile: ${JSON.stringify(statusCalls)}`);
same(byId("overview-updates").querySelector(".overview-updates-summary").textContent, "No server profile", "mods line");
check(!part().hidden && line("summary").textContent === "DayZ server is up to date.", "server part without a profile");
check(!byId("overview-check-now").disabled, "Check now without a profile");
byId("overview-check-now").click(); await wait(40);
same(JSON.stringify(requests.at(-1)), JSON.stringify(["server_build", true]), "scope without a profile");
"""

@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is not installed")
class ServerBuildCardTests(unittest.TestCase):
    """Wording per state, reason and class; scopes of "Check now"; the badge stays a mod count.

    The Mods header keeps scope "mods": `test_mods_update_status_dynamic.py` asserts it.
    """

    def test_states_and_wording(self) -> None:
        """Every state of 14.7 with its line 1, tone, guidance and no raw identifier."""
        self.assertEqual(run_shell_harness(STATES, window_size="1500,900", budget=12000), "PASS")

    def test_without_a_profile(self) -> None:
        """The server part shows and "Check now" checks the server build only."""
        self.assertEqual(run_shell_harness(NO_PROFILE, window_size="1500,900", budget=8000), "PASS")


if __name__ == "__main__":
    unittest.main()
