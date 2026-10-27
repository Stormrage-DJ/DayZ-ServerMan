"""Leaf 8.8: absent files, captured-only files, external changes, backups and unknown realizations of a restore."""

from __future__ import annotations

import json
import re
import shutil
import unittest

from dayz_serverman.domain.mission_map_records import MAX_PROVENANCE, sealed
from dayz_serverman.domain.mission_map_restore_texts import BACKUP_TEXT
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.medical_zone_fragments import patch_zones, zones_enabled
from dayz_serverman.repositories.mission_map_contents import ContentCorrupt
from dayz_serverman.repositories.mission_map_owned import WRITABLE_MISSION_FILES
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.mission_map_mapgroup_support import PROTOTYPE
from tests.mission_map_restore_support import CAPTURED_ONLY, PLAN, RestoreFixture, sha

PROTO, DIRT, AREAFLAGS = "mapgroupproto.xml", "mapgroupdirt.xml", "areaflags.map"
TERRITORY = "env/zombie_territories.xml"
FARM, VILLAGE = b'<usage name="Farm" />', b'<usage name="Village" />'


class FilesFixture(RestoreFixture):
    """Helpers over the restore fixture."""

    def applied_once(self) -> dict[str, bytes | None]:
        """Apply the plan once and return the mission state before it."""
        before = self.mission_state()
        self.save(PLAN, 25)
        self.run_apply()
        return before

    def named_paths(self) -> set[str]:
        """Return every path that the applied record and the ledger entries name."""
        applied = self.records()[2][0]
        named = {item["path"] for item in applied["files_after"] + applied["files_before"]}
        for entry in self.ledger_entries().values():
            named |= {item["path"] for item in entry["files"]}
        return named


class AbsentFileTests(FilesFixture):
    """A restore writes absences and creates missing folders (R16: existence and absence)."""

    def test_deleted_file_and_folder_are_restored(self) -> None:
        """A territory file deleted with its folder comes back; the captured-only wolf file stays absent."""
        before = self.applied_once()
        shutil.rmtree(self.mission / "env")
        review = self.restore_review("original")
        self.assertIn({"path": TERRITORY, "target_class": "mission", "exists_before": False, "exists_after": True,
                       "sha256_before": None, "sha256_after": sha(before[TERRITORY])},
                      review.answer["affected_files"])
        self.run_restore("original", review.fingerprint)
        state = self.mission_state()
        self.assertEqual({path: state[path] for path in WRITABLE_MISSION_FILES},
                         {path: before[path] for path in WRITABLE_MISSION_FILES})
        self.assertIsNone(state["env/wolf_territories.xml"])
        # The deletion was a change outside the map editor: its backup records the absence
        backup = self.records()[1][0]["provenance"][-2]
        self.assertEqual((backup["source"], backup["files"][0]["existed"]), ("replaced", False))

    def test_restore_writes_an_absence(self) -> None:
        """Restore previous state after a restore that created a file removes that file again."""
        before = self.applied_once()
        self.write(DIRT, None)
        output = self.mission_state()
        self.run_restore("original")
        self.assertEqual(self.mission_state()[DIRT], before[DIRT])
        self.run_restore("previous")
        # The state before Restore original: the Apply output without the deleted Dirt file
        self.assertEqual(self.mission_state(), output)
        self.assertFalse((self.mission / DIRT).exists())
        applied = self.records()[2][0]
        self.assertEqual(next(item for item in applied["files_after"] if item["path"] == DIRT)["existed"], False)
        entry = self.ledger_entries()["restore0002"]
        self.assertEqual({item["path"]: item["after_sha256"] for item in entry["files"]},
                         {path: sha(output[path]) for path in WRITABLE_MISSION_FILES if output[path] != before[path]})
        self.assertIsNone({item["path"]: item["after_sha256"] for item in entry["files"]}[DIRT])
        self.assertFalse(list(self.journals.root.glob("*.json")))


class CapturedOnlyTests(FilesFixture):
    """Neither restore writes a captured-only file, also when it differs from the original (7.9)."""

    def test_edited_wolf_file_stays_byte_equal(self) -> None:
        """An edited wolf file and a new bear file stay; no record or ledger entry names them."""
        self.applied_once()
        wolves, bears = b"<territory-type><edited/></territory-type>", b"<territory-type/>"
        self.write(CAPTURED_ONLY[0], wolves)
        self.write(CAPTURED_ONLY[1], bears)
        for mode in ("original", "previous", "original"):
            with self.subTest(mode=mode):
                review = self.restore_review(mode)
                self.assertFalse(set(CAPTURED_ONLY) & {item["path"] for item in review.answer["affected_files"]})
                self.run_restore(mode, review.fingerprint)
                self.assertEqual((self.mission_state()[CAPTURED_ONLY[0]], self.mission_state()[CAPTURED_ONLY[1]]),
                                 (wolves, bears))
                self.assertFalse(set(CAPTURED_ONLY) & self.named_paths())


