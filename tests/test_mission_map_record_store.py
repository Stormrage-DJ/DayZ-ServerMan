"""Cover the original, baseline and last-applied stores, their contents and the shared-mission lookup."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.mission_map_plans import MissionMapPlanService, MissionPathChanged
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_contents import ContentCorrupt, ContentStore
from dayz_serverman.repositories.mission_map_layout import TargetClass, association_folder, target_key
from dayz_serverman.repositories.mission_map_records import (
    AssociationRecordStore, OriginalStore, RecordConflict, RecordCorrupt, association_profiles,
)
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_plan_service import Profiles, profile
from tests.test_mission_map_records import MISSION_KEY, OUTPUT, VANILLA, applied, baseline, original, sha


class StoreFixture(unittest.TestCase):
    """A temporary editor area."""

    def setUp(self) -> None:
        """Create the temporary editor area."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_record_store_")
        self.area = Path(self.temp.name) / "mission-map"

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temp.cleanup()


class OriginalStoreTests(StoreFixture):
    """Verify the create-once protected original and its contents."""

    def test_second_different_original_is_refused_and_identical_rewrite_is_a_no_op(self) -> None:
        """The first original is kept; a different one is refused; the same one writes nothing."""
        store = OriginalStore(self.area, MISSION_KEY)
        self.assertIsNone(store.read())
        created = store.create(original(), {sha(VANILLA): VANILLA})
        record_path = store.folder / "original.json"
        before = (record_path.read_bytes(), os.stat(record_path).st_mtime_ns)
        self.assertEqual(store.create(original(), {sha(VANILLA): VANILLA}), created)
        self.assertEqual((record_path.read_bytes(), os.stat(record_path).st_mtime_ns), before)
        with self.assertRaises(RecordConflict):
            store.create(original(data=OUTPUT), {sha(OUTPUT): OUTPUT})
        self.assertEqual(store.read(), created)
        self.assertEqual(store.contents.read(sha(VANILLA)), VANILLA)
        self.assertEqual(store.identity(), {"target_key": MISSION_KEY, "manifest_sha256": created["manifest_sha256"]})

    def test_contents_must_match_and_records_must_name_their_key(self) -> None:
        """Missing or wrong content and another target key are refused before any record is written."""
        store = OriginalStore(self.area, MISSION_KEY)
        self.assertRaises(ValueError, store.create, original(), {})
        self.assertRaises(ValueError, store.create, original(), {sha(VANILLA): OUTPUT})
        self.assertRaises(ValueError, store.create, original(key="1" * 32), {sha(VANILLA): VANILLA})
        self.assertIsNone(store.read())

    def test_corrupt_record_and_content_need_recovery(self) -> None:
        """A changed record or content file is reported, never repaired."""
        store = OriginalStore(self.area, MISSION_KEY)
        store.create(original(), {sha(VANILLA): VANILLA})
        content = store.contents.folder / sha(VANILLA)
        content.write_bytes(b"changed")
        self.assertRaises(ContentCorrupt, store.contents.read, sha(VANILLA))
        self.assertRaises(ContentCorrupt, store.contents.write, VANILLA)
        self.assertEqual(content.read_bytes(), b"changed")
        record = store.folder / "original.json"
        record.write_text(record.read_text(encoding="utf-8").replace("chernarusplus", "enoch"), encoding="utf-8")
        self.assertRaises(RecordCorrupt, store.read)

    def test_unused_content_sweep(self) -> None:
        """Unused content and temporary content files are removed; used content and other files stay."""
        contents = ContentStore(self.area / "originals" / MISSION_KEY)
        used, unused = contents.write(VANILLA), contents.write(OUTPUT)
        temporary = contents.folder / f".{unused}.abc123.tmp"
        temporary.write_bytes(b"partial")
        other = contents.folder / "notes.txt"
        other.write_text("kept", encoding="utf-8")
        removed = contents.sweep([used])
        self.assertEqual(sorted(path.name for path in removed), sorted([unused, temporary.name]))
        self.assertTrue((contents.folder / used).exists() and other.exists())
        self.assertRaises(ContentCorrupt, contents.read, unused)


