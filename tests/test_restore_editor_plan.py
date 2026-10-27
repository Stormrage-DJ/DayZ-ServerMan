"""The plan of a same-profile restore: set aside (R15), a revision that never repeats (AR-7) and stale previews
(D19, leaf 10.10)."""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_layout import set_aside_name
from dayz_serverman.repositories.mission_map_preview_base import read_records
from tests.backup_editor_fixtures import rewrite_archive
from tests.restore_editor_fixtures import MOMENT, RestoreEditorFixture, sha


class RestoredPlanTests(RestoreEditorFixture):
    """The archived plan, the current plan and the preview that binds both."""

    def test_a_current_invalid_plan_is_kept_under_a_set_aside_name(self) -> None:
        self.write_all()
        backup = self.backup()
        damaged = b"{not a plan"
        (self.folder() / "plan.json").write_bytes(damaged)
        self.restore(backup)
        # Invalid saved data is never overwritten: it moves to the dated name in the same journal (R15)
        self.assertEqual((self.folder() / set_aside_name(MOMENT)).read_bytes(), damaged)
        loaded = self.plans.load("main")
        self.assertEqual((loaded["state"], loaded["revision"]), ("VALID", 1))

    def test_the_plan_revision_never_repeats(self) -> None:
        self.write_all()
        backup = self.backup()
        archived = self.envelope(self.folder() / "plan.json")
        # A current revision above the archived one: the next one after the current
        for revision in range(3):
            self.save_plan(revision)
        self.restore(backup)
        restored = self.envelope(self.folder() / "plan.json")
        self.assertEqual(restored["revision"], 4)
        self.assertEqual(restored["plan"], archived["plan"])
        # No current plan: the next one after the archived revision
        (self.folder() / "plan.json").unlink()
        self.restore(backup, "restore-2")
        self.assertEqual(self.envelope(self.folder() / "plan.json")["revision"], 1)

    def test_an_invalid_archived_plan_installs_as_stored(self) -> None:
        self.write_all()
        backup = self.backup()
        stored = json.dumps({"schema_version": 1, "revision": 7, "plan": {"format": "other"}}).encode("utf-8")

        def damage(manifest: dict, members: dict) -> None:
            """Replace the archived plan with invalid saved data."""
            members["editor/association/plan.json"] = stored
            for entry in manifest["entries"]:
                if entry["path"] == "editor/association/plan.json":
                    entry.update(sha256=sha(stored), size=len(stored))
        rewrite_archive(self.paths.backups / f"{backup}.zip", damage)
        self.restore(backup)
        self.assertEqual((self.folder() / "plan.json").read_bytes(), stored)
        self.assertEqual(self.plans.load("main")["state"], "INVALID_PLAN")

    def test_a_plan_save_after_the_preview_makes_it_stale(self) -> None:
        self.write_all()
        backup = self.backup()
        service = self.restore_service()
        preview = service.preview("main", backup)
        before = self.tree()
        self.save_plan(0, 25)
        manifest = service._backups.verified_manifest(self.paths.backups, backup, "main")
        with self.assertRaisesRegex(RevisionConflict, "stale"):
            service.apply("main", backup, 3, 4, manifest.manifest_digest, preview["fingerprint"], "restore-1",
                          lambda _phase, _percent: None)
        self.assertEqual(set(self.tree()) - set(before), set())

    def test_a_save_before_the_journal_is_refused_under_the_association_lock(self) -> None:
        self.write_all()
        backup = self.backup()
        self.write_mission(b"<areaflags>edited</areaflags>")

        def autosave(phase: str, _percent: int) -> None:
            """Save the plan after the review read and before the writer side, as a window autosave can."""
            if phase == "PREPARE_RECOVERY":
                self.save_plan(0, 25)
        with self.assertRaisesRegex(RevisionConflict, "stale"):
            self.restore(backup, checkpoint=autosave)
        # Nothing was kept: the DayZ file, the saved plan and the records stay as they were
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), b"<areaflags>edited</areaflags>")
        self.assertEqual(self.envelope(self.folder() / "plan.json")["revision"], 1)
        self.assertFalse(self.journals.path_for("restore-1").exists())
        self.assertFalse(any(self.area.rglob("*.restore-stage")))

    def test_the_apply_preview_inputs_change_with_the_restored_records(self) -> None:
        self.write_all()
        backup = self.backup()
        self.clear_area()
        self.save_plan()
        before = read_records(self.area, "main", self.mission_key).identities()
        self.restore(backup)
        after = read_records(self.area, "main", self.mission_key).identities()
        # The 8.6 preview binds these identities, so an Apply preview made before the restore is stale
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
