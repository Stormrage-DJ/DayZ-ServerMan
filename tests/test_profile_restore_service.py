"""Direct profile reconstruction, stale previews and joint recovery fault checks."""

import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_backups import FakeSettings, record, create_runtime_profile
from dayz_serverman.application.backups import BackupService
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.application.profile_restores import ProfileRestoreService
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.backups import BackupStorage
from dayz_serverman.repositories.profiles import ProfileRepository, ProfileNotFound
from dayz_serverman.repositories.profile_restore_journal import ProfileRestoreJournal
from dayz_serverman.repositories.profile_restore_storage import ProfileRestoreStorage
from dayz_serverman.repositories.profile_restore_inventory import inventory


class DirectRestoreTests(unittest.TestCase):
    """Exercise the production service with real archives and temporary repositories."""

    def setUp(self):
        """Capture a selected and unrelated world before removing the original profile."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dayz = self.root / "dayz"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname="original"; instanceId=7; steamQueryPort=2305; class Missions { class DayZ { template="dayzOffline.chernarusplus"; }; };')
        self.mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        (self.mission / "storage_7" / "empty").mkdir(parents=True)
        (self.mission / "storage_7" / "players.db").write_bytes(b"players\x00\xff")
        (self.mission / "storage_8").mkdir()
        (self.mission / "storage_8" / "unrelated.bin").write_bytes(b"other world")
        (self.mission / "init.c").write_bytes(b"common mission")
        create_runtime_profile(self.dayz)
        (self.dayz / "DayZServer_x64.exe").write_bytes(b"fixture")
        self.backup_root = self.root / "backups"
        self.backup_root.mkdir()
        self.settings = FakeSettings(self.dayz, self.backup_root)
        self.repository = ProfileRepository(self.root / "profiles")
        self.profiles = ProfileService(self.repository, self.settings)
        self.profiles.save(record().values, None)
        self.backups = BackupStorage()
        backup = BackupService(self.profiles, self.settings, self.backups).create("main", 0, 4, lambda *_: None)
        self.backup_id = backup["backup_id"]
        self.profiles.delete("main", 0)
        self.original_inventory = inventory(self.mission)
        self.journals = ProfileRestoreJournal(self.root / "journals")
        self.storage = ProfileRestoreStorage(self.journals, self.repository.root, self.root / "recovery")
        self.service = ProfileRestoreService(self.profiles, self.settings, self.backups, self.storage,
            SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.STOPPED)),
            SimpleNamespace(guard=lambda *_: contextlib.nullcontext()), lambda: frozenset())
        self.request = {"backup_id": self.backup_id, "profile_id": "restored", "display_name": "Restored",
                        "storage_policy": None, "game_port": None, "steam_query_port": None}

    def parameters(self):
        """Repeat the original request with the exact reviewed digest and fingerprint."""
        preview = self.service.preview(self.request)
        return {**self.request, "expected_manifest_digest": preview["manifest_digest"],
                "preview_fingerprint": preview["preview_fingerprint"], "overwrite_confirmation": None}

    def test_deleted_profile_restores_selected_world_in_isolation(self):
        """No original record is required and unrelated storage remains untouched."""
        result = self.service.apply(self.parameters(), "restore1", lambda *_: None)
        restored = self.profiles.read("restored")
        destination = self.dayz.joinpath(*result["mission_root"].split("\\"))
        self.assertEqual((destination / f"storage_{result['instance_id']}" / "players.db").read_bytes(), b"players\x00\xff")
        self.assertTrue((destination / f"storage_{result['instance_id']}" / "empty").is_dir())
        self.assertFalse((destination / "storage_8").exists())
        self.assertEqual(inventory(self.mission), self.original_inventory)
        self.assertEqual(restored.revision, 0)
        self.assertEqual(restored.values.display_name, "Restored")
        self.assertEqual(self.journals.records()[0]["phase"], "COMMITTED")

    def test_absent_original_preserves_identity(self):
        """An absent unreferenced mission is recreated at its original storage ID."""
        shutil.rmtree(self.mission)
        preview = self.service.preview(self.request)
        self.assertEqual(preview["storage_policy"], "preserve_original")
        self.assertEqual(preview["instance_id"], 7)
        self.service.apply(self.parameters(), "restore2", lambda *_: None)
        self.assertEqual((self.mission / "storage_7" / "players.db").read_bytes(), b"players\x00\xff")

    def test_stale_target_and_port_conflicts_publish_nothing(self):
        """A new target or bound port invalidates a reviewed allocation before writes."""
        parameters = self.parameters()
        self.service.udp_inventory = lambda: frozenset({2302})
        with self.assertRaises(RevisionConflict):
            self.service.apply(parameters, "stale", lambda *_: None)
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")
        self.assertEqual(self.journals.records(), [])

    def test_each_publication_failure_rolls_back_both_files_and_record(self):
        """Failures across publication boundaries leave no half-created profile."""
        for index, phase in enumerate(("PREPARING", "PREPARED", "PUBLISHING", "PUBLISHED_GENERATED", "PUBLISHED_MISSION", "FILES_PUBLISHED", "PROFILE_PUBLISHING", "PUBLISHED_PROFILE", "PROFILE_PUBLISHED")):
            def fail(actual):
                """Inject one ordinary failure at the selected durable boundary."""
                if actual == phase:
                    raise OSError("injected")
            self.storage.hook = fail
            with self.subTest(phase=phase), self.assertRaises(OSError):
                self.service.apply(self.parameters(), f"fault{index}", lambda *_: None)
            with self.assertRaises(ProfileNotFound):
                self.profiles.read("restored")
            self.assertFalse((self.dayz / "serverman" / "restored").exists())
            self.assertEqual(inventory(self.mission), self.original_inventory)

    def test_crash_after_profile_publication_recovers_jointly(self):
        """A restart before durable commit compensates even a published profile record."""
        self.storage.hook = lambda phase: (_ for _ in ()).throw(KeyboardInterrupt()) if phase == "PROFILE_PUBLISHED" else None
        with self.assertRaises(KeyboardInterrupt):
            self.service.apply(self.parameters(), "crash1", lambda *_: None)
        self.assertEqual(self.profiles.read("restored").revision, 0)
        self.storage.hook = lambda _: None
        self.assertFalse(self.storage.inspect(self.dayz)["blocked"])
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")

    def test_external_change_blocks_compensation(self):
        """Recovery never deletes independently changed operation destinations."""
        self.storage.hook = lambda phase: (_ for _ in ()).throw(KeyboardInterrupt()) if phase == "PROFILE_PUBLISHED" else None
        with self.assertRaises(KeyboardInterrupt):
            self.service.apply(self.parameters(), "crash2", lambda *_: None)
        config = self.dayz / "serverman" / "restored" / "serverDZ.cfg"
        config.write_bytes(b"external edit")
        self.storage.hook = lambda _: None
        self.assertTrue(self.storage.inspect(self.dayz)["blocked"])
        self.assertEqual(config.read_bytes(), b"external edit")

    def test_replacement_whole_tree_and_bound_consumer_confirmation(self):
        """Confirmed replacement deletes obsolete world files and retains a verified recovery copy."""
        owner = self.profiles.save(record().values, None)
        world = self.mission / "storage_7"
        (world / "players.db").write_bytes(b"newer world")
        (world / "obsolete.bin").write_bytes(b"must not survive replacement")
        before = inventory(world)
        unrelated = inventory(self.mission / "storage_8")
        self.request["storage_policy"] = "replace_existing"
        parameters = self.parameters()
        with self.assertRaises(ValueError):
            self.service.apply(parameters, "unconfirmed", lambda *_: None)
        parameters["overwrite_confirmation"] = {"preview_fingerprint": parameters["preview_fingerprint"], "affected_profile_ids": ["main"]}
        result = self.service.apply(parameters, "replace1", lambda *_: None)
        self.assertEqual((world / "players.db").read_bytes(), b"players\x00\xff")
        self.assertFalse((world / "obsolete.bin").exists())
        self.assertEqual(inventory(Path(result["recovery_copy"])), before)
        self.assertEqual(inventory(self.mission / "storage_8"), unrelated)
        self.assertEqual(self.profiles.read("main"), owner)

    def test_replacement_unknown_owner_or_common_mismatch_is_rejected(self):
        """Orphan storage and mixed mission scripts cannot be overwritten."""
        self.request["storage_policy"] = "replace_existing"
        with self.assertRaisesRegex(ValueError, "registered consumers"):
            self.service.preview(self.request)
        self.profiles.save(record().values, None)
        (self.mission / "init.c").write_bytes(b"different scripts")
        with self.assertRaisesRegex(ValueError, "common mission content differs"):
            self.service.preview(self.request)

    def test_crash_while_holding_original_world_restores_original(self):
        """Recovery detects a rename before its subsequent journal flags are written."""
        self.profiles.save(record().values, None)
        (self.mission / "storage_7" / "players.db").write_bytes(b"current original")
        before = inventory(self.mission)
        self.request["storage_policy"] = "replace_existing"
        parameters = self.parameters()
        parameters["overwrite_confirmation"] = {"preview_fingerprint": parameters["preview_fingerprint"], "affected_profile_ids": ["main"]}
        self.storage.hook = lambda phase: (_ for _ in ()).throw(KeyboardInterrupt()) if phase == "HELD_MISSION" else None
        with self.assertRaises(KeyboardInterrupt):
            self.service.apply(parameters, "heldcrash", lambda *_: None)
        self.storage.hook = lambda _: None
        self.assertFalse(self.storage.inspect(self.dayz)["blocked"])
        self.assertEqual(inventory(self.mission), before)

    def test_insufficient_space_and_preparation_cancellation_publish_nothing(self):
        """Space failures and declared cancellation leave original data unchanged."""
        from dayz_serverman.application.operations.models import OperationCancelled
        parameters = self.parameters()
        self.storage.disk_usage = lambda _: SimpleNamespace(free=0)
        with self.assertRaises(OSError):
            self.service.apply(parameters, "space", lambda *_: None)
        self.storage.disk_usage = shutil.disk_usage
        def cancel(phase, _progress):
            """Cancel only at the fully prepared safe point."""
            if phase == "PREPARED":
                raise OperationCancelled("cancel")
        with self.assertRaises(OperationCancelled):
            self.service.apply(parameters, "cancel", cancel)
        self.assertEqual(inventory(self.mission), self.original_inventory)
        self.assertFalse((self.dayz / "serverman" / "restored").exists())

    def test_duplicate_operation_returns_original_commit(self):
        """A replay of the same operation cannot create another destination or record."""
        parameters = self.parameters()
        first = self.service.apply(parameters, "duplicate", lambda *_: None)
        self.assertEqual(self.service.apply(parameters, "duplicate", lambda *_: None), first)
        self.assertEqual(len(self.profiles.list()), 1)

    def test_failed_commit_write_is_compensated(self):
        """An in-memory commit phase is never mistaken for a durable commit."""
        from unittest.mock import patch
        write = self.journals.write
        def fail_commit(record):
            """Reject only the durable commit write, leaving compensation available."""
            if record["phase"] == "COMMITTED":
                raise OSError("commit not persisted")
            write(record)
        with patch.object(self.journals, "write", side_effect=fail_commit), self.assertRaises(OSError):
            self.service.apply(self.parameters(), "badcommit", lambda *_: None)
        self.assertEqual(self.journals.records()[0]["phase"], "ROLLED_BACK")
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")

    def test_absent_selected_world_remains_absent_after_restore(self):
        """Presence metadata distinguishes no world from an existing empty world."""
        shutil.rmtree(self.mission / "storage_7")
        self.profiles.save(record().values, None)
        backup = BackupService(self.profiles, self.settings, self.backups).create("main", 0, 4, lambda *_: None)
        self.backup_id = backup["backup_id"]
        self.request["backup_id"] = self.backup_id
        self.profiles.delete("main", 0)
        result = self.service.apply(self.parameters(), "absentworld", lambda *_: None)
        mission = self.dayz.joinpath(*result["mission_root"].split("\\"))
        self.assertFalse((mission / f"storage_{result['instance_id']}").exists())

    def test_confirmed_replacement_can_restore_absent_world(self):
        """An absent archived world removes the selected tree with recovery, preserving other worlds."""
        shutil.rmtree(self.mission / "storage_7")
        self.profiles.save(record().values, None)
        backup = BackupService(self.profiles, self.settings, self.backups).create("main", 0, 4, lambda *_: None)
        self.request["backup_id"] = backup["backup_id"]
        (self.mission / "storage_7").mkdir()
        (self.mission / "storage_7" / "current.bin").write_bytes(b"current")
        before = inventory(self.mission / "storage_7")
        self.request["storage_policy"] = "replace_existing"
        parameters = self.parameters()
        parameters["overwrite_confirmation"] = {"preview_fingerprint": parameters["preview_fingerprint"], "affected_profile_ids": ["main"]}
        result = self.service.apply(parameters, "absentreplace", lambda *_: None)
        self.assertFalse((self.mission / "storage_7").exists())
        self.assertEqual(inventory(Path(result["recovery_copy"])), before)
        self.assertTrue((self.mission / "storage_8" / "unrelated.bin").exists())
