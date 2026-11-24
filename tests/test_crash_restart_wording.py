"""Crash restart wording (plan 2.4): the activity sentences of the crash restart events."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402


def activity(event: str, fields: dict, level: str = "INFO") -> str:
    """Return the activity sentence of one record without its time and level columns."""
    line = format_manager_record({"event": event, "level": level, "fields": fields,
                                  "occurred_at": "2026-10-03T10:00:00Z"})
    return line.split("  ", 2)[2].strip() if line else ""


class FixedSentenceTests(unittest.TestCase):
    """The sentences are typed by hand from the brief."""

    def test_pending(self) -> None:
        """A pending restart says that the server starts again soon."""
        self.assertEqual(
            activity("server_crash.restart_pending", {"profile_id": "livonia-main", "wait_seconds": 10.0}),
            "The server stopped unexpectedly. DayZ-ServerMan will start it again in about 10 seconds.",
        )

    def test_restarted(self) -> None:
        """A restart that worked says so."""
        self.assertEqual(
            activity("server_crash.restarted",
                     {"profile_id": "livonia-main", "operation_id": "op-1", "trigger": "crash_restart"}),
            "The server stopped unexpectedly. DayZ-ServerMan started it again.",
        )

    def test_failed_check(self) -> None:
        """A failed check says that a restart may not happen."""
        self.assertEqual(
            activity("server_crash.tick_failed", {"error": "OSError"}, "ERROR"),
            "The check for a stopped server failed, so DayZ-ServerMan may not restart it. "
            "Details are in Manager diagnostics.",
        )


FALLBACK = (
    "DayZ-ServerMan did not start the server again after it stopped unexpectedly. "
    "The server stays stopped. Details are in Manager diagnostics."
)


class GiveUpTests(unittest.TestCase):
    """Each give-up reason has its own sentence; an unknown or missing reason gets a safe one."""

    def test_each_give_up_reason_has_its_sentence(self) -> None:
        """The sentences are typed by hand from the brief."""
        rows = {
            "guard_reached": "The server stopped again after 3 automatic restarts in 10 minutes. "
                             "DayZ-ServerMan leaves it stopped.",
            "ended_before_ready": "The restarted server stopped before it was ready. "
                                  "DayZ-ServerMan does not try again. The server stays stopped.",
            "start_failed": "DayZ-ServerMan could not start the server again. The server stays stopped. "
                            "Details are in Manager diagnostics.",
        }
        for reason, expected in rows.items():
            with self.subTest(reason=reason):
                fields = {"profile_id": "livonia-main", "reason": reason}
                self.assertEqual(activity("server_crash.gave_up", fields, "WARNING"), expected)

    def test_an_unknown_or_missing_reason_gets_the_safe_sentence(self) -> None:
        """A later reason, no reason and a null reason all give the fallback."""
        for fields in ({"reason": "later_reason"}, {}, {"reason": None}):
            with self.subTest(fields=fields):
                self.assertEqual(activity("server_crash.gave_up", fields, "WARNING"), FALLBACK)

    def test_the_line_shows_the_time_and_the_warning_level(self) -> None:
        """The full line has the time, the level word and the sentence."""
        line = format_manager_record({
            "event": "server_crash.gave_up", "level": "WARNING", "fields": {"reason": "guard_reached"},
            "occurred_at": "2026-10-03T10:00:00Z",
        })
        self.assertEqual(
            line,
            "2026-10-03 10:00:00  Warning  The server stopped again after 3 automatic restarts in 10 minutes. "
            "DayZ-ServerMan leaves it stopped.",
        )

    def test_no_sentence_leaks_an_identifier(self) -> None:
        """No sentence has an identifier, a reason code or an event name."""
        records = [
            ("server_crash.restart_pending", {"profile_id": "livonia-main", "wait_seconds": 10.0}, "INFO"),
            ("server_crash.restarted",
             {"profile_id": "livonia-main", "operation_id": "op-1", "trigger": "crash_restart"}, "INFO"),
            ("server_crash.tick_failed", {"error": "OSError"}, "ERROR"),
        ] + [
            ("server_crash.gave_up", {"profile_id": "livonia-main", "reason": reason}, "WARNING")
            for reason in ("guard_reached", "ended_before_ready", "start_failed", "later_reason")
        ]
        forbidden = ("livonia-main", "op-1", "crash_restart", "guard_reached", "ended_before_ready",
                     "start_failed", "server_crash")
        for event, fields, level in records:
            text = activity(event, fields, level)
            with self.subTest(event=event, fields=fields):
                self.assertFalse(wording.leaks_identifier(text))
                for item in forbidden:
                    self.assertNotIn(item, text)


if __name__ == "__main__":
    unittest.main()
