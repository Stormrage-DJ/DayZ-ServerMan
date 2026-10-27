"""An interrupted same-profile restore with editor groups: rollback, startup recovery and journal paths (10.10)."""

from __future__ import annotations

import json
import shutil
import unittest
from dataclasses import replace

from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.restore_editor_targets import editor_paths_safe
from dayz_serverman.repositories.restore_journal import RestoreJournalError
from dayz_serverman.repositories.restore_storage import RestoreStorage, RestoreStorageError
from tests.restore_editor_fixtures import APPLIED, BANDITS, VANILLA, RestoreEditorFixture


class InterruptedRestoreTests(RestoreEditorFixture):
    """A fault at each group leaves the DayZ files and every editor file as they were."""

    def prepare(self) -> tuple[str, dict[str, bytes]]:
        """Back up, change the state that a restore overwrites, and return the backup and the editor tree.

        The restore then writes content, creates records, replaces the plan and removes applied.json.
        """
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records(after=VANILLA)
        backup = self.backup()
        self.save_plan(0, 25)
        shutil.rmtree(self.area / "originals" / self.runtime_key)
        self.write_mission(VANILLA)
        return backup, self.tree()

    def assert_unchanged(self, before: dict[str, bytes]) -> None:
        """Assert the editor area and the managed mission file hold their state before the restore."""
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), VANILLA)
        self.assertFalse(any(self.area.rglob("*.restore-stage")))

    def test_a_fault_at_each_editor_group_rolls_the_whole_restore_back(self) -> None:
        backup, before = self.prepare()
        journal = self.dry_journal(backup)
        editor = [index for index, group in enumerate(journal.groups) if group.target_class == "manager"]
        self.assertTrue(any(not journal.groups[index].new_exists for index in editor))
        for index in editor:
            with self.subTest(group=journal.groups[index].entry_path):
                def fault(phase: str, at: int, stop: int = index) -> None:
                    """Fail after the publication of one editor group."""
                    if phase == "AFTER_PUBLISH" and at == stop:
                        raise OSError("synthetic interruption")
                operation = f"fault-{index}"
                with self.assertRaisesRegex(RestoreStorageError, "prior files were restored"):
                    self.restore(backup, operation, self.restore_service(RestoreStorage(fault_hook=fault)))
                self.assert_unchanged(before)
                self.assertEqual(self.journals.load(self.journals.retired_path_for(operation)).phase, "ROLLED_BACK")

    def test_startup_recovery_rolls_back_an_interrupted_restore(self) -> None:
        backup, before = self.prepare()
        last = len(self.dry_journal(backup).groups) - 1

        def crash(phase: str, index: int) -> None:
            """Stop after the last group and before any compensation, as a process end does."""
            if (phase == "AFTER_PUBLISH" and index == last) or phase == "BEFORE_COMPENSATE":
                raise OSError("synthetic crash")
        with self.assertRaises(RestoreStorageError) as raised:
            self.restore(backup, "crash", self.restore_service(RestoreStorage(fault_hook=crash)))
        self.assertTrue(raised.exception.recovery_required)
        self.assertNotEqual(self.tree(), before)
        # Without the manager layout the editor groups cannot be checked, so recovery blocks without a write
        blocked = RestoreStorage().inspect(self.journals, self.dayz, self.paths.backup_recovery)
        self.assertTrue(blocked["blocked"])
        result = self.restore_service().inspect_recovery()
        self.assertFalse(result["blocked"])
        self.assert_unchanged(before)
        self.assertEqual(self.journals.load(self.journals.retired_path_for("crash")).phase, "ROLLED_BACK")

    def test_the_association_lock_is_held_while_editor_groups_publish(self) -> None:
        self.write_all()
        backup = self.backup()
        self.clear_area()
        held: list[bool] = []
        lock = self.locks.lock_for("main", self.mission_key)
        storage = RestoreStorage(fault_hook=lambda phase, _index: held.append(lock.locked())
                                 if phase == "BEFORE_PUBLISH" else None)
        self.restore(backup, service=self.restore_service(storage))
        self.assertTrue(held and all(held))
        self.assertFalse(lock.locked())
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)

    def test_a_save_at_the_journal_checkpoint_is_refused_under_the_lock(self) -> None:
        self.write_all()
        backup = self.backup()
        before = self.tree()

        def autosave(phase: str, _percent: int) -> None:
            """Save while the writer side is held, just before the association lock."""
            if phase == "WRITE_JOURNAL":
                self.save_plan(0, 50)
        with self.assertRaisesRegex(RevisionConflict, "stale"):
            self.restore(backup, checkpoint=autosave)
        self.assertEqual(set(self.tree()), set(before))
        self.assertFalse(self.journals.path_for("restore-1").exists())

    def test_journal_paths_of_editor_groups_are_derived_again(self) -> None:
        backup, _before = self.prepare()
        journal = self.dry_journal(backup)
        self.assertEqual(journal.schema_version, 4)
        self.assertTrue(editor_paths_safe(journal, self.paths, self.paths.backup_recovery))
        self.assertFalse(editor_paths_safe(journal, None, self.paths.backup_recovery))
        index = next(index for index, group in enumerate(journal.groups) if group.target_class == "manager")
        for change in ({"entry_path": "data/elsewhere/plan.json"}, {"staging_path": str(self.dayz / "x")},
                       {"recovery_path": str(self.dayz / "copy")}):
            tampered = replace(journal, groups=list(journal.groups))
            tampered.groups[index] = replace(journal.groups[index], **change)
            self.assertFalse(editor_paths_safe(tampered, self.paths, self.paths.backup_recovery), change)
        # A schema 4 group of the DayZ root never ends absent
        raw = json.loads(json.dumps(journal.to_dict()))
        dayz = next(group for group in raw["groups"] if group["target_class"] == "dayz")
        dayz.update(new_exists=False, new_digest=None)
        path = self.journals.path_for("tampered")
        raw["operation_id"] = "tampered"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RestoreJournalError):
            self.journals.load(path)

    def dry_journal(self, backup: str):
        """Return the PREPARED journal that a restore of the backup writes, by stopping it at its first group."""
        def stop(phase: str, _index: int) -> None:
            """Fail before the first group publishes, so nothing changes."""
            if phase == "BEFORE_PUBLISH":
                raise OSError("stop before publication")
        with self.assertRaises(RestoreStorageError):
            self.restore(backup, "dry", self.restore_service(RestoreStorage(fault_hook=stop)))
        journal = self.journals.load(self.journals.retired_path_for("dry"))
        self.journals.retired_path_for("dry").unlink()
        for group in journal.groups:
            group.state = "READY"
        journal.phase, journal.publication_started, journal.resolved, journal.result = "PREPARED", False, False, None
        return journal


if __name__ == "__main__":
    unittest.main()
