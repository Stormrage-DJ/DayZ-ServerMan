"""Leaf 6.5: `tweaks medical show` names an unmappable loot-zone baseline with its way out.

Real compositions over the edit fixture. The read keeps its exit code. A zone refusal that stops the
load still exits 3. A refusal that the load reads past exits 0, with a sentence after the table.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, EditRoot  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402

from dayz_serverman.application.activity_wording import ZONE_ORIGINAL_WAY_OUT  # noqa: E402

SHOW = ["tweaks", "medical", "show", *PROFILE]
# A medical group, and a stored original that lacks the second one
HOSPITAL = '<group name="Hospital_A"><usage name="Medic"/><usage name="Town"/></group>'
CLINIC = '<group name="Clinic_New"><usage name="Medic"/></group>'
REASON = ("mapgroupproto.xml differs from its stored original: group Clinic_New is not in the stored original. "
          "No file was changed.")
EDITOR = "Medical loot zones cannot change because the map editor manages this mission."


class TweaksMedicalShowTests(EditRoot):
    """The read with a stored original in the legacy folder of the fixture mission."""

    def setUp(self) -> None:
        """Add the medical files and the folder of the legacy stored original."""
        super().setUp()
        self.add_medical_files()
        (self.mission / ".dayz_manager_backups").mkdir()

    def files(self, current: str, original: str, newline: str = "\n") -> None:
        """Write the prototype file and its stored original, each a list of groups."""
        (self.mission / "mapgroupproto.xml").write_bytes(f"<prototype>{newline}{current}{newline}</prototype>"
                                                         .encode("utf-8"))
        (self.mission / ".dayz_manager_backups" / "mapgroupproto.xml").write_bytes(
            f"<prototype>\n{original}\n</prototype>".encode("utf-8"))

    def test_unmappable_original_keeps_exit_3_and_names_the_way_out(self) -> None:
        """The reason, the way out and the stored original are named; text and JSON keep exit 3."""
        self.files(HOSPITAL + CLINIC, HOSPITAL)
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*SHOW)
        stored = str(Path("mpmissions") / "dayzOffline.enoch" / ".dayz_manager_backups" / "mapgroupproto.xml")
        self.assertEqual((code, stdout), (3, ""), stderr)
        self.assertIn(f"{REASON} {ZONE_ORIGINAL_WAY_OUT} Stored original: {stored}", stderr)
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["zone_baseline"]["reason"]),
                         (3, "REVISION_CONFLICT", REASON))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_unscannable_file_keeps_exit_6_and_names_the_correction(self) -> None:
        """M03: a UTF-16 file keeps the recovery-required exit; the text adds the layout way out."""
        self.files(HOSPITAL, HOSPITAL)
        (self.mission / "mapgroupproto.xml").write_bytes("<prototype/>".encode("utf-16"))
        code, _stdout, stderr = self.cli(*SHOW)
        self.assertEqual(code, 6, stderr)
        self.assertIn("the file is not UTF-8. No file was changed. Correct this in mapgroupproto.xml, then try again.",
                      stderr)
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (6, "RECOVERY_REQUIRED"))

    def test_unknown_encoding_keeps_exit_6_and_names_the_file_and_the_cause(self) -> None:
        """QA-6.5-6: the text names the file and the cause and ends the reason with "No file was changed."."""
        self.files(HOSPITAL, HOSPITAL)
        (self.mission / "mapgroupproto.xml").write_bytes(b'<?xml version="1.0" encoding="rtf-8"?><prototype/>')
        code, _stdout, stderr = self.cli(*SHOW)
        self.assertEqual(code, 6, stderr)
        self.assertEqual(stderr.strip(), "mapgroupproto.xml has a layout that this setting cannot change safely: the "
                         "file declares an unknown encoding. No file was changed. Correct this in mapgroupproto.xml, "
                         "then try again.")

    def test_editor_managed_mission_shows_the_state_and_the_refusal(self) -> None:
        """The table keeps the read state; one sentence after it names the refusal; the read exits 0."""
        editor = '<group name="ServerManMap_G_1"><usage name="Town"/></group>'
        self.files(HOSPITAL + editor, HOSPITAL)
        code, stdout, stderr = self.cli(*SHOW)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Medical loot zones", stdout)
        self.assertTrue(stdout.rstrip().endswith(EDITOR), stdout)
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        zones = json.loads(stdout)["value"]["features"]["medical_loot_zones"]
        self.assertEqual((code, zones["enabled"], zones["zone_baseline"]["mappable"]), (0, False, False))

    def test_other_line_endings_are_information_only(self) -> None:
        """A stored original with other line endings is mappable: no sentence follows the table."""
        self.files(HOSPITAL, HOSPITAL, "\r\n")
        code, stdout, stderr = self.cli(*SHOW)
        self.assertEqual(code, 0, stderr)
        for text in ("Stored original", "cannot change", "differs"):
            self.assertNotIn(text, stdout + stderr)
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        self.assertTrue(json.loads(stdout)["value"]["features"]["medical_loot_zones"]["zone_baseline"]["mappable"])


if __name__ == "__main__":
    unittest.main()
