"""Exercise D4 record publication, capture guards and the A3 lock order."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_commit_capture import OriginalCaptureRefused
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from dayz_serverman.repositories.mission_map_records import OriginalStore, RecordConflict
from tests.test_mission_map_commit_fixture import CommitFixture
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_records import OUTPUT, VANILLA, sha


class CommitTests(CommitFixture):
    """A successful commit publishes all records after the durable commit point."""

    def test_records_publish_at_commit_point_in_the_a3_lock_order(self):
        """Every manager record remains absent until COMMITTING; the journal precedes all DayZ staging."""
        journal, updates, candidates = self.candidates()
        seen = []
        def observe(phase, index):
            """Check durable boundaries and publication order under the writer and association holds."""
            seen.append((phase, index))
            if phase == "AFTER_PREPARED":
                self.assertTrue(self.repository.path_for(journal.operation_id).is_file())
                self.assertFalse(Path(journal.groups[0].staging_path).exists())
                self.assertFalse(Path(journal.groups[0].recovery_path).exists())
            if phase in ("AFTER_PREPARED", "AFTER_PUBLISHED", "AFTER_COMMITTING"):
                self.assertTrue(self.ports.held)
                self.assertTrue(all(not (self.paths.root / update.target_path).exists() for update in updates))
            if phase == "AFTER_PUBLISH_RECORD":
                self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")
        result = self.run_commit(journal, updates, candidates, self.service(observe))
        self.assertEqual(result["result"], "COMMITTED")
        self.assertEqual(self.events, ["lane", "mutex", "stopped", "revalidate", "writer", "association", "revalidate",
                                      "release association", "release writer", "release mutex"])
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), OUTPUT)
        self.assertTrue(all((self.paths.root / update.target_path).exists() for update in updates))
        self.assertEqual(self.repository.load(self.repository.retired_path_for(journal.operation_id)).result, "COMMITTED")
        self.assertFalse(list(self.paths.root.rglob("*.staged-*")))
        self.assertEqual([index for phase, index in seen if phase == "AFTER_PUBLISH_RECORD"], [0, 1, 2, 3])

    def test_nonstopped_server_and_revalidation_refuse_without_writes(self):
        """Guard failures release the mutex before writer acquisition or staging."""
        journal, updates, candidates = self.candidates()
        before = snapshot(Path(self.temp.name))
        self.ports.state = ServerState.RUNNING_EXTERNAL
        with self.assertRaises(LifecycleFailure):
            self.run_commit(journal, updates, candidates)
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.ports.state = ServerState.STOPPED
        def refuse():
            """Reject a stale preview inside the installation mutex."""
            raise ValueError("stale preview")
        with self.assertRaisesRegex(ValueError, "stale preview"):
            self.service().commit(journal, updates, candidates, revalidate=refuse, existing_profiles={"main"})
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_reserved_name_conflict_precedes_original_staging(self):
        """The adapter's D8 refusal creates no original, record staging or DayZ byte."""
        journal, updates, candidates = self.candidates()
        before = snapshot(Path(self.temp.name))
        def reserved(_class, _path, _data):
            """Simulate a reserved-prefix or territory-marker conflict in the adapter."""
            raise MissionMapPublicationError("RESERVED_NAME_CONFLICT", "Reserved editor name already exists.")
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.run_commit(journal, updates, candidates, self.service(reserved=reserved))
        self.assertEqual(caught.exception.code, "RESERVED_NAME_CONFLICT")
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_identical_original_is_kept_and_a_different_one_is_refused(self):
        """Create-once originals neither change bytes nor their revision during an apply."""
        journal, updates, candidates = self.candidates()
        store = OriginalStore(self.paths.mission_map, journal.mission_key)
        store.create(updates[0].record, updates[0].contents)
        original_path = store.folder / "original.json"
        before = original_path.read_bytes()
        self.run_commit(journal, updates, candidates)
        self.assertEqual(original_path.read_bytes(), before)
        # A new operation cannot change the protected original's provenance or file digest
        journal, updates, candidates = self.candidates()
        altered = dict(updates[0].record, terrain_id="enoch")
        updates[0] = replace(updates[0], record=altered)
        with self.assertRaises(RecordConflict):
            self.run_commit(journal, updates, candidates)
        self.assertEqual(original_path.read_bytes(), before)

    def test_stale_manager_revision_refuses_before_any_staging(self):
        """A preview's stale baseline revision never writes content or original staging."""
        journal, updates, candidates = self.candidates()
        updates[1] = replace(updates[1], expected_revision=1)
        before = snapshot(Path(self.temp.name))
        with self.assertRaises(RevisionConflict):
            self.run_commit(journal, updates, candidates)
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_inconsistent_applied_snapshot_refuses_without_writes(self):
        """The previous-state record must describe the same old digest as the DayZ journal."""
        journal, updates, candidates = self.candidates()
        updates[2].record["files_before"][0]["sha256"] = sha(b"different")
        before = snapshot(Path(self.temp.name))
        with self.assertRaisesRegex(ValueError, "snapshots"):
            self.run_commit(journal, updates, candidates)
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_missing_owned_projection_refuses_without_writes(self):
        """A changed file requires the adapter's owned-value projection before capture or staging."""
        journal, updates, candidates = self.candidates()
        service = self.service()
        service.owned_digest = lambda _class, _path, _bytes: None
        before = snapshot(Path(self.temp.name))
        with self.assertRaisesRegex(ValueError, "projection"):
            self.run_commit(journal, updates, candidates, service)
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_ledger_whole_and_owned_output_digests_must_match_candidates(self):
        """A shaped ledger entry cannot claim output bytes or owned values the operation never writes."""
        for field in ("after_sha256", "owned_after_sha256"):
            with self.subTest(field=field):
                journal, updates, candidates = self.candidates()
                updates[-1].record["files"][0][field] = sha(b"different output")
                before = snapshot(Path(self.temp.name))
                with self.assertRaisesRegex(ValueError, "ledger file digests"):
                    self.run_commit(journal, updates, candidates)
                self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_restore_kinds_commit_without_changing_original_or_identical_baseline(self):
        """Both restore kinds use the same commit point and leave create-once original content unchanged."""
        for kind in ("restore_original", "restore_previous"):
            with self.subTest(kind=kind):
                case = CommitFixture(methodName="runTest")
                case.setUp()
                self.addCleanup(case.tearDown)
                journal, updates, candidates = case.candidates()
                case.run_commit(journal, updates, candidates)
                original_bytes = (case.paths.root / updates[0].target_path).read_bytes()
                baseline_bytes = (case.paths.root / updates[1].target_path).read_bytes()
                original_manifest = OriginalStore(case.paths.mission_map, journal.mission_key).read()["manifest_sha256"]
                journal, updates, candidates = case.candidates()
                journal.operation_kind = kind
                journal.plan_id = journal.plan_revision = journal.plan_fingerprint = journal.preview_fingerprint = None
                journal.groups[0].new_digest = sha(VANILLA)
                updates = updates[1:]
                updates[0].record["originals"][0]["manifest_sha256"] = original_manifest
                updates[0] = replace(updates[0], expected_revision=0)
                after = updates[1].record
                after.update(operation_kind=kind, plan_id=None, plan_revision=None, configuration_fingerprint=None)
                after["files_before"][0].update(sha256=sha(OUTPUT), size=len(OUTPUT))
                after["files_after"][0].update(sha256=sha(VANILLA), owned_sha256=sha(VANILLA))
                after["baseline_manifest_sha256"] = sealed(updates[0].record)["manifest_sha256"]
                updates[1] = replace(updates[1], expected_revision=0, contents={sha(OUTPUT): OUTPUT})
                updates[2].record.update(operation_kind=kind, output=False)
                updates[2].record["files"][0].update(before_sha256=sha(OUTPUT), after_sha256=sha(VANILLA),
                                                   owned_before_sha256=sha(OUTPUT), owned_after_sha256=sha(VANILLA))
                self.assertEqual(case.run_commit(journal, updates, {("mission", "mapgroupproto.xml"): VANILLA})["result"], "COMMITTED")
                self.assertEqual((case.mission / "mapgroupproto.xml").read_bytes(), VANILLA)
                self.assertEqual((case.paths.root / updates[0].target_path).read_bytes(), baseline_bytes)
                self.assertEqual((OriginalStore(case.paths.mission_map, journal.mission_key).folder / "original.json").read_bytes(), original_bytes)


