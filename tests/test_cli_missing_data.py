"""Task 3.2, criterion 27 (QF-21 ruling): data missing behind valid names exits 1; an unknown typed name exits 2."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import populate, tree_hashes  # noqa: E402
from test_cli_check_order import RecordingHandler  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.bridge_client import CliBridgeError  # noqa: E402
from dayz_serverman.cli.exit_codes import dispatch_exit  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402


class PopulatedRoot(unittest.TestCase):
    """A populated root per class, and the run of one case in text and JSON mode."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build one populated manager root and DayZ root; its profile has no gameplay file and no medical file."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_missing_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the roots."""
        cls.temporary.cleanup()

    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        """Run one command line; later-phase commands get a recording handler so their pre-step runs."""
        handler = RecordingHandler()
        real = runner.load_handler

        def load(spec):
            """Use the real handler of a built command, else the recording one."""
            return handler if spec.pending else real(spec)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(runner, "load_handler", load):
            code = runner.run(arguments, self.manager, stdout=stdout, stderr=stderr, interrupts=Interrupts())
        self.assertEqual(handler.contexts, [], "no handler may run after a refusal")
        return code, stdout.getvalue(), stderr.getvalue()

    def check(self, arguments: list[str], expected: int, json_code: str, sentence: str) -> None:
        """Run the case in text and JSON mode; the trees stay unchanged."""
        before = tree_hashes(self.manager, self.dayz)
        code, stdout, stderr = self.run_cli(arguments)
        self.assertEqual((code, stdout), (expected, ""), stderr)
        self.assertIn(sentence, stderr)
        code, stdout, _stderr = self.run_cli(arguments + ["--json"])
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (expected, json_code))
        self.assertIn(sentence, error["message"])
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)


class MissingDataTests(PopulatedRoot):
    """Five cases of criterion 27 against a populated root, in text and JSON; nothing changes."""

    def test_config_show_with_the_gameplay_file_absent_exits_1(self) -> None:
        """The target name is valid; the profile's file is not there."""
        self.check(["config", "show", "--target", "gameplay"], 1, "NOT_FOUND",
                   "The gameplay configuration file of profile ")

    def test_tweaks_medical_show_with_the_mission_file_absent_exits_1(self) -> None:
        """The profile resolves; its mission file is missing."""
        self.check(["tweaks", "medical", "show"], 1, "NOT_FOUND", "The mission files of profile ")

    def test_config_show_with_an_unknown_target_name_exits_2(self) -> None:
        """A target name that is not one of the known targets is a usage error."""
        self.check(["config", "show", "--target", "gameplai"], 2, "USAGE", "gameplai")

    def test_backup_restore_with_an_unknown_backup_id_exits_2(self) -> None:
        """The ID is not in the profile's backup list."""
        self.check(["backup", "restore", "--profile", "livonia-main", "no-such-backup"], 2, "USAGE",
                   "No backup of this profile has the ID no-such-backup.")

    def test_a_profile_that_matches_nothing_exits_2(self) -> None:
        """--profile names no profile."""
        self.check(["config", "show", "--target", "server", "--profile", "Nobody"], 2, "USAGE",
                   "No profile is named Nobody.")


class FeatureNameTests(PopulatedRoot):
    """A profile whose mission has the medical files: an unknown feature name that the operator typed exits 2."""

    @classmethod
    def setUpClass(cls) -> None:
        """Add the two medical files to the fixture mission."""
        super().setUpClass()
        mission = cls.dayz / "mpmissions" / "dayzOffline.enoch"
        (mission / "db" / "types.xml").write_text('<?xml version="1.0"?><types/>', encoding="utf-8")
        (mission / "mapgroupproto.xml").write_text("<groups/>", encoding="utf-8")

    def test_tweaks_medical_set_with_an_unknown_feature_exits_2(self) -> None:
        """The pre-step lists the profile's features; the typed name is not among them."""
        self.check(["tweaks", "medical", "set", "--profile", "livonia-main", "noSuchFeature", "on"], 2, "USAGE",
                   "No medical loot setting has the feature name noSuchFeature.")


class UnclassifiableFeatureTests(PopulatedRoot):
    """Criterion 27 of 2026-10-08 (QF-60 b): the typed name is checked before the medical data is loaded."""

    @classmethod
    def setUpClass(cls) -> None:
        """Give the mission a loot zone file that differs from both its original (a legacy baseline) and its
        managed form, so the medical data of the profile cannot be classified."""
        super().setUpClass()
        mission = cls.dayz / "mpmissions" / "dayzOffline.enoch"
        (mission / "db" / "types.xml").write_text('<?xml version="1.0"?><types/>', encoding="utf-8")
        (mission / "mapgroupproto.xml").write_text("<groups/>", encoding="utf-8")
        (mission / ".dayz_manager_backups").mkdir()
        (mission / ".dayz_manager_backups" / "mapgroupproto.xml").write_text(
            '<groups><group name="Hand edited"/></groups>', encoding="utf-8")

    def test_unknown_feature_exits_2_although_the_data_cannot_be_classified(self) -> None:
        """An unknown name exits 2 in text and JSON; nothing changed."""
        self.check(["tweaks", "medical", "set", "--profile", "livonia-main", "noSuchFeature", "on"], 2, "USAGE",
                   "No medical loot setting has the feature name noSuchFeature.")

    def test_a_known_feature_still_meets_the_unclassified_data(self) -> None:
        """Control: the fixture really cannot be classified; a known name keeps today's refusal (exit 3, QF-22)."""
        code, stdout, _stderr = self.run_cli(["tweaks", "medical", "set", "--profile", "livonia-main",
                                              "medical_loot_zones", "on", "--json"])
        self.assertEqual(code, 3, stdout)


class FeaturePrestepTests(unittest.TestCase):
    """The `tweaks medical set` pre-step on a feature list that exists (criterion 27, unknown typed name)."""

    def test_unknown_feature_exits_2_and_a_known_one_resolves(self) -> None:
        """An unknown feature name is a usage error; a known one is passed on."""
        from types import SimpleNamespace
        from dayz_serverman.cli.output import CliFailure, line_text
        from dayz_serverman.cli.presteps import medical_feature

        def call(method: str, **_parameters: object) -> dict:
            """Answer the feature list of a profile with a mission."""
            self.assertEqual(method, "load_medical_features")
            return {"features": {"medical_loot_zones": {"enabled": True}}}

        with self.assertRaises(CliFailure) as raised:
            medical_feature(call, SimpleNamespace(feature="noSuchFeature"), "livonia-main")
        self.assertEqual((raised.exception.exit_code, raised.exception.code), (2, "USAGE"))
        self.assertIn("noSuchFeature", line_text(raised.exception.message))
        self.assertEqual(medical_feature(call, SimpleNamespace(feature="medical_loot_zones"), "livonia-main"),
                         {"feature": "medical_loot_zones"})

    def test_not_found_maps_by_whether_the_names_were_confirmed(self) -> None:
        """At dispatch, NOT_FOUND is 2 for a name the CLI has not confirmed and 1 after it confirmed every name."""
        error = {"code": "NOT_FOUND", "message": "x"}
        self.assertEqual((dispatch_exit(error), dispatch_exit(error, names_confirmed=True)), (2, 1))
        self.assertEqual(CliBridgeError("read_profile", error).failure().exit_code, 1)
        self.assertEqual(CliBridgeError("read_profile", error, names_confirmed=False).failure().exit_code, 2)


if __name__ == "__main__":
    unittest.main()