class AssociationRecordTests(StoreFixture):
    """Verify the revision-guarded baseline and last-applied records of one association."""

    def test_revisions_identities_and_contents(self) -> None:
        """Records start at revision 0, follow the revision guard and store the bytes they name."""
        store = AssociationRecordStore(self.area, "main", MISSION_KEY, "baseline")
        self.assertIsNone(store.read())
        self.assertEqual(store.save(baseline(), {sha(VANILLA): VANILLA}, None), 0)
        self.assertRaises(RevisionConflict, store.save, baseline(), {sha(VANILLA): VANILLA}, None)
        self.assertEqual(store.save(baseline(), {sha(VANILLA): VANILLA}, 0), 1)
        self.assertEqual(store.identity()["revision"], 1)
        self.assertEqual(store.contents.read(sha(VANILLA)), VANILLA)
        last = AssociationRecordStore(self.area, "main", MISSION_KEY, "applied")
        last.save(applied(), {sha(VANILLA): VANILLA}, None)
        self.assertEqual(last.read()[0]["configuration_fingerprint"], "c" * 64)
        self.assertEqual(last.identity()["operation_id"], "op-2")
        self.assertRaises(ValueError, AssociationRecordStore, self.area, "main", MISSION_KEY, "plan")

    def test_profile_isolation(self) -> None:
        """Two profiles of one mission keep separate records; a record of another profile is refused."""
        main = AssociationRecordStore(self.area, "main", MISSION_KEY, "baseline")
        second = AssociationRecordStore(self.area, "second", MISSION_KEY, "baseline")
        main.save(baseline(), {sha(VANILLA): VANILLA}, None)
        self.assertIsNone(second.read())
        self.assertRaises(ValueError, second.save, baseline(), {sha(VANILLA): VANILLA}, None)
        second.save(baseline("second", data=OUTPUT), {sha(OUTPUT): OUTPUT}, None)
        self.assertEqual(main.read()[0]["files"][0]["sha256"], sha(VANILLA))
        self.assertNotEqual(main.folder, second.folder)

    def test_case_folded_mission_keys_and_shared_lookup(self) -> None:
        """Spellings of one mission folder share one key; the lookup names every profile with a folder for it."""
        key = target_key(TargetClass.MISSION, Path("D:/DayZ/MPMissions/dayzOffline.Test"))
        self.assertEqual(key, target_key(TargetClass.MISSION, Path("d:/dayz/mpmissions/DAYZOFFLINE.test")))
        for profile_id in ("main", "test20261001"):
            AssociationRecordStore(self.area, profile_id, key, "baseline").save(
                baseline(profile_id, key), {sha(VANILLA): VANILLA}, None)
        self.assertEqual(association_profiles(self.area, key), ["main", "test20261001"])
        self.assertEqual(association_profiles(self.area, MISSION_KEY), [])
        self.assertEqual(association_profiles(self.area, "../x"), [])


class PathChangeFallbackTests(StoreFixture):
    """Verify that a folder with records but no plan still takes part in the path-change check."""

    def test_records_without_a_plan_name_the_mission_root(self) -> None:
        """A set-aside plan leaves the last-applied record, which still names the mission root."""
        root = Path(self.temp.name) / "DayZServer"
        (root / "mpmissions" / "dayzOffline.test").mkdir(parents=True)
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(root), revision=1))
        service = MissionMapPlanService(Profiles(profile("main")), settings, self.area,  # type: ignore[arg-type]
                                        clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
        old_key = "a" * 32
        AssociationRecordStore(self.area, "main", old_key, "applied").save(
            applied(key=old_key), {sha(VANILLA): VANILLA}, None)
        loaded = service.load("main")
        self.assertEqual(loaded["path_change"]["shown_mission_key"], old_key)
        self.assertEqual(loaded["state"], "MISSING")
        active = service.active_association("main")
        self.assertRaises(MissionPathChanged, service.save, "main", dict(stored_plan(), association=active), None)
        self.assertFalse(association_folder(self.area, "main", active["mission_key"]).exists())


if __name__ == "__main__":
    unittest.main()
