"""Cover the staged original kinds of 10.8: supersede, restored copy and the provenance-linked original (D5)."""

from __future__ import annotations

import copy
import unittest
import uuid

from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.domain.mission_map_records import RecordShapeError, copied_original, copy_matches, sealed
from dayz_serverman.repositories.mission_map_commit_originals import (
    copied_original_update, restored_copy_update, supersede_updates,
)
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords, RecordUpdate, parse_record
from dayz_serverman.repositories.mission_map_journal import MapJournal, MapRecord, record_staging
from dayz_serverman.repositories.mission_map_journal_paths import journal_paths_safe
from dayz_serverman.repositories.mission_map_journal_store import parse_journal
from dayz_serverman.repositories.mission_map_layout import TargetClass, association_folder, target_key
from dayz_serverman.repositories.mission_map_records import (
    OriginalStore, RecordConflict, kept_originals, restored_copy_file, superseded_file,
)
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from tests.test_mission_map_journal import JournalFixture
from tests.test_mission_map_records import OUTPUT, VANILLA, original, sha


# A source key that no journal of these tests names
SOURCE_KEY = "5" * 32


class OriginalKindsFixture(JournalFixture):
    """A manager root with one active mission original and a records-only restore journal."""

    def setUp(self) -> None:
        """Create the active original A of the fixture mission key."""
        super().setUp()
        self.area = self.paths.mission_map
        self.key = self.mission_key()
        self.active = OriginalStore(self.area, self.key).create(original(self.key), {sha(VANILLA): VANILLA})
        self.incoming = sealed(original(self.key, OUTPUT))

    def new_journal(self, key: str | None = None) -> MapJournal:
        """Return a PREPARED journal without DayZ-side groups for the key, by default the fixture mission key."""
        return MapJournal("op-" + uuid.uuid4().hex, "restore_original", "main", "mpmissions\\dayzOffline.chernarusplus",
                          key or self.key, None, None, str(self.dayz), None, None, None, None, 2, 7, [], [])

    def commit(self, journal: MapJournal, updates: list[RecordUpdate]) -> CommitRecords:
        """Stage the updates and roll them forward as the commit point does."""
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, updates)
        journal.phase, journal.publication_started = "COMMITTING", True
        self.assertTrue(records.roll_forward(journal))
        records.sweep(journal)
        return records

    def refused(self, error: type[Exception], pattern: str, journal: MapJournal, updates: list[RecordUpdate]) -> None:
        """Assert that staging refuses with the reason before any write: no entry and no staging file."""
        with self.assertRaisesRegex(error, pattern):
            CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(journal, updates)
        self.assertEqual(journal.records, [])
        self.assertFalse(any(self.area.rglob("*.staged-*")))


class ProvenanceShapeTests(OriginalKindsFixture):
    """The original of a new key holds the source content and names its source (10.2 OP-3)."""

    def test_copy_names_its_source_and_keeps_the_content(self) -> None:
        """Manifest digests: the copy has its own digest and names the source digest; the files stay equal."""
        copy_record = copied_original(self.active, target_key=SOURCE_KEY, display_path="C:\\DayZ\\copy",
                                      mission_root="mpmissions\\copy", case="copy", operation_id="op-9")
        self.assertEqual(parse_record("copied_original", copy_record), copy_record)
        self.assertEqual(copy_record["provenance"], {"case": "copy", "source_key": self.key, "operation_id": "op-9",
                                                     "source_manifest_sha256": self.active["manifest_sha256"]})
        self.assertNotEqual(copy_record["manifest_sha256"], self.active["manifest_sha256"])
        self.assertEqual(copy_record["files"], self.active["files"])
        self.assertTrue(copy_matches(copy_record, self.active))
        self.assertFalse(copy_matches(copy_record, self.incoming))
        self.assertFalse(copy_matches(self.active, self.active))
        # A copy of a copy names its direct source
        again = copied_original(copy_record, target_key="6" * 32, display_path="C:\\DayZ\\again",
                                mission_root="mpmissions\\again", case="move", operation_id="op-10")
        self.assertEqual(again["provenance"]["source_key"], SOURCE_KEY)
        self.assertTrue(copy_matches(again, copy_record))

    def test_provenance_rules(self) -> None:
        """A wrong case, the own key as source, extra fields and a missing provenance are refused."""
        good = copied_original(self.active, target_key=SOURCE_KEY, display_path="C:\\DayZ\\copy",
                               mission_root="mpmissions\\copy", case="move", operation_id="op-9")
        edits = [
            lambda raw: raw["provenance"].update(case="adoption"),
            lambda raw: raw["provenance"].update(source_key=SOURCE_KEY),
            lambda raw: raw["provenance"].update(source_manifest_sha256="x"),
            lambda raw: raw["provenance"].update(operation_id="bad id"),
            lambda raw: raw["provenance"].update(extra=1),
            lambda raw: raw.update(provenance=None),
        ]
        for edit in edits:
            raw = copy.deepcopy(good)
            edit(raw)
            with self.subTest(raw=raw), self.assertRaises(RecordShapeError):
                parse_record("original", sealed(raw))
        self.assertRaises(RecordShapeError, parse_record, "copied_original", self.active)
        self.assertEqual(parse_record("original", good), good)
        # The case `restore` of 10.37 is valid, and like every case it names another key (Architect 07:28:52)
        restore = copy.deepcopy(good)
        restore["provenance"].update(case="restore")
        self.assertEqual(parse_record("original", sealed(restore))["provenance"]["case"], "restore")
        restore["provenance"].update(source_key=SOURCE_KEY)
        self.assertRaises(RecordShapeError, parse_record, "original", sealed(restore))


