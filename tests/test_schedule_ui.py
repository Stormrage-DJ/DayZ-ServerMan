"""Composed UI tests for the overview daily schedule controls."""
from __future__ import annotations

import unittest
from pathlib import Path

from dayz_serverman.host.assets import compose_shell_html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"


class ScheduleUiTests(unittest.TestCase):
    """Composed shell contract for the overview schedule controls."""
    def test_composed_ui_has_compact_daily_schedule_controls(self) -> None:
        """The composed shell ships the compact daily schedule controls."""
        html = compose_shell_html(FRONTEND)
        # The schedule is a row: summary line, status line, the open-manager note; the editor is a row under it
        # that a toggle button opens (D17, variant C)
        self.assertIn('"Next scheduled action"', html)
        self.assertIn('"Runs at local time, only while DayZ-ServerMan is open."', html)
        self.assertIn('scheduleNode("li", "overview-editor")', html)
        self.assertIn('toggle.setAttribute("aria-controls", editor.row.id)', html)
        self.assertNotIn('scheduleNode("details", "schedule-editor")', html)
        for text in ('"Change schedule"', '"Set schedule"', '"No scheduled action"', '"Save schedule"'):
            self.assertIn(text, html)
        self.assertNotIn('"Daily schedule"', html)
        self.assertIn('"Save & Stop"', html)
        self.assertIn('"Save & Restart"', html)
        self.assertIn('input.type = "number"', html)
        self.assertIn('input.type = "checkbox"', html)
        self.assertIn('aria-live", "polite"', html)
        self.assertIn("ServerManOverviewSchedule.create(profile)", html)
        # A failed load or save keeps the page; the row names the problem (QF-056)
        self.assertIn('"The schedule could not be loaded."', html)
        self.assertIn('"The schedule could not be saved."', html)

    def test_schedule_calls_only_named_host_methods(self) -> None:
        """The schedule module calls only named host methods and avoids innerHTML."""
        script = (FRONTEND / "overview_schedule.js").read_text(encoding="utf-8")
        self.assertIn("get_lifecycle_schedule(profile.profile_id)", script)
        self.assertIn("save_lifecycle_schedule(", script)
        self.assertIn("if (selected.checked) other.checked = false", script)
        self.assertNotIn("innerHTML", script)
        # A schedule failure never replaces the whole Overview (QF-056)
        self.assertNotIn("renderHostError", script)


if __name__ == "__main__":
    unittest.main()