class ExternalChangeTests(FilesFixture):
    """A file that changed outside the map editor and that a restore overwrites is backed up first (R17)."""

    def test_overwritten_external_edit_is_backed_up(self) -> None:
        """The preview flags it; the baseline keeps a `replaced` entry with the edited bytes."""
        before = self.applied_once()
        edited = (self.mission / PROTO).read_bytes().replace(FARM, VILLAGE)
        self.assertNotEqual(edited, (self.mission / PROTO).read_bytes())
        self.write(PROTO, edited)
        review = self.restore_review("previous")
        change = next(item for item in review.answer["external_changes"] if item["path"] == PROTO)
        self.assertEqual((change["overwritten"], change["backup"], change["text"]),
                         (True, True, BACKUP_TEXT.format(path=PROTO)))
        _original, (_baseline, revision), _applied = self.records()
        result = self.run_restore("previous", review.fingerprint)
        self.assertEqual(result["backups"], [{"path": PROTO, "sha256": sha(edited)}])
        self.assertEqual(self.mission_state()[PROTO], before[PROTO])
        _original, (baseline, new_revision), (applied, _revision) = self.records()
        self.assertEqual(new_revision, revision + 1)
        entry = baseline["provenance"][-1]
        self.assertEqual((entry["source"], entry["paths"], entry["operation_id"]), ("replaced", [PROTO], "restore0001"))
        self.assertEqual(entry["files"][0], {"target_key": self.key(), "path": PROTO, "existed": True,
                                             "sha256": sha(edited), "size": len(edited)})
        self.assertEqual((self.folder() / "files" / sha(edited)).read_bytes(), edited)
        raw = json.loads((self.folder() / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(applied["baseline_revision"], new_revision)

    def test_external_edit_of_a_file_that_the_restore_keeps_stays_visible(self) -> None:
        """Restore previous state does not write areaflags.map; its edit keeps the old reference."""
        self.applied_once()
        reference = next(item for item in self.records()[2][0]["files_after"] if item["path"] == AREAFLAGS)
        edited = (self.mission / AREAFLAGS).read_bytes() + b"\0"
        self.write(AREAFLAGS, edited)
        review = self.restore_review("previous")
        change = next(item for item in review.answer["external_changes"] if item["path"] == AREAFLAGS)
        self.assertEqual((change["overwritten"], change["backup"]), (False, False))
        self.assertEqual(self.run_restore("previous", review.fingerprint)["backups"], [])
        self.assertEqual(self.mission_state()[AREAFLAGS], edited)
        applied = self.records()[2][0]
        self.assertEqual(next(item for item in applied["files_after"] if item["path"] == AREAFLAGS), reference)
        # The next Apply preview still asks for a choice for that file
        self.assertIn(AREAFLAGS, self.review().answer["choices"]["external_changes"])

    def test_restore_original_keeps_the_fifty_newest_provenance_entries(self) -> None:
        """A full provenance list drops its oldest entry for the restore entry (MAX_PROVENANCE)."""
        self.applied_once()
        store = AssociationRecordStore(self.area, "main", self.key(), "baseline")
        baseline, revision = store.read()
        entries = [{"time": "2026-10-15T07:00:00Z", "source": "adoption", "operation_id": f"old{index:02d}",
                    "paths": [PROTO]} for index in range(MAX_PROVENANCE)]
        content = {name: value for name, value in baseline.items() if name != "manifest_sha256"}
        files = {item["sha256"]: (self.folder() / "files" / item["sha256"]).read_bytes()
                 for item in baseline["files"] if item["existed"]}
        store.save(dict(content, provenance=entries), files, revision)
        # The applied record names the baseline that it was built on; a new review reads both again
        applied = AssociationRecordStore(self.area, "main", self.key(), "applied")
        record, applied_revision = applied.read()
        new_baseline = store.read()
        applied.save(dict({name: value for name, value in record.items() if name != "manifest_sha256"},
                          baseline_revision=new_baseline[1],
                          baseline_manifest_sha256=new_baseline[0]["manifest_sha256"]),
                     {item["sha256"]: (self.folder() / "files" / item["sha256"]).read_bytes()
                      for item in record["files_before"] if item["existed"]}, applied_revision)
        self.run_restore("original")
        provenance = self.records()[1][0]["provenance"]
        self.assertEqual(len(provenance), MAX_PROVENANCE)
        self.assertEqual((provenance[0]["operation_id"], provenance[-1]["source"]), ("old01", "restore_original"))

    def test_replace_backup_survives_restore_original_and_previous(self) -> None:
        """A Replace backup of 8.7 stays in the provenance and in the content store after both restores."""
        self.save([], 50)
        self.run_apply()
        edited = re.sub(rb'(name="Land_Shed" lootmax=")[0-9]+', rb"\g<1>99", (self.mission / PROTO).read_bytes())
        self.write(PROTO, edited)
        self.run_apply({"external_changes": {PROTO: "replace"}, "unavailable_encounters": {}})
        for mode in ("original", "previous"):
            with self.subTest(mode=mode):
                self.run_restore(mode)
                baseline = self.records()[1][0]
                kept = [entry for entry in baseline["provenance"] if entry["source"] == "replaced"]
                self.assertEqual([entry["operation_id"] for entry in kept], ["apply0002"])
                self.assertEqual((self.folder() / "files" / sha(edited)).read_bytes(), edited)


class MedicalStateTests(FilesFixture):
    """A restore writes the literal promised bytes, with no current medical state on top (D5 notes, A182)."""

    def test_coordinated_medical_toggle_is_overwritten_without_a_backup(self) -> None:
        """A medical toggle after Apply is a coordinated change; Restore previous state writes the captured bytes."""
        self.set_medical_baseline()
        before = self.applied_once()
        applied = (self.mission / PROTO).read_bytes()
        self.write(PROTO, patch_zones(applied, PROTOTYPE, True))
        review = self.restore_review("previous")
        self.assertEqual(review.answer["coordinated_changes"], [
            {"path": PROTO, "feature": "medical_loot_zones", "reference": "applied", "overwritten": True}])
        self.assertEqual(review.answer["external_changes"], [])
        self.assertEqual(self.run_restore("previous", review.fingerprint)["backups"], [])
        self.assertEqual(self.mission_state()[PROTO], before[PROTO])
        self.assertFalse(zones_enabled(self.mission_state()[PROTO], PROTOTYPE))


class MissingContentTests(FilesFixture):
    """A restore writes only verified content: a missing or changed content file refuses before any write."""

    def test_missing_or_changed_content_refuses(self) -> None:
        """The restore point and the original are read with their digests (D2); nothing is repaired."""
        self.applied_once()
        item = next(entry for entry in self.records()[2][0]["files_before"] if entry["existed"])
        content = self.folder() / "files" / item["sha256"]
        data = content.read_bytes()
        content.write_bytes(data + b" ")
        before = self.snapshot()
        with self.assertRaises(ContentCorrupt):
            self.run_restore("previous", "0" * 64)
        content.unlink()
        with self.assertRaises(ContentCorrupt):
            self.restorer.preview("main", "previous")
        self.assertEqual(self.snapshot()[0], before[0])
        original = self.records()[0]["files"][1]
        (self.area / "originals" / self.key() / "files" / original["sha256"]).unlink()
        with self.assertRaises(ContentCorrupt):
            self.restorer.preview("main", "original")


class UnknownRealizationTests(FilesFixture):
    """A null realization stays null: it means unknown, never "no groups" (Architect 01:18:47)."""

    def test_restore_previous_of_a_first_apply_on_an_existing_original(self) -> None:
        """Profile second applied over profile main's output: its restore point has an unknown realization."""
        self.applied_once()
        main_state = self.mission_state()
        self.save(PLAN, 50, identifier="second")
        changes = self.review("second").answer["choices"]["external_changes"]
        self.run_apply({"external_changes": {path: "replace" for path in changes}, "unavailable_encounters": {}},
                       identifier="second")
        second = self.records("second")[2][0]
        self.assertIsNone(second["generated_groups_before"])
        self.run_restore("previous", identifier="second")
        self.assertEqual({path: self.mission_state()[path] for path in WRITABLE_MISSION_FILES},
                         {path: main_state[path] for path in WRITABLE_MISSION_FILES})
        applied = self.records("second")[2][0]
        self.assertEqual((applied["generated_groups"], applied["generated_groups_before"]),
                         (None, second["generated_groups"]))
        # Back again: the bytes of the second Apply, with their known realization
        self.run_restore("previous", identifier="second")
        applied = self.records("second")[2][0]
        self.assertEqual((applied["generated_groups"], applied["generated_groups_before"]),
                         (second["generated_groups"], None))

    def test_restore_previous_of_a_version_one_record(self) -> None:
        """A version 1 record has no realization fields: both stay unknown in the restore record."""
        self.applied_once()
        path = self.folder() / "applied.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {key: value for key, value in raw.items() if key not in (
            "schema_version", "revision", "manifest_sha256", "global_abundance", "plan_objects", "generated_groups",
            "generated_groups_before")}
        path.unlink()
        VersionedJsonRepository(path, 1).save(sealed(fields), None)
        self.run_restore("previous")
        applied = self.records()[2][0]
        self.assertEqual((applied["generated_groups"], applied["generated_groups_before"]), (None, None))


if __name__ == "__main__":
    unittest.main()