class SupersedeTests(OriginalKindsFixture):
    """D5 row "different original, not shared": the backup original becomes active and the current one is kept."""

    def test_supersede_keeps_the_current_original_and_sets_the_incoming_active(self) -> None:
        """The superseded record publishes first with the old manifest; the active original then changes."""
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, supersede_updates(self.paths, journal, self.incoming, {sha(OUTPUT): OUTPUT}))
        kept = superseded_file(self.area, self.key, journal.operation_id)
        self.assertEqual([(entry.kind, entry.old_manifest_sha256, entry.new_manifest_sha256) for entry in journal.records],
                         [("superseded", None, self.active["manifest_sha256"]),
                          ("original", self.active["manifest_sha256"], self.incoming["manifest_sha256"])])
        # Nothing publishes before the commit point
        store = OriginalStore(self.area, self.key)
        self.assertEqual(store.read(), self.active)
        self.assertFalse(kept.exists())
        journal.phase, journal.publication_started = "COMMITTING", True
        self.assertTrue(records.roll_forward(journal))
        records.sweep(journal)
        self.assertEqual(store.read(), self.incoming)
        self.assertEqual(kept_originals(store.folder), [self.active])
        self.assertEqual((store.contents.read(sha(VANILLA)), store.contents.read(sha(OUTPUT))), (VANILLA, OUTPUT))
        # The journal with both kinds round-trips and its derived paths are safe
        parse_journal(journal.to_dict(), journal.operation_id)
        self.assertTrue(journal_paths_safe(journal, self.dayz, self.paths))

    def test_shared_target_and_incomplete_supersede_are_refused(self) -> None:
        """A4 condition 3 and the pair rule: no replacement without the kept copy, and no copy without it."""
        journal = self.new_journal()
        kept, replace = supersede_updates(self.paths, journal, self.incoming, {sha(OUTPUT): OUTPUT})
        self.refused(RecordConflict, "already exists", journal, [replace])
        self.refused(RecordConflict, "already exists", journal, [replace, kept])
        self.refused(ValueError, "needs the replacement", journal, [kept])
        wrong = RecordUpdate(kept.kind, kept.target_path, self.incoming, contents={sha(OUTPUT): OUTPUT})
        self.refused(ValueError, "must keep the active original", journal, [wrong, replace])
        association_folder(self.area, "other", self.key).mkdir(parents=True)
        self.refused(RecordConflict, "shared target", journal, [kept, replace])
        self.assertEqual(OriginalStore(self.area, self.key).read(), self.active)
        self.assertRaises(ValueError, supersede_updates, self.paths, journal, self.active, {})

    def test_rollback_discards_the_staged_pair(self) -> None:
        """A discarded journal leaves the active original and its content; no superseded record exists."""
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, supersede_updates(self.paths, journal, self.incoming, {sha(OUTPUT): OUTPUT}))
        self.assertTrue(records.rollback(journal))
        store = OriginalStore(self.area, self.key)
        self.assertEqual(store.read(), self.active)
        self.assertEqual(kept_originals(store.folder), [])
        self.assertEqual(store.contents.read(sha(VANILLA)), VANILLA)
        self.assertFalse(any(self.area.rglob("*.staged-*")))


