"""Cover export, import and copy into the draft: previews, confirmation, guarded saves and no game-file writes."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dayz_serverman.application.mission_map_transfer import (
    ConfirmationRequired, MissionMapTransferService, TransferRefused,
)
from dayz_serverman.domain.mission_map_values import PlanValidationError, PlanVersionError
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_layout import association_folder
from tests.test_mission_map_plan import circle, encounter, square, stored_plan
from tests.test_mission_map_plan_service import ServiceFixture


def tree(root: Path) -> dict[str, bytes]:
    """Return every file below a folder with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class TransferFixture(ServiceFixture):
    """Saved drafts of profiles main and second on one mission, and other on another mission."""

    def setUp(self) -> None:
        """Save a plan for each profile and record the DayZ root before any transfer."""
        super().setUp()
        self.transfer = MissionMapTransferService(self.service)
        for profile_id, objects in (("main", [circle("m1"), square("m2")]), ("second", [circle("s1")]),
                                    ("other", [circle("o1")])):
            self.save(profile_id, objects)
        self.game_files = tree(self.root)

    def save(self, profile_id: str, objects: list[dict[str, Any]], **changes: Any) -> dict[str, Any]:
        """Save a first plan of the profile's active association."""
        association = self.service.active_association(profile_id)
        return self.service.save(profile_id, dict(stored_plan(objects), association=association, **changes), None)

    def text(self, objects: list[dict[str, Any]], **changes: Any) -> str:
        """Return an export-like file text of the objects on the test terrain."""
        return json.dumps(dict(stored_plan(objects), global_abundance=25, **changes))

    def tearDown(self) -> None:
        """Prove that no transfer wrote a game file, then remove the temporary folders."""
        self.assertEqual(tree(self.root), self.game_files)
        super().tearDown()


class ExportImportTests(TransferFixture):
    """Verify export, import validation, confirmation and the guarded save."""

    def test_export_round_trip_has_no_local_paths(self) -> None:
        """Export writes the indented portable form; importing it with Replace reproduces the configuration."""
        text = self.transfer.export_text("main")
        self.assertTrue(text.endswith("}\n") and '\n  "background"' in text)
        exported = json.loads(text)
        self.assertNotIn("path", exported["background"])
        self.assertFalse({"plan_id", "association", "records"} & set(exported))
        saved = self.transfer.import_plan("second", text, "replace", 0)
        self.assertEqual(saved["revision"], 1)
        source = self.service.load("main")["plan"]
        self.assertEqual(saved["plan"]["objects"], source["objects"])
        self.assertEqual(saved["plan"]["association"]["profile_id"], "second")
        self.assertEqual(saved["plan"]["background"]["path"], "C:\\maps\\map.png")

    def test_malformed_imports_change_nothing(self) -> None:
        """Bad JSON, NaN, repeated keys, a newer version, a bad prototype schema and oversized files are refused."""
        good = self.text([circle("x")])
        cases = [(PlanValidationError, "{"), (PlanValidationError, good.replace("25", "NaN", 1)),
                 (PlanValidationError, good[:-1] + ', "objects": []}'), (PlanVersionError, good.replace(
                     '"version": 1', '"version": 2')), (PlanValidationError, '{"schemaVersion": 3, "world": 20480}'),
                 (PlanValidationError, " " * (8 * 1024 * 1024 + 1)), (PlanValidationError, b"\xff{}")]
        for error, text in cases:
            with self.subTest(text=text[:40]), self.assertRaises(error):
                self.transfer.import_plan("main", text, "merge", 0, confirmed=True)
        self.assertEqual(self.service.load("main")["revision"], 0)
        self.assertRaises(ValueError, self.transfer.preview_import, "main", good, "append")

    def test_exclusion_only_after_confirmation(self) -> None:
        """Out-of-bounds objects, an encounter with its linked loot included, leave only after confirmation."""
        loot = {"enabled": True, "tier": None, "abundance": "rich", "radius": 2000.0}
        text = self.text([circle("in"), circle("out", x=5000.0), dict(encounter("enc", "ai_bandits"),
                                                                       linked_loot=loot)])
        preview = self.transfer.preview_import("main", text, "merge")
        self.assertEqual(([item["id"] for item in preview["excluded"]], preview["destination_revision"]),
                         (["out", "enc"], 0))
        with self.assertRaises(ConfirmationRequired) as caught:
            self.transfer.import_plan("main", text, "merge", 0)
        self.assertEqual(caught.exception.report["excluded_count"], 2)
        self.assertEqual(self.service.load("main")["revision"], 0)
        saved = self.transfer.import_plan("main", text, "merge", 0, confirmed=True)
        self.assertEqual([item["id"] for item in saved["plan"]["objects"]], ["in", "m1", "m2"])
        self.assertEqual([item["name"] for item in saved["report"]["excluded"]], ["Loot circle 1", "Encounter 1"])

    def test_bounds_independent_failure_refuses_the_whole_import(self) -> None:
        """One self-crossing polygon refuses everything, also the valid objects."""
        bow_tie = dict(square("bow"), vertices=[{"x": 0.0, "z": 0.0}, {"x": 10.0, "z": 10.0},
                                                 {"x": 10.0, "z": 0.0}, {"x": 0.0, "z": 10.0}])
        with self.assertRaises(PlanValidationError):
            self.transfer.import_plan("main", self.text([circle("ok"), bow_tie]), "merge", 0, confirmed=True)
        self.assertEqual(len(self.service.load("main")["plan"]["objects"]), 2)

    def test_merge_above_the_object_limit_is_refused_before_the_save(self) -> None:
        """A Merge result above 2,000 objects is refused with the counts, even when confirmed."""
        text = self.text([circle(f"i{index}") for index in range(1999)])
        self.assertIn("2001 objects", self.transfer.preview_import("main", text, "merge")["refusal"])
        self.assertRaises(TransferRefused, self.transfer.import_plan, "main", text, "merge", 0, True)
        self.assertEqual(self.service.load("main")["revision"], 0)
        self.assertEqual(self.transfer.import_plan("main", text, "replace", 0)["revision"], 1)

    def test_different_terrain_needs_placement_review(self) -> None:
        """Another terrain identity needs a confirmed review; then the objects keep their native positions."""
        text = self.text([circle("x", x=10.0)], terrain=dict(stored_plan()["terrain"], terrain_id="enoch"))
        self.assertTrue(self.transfer.preview_import("main", text, "replace")["placement_review"])
        self.assertRaises(ConfirmationRequired, self.transfer.import_plan, "main", text, "replace", 0)
        saved = self.transfer.import_plan("main", text, "replace", 0, confirmed=True)
        self.assertEqual(saved["plan"]["objects"][0]["centre"], {"x": 10.0, "z": 100.0})
        self.assertEqual(saved["plan"]["terrain"]["terrain_id"], "test.terrain")

    def test_stale_revision_and_unusable_destinations_are_refused(self) -> None:
        """A stale revision is a conflict; a destination in the path-change state or without a valid plan is refused."""
        text = self.text([circle("x")])
        self.assertRaises(RevisionConflict, self.transfer.import_plan, "main", text, "merge", 5)
        # A destination whose saved plan is invalid is refused, and its record stays as it was
        other = self.service.active_association("other")
        record = association_folder(self.area, "other", other["mission_key"]) / "plan.json"
        record.write_text("{broken", encoding="utf-8")
        self.assertRaises(TransferRefused, self.transfer.import_plan, "other", text, "merge", 0, True)
        self.assertEqual(record.read_text(encoding="utf-8"), "{broken")
        self.old_folder("old-root")
        self.assertRaises(TransferRefused, self.transfer.preview_import, "main", text, "merge")


