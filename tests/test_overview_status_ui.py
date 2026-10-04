"""Static contracts for the shared server state and the live Overview reconciliation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402


class OverviewStatusUiTests(unittest.TestCase):
    """Keep externally changed DayZ state synchronized without noisy redraws."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the shared state, the Overview listener and the composed offline application."""
        frontend = PROJECT_ROOT / "runnable" / "src" / "frontend"
        cls.source = (frontend / "overview_status.js").read_text(encoding="utf-8")
        cls.shared = (frontend / "server_state.js").read_text(encoding="utf-8")
        cls.readiness = (frontend / "overview_readiness.js").read_text(encoding="utf-8")
        cls.app = (frontend / "app.js").read_text(encoding="utf-8")
        cls.composed = compose_shell_html(frontend)

    def test_event_poll_reconciles_server_state_in_every_section(self) -> None:
        """The shared poll asks for live status in every section, not only on Overview."""
        self.assertIn("await window.ServerManServerState.refresh();", self.app)
        self.assertNotIn('shellState.section === "overview"', self.app)
        self.assertNotIn("ServerManOverviewStatus", self.app)
        self.assertIn("pywebview.api.get_server_status()", self.shared)
        self.assertNotIn("get_server_status", self.source)
        self.assertIn('addEventListener("serverman:server-status", followOverviewStatus)', self.source)

    def test_refresh_is_guarded_and_publishes_only_changed_state(self) -> None:
        """Concurrent and unchanged results neither read twice nor redraw a page."""
        for marker in (
            "if (!serverStateStore.reading)", "serverStateChanged(serverStateStore.status, value)",
            'new CustomEvent("serverman:server-status"',
            '"Server status could not be refreshed", "is-warning", "lifecycle"',
            'clearHostStatus("lifecycle")',
        ):
            self.assertIn(marker, self.shared)
        # Overview redraws its notice and server panel from the event, only while it is visible,
        # loaded and not reloading; the cards keep their state
        self.assertNotIn("renderOverview()", self.source)
        for marker in ("ServerManWorkspace.capture", 'shellState.section !== "overview"',
                       "!overviewState.status", "renderOverviewTop()"):
            self.assertIn(marker, self.source)
        self.assertIn(self.source, self.composed)
        self.assertIn(self.shared, self.composed)

    def test_every_shown_field_is_compared_and_states_have_distinct_presentations(self) -> None:
        """Readiness, port, start time and profile changes are published; labels stay honest."""
        for field in ("state", "readiness", "process_id", "query_port", "diagnostic_code",
                      "started_at", "profile_id"):
            self.assertIn(f'"{field}"', self.shared)
        for marker in (
            'STARTING: ["Starting"', 'READY: ["Ready"',
            'UNRESPONSIVE: [', '"Not responding"', "Steam query port",
            # Wording of the shell specification: three reworded labels, and "Stopped" in neutral grey
            'STOPPED: ["Stopped", "status-neutral"', '"Running outside the manager"',
            '"Several servers found"', '"State unknown"',
        ):
            self.assertIn(marker, self.readiness)
        for retired in ('"External process"', '"Ambiguous"', '["Unknown"'):
            self.assertNotIn(retired, self.readiness)
        self.assertIn(self.readiness, self.composed)


if __name__ == "__main__":
    unittest.main()
