"""Verify editor load/save results, profile isolation and preservation of invalid or sensitive data."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator, unknown_terrain_id
from dayz_serverman.bridge.contracts import ErrorCode, ensure_json_value
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.mission_map_plan import PlanForm, canonical_json, parse_plan
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.test_mission_map_plan import circle
from tests.test_mission_map_plan_service import ServiceFixture, profile
from tests.test_mission_map_terrain import areaflags_bytes
from tests.test_mission_map_terrain_files import limits_text


class CoordinatorFixture(ServiceFixture):
    """Reuse the temporary profile/mission fixture and the real backend plan service."""

    def setUp(self) -> None:
        """Build a coordinator over synthetic mission files and temporary manager records."""
        super().setUp()
        self.coordinator = MissionMapCoordinator(self.service)
        self.mission = self.root / "mpmissions" / "dayzOffline.test"

    def add_header(self, mission: Path | None = None) -> None:
        """Provide a small valid header, value layer and flag list for bounds discovery."""
        destination = mission or self.mission
        (destination / "areaflags.map").write_bytes(areaflags_bytes(8, 8, 1000, 1000, 16, 4))
        (destination / "cfglimitsdefinition.xml").write_text(limits_text("Tier1", "Tier2"), encoding="utf-8")

    def draft(self, identifier: str = "main", objects: list | None = None) -> dict[str, Any]:
        """Return a validated draft without a caller-provided background path."""
        plan = self.plan_for(self.service.active_association(identifier), objects or [])
        plan["background"] = None
        return plan

    def save(self, plan: object, expected: object = None, identifier: str = "main",
             selection: object = None) -> dict[str, Any]:
        """Call the complete save handler request directly; handlers are not registered yet."""
        return self.coordinator.save_mission_map_plan({"profile_id": identifier, "plan": plan,
                    "expected_revision": expected, "background_selection_id": selection})

    def load(self, identifier: str = "main") -> dict[str, Any]:
        """Load a single profile through the coordinator."""
        return self.coordinator.load_mission_map({"profile_id": identifier})

    def refused(self, action: Any, code: ErrorCode, reason: str) -> ApplicationCallError:
        """Require the accepted code, reason and a JSON-safe details object."""
        with self.assertRaises(ApplicationCallError) as caught:
            action()
        error = caught.exception
        self.assertEqual((error.code, error.details["reason"]), (code, reason))
        ensure_json_value(dict(error.details))
        return error


class EditorLoadSaveTests(CoordinatorFixture):
    """Cover the real plan store boundary and editor result shape."""

    def test_missing_plan_load_creates_no_folder_and_never_guesses_bounds(self) -> None:
        """A missing header gives a null plan; detected bounds give a neutral unsaved plan."""
        missing = self.load()
        self.assertEqual((missing["state"], missing["revision"], missing["plan"]), ("MISSING", None, None))
        self.assertFalse(missing["draft_changes"])
        self.assertFalse(self.area.exists())
        # Discovery supplies a new document, but never confirms its bounds or saves it
        self.add_header()
        detected = self.load()
        ensure_json_value(detected)
        self.assertEqual(detected["profile"], {"profile_id": "main", "display_name": "Main"})
        self.assertEqual(detected["mission_name"], "dayzOffline.test")
        self.assertEqual(detected["plan"], parse_plan(detected["plan"], PlanForm.STORED))
        self.assertFalse(detected["plan"]["terrain"]["bounds_confirmed"])
        self.assertEqual(detected["capabilities"]["tier_writes"]["reason"], "bounds_not_confirmed")
        self.assertNotEqual(detected["plan"]["plan_id"], self.load()["plan"]["plan_id"])
        self.assertFalse(self.area.exists())

    def test_bounds_source_and_confirmation_round_trip_without_game_writes(self) -> None:
        """Actual detected and manual bounds keep their source, confirmation and full precision."""
        self.add_header()
        game = {path.relative_to(self.mission): path.read_bytes() for path in self.mission.rglob("*") if path.is_file()}
        plan = self.load()["plan"]
        revision = None
        for source, confirmed in (("detected", False), ("detected", True), ("manual", False), ("manual", True)):
            with self.subTest(source=source, confirmed=confirmed):
                if source == "manual":
                    plan["terrain"]["bounds"] = {"x_min": -123.123456789, "z_min": 87.987654321,
                                                "width": 777.777777777, "height": 999.999999999}
                plan["terrain"].update(bounds_source=source, bounds_confirmed=confirmed)
                revision = self.save(plan, revision)["revision"]
                loaded = self.load()["plan"]
                self.assertEqual(loaded["terrain"], plan["terrain"])
                self.assertEqual(loaded["plan_id"], plan["plan_id"])
        self.assertEqual({path.relative_to(self.mission): path.read_bytes()
                          for path in self.mission.rglob("*") if path.is_file()}, game)

    def test_manual_subnormal_bounds_stay_admitted_and_exact(self) -> None:
        """A local failed transform cannot reject the D1-positive minimum binary64 extent."""
        self.add_header()
        before = (self.mission / "areaflags.map").read_bytes()
        plan = self.draft()
        plan["terrain"].update(bounds_source="manual", bounds_confirmed=True,
            bounds={"x_min": 1000000, "z_min": 0, "width": 5e-324, "height": 1000})
        saved = self.save(plan)
        loaded = self.load()
        self.assertEqual((saved["revision"], loaded["revision"]), (0, 0))
        self.assertEqual(loaded["plan"]["terrain"], plan["terrain"])
        self.assertFalse(loaded["read_only"])
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), before)

    def test_changed_bounds_cannot_delete_or_repair_invalidated_objects(self) -> None:
        """Existing D1 refuses a changed rectangle without altering the saved plan or placements."""
        plan = self.draft(objects=[circle("retained", x=100.123456789, z=200.987654321)])
        self.save(plan)
        folder = association_folder(self.area, "main", plan["association"]["mission_key"])
        before = (folder / "plan.json").read_bytes()
        changed = copy.deepcopy(plan)
        changed["terrain"]["bounds"]["width"] = 50
        error = self.refused(lambda: self.save(changed, 0), ErrorCode.INVALID_REQUEST, "invalid_document")
        self.assertEqual(error.details["problems"][0]["object_id"], "retained")
        self.assertEqual((folder / "plan.json").read_bytes(), before)
        self.assertEqual(self.load()["plan"]["objects"], plan["objects"])

    def test_profiles_on_one_mission_have_separate_plans_and_revisions(self) -> None:
        """One profile cannot load or overwrite the other profile's draft."""
        main, second = self.draft(objects=[circle("a")]), self.draft("second", [circle("b")])
        first = self.save(main)
        self.assertEqual(self.save(second, identifier="second")["revision"], 0)
        self.assertEqual(self.save(dict(main, global_abundance=25), 0)["revision"], 1)
        self.assertEqual(self.load("second")["plan"]["objects"][0]["id"], "b")
        self.assertEqual(self.load()["plan"]["objects"][0]["id"], "a")
        self.assertEqual(self.load("second")["revision"], 0)
        self.assertNotIn("plan", first)
        self.assertTrue(first["draft_changes"])
        self.refused(lambda: self.save(main, 0, "second"), ErrorCode.PROFILE_CONTEXT_MISMATCH, "association_mismatch")

    def test_stale_revision_is_retryable_and_keeps_saved_data(self) -> None:
        """A stale revision refuses without replacing the latest draft."""
        plan = self.draft()
        self.save(plan)
        self.save(dict(plan, global_abundance=25), 0)
        error = self.refused(lambda: self.save(plan, 0), ErrorCode.REVISION_CONFLICT, "stale_revision")
        self.assertTrue(error.retryable)
        self.assertEqual(self.load()["plan"]["global_abundance"], 25)
        for invalid in (True, -1, "0"):
            with self.subTest(revision=invalid):
                self.refused(lambda: self.save(plan, invalid), ErrorCode.INVALID_REQUEST, "invalid_document")

    def test_invalid_saved_data_is_read_only_and_never_overwritten(self) -> None:
        """Corrupt, newer and invalid plans remain byte-identical after refused saves."""
        plan = self.draft()
        folder = association_folder(self.area, "main", plan["association"]["mission_key"])
        folder.mkdir(parents=True)
        valid = copy.deepcopy(plan)
        valid["version"] = 2
        samples = [(b"{bad JSON", "CORRUPT"),
                   (canonical_json({"schema_version": 2, "revision": 3, "plan": plan}), "FUTURE_SCHEMA"),
                   (canonical_json({"schema_version": 1, "revision": 3, "plan": valid}), "NEWER_PLAN"),
                   (canonical_json({"schema_version": 1, "revision": 3, "plan": {"format": 1}}), "INVALID_PLAN")]
        for data, state in samples:
            with self.subTest(state=state):
                (folder / "plan.json").write_bytes(data)
                loaded = self.load()
                self.assertEqual((loaded["state"], loaded["read_only_reason"]), (state, "invalid_saved_data"))
                self.assertTrue(loaded["read_only"])
                self.assertIsNone(loaded["plan"])
                error = self.refused(lambda: self.save(plan, 3), ErrorCode.REVISION_CONFLICT, "invalid_saved_data")
                self.assertFalse(error.retryable)
                self.assertEqual((folder / "plan.json").read_bytes(), data)

    def test_background_paths_stay_backend_owned_and_matching_path_is_preserved(self) -> None:
        """Load hides a stored image path; save cannot inject one and preserves a matching native reference."""
        plan = self.plan_for(self.service.active_association("main"), [])
        self.service.save("main", plan, None)
        path = association_folder(self.area, "main", plan["association"]["mission_key"]) / "plan.json"
        before = path.read_bytes()
        error = self.refused(lambda: self.save(plan, 0), ErrorCode.INVALID_REQUEST, "invalid_document")
        self.assertEqual(error.details["problems"][0]["rule"], "background_path_not_accepted")
        self.assertEqual(path.read_bytes(), before)
        # Browser saves can keep the known digest, but never send its private path
        loaded = self.load()["plan"]
        self.assertIsNone(loaded["background"]["path"])
        self.save(loaded, 0)
        self.assertEqual(self.service.load("main")["plan"]["background"]["path"], plan["background"]["path"])
        loaded["background"]["sha256"] = "c" * 64
        self.save(loaded, 1)
        self.assertIsNone(self.service.load("main")["plan"]["background"]["path"])
        self.refused(lambda: self.save(loaded, 2, selection="unregistered"), ErrorCode.INVALID_REQUEST, "selection_unknown")

    def test_invalid_objects_report_their_ids_without_writing(self) -> None:
        """D1 validation marks the failing object and leaves the manager area absent."""
        plan = self.draft(objects=[circle("bad", x=-1)])
        error = self.refused(lambda: self.save(plan), ErrorCode.INVALID_REQUEST, "invalid_document")
        self.assertEqual(error.details["problems"][0]["object_id"], "bad")
        self.assertFalse(self.area.exists())

    def test_unknown_terrain_identity_is_sanitized_and_bounded(self) -> None:
        """Unknown folders get only D1 characters and at most 64 characters in the generated ID."""
        name = "Mystery terrain ! ü"
        mission = self.root / "mpmissions" / name
        mission.mkdir()
        self.profiles.records["mystery"] = profile("mystery", "mpmissions\\" + name)
        self.add_header(mission)
        loaded = self.load("mystery")
        self.assertIsNone(loaded["capabilities"]["terrain_id"])
        self.assertEqual(loaded["plan"]["terrain"]["terrain_id"], "unknown-Mysteryterrain")
        for folder, expected in (("! ü", "unknown"), ("x" * 57, "unknown-" + "x" * 56),
                                 ("x" * 56, "unknown-" + "x" * 56), ("A._-1", "unknown-A._-1")):
            with self.subTest(folder=folder):
                self.assertEqual(unknown_terrain_id(folder), expected)

    def test_missing_profile_and_unresolved_mission_have_closed_reasons(self) -> None:
        """Profile and mission resolution failures use the accepted NOT_FOUND reasons."""
        with mock.patch.object(self.profiles, "read", side_effect=ProfileNotFound("gone")):
            self.refused(self.load, ErrorCode.NOT_FOUND, "profile_not_found")
        self.profiles.records["main"] = profile("main", "mpmissions\\gone")
        self.refused(self.load, ErrorCode.NOT_FOUND, "mission_unresolved")


if __name__ == "__main__":
    unittest.main()
