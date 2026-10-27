"""The last-applied record version 2 of leaf 8.7: shape rules, and version 1 records that stay readable."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dayz_serverman.domain.mission_map_applied import (
    APPLIED_VERSION, check_realization, content_items, generated_name, record_version, RealizationError,
)
from dayz_serverman.domain.mission_map_records import RecordShapeError, parse_applied, parse_baseline, sealed
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, RecordCorrupt
from tests.test_mission_map_plan import circle
from tests.test_mission_map_records import MISSION_KEY, OUTPUT, VANILLA, applied, baseline, sha

# The key of one generated group: version, role, source, containers and ceilings (D8)
KEY = [1, "native_capacity", ["mapgroupproto.xml", "Land_Shed"], ["a", "b"], "5", ["2", "2"]]


def group(**changes: Any) -> dict[str, Any]:
    """Return one valid generated group with one detached placement."""
    item = {"name": generated_name(KEY), "key": copy.deepcopy(KEY), "source": ["mapgroupproto.xml", "Land_Shed"],
            "containers": ["a", "b"], "group_ceiling": "5", "container_ceilings": ["2", "2"],
            "detached": [{"file": "mapgrouppos.xml", "name": "Land_Shed", "position": [510.25, 3.0, 495.5],
                          "rotation": [0.0, 0.0, 12.5], "angle": 77.5, "multiplicity": 1}]}
    item.update(changes)
    return item


def version_two(kind: str = "apply", **changes: Any) -> dict[str, Any]:
    """Return an unsealed version 2 record of an apply, or of a restore with null plan fields."""
    record = applied(kind=kind)
    plan_fields = (25, [circle("a")]) if kind == "apply" else (None, None)
    record.update(global_abundance=plan_fields[0], plan_objects=plan_fields[1], generated_groups=[group()],
                  generated_groups_before=None)
    record.update(changes)
    return record


class AppliedShapeTests(unittest.TestCase):
    """Version 2 adds four fields with strict shapes; version 1 keeps its exact field set."""

    def test_version_follows_the_fields(self) -> None:
        """A record with the realization fields is version 2; any other record is version 1."""
        self.assertEqual((record_version(applied()), record_version(version_two()), record_version("x")),
                         (1, APPLIED_VERSION, 1))
        self.assertEqual(parse_applied(sealed(version_two()))["generated_groups"][0]["name"], generated_name(KEY))
        self.assertEqual(parse_applied(sealed(version_two("restore_previous")))["plan_objects"], None)
        parse_applied(sealed(applied()))
        # Null means unknown: the groups of a restore and the groups before any commit may be null
        parse_applied(sealed(version_two("restore_previous", generated_groups=None, generated_groups_before=None)))
        parse_applied(sealed(version_two(generated_groups_before=[group()])))
        parse_applied(sealed(version_two(generated_groups_before=[])))

    def test_partial_or_invalid_realization_fields_are_refused(self) -> None:
        """One missing field, an apply without its plan, a restore with a plan and bad groups are refused."""
        partial, before = version_two(), version_two()
        del partial["plan_objects"]
        del before["generated_groups_before"]
        bad = [partial, before, version_two(generated_groups=None),
               version_two(generated_groups_before=[group(group_ceiling="05")]),
               version_two(generated_groups_before={}), version_two(global_abundance=30), version_two(global_abundance=None),
               version_two(global_abundance=True), version_two(plan_objects=[circle("a"), circle("a")]),
               version_two(plan_objects={"a": 1}), version_two("restore_original", global_abundance=0),
               version_two(generated_groups=[group(), group()]), version_two(generated_groups={}),
               version_two(generated_groups=[group(name="ServerManMap_G_000000000000")]),
               version_two(generated_groups=[group(group_ceiling="05")]),
               version_two(generated_groups=[group(containers=["a"])]),
               version_two(generated_groups=[group(extra=1)]),
               version_two(generated_groups=[group(detached=[dict(group()["detached"][0], multiplicity=0)])]),
               version_two(generated_groups=[group(detached=[dict(group()["detached"][0], file="mapgroupdirt.xml")])]),
               version_two(generated_groups=[group(detached=[dict(group()["detached"][0], position=[1, 2])])])]
        for record in bad:
            with self.subTest(record={name: record.get(name) for name in ("global_abundance", "operation_kind")}):
                self.assertRaises(RecordShapeError, parse_applied, sealed(record))
        self.assertRaises(RealizationError, check_realization, {"global_abundance": 0, "plan_objects": [],
                                                                "generated_groups": [1],
                                                                "generated_groups_before": None}, apply=True)
        infinite = version_two(generated_groups=[group(detached=[dict(group()["detached"][0], angle=float("inf"))])])
        self.assertRaises(RealizationError, check_realization, infinite, apply=True)


class BaselineBackupShapeTests(unittest.TestCase):
    """A `replaced` provenance entry lists stored file items and makes the baseline version 2 (Architect 01:18:47)."""

    def backup(self, **changes: Any) -> dict[str, Any]:
        """Return a baseline with one `replaced` entry that keeps an external version of the prototype."""
        record = baseline()
        entry = {"time": "2026-10-15T08:00:00Z", "source": "replaced", "operation_id": "op-3",
                 "paths": ["mapgroupproto.xml"],
                 "files": [{"target_key": MISSION_KEY, "path": "mapgroupproto.xml", "existed": True,
                            "sha256": sha(OUTPUT), "size": len(OUTPUT)}]}
        entry.update(changes)
        record["provenance"].append(entry)
        return record

    def test_shape_version_and_content_items(self) -> None:
        """The entry parses, the record is version 2 and its backup is a content item; other shapes refuse."""
        record = parse_baseline(sealed(self.backup()))
        self.assertEqual((record_version(record), record_version(baseline())), (APPLIED_VERSION, 1))
        self.assertIn(sha(OUTPUT), [item["sha256"] for item in content_items("baseline", record)])
        self.assertEqual(content_items("ledger", record), [])
        adoption = baseline()
        adoption["provenance"][0]["files"] = []
        missing = self.backup()
        del missing["provenance"][-1]["files"]
        for bad in (adoption, missing, self.backup(paths=["mapgrouppos.xml"]),
                    self.backup(files=[{"target_key": "f" * 32, "path": "mapgroupproto.xml", "existed": True,
                                        "sha256": sha(OUTPUT), "size": 1}])):
            self.assertRaises(RecordShapeError, parse_baseline, sealed(bad))


class AppliedStoreTests(unittest.TestCase):
    """Stored records: version 1 stays readable; the envelope version must be the version of the fields."""

    def setUp(self) -> None:
        """Create a temporary editor area."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_applied_")
        self.area = Path(self.temp.name)
        self.store = AssociationRecordStore(self.area, "main", MISSION_KEY, "applied")

    def tearDown(self) -> None:
        """Remove the temporary area."""
        self.temp.cleanup()

    def write(self, version: int, record: dict[str, Any]) -> None:
        """Write a record file with an envelope version."""
        self.store.folder.mkdir(parents=True, exist_ok=True)
        path = self.store.folder / "applied.json"
        path.write_text(json.dumps({"schema_version": version, "revision": 3, **sealed(record)}), encoding="utf-8")

    def test_both_versions_read_and_a_wrong_envelope_is_corrupt(self) -> None:
        """Version 1 and 2 read; a version 2 envelope with version 1 fields, the reverse, and version 3 are corrupt."""
        for version, record in ((1, applied()), (APPLIED_VERSION, version_two())):
            self.write(version, record)
            self.assertEqual(self.store.read(), (sealed(record), 3))
        for version, record in ((APPLIED_VERSION, applied()), (1, version_two()), (3, version_two())):
            with self.subTest(version=version):
                self.write(version, record)
                self.assertRaises(RecordCorrupt, self.store.read)

    def test_save_writes_the_version_of_the_fields(self) -> None:
        """A new version 2 record is saved with envelope version 2; a version 1 record with version 1."""
        self.store.save(version_two(), {sha(VANILLA): VANILLA}, None)
        raw = json.loads((self.store.folder / "applied.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], APPLIED_VERSION)
        other = AssociationRecordStore(self.area, "second", MISSION_KEY, "applied")
        other.save(dict(applied(), profile_id="second"), {sha(VANILLA): VANILLA}, None)
        raw = json.loads((other.folder / "applied.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
