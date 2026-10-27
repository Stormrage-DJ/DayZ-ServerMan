"""Verify the family field of the load snapshot and that only Apply and Restore original consult the gate (6.11)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dayz_serverman.application.mission_map_transfer import MissionMapTransferService
from dayz_serverman.bridge.contracts import ensure_json_value
from dayz_serverman.domain.mission_map_families import family_gate
from dayz_serverman.repositories.mission_map_layout import association_folder
from tests.mission_map_inspection_support import InspectionFixture, write_mission
from tests.test_mission_map_plan import circle
from tests.test_mission_map_plan_service import profile
from tests.test_mission_map_terrain import areaflags_bytes


# Production package; the gate module may be named only where the snapshot, the refusal and the 8.6 preview
# report are built, where the 8.7 Apply lane operation calls the gate and maps its refusal, and where the 8.8
# Restore original review reports the gate and its lane operation calls it
PACKAGE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
GATE_USERS = {"domain/mission_map_families.py", "application/mission_map_coordinator.py",
              "application/mission_map_coordinator_errors.py", "application/mission_map_preview.py",
              "application/mission_map_apply.py", "application/mission_map_apply_coordinator.py",
              "application/mission_map_restore_review.py", "application/mission_map_restore.py"}


def tree(root: Path) -> dict[str, bytes]:
    """Return every file below a folder with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class FamilyGateLoadTests(InspectionFixture):
    """Missions of an unverified family, and custom missions of the two verified families."""

    def setUp(self) -> None:
        """Add valid deerisle and custom Pripyat missions and a custom chernarusplus mission with a bad header."""
        super().setUp()
        # "chernarusplus" has no dot, so it has no family (QA-6.11-F1)
        for identifier, folder in (("deerisle", "dayzOffline.deerisle"), ("custom", "serverman-test.chernarusplus"),
                                   ("planner", "plannerProbe.Pripyat"), ("nofamily", "chernarusplus")):
            mission = self.root / "mpmissions" / folder
            mission.mkdir()
            write_mission(mission)
            self.profiles.records[identifier] = profile(identifier, "mpmissions\\" + folder)
        # A size that does not match the A7 formula: the per-mission check refuses the tier layer
        (self.root / "mpmissions" / "serverman-test.chernarusplus" / "areaflags.map").write_bytes(
            areaflags_bytes(8, 8, 1000, 1000, 16, 4) + b"\0")

    def test_unverified_family_keeps_planning_saving_inspect_import_and_export(self) -> None:
        """deerisle: the snapshot gives the reason; load, save, Inspect, export and import all work."""
        game_files = tree(self.root)
        loaded = self.load("deerisle")
        ensure_json_value(loaded)
        self.assertEqual(loaded["capabilities"]["family"], family_gate("deerisle").to_dict())
        self.assertEqual(loaded["capabilities"]["family"]["reason"], "family_not_verified")
        self.assertIn(" The terrain family deerisle is not one of them. ", loaded["capabilities"]["family"]["text"])
        self.assertFalse(loaded["read_only"])
        self.assertTrue(loaded["capabilities"]["tier_writes"]["reason"] == "bounds_not_confirmed")
        # Save
        saved = self.save(self.draft("deerisle", [circle("c1", x=500.0, z=500.0, radius=600.0)]), identifier="deerisle")
        self.assertEqual(saved["revision"], 0)
        again = self.load("deerisle")
        self.assertEqual((again["state"], again["read_only"]), ("VALID", False))
        # The family comes from the mission folder, not from the plan terrain ID of the saved plan (QA-6.11-F1)
        self.assertNotEqual(again["plan"]["terrain"]["terrain_id"], "deerisle")
        self.assertEqual(again["capabilities"]["family"], family_gate("deerisle").to_dict())
        # Inspect of the saved plan
        answer = self.inspection.inspect_mission_map({"profile_id": "deerisle", "plan": None,
                                                      "target": {"point": {"x": 500.0, "z": 500.0}}})
        for _ in range(100):
            if answer["status"] != "running":
                break
            answer = self.inspection.continue_mission_map_inspection({"profile_id": "deerisle",
                                                                      "job_id": answer["job"]["id"]})
        self.assertEqual((answer["status"], answer["result"]["winner"]["id"]), ("done", "c1"))
        # Export and import
        transfer = MissionMapTransferService(self.service)
        text = transfer.export_text("deerisle")
        imported = transfer.import_plan("deerisle", text, "replace", 0)
        self.assertEqual(imported["revision"], 1)
        self.assertEqual(tree(self.root), game_files)

    def test_custom_missions_of_verified_families_keep_per_mission_checks(self) -> None:
        """A custom mission takes the family of its suffix; the A7 size rule and the bounds check still apply."""
        custom = self.load("custom")["capabilities"]
        self.assertEqual(custom["family"], {"terrain_id": "chernarusplus", "verified": True, "reason": None,
                                            "text": None, "refused": []})
        self.assertEqual(custom["tier_writes"], {"available": False, "reason": "tier_header_invalid"})
        self.assertIsNone(custom["header"])
        self.assertIn({"operation": "tier_writes", "reason": "tier_header_invalid"}, custom["unsupported"])
        planner = self.load("planner")
        self.assertEqual(planner["capabilities"]["family"]["terrain_id"], "Pripyat")
        self.assertTrue(planner["capabilities"]["family"]["verified"])
        self.assertEqual(planner["capabilities"]["tier_writes"]["reason"], "bounds_not_confirmed")

    def test_a_folder_without_a_family_before_and_after_a_save(self) -> None:
        """Folder "chernarusplus": the snapshot is the gate of no family, never unknown-<folder> (QA-6.11-F1)."""
        expected = family_gate(None).to_dict()
        self.assertEqual(expected["reason"], "terrain_family_unknown")
        first = self.load("nofamily")
        self.assertEqual(first["plan"]["terrain"]["terrain_id"], "unknown-chernarusplus")
        self.assertEqual(first["capabilities"]["family"], expected)
        self.save(self.draft("nofamily", [circle("n1")]), identifier="nofamily")
        after = self.load("nofamily")
        self.assertEqual((after["state"], after["read_only"]), ("VALID", False))
        self.assertEqual(after["capabilities"]["family"], expected)

    def test_set_aside_of_an_invalid_plan_on_an_unverified_family(self) -> None:
        """deerisle: set aside works and changes no mission file; it never consults the gate (QA-6.11-F2)."""
        self.save(self.draft("deerisle", [circle("e1")]), identifier="deerisle")
        key = self.service.active_association("deerisle")["mission_key"]
        (association_folder(self.area, "deerisle", key) / "plan.json").write_bytes(b"{bad JSON")
        loaded = self.load("deerisle")
        self.assertEqual((loaded["read_only_reason"], loaded["capabilities"]["family"]["verified"]),
                         ("invalid_saved_data", False))
        game_files = tree(self.root)
        result = self.coordinator.set_aside_mission_map_plan({"profile_id": "deerisle", "mission_key": key})
        self.assertEqual(result["association"]["mission_key"], key)
        self.assertRegex(result["set_aside"], r"\Aplan\.invalid-\d{8}T\d{6}Z\.json\Z")
        self.assertTrue((association_folder(self.area, "deerisle", key) / result["set_aside"]).is_file())
        self.assertEqual(tree(self.root), game_files)
        fresh = self.load("deerisle")
        self.assertEqual((fresh["state"], fresh["read_only"]), ("MISSING", False))
        self.assertEqual(fresh["capabilities"]["family"], family_gate("deerisle").to_dict())

    def test_the_fixture_mission_of_other_tests_is_unverified(self) -> None:
        """dayzOffline.test has the family "test", so every existing load and save test runs unverified."""
        family = self.load()["capabilities"]["family"]
        self.assertEqual((family["terrain_id"], family["verified"]), ("test", False))


class GateUsersTests(unittest.TestCase):
    """Save, import, export, copy and Inspect never consult the gate."""

    def test_only_the_snapshot_and_the_refusal_name_the_gate(self) -> None:
        """A source scan finds the gate module only in the load snapshot and the refusal translation."""
        users = set()
        for path in PACKAGE.rglob("*.py"):
            names = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
                elif isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
            if any(name.endswith("mission_map_families") for name in names):
                users.add(path.relative_to(PACKAGE).as_posix())
        self.assertEqual(users, GATE_USERS - {"domain/mission_map_families.py"})
        # The services of load, save, set aside, import, export, copy and Inspect stay free of it after task 8 too
        patterns = ("application/mission_map_plans.py", "application/mission_map_transfer.py",
                    "application/mission_map_inspection*.py", "repositories/mission_map_plans.py")
        services = {path.relative_to(PACKAGE).as_posix() for pattern in patterns for path in PACKAGE.glob(pattern)}
        self.assertGreaterEqual(len(services), 5)
        self.assertFalse(services & users)


if __name__ == "__main__":
    unittest.main()