class RestoredCopyTests(OriginalKindsFixture):
    """D5 row "different original, shared": the backup original stays inactive in the restoring profile's folder."""

    def test_restored_copy_is_kept_beside_the_active_original(self) -> None:
        """The copy keeps the backup manifest; the active original and the other folders stay unchanged."""
        association_folder(self.area, "other", self.key).mkdir(parents=True)
        journal = self.new_journal()
        self.commit(journal, [restored_copy_update(self.paths, journal, self.incoming, {sha(OUTPUT): OUTPUT})])
        folder = association_folder(self.area, "main", self.key)
        name = restored_copy_file(self.area, "main", self.key, TargetClass.MISSION, journal.operation_id)
        self.assertEqual(name.parent, folder)
        self.assertTrue(name.is_file())
        self.assertEqual(kept_originals(folder), [self.incoming])
        self.assertEqual(OriginalStore(self.area, self.key).read(), self.active)
        self.assertEqual(list(association_folder(self.area, "other", self.key).iterdir()), [])
        # The content stays after the sweep and after the orphan cleanup
        self.journal_retired()
        cleanup_orphans(self.paths, AssociationLocks())
        self.assertEqual(kept_originals(folder), [self.incoming])
        self.assertTrue((folder / "files" / sha(OUTPUT)).is_file())

    def test_restored_copy_needs_a_different_active_original_and_is_created_once(self) -> None:
        """The same digest as the active one, no active original, or a different copy at the same name refuse."""
        journal = self.new_journal()
        same = restored_copy_update(self.paths, journal, self.active, {sha(VANILLA): VANILLA})
        self.refused(ValueError, "different active original", journal, [same])
        fresh = self.new_journal("7" * 32)
        other = sealed(original("7" * 32, OUTPUT))
        alone = restored_copy_update(self.paths, fresh, other, {sha(OUTPUT): OUTPUT})
        self.refused(ValueError, "different active original", fresh, [alone])
        update = restored_copy_update(self.paths, journal, self.incoming, {sha(OUTPUT): OUTPUT})
        self.commit(journal, [update])
        # An identical rewrite stages nothing; a different record at the same name is refused
        again = MapJournal(**{**vars(self.new_journal()), "operation_id": journal.operation_id, "records": []})
        CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(again, [update])
        self.assertEqual(again.records, [])
        changed = dict(self.incoming, captured_at="2026-10-11T12:00:00Z")
        changed_update = RecordUpdate(update.kind, update.target_path, changed, contents=update.contents)
        self.refused(RecordConflict, "create-once", again, [changed_update])

    def journal_retired(self) -> None:
        """Leave no active journal, so the orphan cleanup runs."""
        for path in self.repository.root.glob("*.json"):
            path.unlink()


