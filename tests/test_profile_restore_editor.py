"""The fourth group of the profile-restore journal: staging, publication, verification, rollback and recovery.

Each test restores profile main of a schema 3 backup as profile `restored`, with fixture editor records (10.11).
"""

from __future__ import annotations

import unittest

from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.profile_restore_editor_fixtures import (
    AREA, MISSION_KEY, PHASES, SUPERSEDE_KEY, VANILLA, Crash, ProfileEditorFixture, sha,
)


class EditorGroupTests(ProfileEditorFixture):
    """The group commits with the profile record and rolls back with it."""

    def test_the_group_publishes_each_record_with_the_profile(self) -> None:
        files = self.files("editor-1")
        before, seen = self.tree(), {}
        plan = self.area / "profiles" / "restored" / MISSION_KEY / "plan.json"
        # The editor records are published before the profile record
        self.storage.hook = lambda actual: seen.update({actual: (plan.is_file(), self.profile_exists())})
        self.apply("editor-1", files)
        self.assertEqual((seen["FILES_PUBLISHED"], seen["PUBLISHED_PROFILE"]), ((True, False), (True, True)))
        tree = self.tree()
        for file in files:
            self.assertEqual(tree["/".join(file.relative.split("/")[1:])], file.data, file.relative)
        # The superseded record keeps the replaced original, and no other file changed
        self.assertIn(f"mission-map/originals/{SUPERSEDE_KEY}/superseded-editor-1.json", tree)
        new = {"/".join(file.relative.split("/")[1:]) for file in files}
        self.assertEqual({path: data for path, data in tree.items() if path not in new},
                         {path: data for path, data in before.items() if path not in new})
        journal = self.journal("editor-1")
        self.assertEqual((journal["version"], len(journal["groups"]), journal["phase"]), (2, 4, "COMMITTED"))
        group = journal["groups"][3]
        self.assertTrue(all(item["published"] for item in group["files"]))
        self.assertEqual([item["relative"] for item in group["files"] if item["before"] is not None],
                         [f"{AREA}originals/{SUPERSEDE_KEY}/original.json"])
        # Content that already existed with its bytes is not part of the group
        self.assertNotIn(f"{AREA}originals/{SUPERSEDE_KEY}/files/{sha(VANILLA)}",
                         [item["relative"] for item in group["files"]])
        self.assertTrue(journal["cleanup_complete"])
        self.assertFalse(any(".restore-holding" in path or ".stage" in path for path in tree))
        self.assertEqual(self.profiles.read("restored").revision, 0)

    def test_a_failure_at_each_phase_rolls_the_group_back(self) -> None:
        before, folders = self.tree(), self.folders()
        for index, phase in enumerate(PHASES[1:]):
            def fail(actual: str, phase: str = phase) -> None:
                """Raise one ordinary failure at the selected boundary."""
                if actual == phase:
                    raise OSError("injected")
            self.storage.hook = fail
            with self.subTest(phase=phase):
                with self.assertRaises(OSError):
                    self.apply(f"fault-{index}")
                self.assertEqual(self.journal(f"fault-{index}")["phase"], "ROLLED_BACK")
                self.assertEqual(self.tree(), before)
                self.assertEqual(self.folders(), folders)
                with self.assertRaises(ProfileNotFound):
                    self.profiles.read("restored")

    def test_an_interruption_at_each_phase_is_rolled_back_by_recovery(self) -> None:
        before, folders = self.tree(), self.folders()
        for index, phase in enumerate(PHASES):
            def crash(actual: str, phase: str = phase) -> None:
                """End the process at the selected boundary, after its journal write."""
                if actual == phase:
                    raise Crash()
            self.storage.hook = crash
            with self.subTest(phase=phase):
                with self.assertRaises(Crash):
                    self.apply(f"crash-{index}")
                self.assertEqual(self.journal(f"crash-{index}")["version"], 2)
                self.assertFalse(self.new_storage().inspect(self.dayz)["blocked"])
                self.assertEqual(self.journal(f"crash-{index}")["phase"], "ROLLED_BACK")
                self.assertEqual(self.tree(), before)
                self.assertEqual(self.folders(), folders)
                with self.assertRaises(ProfileNotFound):
                    self.profiles.read("restored")

    def test_an_interruption_after_the_commit_is_completed_by_recovery(self) -> None:
        files = self.files("late")
        self.storage.hook = lambda actual: (_ for _ in ()).throw(Crash()) if actual == "COMMITTED" else None
        with self.assertRaises(Crash):
            self.apply("late", files)
        holding = f"mission-map/originals/{SUPERSEDE_KEY}/.original.json.late.restore-holding"
        self.assertIn(holding, self.tree())
        self.assertFalse(self.new_storage().inspect(self.dayz)["blocked"])
        tree = self.tree()
        self.assertNotIn(holding, tree)
        self.assertEqual(tree[f"mission-map/originals/{SUPERSEDE_KEY}/original.json"], files[-3].data)
        self.assertTrue(self.journal("late")["cleanup_complete"])
        self.assertEqual(self.profiles.read("restored").revision, 0)

    def test_a_changed_editor_file_blocks_recovery(self) -> None:
        self.storage.hook = lambda actual: (_ for _ in ()).throw(Crash()) if actual == "PUBLISHED_EDITOR" else None
        with self.assertRaises(Crash):
            self.apply("changed")
        plan = self.area / "profiles" / "restored" / MISSION_KEY / "plan.json"
        plan.write_bytes(b"an autosave of another process")
        self.assertTrue(self.new_storage().inspect(self.dayz)["blocked"])
        self.assertEqual(plan.read_bytes(), b"an autosave of another process")

    def test_recovery_without_the_manager_layout_blocks_and_changes_nothing(self) -> None:
        self.storage.hook = lambda actual: (_ for _ in ()).throw(Crash()) if actual == "PUBLISHED_EDITOR" else None
        with self.assertRaises(Crash):
            self.apply("layout")
        tree = self.tree()
        self.assertTrue(self.new_storage(manager=False).inspect(self.dayz)["blocked"])
        self.assertEqual(self.tree(), tree)
        self.assertEqual(self.journal("layout")["phase"], "PUBLISHING")
        # The storage with the layout then rolls the restore back
        self.assertFalse(self.new_storage().inspect(self.dayz)["blocked"])
        self.assertNotIn(f"mission-map/profiles/restored/{MISSION_KEY}/plan.json", self.tree())

    def test_verification_refuses_a_changed_record_after_the_commit(self) -> None:
        self.apply("verified")
        record = self.journals.records()[0]
        self.storage.verify_committed(record, self.dayz)
        next((self.area / "ledger" / MISSION_KEY).iterdir()).write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.storage.verify_committed(record, self.dayz)

    def test_a_target_that_appears_before_staging_is_never_removed(self) -> None:
        plan = self.area / "profiles" / "restored" / MISSION_KEY / "plan.json"

        def appear(actual: str) -> None:
            """Let another writer create a target after the journal starts."""
            if actual == "PLANNED":
                plan.parent.mkdir(parents=True)
                plan.write_bytes(b"other writer")
        self.storage.hook = appear
        with self.assertRaises(RuntimeError):
            self.apply("appeared")
        self.assertEqual(plan.read_bytes(), b"other writer")
        self.assertEqual(self.journal("appeared")["phase"], "RECOVERY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
