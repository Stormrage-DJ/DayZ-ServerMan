"""Static contracts for live Overview process reconciliation."""

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
        """Load the dedicated refresher and composed offline application."""
        frontend = PROJECT_ROOT / "runnable" / "src" / "frontend"
        cls.source = (frontend / "overview_status.js").read_text(encoding="utf-8")
        cls.readiness = (frontend / "overview_readiness.js").read_text(encoding="utf-8")
        cls.app = (frontend / "app.js").read_text(encoding="utf-8")
        cls.composed = compose_shell_html(frontend)

    def test_event_poll_reconciles_overview_process_state(self) -> None:
        """The shared poll asks for live status while Overview is visible."""
        self.assertIn('shellState.section === "overview"', self.app)
        self.assertIn("ServerManOverviewStatus.refresh()", self.app)
        self.assertIn("pywebview.api.get_server_status()", self.source)

    def test_refresh_is_guarded_and_renders_only_changed_state(self) -> None:
        """Concurrent, stale, and unchanged results do not replace the workspace."""
        for marker in (
            "overviewStatusRefresh.loading", "ServerManWorkspace.capture",
            "generation !== overviewState.generation", "const changed =",
            "if (changed)", "renderOverview()",
        ):
            self.assertIn(marker, self.source)
        self.assertIn(self.source, self.composed)

    def test_readiness_changes_refresh_and_have_distinct_presentations(self) -> None:
        """Steam readiness updates redraw Overview with honest state labels."""
        self.assertIn("current.readiness !== result.value.readiness", self.source)
        self.assertIn("current.query_port !== result.value.query_port", self.source)
        for marker in (
            'STARTING: ["Starting"', 'READY: ["Ready"',
            'UNRESPONSIVE: [', '"Not responding"', "Steam query port",
        ):
            self.assertIn(marker, self.readiness)
        self.assertIn(self.readiness, self.composed)


if __name__ == "__main__":
    unittest.main()