class CopiedOriginalTests(OriginalKindsFixture):
    """The new key of a true move or a copy gets a provenance-linked original; the source original stays."""

    def setUp(self) -> None:
        """Add the source original; the fixture mission key stands for a key that already has an original."""
        super().setUp()
        self.source = OriginalStore(self.area, SOURCE_KEY).create(original(SOURCE_KEY), {sha(VANILLA): VANILLA})
        self.new_key = "8" * 32

    def test_move_and_copy_create_the_original_of_the_new_key(self) -> None:
        """Both cases copy the content, name the source and keep the source original unchanged."""
        for case in ("move", "copy"):
            key = self.new_key[:-1] + ("1" if case == "move" else "2")
            journal = self.new_journal(key)
            update = copied_original_update(self.paths, journal, SOURCE_KEY, display_path="C:\\DayZ\\new",
                                            mission_root="mpmissions\\new", case=case)
            self.commit(journal, [update])
            created = OriginalStore(self.area, key)
            record = created.read()
            self.assertEqual(record["provenance"], {"case": case, "source_key": SOURCE_KEY,
                                                    "source_manifest_sha256": self.source["manifest_sha256"],
                                                    "operation_id": journal.operation_id})
            self.assertEqual(created.contents.read(sha(VANILLA)), VANILLA)
            self.assertEqual(OriginalStore(self.area, SOURCE_KEY).read(), self.source)

    def test_copied_original_is_created_once_and_must_match_its_source(self) -> None:
        """Another original at the new key is never replaced; a changed copy or another operation is refused."""
        journal = self.new_journal()
        update = copied_original_update(self.paths, journal, SOURCE_KEY, display_path="C:\\DayZ\\new",
                                        mission_root="mpmissions\\new", case="copy")
        self.refused(RecordConflict, "create-once", journal, [update])
        self.assertEqual(OriginalStore(self.area, self.key).read(), self.active)
        journal = self.new_journal(self.new_key)
        update = copied_original_update(self.paths, journal, SOURCE_KEY, display_path="C:\\DayZ\\new",
                                        mission_root="mpmissions\\new", case="copy")
        tampered = copy.deepcopy(update.record)
        tampered["files"][1]["sha256"] = sha(OUTPUT)
        tampered_update = RecordUpdate(update.kind, update.target_path, tampered, contents={sha(OUTPUT): OUTPUT})
        self.refused(ValueError, "differs from its source", journal, [tampered_update])
        foreign = dict(update.record, provenance=dict(update.record["provenance"], operation_id="op-other"))
        foreign_update = RecordUpdate(update.kind, update.target_path, foreign, contents=update.contents)
        self.refused(ValueError, "differs from its source", journal, [foreign_update])
        plain = RecordUpdate(update.kind, update.target_path, self.source)
        self.refused(RecordShapeError, "names its source", journal, [plain])
        self.commit(journal, [update])
        # An identical rewrite in a later journal of the same operation stages nothing
        again = MapJournal(**{**vars(self.new_journal(self.new_key)), "operation_id": journal.operation_id, "records": []})
        CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(again, [update])
        self.assertEqual(again.records, [])
        self.assertRaises(ValueError, copied_original_update, self.paths, journal, "9" * 32, display_path="C:\\x",
                          mission_root="x", case="copy")



class JournalPathTests(OriginalKindsFixture):
    """The journal derives the path of each new kind again; a kind at another path is unsafe."""

    def test_each_kind_has_its_derived_path(self) -> None:
        """A copied original uses the original path; a restored copy is named by its class in the association."""
        journal = self.new_journal()
        area, operation = self.area, journal.operation_id
        paths = {
            "copied_original": self.paths.relative(OriginalStore(area, self.key).folder / "original.json"),
            "superseded": self.paths.relative(superseded_file(area, self.key, operation)),
            "restored_copy": self.paths.relative(restored_copy_file(area, "main", self.key, TargetClass.MISSION, operation)),
        }
        for kind, path in paths.items():
            journal.records = [MapRecord(kind, path, record_staging(path, operation), False, None, sha(kind.encode()))]
            with self.subTest(kind=kind):
                parse_journal(journal.to_dict(), operation)
                self.assertTrue(journal_paths_safe(journal, self.dayz, self.paths))
            journal.records = [MapRecord("superseded" if kind != "superseded" else "restored_copy", path,
                                         record_staging(path, operation), False, None, sha(kind.encode()))]
            with self.subTest(kind=kind, swapped=True):
                self.assertFalse(journal_paths_safe(journal, self.dayz, self.paths))
        # A runtime restored copy needs the runtime profile of the journal
        runtime = restored_copy_file(area, "main", self.key, TargetClass.RUNTIME, operation)
        journal.records = [MapRecord("restored_copy", self.paths.relative(runtime),
                                     record_staging(self.paths.relative(runtime), operation), False, None, sha(b"r"))]
        self.assertFalse(journal_paths_safe(journal, self.dayz, self.paths))
        journal.runtime_profile = "serverman\\main\\profile"
        journal.runtime_profile_key = target_key(TargetClass.RUNTIME, self.runtime.resolve(strict=True))
        self.assertTrue(journal_paths_safe(journal, self.dayz, self.paths))


if __name__ == "__main__":
    unittest.main()
