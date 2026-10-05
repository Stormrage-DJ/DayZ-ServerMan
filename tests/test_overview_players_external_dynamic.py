"""Headless Edge checks of the D18 extension: a server outside the manager shows its count only when the status
carries one (the backend matched the selected profile), and the count changes no state, control or guard."""
from __future__ import annotations

import unittest

try:
    from tests.overview_harness import OVERVIEW_HEAD
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD
    from ui_harness_support import EDGE, run_shell_harness


EXTERNAL = OVERVIEW_HEAD + r"""
const reads = [];
window.pywebview.api.get_online_players = async () => { reads.push(1);
  return ok({state: "OK", players: [{name: "Survivor_01", duration_seconds: 120}]}); };
const outside = (extra = {}) => ({state: "RUNNING_EXTERNAL", readiness: null, process_id: 4711, diagnostic_code: null,
  query_port: null, profile_id: null, started_at: null, players: null, max_players: null, ...extra});
// Without a match: "Players not known", and the page as before D18's extension.
host.status = outside();
await start(); await wait(80);
const unknown = region().querySelector(".overview-players-unknown");
check(unknown && unknown.textContent === "Players not known" && !byId("overview-players-toggle"), "unknown");
const before = {reasons: reasons(), state: region().querySelector(".overview-state").textContent,
  sidebar: byId("sidebar-server-state").textContent, notice: Boolean(byId("overview-running-notice")),
  facts: region().querySelector(".overview-facts").textContent};
same(before.state, "Running outside the manager", "state label");
// With a match the count appears; the state, every control and its reason, the sidebar and the facts stay.
host.status = outside({players: 7, max_players: 60}); await tick();
same(byId("overview-players-toggle").textContent, "Players 7 / 60", "count");
same(region().querySelector(".overview-state").textContent, before.state, "state label with a count");
same(reasons(), before.reasons, "control reasons with a count");
same(byId("sidebar-server-state").textContent, before.sidebar, "sidebar state");
same(Boolean(byId("overview-running-notice")), before.notice, "running notice");
same(region().querySelector(".overview-facts").textContent, before.facts, "process facts");
check(["Start server", "Save & Stop", "Save & Restart"].every((label) => control(label).disabled), "a control on");
// The names panel reads through the same bridge method; the backend applies the match.
byId("overview-players-toggle").click(); await wait(60);
same(reads.length, 1, "names read");
same([...byId("overview-players").querySelectorAll(".overview-player-name")].map((node) => node.textContent).join(),
  "Survivor_01", "names");
// The match is lost (another profile selected, or the server changed): the panel closes, "Players not known".
host.status = outside(); await tick();
check(!byId("overview-players-toggle") && region().querySelector(".overview-players-unknown"),
  "count after a lost match");
same(reasons(), before.reasons, "control reasons after a lost match");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewPlayersExternalDynamicTests(unittest.TestCase):
    """D18 extension in the composed shell."""

    def test_outside_manager_count_changes_no_state_or_control(self) -> None:
        """The count of a matched outside server shows; the state, controls, sidebar and notice stay."""
        self.assertEqual(run_shell_harness(EXTERNAL, window_size="1174,812", budget=8000), "PASS")


if __name__ == "__main__":
    unittest.main()
