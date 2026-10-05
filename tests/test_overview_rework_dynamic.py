"""Headless Edge checks of the reworked Overview server strip (variant C, D17): notices, state, uptime, controls."""
from __future__ import annotations

import unittest

try:
    from tests.overview_harness import OVERVIEW_HEAD
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD
    from ui_harness_support import EDGE, run_shell_harness


# Specification 5.1 section 4 and D17: notices, the state once, uptime only while running, process details, reasons
SERVER = OVERVIEW_HEAD + r"""
await start(); await wait(60);
// Stopped: one strip "Server" with the state once; the removed parts are gone.
const panel = () => byId("overview-server");
same(panel().querySelector("h2").textContent, "Server", "panel title");
same(region().querySelectorAll(".overview-state").length, 1, "state labels");
check(panel().querySelector(".overview-state").textContent === "Stopped"
  && panel().querySelector(".overview-state").classList.contains("status-neutral"), "stopped state");
same(region().textContent.split("Stopped").length - 1, 1, "times the state word is shown on the page");
for (const gone of ["Recent operations", "Active locations", "Managed process", "Live state", "Server at a glance",
  "Server control", "D:\\Server"]) check(!region().textContent.includes(gone), `still shown: ${gone}`);
same(byId("page-description").textContent, "Server state, updates, backups and the daily schedule.", "description");
check(!byId("overview-uptime") && !region().textContent.includes("Not running"), "uptime while stopped");
// Process details: a closed disclosure with the three values, which keeps the empty values reachable (D17).
const details = () => byId("overview-process");
check(toggle().textContent === "Process details" && toggle().getAttribute("aria-expanded") === "false"
  && toggle().getAttribute("aria-controls") === "overview-process" && details().hidden, "process disclosure");
const values = () => [...details().querySelectorAll("dt, dd")].map((node) => node.textContent).join("|");
same(values(), "Process ID|None|Steam query port|Not known|Note|No problem reported.", "process values");
toggle().click();
check(!details().hidden && toggle().getAttribute("aria-expanded") === "true", "the disclosure did not open");
toggle().click();
check(details().hidden, "the disclosure did not close");
// The four controls in reading and focus order, inside the class that the host probe reads.
same([...region().querySelectorAll(".overview-controls label, .overview-controls button")]
  .map((node) => node.textContent).join("|"), "Backup after stop|Start server|Save & Stop|Save & Restart", "control order");
check(control("Start server").classList.contains("button-primary"), "Start is not the primary action");
same(reasons(), "on|The server is not running.|The server is not running.", "reasons while stopped");
// Ready: uptime from the start time; Start is off and no longer primary.
host.status = running();
await tick();
same(byId("overview-uptime").textContent, "Running for 3 h 12 min", "uptime");
check(byId("overview-uptime").title.startsWith("Started "), "uptime tooltip");
check(!control("Start server").classList.contains("button-primary"), "a locked Start is primary");
same(reasons(), "The server is already running.|on|on", "reasons while ready");
// While running the process facts are written out in one line; there is no disclosure.
same(region().querySelector(".overview-facts").textContent, "Running for 3 h 12 min"
  + "Process ID 18244Steam query port 27016", "process facts while running");
check(!toggle() && !details(), "a disclosure while running");
// A status change redraws the notice and the strip only: the board and an open editor stay.
const card = byId("overview-updates"); const editor = byId("overview-schedule-editor");
byId("overview-schedule-toggle").click(); editor.querySelector("input").value = "17";
host.status = running({readiness: "UNRESPONSIVE", diagnostic_code: "INVENTORY_INCOMPLETE"});
await tick();
same(panel().querySelector(".overview-state").textContent, "Not responding", "state after a redraw");
// A diagnostic note is always visible, in the strip, not behind a disclosure.
same(panel().querySelector(".overview-process-note").textContent, "The running programs could not be read completely.",
  "diagnostic note");
check(byId("overview-updates") === card && editor.isConnected && !editor.hidden
  && editor.querySelector("input").value === "17", "a status change rebuilt the board");
// The uptime follows the poll tick and is written only when it changed.
byId("overview-uptime").textContent = "old";
await tick();
same(byId("overview-uptime").textContent, "Running for 3 h 12 min", "uptime after a poll tick");
// Duration wording and the cases without a known uptime.
const uptime = (status, minutes) => window.ServerManOverviewServer.uptime(
  {state: "RUNNING_MANAGED", started_at: new Date(1000000000000 - minutes * 60000).toISOString(), ...status},
  1000000000000).text;
same(uptime({}, 0.5), "Running for less than a minute", "seconds");
same(uptime({}, 5), "Running for 5 min", "minutes");
same(uptime({}, 47 * 60 + 59), "Running for 47 h 59 min", "hours");
same(uptime({}, 49 * 60), "Running for 2 d 1 h", "days");
same(uptime({started_at: null}, 5), "Uptime not known", "no start time");
same(uptime({state: "RUNNING_EXTERNAL"}, 5), "Uptime not known", "external");
// QF-057: no uptime for a server that does not run.
for (const state of ["STARTING", "STOPPING", "UNKNOWN", "AMBIGUOUS", "STOPPED"]) same(uptime({state}, 5), "", state);
// Every other state turns all three controls off with its own reason; only a running server has an uptime line.
for (const [state, reason] of [["STARTING", "The server is starting."], ["STOPPING", "The server is stopping."],
  ["RUNNING_EXTERNAL", "The server runs outside DayZ-ServerMan. Stop it there."],
  ["AMBIGUOUS", "The server state could not be confirmed."], ["UNKNOWN", "The server state could not be confirmed."]]) {
  host.status = running({state, readiness: null, profile_id: null, started_at: null});
  await tick();
  same(reasons(), [reason, reason, reason].join("|"), state);
  const external = state === "RUNNING_EXTERNAL";
  same(byId("overview-uptime")?.textContent ?? "none", external ? "Uptime not known" : "none", `${state} uptime`);
  check(Boolean(toggle()) !== external, `${state} process disclosure`);
}
// Notices, one at most: recovery with its reason, then setup with a way to Settings.
host.status = {...running(), state: "STOPPED", readiness: null, process_id: null, profile_id: null, started_at: null};
page.block = "Mutations are blocked by unresolved mod publication recovery.";
await reopen();
same(region().querySelectorAll(".notice").length, 1, "notices");
check(region().querySelector(".notice h2").textContent === "Recovery required" && region().querySelector(".notice")
  .textContent.includes("Applying mods to the server folder was interrupted"), "recovery notice");
check(region().firstElementChild.firstElementChild === region().querySelector(".notice"), "the notice is not first");
const blocked = "Changes are blocked until recovery is resolved.";
same(reasons(), [blocked, blocked, blocked].join("|"), "reasons under a recovery block");
page.block = null; page.root = null;
await reopen();
same(region().querySelector(".notice h2").textContent, "Complete application setup", "setup notice");
same(control("Start server").title, "Complete the setup in Settings first.", "setup reason");
button("Open Settings", region().querySelector(".notice")).click(); await wait(60);
same(shellState.section, "settings", "Open Settings");
// No profile: the reason names the first step.
page.root = "D:\\Server"; profiles.length = 0;
await window.ServerManProfileContext.refreshAndSelect(); commitSection("overview"); await wait(80);
same(region().querySelector(".notice h2").textContent, "Create a server profile", "profile notice");
same(control("Start server").title, "Create a server profile first.", "profile reason");
check(document.querySelector(".overview-controls"), "the host probe class is gone");
"""