class CopyTests(TransferFixture):
    """Verify copy from another profile through the same preview."""

    def test_same_terrain_copy_merges_and_keeps_the_source(self) -> None:
        """Copy merges on top with remapped identifiers; the source plan stays unchanged."""
        self.save_second_with_collision()
        source_before = self.service.load("second")
        preview = self.transfer.preview_copy("second", "main", "merge")
        self.assertEqual((preview["placement_review"], preview["discarded"]), (False, []))
        self.assertEqual([entry["from"] for entry in preview["remapped"]], ["M1"])
        saved = self.transfer.copy_plan("second", "main", "merge", 0)
        self.assertEqual(len(saved["plan"]["objects"]), 4)
        self.assertEqual(saved["plan"]["objects"][0]["id"], saved["report"]["remapped"][0]["to"])
        self.assertEqual(self.service.load("second"), source_before)

    def test_copy_keeps_the_background_path(self) -> None:
        """Both profiles are on this machine, so a copied image reference keeps its path."""
        association = self.service.active_association("second")
        self.service.save("second", dict(stored_plan([circle("s1")]), association=association, background=None), 0)
        saved = self.transfer.copy_plan("main", "second", "replace", 1)
        self.assertEqual(saved["plan"]["background"]["path"], "C:\\maps\\map.png")

    def test_copy_refusals(self) -> None:
        """Copy needs another profile whose active association holds a valid plan."""
        self.assertRaises(TransferRefused, self.transfer.preview_copy, "main", "main", "merge")
        self.old_folder("old-root")
        self.assertRaises(TransferRefused, self.transfer.preview_copy, "main", "second", "merge")
        # A copy across missions on the same test terrain needs no review
        self.assertFalse(self.transfer.preview_copy("other", "second", "replace")["placement_review"])

    def save_second_with_collision(self) -> None:
        """Give profile second two objects, one whose identifier matches main's without regard to case."""
        association = self.service.active_association("second")
        self.service.save("second", dict(stored_plan([circle("M1"), circle("s2")]), association=association), 0)


if __name__ == "__main__":
    unittest.main()