class CaptureTests(CommitFixture):
    """Only changed editor output refuses capture; unchanged vanilla does not collide."""

    def test_output_match_reports_operation_folder_and_existing_profile(self):
        """The ledger capture refusal names the earlier output and explains adoption availability."""
        journal, updates, candidates = self.candidates()
        entry = dict(updates[-1].record, target_key="f" * 32, display_path="D:\\other-mission",
                     mission_root="mpmissions\\other", operation_id="prior-output")
        # The current vanilla bytes can represent earlier output changed from different bytes
        entry["files"] = [dict(entry["files"][0], before_sha256=sha(b"earlier"), baseline_sha256=sha(b"earlier"),
                               after_sha256=sha(VANILLA), owned_after_sha256=sha(VANILLA),
                               owned_before_sha256=sha(b"earlier"), owned_baseline_sha256=sha(b"earlier"))]
        LedgerStore(self.paths.mission_map).create(entry)
        before = snapshot(Path(self.temp.name))
        with self.assertRaises(OriginalCaptureRefused) as caught:
            self.run_commit(journal, updates, candidates)
        self.assertEqual(caught.exception.details["match"]["operation_id"], "prior-output")
        self.assertEqual(caught.exception.details["match"]["profile_id"], "main")
        self.assertIn("D:\\other-mission", str(caught.exception))
        self.assertFalse(caught.exception.details["adoption_required"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_byte_identical_vanilla_target_does_not_refuse_another(self):
        """A file whose after digest equals its baseline never matches the capture check."""
        journal, updates, candidates = self.candidates()
        entry = dict(updates[-1].record, target_key="f" * 32, operation_id="other-vanilla")
        entry["files"] = [dict(entry["files"][0], before_sha256=sha(b"earlier"), after_sha256=sha(VANILLA),
                               baseline_sha256=sha(VANILLA), owned_after_sha256=sha(VANILLA),
                               owned_baseline_sha256=sha(VANILLA))]
        LedgerStore(self.paths.mission_map).create(entry)
        self.assertEqual(self.run_commit(journal, updates, candidates)["result"], "COMMITTED")
