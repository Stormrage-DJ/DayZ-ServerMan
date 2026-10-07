"""Task 2.6, design 11.1 and 11.2: field labels and the copied read texts equal the window's catalogues."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import field_wording  # noqa: E402
from dayz_serverman.cli import read_wording  # noqa: E402
from dayz_serverman.cli.commands import server  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


def source(name: str) -> str:
    """Return a frontend file with string concatenations across lines joined, so a split literal is whole."""
    text = (FRONTEND / name).read_text(encoding="utf-8")
    return re.sub(r'"\s*\n\s*\+\s*"', "", text)


class FieldLabelParityTests(unittest.TestCase):
    """Every (key, label) pair of `field_wording.py` is the pair of the frontend catalogue (11.2 bullet 3)."""

    def test_configuration_labels(self) -> None:
        """The server target equals `configuration_catalog.js`."""
        pairs = dict(re.findall(r'configurationField\("([^"]+)", "([^"]+)"', source("configuration_catalog.js")))
        self.assertEqual(field_wording.FIELD_LABELS["server"], pairs)

    def test_tweak_labels_per_target(self) -> None:
        """Gameplay, medical, weather and economy equal the groups of `tweaks_catalog.js` by their target."""
        found: dict[str, dict[str, str]] = {}
        for match in re.finditer(r'group\("[^"]+", "([a-z_]+)", \[(.*?)\]\s*(?:,\s*"[^"]*")?\)',
                                 source("tweaks_catalog.js"), re.DOTALL):
            found.setdefault(match.group(1), {}).update(re.findall(r'field\("([^"]+)", "([^"]+)"', match.group(2)))
        found.pop("starter_loadout", None)
        self.assertEqual({target: labels for target, labels in field_wording.FIELD_LABELS.items() if target != "server"},
                         found)

    def test_table_words_follow_the_window(self) -> None:
        """Column words and entry names use the window's rules; the group titles are the window's."""
        render = source("tweaks_render.js")
        self.assertIn('key.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " ")', render)
        self.assertIn("name.replace(/^Static|^Vehicle|^Animal|^Ambient/, \"\")", render)
        self.assertEqual(field_wording.readable_key("distanceRadius_max"), "distance Radius max")
        self.assertEqual(field_wording.entry_name("StaticHeliCrash"), "HeliCrash")
        catalogue = source("tweaks_catalog.js")
        for title in field_wording.EVENT_GROUP_TITLES:
            self.assertIn(f'title: "{title}"', catalogue)
        self.assertEqual(field_wording.field_label("economy", "Unknown_key"), "Unknown key")


class CopiedReadTextTests(unittest.TestCase):
    """Each text that `cli/read_wording.py` copies is the same string literal in its frontend source."""

    def assert_literals(self, name: str, texts) -> None:
        """Every text appears quoted in the named file."""
        text = source(name)
        for value in texts:
            with self.subTest(file=name, text=value):
                self.assertIn(f'"{value}"', text)

    def test_copied_tables(self) -> None:
        """Path, process, backup, mod, update, build, schedule, Steam and player texts."""
        self.assert_literals("diagnostic_labels.js", [text for pair in read_wording.PATH_STATUS_TEXTS.values()
                                                      for text in pair])
        self.assert_literals("diagnostic_labels.js", read_wording.PROCESS_DIAGNOSTIC_TEXTS.values())
        self.assert_literals("diagnostic_labels.js", read_wording.BACKUP_RESTORE_REASONS.values())
        self.assert_literals("diagnostic_labels.js", read_wording.PATH_STATUS_FALLBACK)
        self.assert_literals("mods_display.js", [*read_wording.MOD_STATE_TEXTS.values(), read_wording.MOD_STATE_FALLBACK,
                                                 read_wording.MOD_NOT_CHECKED, read_wording.VERSION_FALLBACK,
                                                 read_wording.NO_MODS, *read_wording.SCOPE_TEXTS.values()])
        self.assert_literals("update_status.js", read_wording.UPDATE_FAILURE_REASONS.values())
        self.assert_literals("server_build_status.js", [*read_wording.BUILD_FAILURE_REASONS.values(),
                                                        *read_wording.BUILD_UNKNOWN_REASONS.values(),
                                                        *read_wording.BUILD_PENDING_TEXTS.values(),
                                                        "DayZ server is up to date."])
        self.assert_literals("overview_schedule.js", [*read_wording.SCHEDULE_ACTIONS.values(), read_wording.NO_SCHEDULE,
                                                      read_wording.NO_TIMED_ACTION,
                                                      *read_wording.SCHEDULE_LAST_TEXTS.values()])
        self.assert_literals("host_sentences.js", read_wording.STEAM_MODE_TEXTS.values())
        self.assert_literals("overview_players.js", [read_wording.PLAYERS_NOT_KNOWN])
        self.assert_literals("overview_players_panel.js", [server.PLAYERS_TEXTS["EMPTY"], server.PLAYERS_TEXTS["NAMELESS"]])

    def test_summaries_follow_the_window_rules(self) -> None:
        """The mod summary and the server build summary give the window's sentences for typical states."""
        status = {"checking": False, "mods": {"check_state": "OK", "update_count": 2, "pending_apply_count": 1}}
        self.assertEqual(read_wording.mods_summary(status, True), "2 mod updates available · 1 downloaded - not applied")
        failed = {"mods": {"check_state": "FAILED", "error_code": "TIMEOUT"}}
        self.assertEqual(read_wording.mods_summary(failed, True), "Could not check: Steam did not answer in time")
        self.assertEqual(read_wording.mods_summary({"mods": {"check_state": "NEVER"}}, False),
                         "Could not check: automatic checks are off")
        self.assertEqual(read_wording.build_summary({"state": "UPDATE_AVAILABLE", "available_build": "12",
                                                     "installed_build": "11"}, True),
                         "DayZ server update available: build 12.")
        self.assertEqual(read_wording.build_summary({"state": "CHECK_FAILED", "reason": "NEVER"}, True),
                         "Could not check the DayZ server build: not checked yet.")
        self.assertEqual(read_wording.size_text(1077), "1.1 KB")
        self.assertEqual(read_wording.schedule_texts({"action": "restart", "hour": 4, "minute": 5,
                                                      "next_run_local": "2026-10-08T04:05", "last_status": "QUEUED"}),
                         ("Save & Restart daily at 04:05", "Next: 2026-10-08 04:05 local time. Last run was queued."))


if __name__ == "__main__":
    unittest.main()
