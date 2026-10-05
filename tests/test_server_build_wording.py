"""Catalogue entries of the server build check: phase, codes on both sides and Manager activity."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.log_activity import HIDDEN_EVENTS, format_manager_record
from dayz_serverman.application.steamcmd_guard import BUSY_TEXT, UNPROVEN_TEXT

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


def activity(event: str, fields: dict, level: str = "INFO") -> str | None:
    """Return the sentence of one record without its time and level columns."""
    line = format_manager_record({"event": event, "level": level, "fields": fields})
    return None if line is None else re.split(r"\s{2,}", line, maxsplit=2)[-1]


class WordingTests(unittest.TestCase):
    """Detailed design 14.10 and 14.11 and Architect change 1."""

    def test_both_catalogues_word_the_two_codes_alike(self) -> None:
        """STEAMCMD_BUSY and STEAMCMD_EXIT_UNPROVEN carry the host sentence on both sides."""
        messages = (FRONTEND / "operation_messages.js").read_text(encoding="utf-8")
        for code, text in (("STEAMCMD_BUSY", BUSY_TEXT), ("STEAMCMD_EXIT_UNPROVEN", UNPROVEN_TEXT)):
            with self.subTest(code=code):
                self.assertEqual(wording.ERROR_TEXTS[code], text)
                self.assertIn(f'  {code}: "{text}",', messages)

    def test_the_wait_phase_is_worded_for_both_kinds(self) -> None:
        """The checkpoint wait_steamcmd has an indeterminate row for each kind that waits."""
        labels = (FRONTEND / "operation_labels.js").read_text(encoding="utf-8")
        for kind in ("UPDATE_WORKSHOP_ITEMS", "AUTHENTICATE_STEAMCMD"):
            with self.subTest(kind=kind):
                self.assertIn(f'"{kind}/wait_steamcmd": ["Waiting for the server build check to finish", false],', labels)

    def test_manager_activity_sentences(self) -> None:
        """Finished, failed, unproven and cache failure have fixed sentences; routine events are hidden."""
        self.assertEqual(activity("server_build.check_completed", {"outcome": "OK"}), "The server build check finished.")
        self.assertEqual(activity("server_build.check_completed", {"outcome": "FAILED"}, "WARNING"),
                         "The server build check could not finish. Details are in Manager diagnostics.")
        self.assertEqual(activity("server_build.steamcmd_exit_unproven", {}, "ERROR"),
                         "SteamCMD did not close after the server build check. Close SteamCMD, then restart DayZ-ServerMan.")
        self.assertEqual(activity("server_build.cache_write_failed", {}, "WARNING"),
                         "The result of the server build check could not be saved. Details are in Manager diagnostics.")
        for event in ("server_build.check_started", "server_build.check_skipped", "server_build.installed_read",
                      "server_build.scheduler_started", "server_build.scheduler_stopped", "steamcmd.guard_waited"):
            with self.subTest(event=event):
                self.assertIn(event, HIDDEN_EVENTS)
                self.assertIsNone(activity(event, {}))
        self.assertEqual(activity("server_build.scheduler_failed", {}, "ERROR"),
                         "The automatic server build check failed. Details are in Manager diagnostics.")


if __name__ == "__main__":
    unittest.main()