# The strip and the rows by the width of the workspace: controls beside the state from 760 px on, else under it;
# in a narrow workspace each row puts its title above the state. The page never scrolls sideways.
LAYOUT = OVERVIEW_HEAD + r"""
await start(); await wait(80);
const workspace = document.querySelector(".workspace").getBoundingClientRect().width;
const box = (selector) => region().querySelector(selector).getBoundingClientRect();
const wide = workspace >= 760;
same(box(".overview-controls").top >= box(".overview-server-state").bottom - 1, !wide, "the controls wrap under the state");
const row = byId("overview-updates");
const title = row.querySelector(".overview-row-title").getBoundingClientRect();
const body = row.querySelector(".overview-row-body").getBoundingClientRect();
same(body.top >= title.bottom - 1, !wide, `the row body goes under the title at a workspace of ${workspace} px`);
check(document.documentElement.scrollWidth <= window.innerWidth && box(".overview-board").right <= window.innerWidth,
  "the page scrolls sideways");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewReworkDynamicTests(unittest.TestCase):
    """Criterion 12 and D17 for the server strip."""

    def test_notices_state_uptime_process_details_and_control_reasons(self) -> None:
        """The state once, the uptime while running, the process disclosure, the reasons, and the notices."""
        self.assertEqual(run_shell_harness(SERVER, window_size="1500,900", budget=15000), "PASS")

    def test_strip_and_rows_follow_the_workspace_width(self) -> None:
        """Controls beside or under the state; row titles beside or above the state; no sideways scroll."""
        for size in ("1500,900", "1166,900", "1100,900", "900,900", "700,900", "620,900"):
            with self.subTest(size=size):
                self.assertEqual(run_shell_harness(LAYOUT, window_size=size, budget=6000), "PASS")


if __name__ == "__main__":
    unittest.main()
