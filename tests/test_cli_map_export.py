"""Leaf 10.6: `map export` through the runner on real compositions (spec, help, round trip, path policy, exits)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from folder_lock_fixtures import tree_hashes  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402

from dayz_serverman.cli import help_wording, map_files, runner  # noqa: E402
from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.parser import leaf_help  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402

EXPORT = ["map", "export", "--profile", PROFILE_ID]
NO_PLAN = "There is no valid map plan to export. Run map show for the state of the plan."


class MapExportSpecTests(unittest.TestCase):
    """The spec and the help texts of `map export`."""

    def test_spec_is_an_observer_read_of_phase_10_that_asks_nothing(self) -> None:
        """READ, default profile, no question, one observer method (D11, PO-4)."""
        command = {command.name: command for command in COMMANDS}["map export"]
        self.assertEqual((command.kind, command.phase, command.profile, command.confirm, command.pending),
                         (Kind.READ, 10, ProfileRule.READ_DEFAULT, Confirm.NONE, False))
        self.assertEqual(command.bridge_methods, {"export_mission_map_plan"})
        self.assertEqual([(option.dest, option.flag, option.required) for option in command.options],
                         [("profile", "--profile", False), ("output_file", "--output", True),
                          ("overwrite_output", "--overwrite", False)])

    def test_help_texts_are_the_product_owner_texts(self) -> None:
        """The command and both destinations use the texts of evidence 10.1."""
        self.assertEqual(help_wording.COMMAND_TEXTS["map export"], "Write the saved map plan to a file.")
        self.assertEqual(help_wording.OPTION_TEXTS["output_file"], "The file to write.")
        self.assertEqual(help_wording.OPTION_TEXTS["overwrite_output"], "Replace the file when it exists.")
        page = leaf_help({command.name: command for command in COMMANDS}["map export"])
        self.assertIn("--output FILE", page)
        self.assertIn("--overwrite", page)
        self.assertNotIn("--yes", page)


class MapExportTests(MapFixture):
    """`map export` on the fixture profile, with an output folder outside the manager root and the DayZ root."""

    def setUp(self) -> None:
        """Keep a folder for the exports beside the two roots."""
        super().setUp()
        self.exports = self.manager.parent / "exports"
        self.exports.mkdir()

    def service_text(self) -> str:
        """Return the export text of the transfer service of an owner composition."""
        composition = build_composition(self.manager)
        try:
            return composition.mission_map.transfer.export_text(PROFILE_ID)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def test_round_trip_writes_the_text_unchanged_and_imports_again(self) -> None:
        """The file holds the bridge text byte for byte; the JSON value names it; an import of it gives the plan."""
        output = self.exports / "plan.json"
        code, stdout, stderr = self.cli(*EXPORT, "--output", str(output))
        self.assertEqual((code, stdout.strip()), (0, f"Map plan written to {output.resolve()}."), stderr)
        text = self.service_text()
        self.assertEqual(output.read_bytes(), text.encode("utf-8"))
        self.assertEqual(json.loads(text)["objects"][0]["id"], "observer-loot")
        self.assertEqual(list(self.exports.iterdir()), [output])
        # JSON: profile, output, size, SHA-256 and the revision of the read (D12)
        output.unlink()
        code, stdout, stderr = self.cli(*EXPORT, "--output", str(output), "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["profile_id"], value["output"], value["size"], value["revision"]),
                         (PROFILE_ID, str(output.resolve()), len(text.encode("utf-8")), 0))
        self.assertEqual(len(value["sha256"]), 64)
        composition = build_composition(self.manager)
        try:
            transfer = composition.mission_map.transfer
            imported = transfer.import_plan(PROFILE_ID, output.read_text(encoding="utf-8"), "replace", 0, True)
            self.assertEqual(imported["revision"], 1)
            self.assertEqual(transfer.export_text(PROFILE_ID), text)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def test_observer_roots_stay_unchanged(self) -> None:
        """The export output is outside the hashed trees; the manager root and the DayZ root do not change."""
        before = tree_hashes(self.manager, self.dayz)
        code, _stdout, stderr = self.cli(*EXPORT, "--output", str(self.exports / "plan.json"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)

    def test_an_existing_file_needs_overwrite_also_with_yes(self) -> None:
        """Exit 4 with the PO-4 text and the file unchanged; with --overwrite the file holds the export."""
        output = self.exports / "plan.json"
        output.write_bytes(b"old")
        for extra in ((), ("--yes",)):
            with self.subTest(extra=extra):
                code, stdout, stderr = self.cli(*EXPORT, "--output", str(output), *extra)
                self.assertEqual((code, stdout), (4, ""))
                self.assertIn(f"{output} exists already. Nothing was written. Add --overwrite to replace it.", stderr)
                self.assertEqual(output.read_bytes(), b"old")
        code, stdout, _stderr = self.cli(*EXPORT, "--output", str(output), "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (4, "CONFIRMATION_REQUIRED"))
        code, _stdout, stderr = self.cli(*EXPORT, "--output", str(output), "--overwrite")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output.read_bytes(), self.service_text().encode("utf-8"))
        self.assertEqual(list(self.exports.iterdir()), [output])

    def test_a_file_that_appears_after_the_check_is_not_replaced(self) -> None:
        """The rename fails: exit 4 with the same text, the other file stays and the staging file is gone."""
        output = self.exports / "plan.json"
        original = map_files.stage_bytes

        def stage_then_appear(*arguments):
            """Stage the export, then let another program create the target."""
            staged = original(*arguments)
            output.write_bytes(b"theirs")
            return staged

        with patch.object(map_files, "stage_bytes", stage_then_appear):
            code, _stdout, stderr = self.cli(*EXPORT, "--output", str(output))
        self.assertEqual(code, 4)
        self.assertIn(f"{output} exists already. Nothing was written. Add --overwrite to replace it.", stderr)
        self.assertEqual(output.read_bytes(), b"theirs")
        self.assertEqual(list(self.exports.iterdir()), [output])

    def test_the_output_path_refusals_exit_2(self) -> None:
        """Missing parent folder, a folder, a junction as the final path; nothing is written."""
        target = self.exports / "target"
        target.mkdir()
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(self.exports / "link"))
        cases = [(self.exports / "missing" / "plan.json", "The folder of {path} does not exist. Create the folder "
                  "first."), (self.exports, "{path} is a folder or a link. Name a file."),
                 (self.exports / "link", "{path} is a folder or a link. Name a file.")]
        before = tree_hashes(self.exports)
        for path, text in cases:
            with self.subTest(path=path):
                code, stdout, stderr = self.cli(*EXPORT, "--output", str(path))
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn(text.format(path=path), stderr)
        self.assertEqual(tree_hashes(self.exports), before)
        code, _stdout, stderr = self.cli("map", "export", "--profile", PROFILE_ID)
        self.assertEqual(code, 2)
        self.assertIn("--output", stderr)

    def test_protected_folders_exit_2_also_through_a_junction_and_another_case(self) -> None:
        """The manager root, its backup folder, the DayZ root, a junction into the DayZ root and an upper-case path."""
        paths = [self.manager / "plan.json", self.manager / "backups" / "plan.json", self.dayz / "plan.json",
                 self.dayz / "mpmissions" / "plan.json", Path(str(self.manager).upper()) / "plan.json"]
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(self.dayz / "mpmissions"), str(self.exports / "into-dayz"))
            paths.append(self.exports / "into-dayz" / "plan.json")
        before = tree_hashes(self.manager, self.dayz)
        for path in paths:
            with self.subTest(path=path):
                code, _stdout, stderr = self.cli(*EXPORT, "--output", str(path), "--overwrite")
                self.assertEqual(code, 2, stderr)
                self.assertIn(words.EXPORT_PROTECTED.format(path=path), stderr)
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)

    def test_a_custom_backup_folder_is_protected(self) -> None:
        """The backup folder of the settings, outside the manager root, refuses with exit 2 (DC-3)."""
        custom = self.manager.parent / "Custom Backups"
        custom.mkdir()
        composition = build_composition(self.manager)
        try:
            current = composition.settings.load()
            composition.settings.save(SettingsInput(
                dayz_root=str(current.dayz_root), dayz_executable=str(current.dayz_executable),
                custom_backup_root=str(custom)), current.revision)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)
        code, _stdout, stderr = self.cli(*EXPORT, "--output", str(custom / "plan.json"))
        self.assertEqual(code, 2, stderr)
        self.assertIn(words.EXPORT_PROTECTED.format(path=custom / "plan.json"), stderr)
        self.assertEqual(list(custom.iterdir()), [])

    def test_no_valid_plan_exits_3_and_writes_nothing(self) -> None:
        """A corrupt saved plan: exit 3 with the Product Owner text, no file (D13 `no_valid_plan`)."""
        self.plan_file().write_bytes(b"{")
        code, stdout, stderr = self.cli(*EXPORT, "--output", str(self.exports / "plan.json"))
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn(NO_PLAN, stderr)
        code, stdout, _stderr = self.cli(*EXPORT, "--output", str(self.exports / "plan.json"), "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]), (3, "INVALID_REQUEST", "no_valid_plan"))
        self.assertEqual(list(self.exports.iterdir()), [])

    def test_ctrl_c_during_the_call_writes_nothing(self) -> None:
        """A Ctrl+C before the write exits 5; no file and no staging file."""
        interrupts = Interrupts()
        interrupts.handle(2, None)
        code, _stdout, _stderr = self.cli(*EXPORT, "--output", str(self.exports / "plan.json"),
                                          interrupts=interrupts)
        self.assertEqual(code, 5)
        self.assertEqual(list(self.exports.iterdir()), [])

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: the typed path is an echo, --overwrite and map show are text to type, the written path is data."""
        RecordingOutput.made.clear()
        output = self.exports / "plan.json"
        with patch.object(runner, "Output", RecordingOutput):
            self.cli(*EXPORT, "--output", str(output))
            self.cli(*EXPORT, "--output", str(output))
            self.cli(*EXPORT, "--output", str(self.manager / "plan.json"))
            self.plan_file().write_bytes(b"{")
            self.cli(*EXPORT, "--output", str(self.exports / "other.json"))
        checker = InputNameChecker(self, flags() | {PROFILE_ID}, [str(output), str(self.manager / "plan.json")])
        for recorded in RecordingOutput.made:
            checker.blocks(recorded.written)


if __name__ == "__main__":
    unittest.main()
