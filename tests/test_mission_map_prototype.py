"""Cover the one-time migration of Pripyat prototype plan exports into the draft (R21), with synthetic exports."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from dayz_serverman.application.mission_map_transfer import ConfirmationRequired
from dayz_serverman.domain.mission_map_prototype import is_prototype_export, migrate
from dayz_serverman.domain.mission_map_values import PlanValidationError
from tests.test_mission_map_plan import circle
from tests.test_mission_map_transfer import TransferFixture


# Terrain of a Pripyat destination: the 20,480 m square of the prototype
PRIPYAT = {"terrain_id": "pripyat", "bounds": {"x_min": 0.0, "z_min": 0.0, "width": 20480.0, "height": 20480.0},
           "bounds_source": "detected", "bounds_confirmed": True}


def export(**changes: Any) -> dict[str, Any]:
    """Return an invented schema 2 export with the shape of a prototype file: brushes, polygons, points, policies."""
    zones = [
        {"id": "Z101", "tier": 4, "kind": "polygon", "points": [[100.0, 100.0], [600.0, 100.0], [600.0, 500.0]]},
        {"id": "Z102", "tier": 3, "kind": "brush", "points": [[1000.5, 2000.25]], "radius": 150},
        {"id": "Z103", "tier": 2, "kind": "brush", "points": [[3000.0, 3000.0], [3040.0, 3000.0]], "radius": 120},
    ]
    points = [{"id": "P101", "x": 4321.5, "z": 8765.25, "count": 3, "weapon": "TestRifle"},
              {"id": "P102", "x": 5000.0, "z": 6000.0, "count": 2, "weapon": "TestPistol"}]
    value = {"schemaVersion": 2, "terrain": "Pripyat", "world": 20480, "weaponPolicy": "Test weapon rule",
             "lootPolicy": "Test loot rule", "points": points, "lootOverrides": zones}
    return {**value, **changes}


class MigrationTests(unittest.TestCase):
    """Verify the exact zone mapping and the unmapped list."""

    def test_zones_map_exactly_in_reversed_order_at_normal(self) -> None:
        """Polygons keep vertices; brushes become circles per stroke point; the last zone is on top; Normal."""
        migration = migrate(export())
        objects = migration.document["objects"]
        self.assertEqual([item["id"] for item in objects], ["Z103-1", "Z103-2", "Z102", "Z101"])
        self.assertEqual([item["name"] for item in objects[:2]], ["Z103 point 1", "Z103 point 2"])
        self.assertEqual([item["tier"] for item in objects], ["Tier2", "Tier2", "Tier3", "Tier4"])
        self.assertEqual({item["abundance"] for item in objects}, {"normal"})
        self.assertEqual((objects[2]["centre"], objects[2]["radius"]), ({"x": 1000.5, "z": 2000.25}, 150.0))
        self.assertEqual([item["centre"]["x"] for item in objects[:2]], [3000.0, 3040.0])
        self.assertEqual(objects[3]["vertices"], [{"x": 100.0, "z": 100.0}, {"x": 600.0, "z": 100.0},
                                                  {"x": 600.0, "z": 500.0}])
        terrain = migration.document["terrain"]
        self.assertEqual((terrain["terrain_id"], terrain["bounds"]["width"], migration.document["global_abundance"]),
                         ("Pripyat", 20480, 0))

    def test_points_policies_and_other_fields_are_listed(self) -> None:
        """Bandit points, both policy texts and unknown fields are unmapped items, never imported."""
        unmapped = migrate(export(notes="kept aside")).unmapped
        kinds = [(item["kind"], item["id"]) for item in unmapped]
        self.assertEqual(kinds, [("bandit_point", "P101"), ("bandit_point", "P102"), ("policy", "weaponPolicy"),
                                 ("policy", "lootPolicy"), ("field", "notes")])
        self.assertIn("group of 3 at X 4321.5, Z 8765.25, weapon TestRifle", unmapped[0]["detail"])

    def test_invalid_zones_are_listed_not_repaired(self) -> None:
        """A crossing polygon, a bad tier, a repeated identifier and a broken brush are listed with reasons."""
        zones = export()["lootOverrides"] + [
            {"id": "Z104", "tier": 3, "kind": "polygon", "points": [[0, 0], [10, 10], [10, 0], [0, 10]]},
            {"id": "Z105", "tier": 5, "kind": "brush", "points": [[50, 50]], "radius": 20},
            {"id": "Z102", "tier": 1, "kind": "brush", "points": [[60, 60]], "radius": 20},
            {"id": "Z106", "tier": 1.0, "kind": "brush", "points": [[60, 60]], "radius": 20},
            {"id": "Z107", "tier": 1, "kind": "brush", "points": [], "radius": 20},
            {"id": "Z108", "tier": 1, "kind": "brush", "points": [[70, 70]], "radius": 0.5},
            "not a zone",
        ]
        migration = migrate(export(lootOverrides=zones))
        listed = [(item["id"], item["reason"]) for item in migration.unmapped if item["kind"] == "zone"]
        self.assertEqual([identifier for identifier, _reason in listed],
                         [None, "Z108", "Z107", "Z106", "Z105", "Z104", "Z102"])
        self.assertIn("crosses", dict(listed)["Z104"])
        # The later Z102 wins the identifier, so the earlier one is listed as the repeated zone
        self.assertEqual([item["id"] for item in migration.document["objects"]], ["Z102", "Z103-1", "Z103-2", "Z101"])
        self.assertEqual(sum(1 for item in migration.unmapped if item["id"] == "Z102"), 1)
        self.assertEqual(migration.document["objects"][0]["tier"], "Tier1")

    def test_schema_one_has_points_only(self) -> None:
        """A schema 1 export maps no zones; a loot list in it is listed, as the prototype ignored it."""
        migration = migrate(export(schemaVersion=1))
        self.assertEqual(migration.document["objects"], [])
        self.assertIn(("field", "lootOverrides"), [(item["kind"], item["id"]) for item in migration.unmapped])

    def test_other_files_are_refused(self) -> None:
        """Unknown schemas, a missing world size and another terrain are refused; plans are not prototypes."""
        for changes in ({"schemaVersion": 3}, {"schemaVersion": 2.0}, {"world": 0}, {"world": "20480"},
                        {"terrain": "Chernarus"}, {"points": {}}):
            with self.subTest(changes=changes), self.assertRaises(PlanValidationError):
                migrate(export(**changes))
        self.assertTrue(is_prototype_export(export()))
        self.assertFalse(is_prototype_export({"format": "x", "schemaVersion": 2}))
        self.assertFalse(is_prototype_export([export()]))


class PrototypeImportTests(TransferFixture):
    """Verify the migration through the R18 preview, Replace and Merge."""

    def make_pripyat(self) -> None:
        """Give profile main a saved Pripyat draft with one zone."""
        association = self.service.active_association("main")
        self.service.save("main", dict(self.service.load("main")["plan"], association=association,
                                       terrain=copy.deepcopy(PRIPYAT), objects=[circle("own")]), 0)

    def test_pripyat_destination_needs_no_placement_review_but_confirms_unmapped_items(self) -> None:
        """Same terrain and bounds: no review; the listed points and policies still need confirmation."""
        self.make_pripyat()
        text = json.dumps(export())
        preview = self.transfer.preview_import("main", text, "merge")
        self.assertEqual((preview["placement_review"], preview["confirmation_required"]), (False, True))
        self.assertEqual(len(preview["unmapped"]), 4)
        with self.assertRaises(ConfirmationRequired):
            self.transfer.import_plan("main", text, "merge", 1)
        saved = self.transfer.import_plan("main", text, "merge", 1, confirmed=True)
        self.assertEqual([item["id"] for item in saved["plan"]["objects"]], ["Z103-1", "Z103-2", "Z102", "Z101", "own"])
        self.assertEqual(saved["plan"]["terrain"]["terrain_id"], "pripyat")

    def test_other_destination_needs_placement_review(self) -> None:
        """The 1,000 m test terrain is not Pripyat: review is needed, and zones outside it are excluded."""
        preview = self.transfer.preview_import("main", json.dumps(export()), "replace")
        self.assertTrue(preview["placement_review"])
        self.assertEqual([item["id"] for item in preview["excluded"]], ["Z103-1", "Z103-2", "Z102"])
        saved = self.transfer.import_plan("main", json.dumps(export()), "replace", 0, confirmed=True)
        self.assertEqual([item["id"] for item in saved["plan"]["objects"]], ["Z101"])

    def test_source_file_stays_unchanged(self) -> None:
        """The import reads the file text only; the file on disk keeps its bytes."""
        self.make_pripyat()
        source = self.root.parent / "pripyat-map-plan.json"
        source.write_text(json.dumps(export()), encoding="utf-8")
        before = source.read_bytes()
        self.transfer.import_plan("main", source.read_bytes(), "replace", 1, confirmed=True)
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
