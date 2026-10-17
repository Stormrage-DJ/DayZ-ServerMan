"""After a restore that invalidated the last-applied record: adoption_required, Apply refused, the plan editable
(AR-6, R20, D19, leaf 10.10)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dayz_serverman.application.mission_map_coordinator_errors import translate
from dayz_serverman.application.mission_map_plans import ADOPTION_REQUIRED_TEXT, AdoptionRequired
from dayz_serverman.application.mission_map_preview import MissionMapPreview
from dayz_serverman.application.mission_map_restore_review import RestoreReader
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.repositories.mission_map_layout import invalidated_name
from tests.restore_editor_fixtures import APPLIED, BANDITS, MOMENT, VANILLA, RestoreEditorFixture, sha


class AdoptionRequiredTests(RestoreEditorFixture):
    """An old archive followed by the `adoption_required` refusal of Apply."""

    def restore_old_archive(self) -> None:
        """Back up at schema 3 before the first Apply, apply, then restore the old archive."""
        self.save_plan()
        self.write_mission(VANILLA)
        backup = self.backup(editor=False)
        self.write_mission(APPLIED)
        self.write_original(self.mission_key)
        self.write_records()
        self.restore(backup)

    def test_load_reports_adoption_required_and_the_plan_stays_editable(self) -> None:
        self.restore_old_archive()
        loaded = self.plans.load("main")
        self.assertEqual((loaded["read_only"], loaded["read_only_reason"], loaded["state"]),
                         (False, "adoption_required", "VALID"))
        # R20 blocks only editor application: a plan save still works
        saved = self.save_plan(loaded["revision"])
        self.assertEqual(saved["revision"], loaded["revision"] + 1)
        self.assertEqual(self.plans.load("main")["read_only_reason"], "adoption_required")

    def test_apply_refuses_with_exit_3_and_the_adopt_6_text(self) -> None:
        self.restore_old_archive()
        preview = MissionMapPreview(self.plans, self.profiles, medical_baseline=lambda _pid, _mission: None)  # type: ignore[arg-type]
        with self.assertRaises(AdoptionRequired) as raised:
            preview.review("main")
        call = translate(raised.exception)
        self.assertEqual(call.code, ErrorCode.PROFILE_CONTEXT_MISMATCH)
        self.assertEqual(call.details["reason"], "adoption_required")
        self.assertEqual(call.safe_message, ADOPTION_REQUIRED_TEXT)
        self.assertEqual(ADOPTION_REQUIRED_TEXT, "A restore removed the last Apply record of this profile for this "
                                                 "mission. Adopt the mission before you apply.")

    def test_restore_previous_refuses_and_restore_original_stays_available(self) -> None:
        self.restore_old_archive()
        reader = RestoreReader(self.plans, self.profiles, medical_baseline=lambda _pid, _mission: None,  # type: ignore[arg-type]
                               capabilities=lambda _mission: SimpleNamespace(terrain_id="chernarusplus"))
        previous = reader.review("main", "previous").answer
        self.assertIn("no_previous_state", [item["reason"] for item in previous["refusals"]])
        original = reader.review("main", "original").answer
        self.assertNotIn("no_original", [item["reason"] for item in original["refusals"]])
        self.assertIsNotNone(original["original"])

    def test_only_an_invalidated_record_of_the_active_folder_requires_adoption(self) -> None:
        self.save_plan()
        self.assertIsNone(self.plans.load("main")["read_only_reason"])
        # A closed record under another dated name, as adoption leaves it, does not require adoption
        (self.folder() / "applied.closed-20261016T180000Z.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(self.plans.load("main")["read_only_reason"])
        (self.folder() / invalidated_name(MOMENT, 1)).write_text("{}", encoding="utf-8")
        self.assertEqual(self.plans.load("main")["read_only_reason"], "adoption_required")
        # Invalid saved data keeps its own way out, set aside
        (self.folder() / "plan.json").write_text("{", encoding="utf-8")
        self.assertIsNone(self.plans.load("main")["read_only_reason"])

    def test_a_carried_record_that_does_not_match_the_restored_files_is_invalidated(self) -> None:
        # The archived record names "files after" that differ from the archived mission file
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records(after=VANILLA)
        backup = self.backup()
        self.restore(backup)
        # The carried record and the current record both stay as dated history; no applied.json remains
        names = sorted(path.name for path in self.folder().glob("applied*"))
        self.assertEqual(names, [invalidated_name(MOMENT, 2), invalidated_name(MOMENT, 1)])
        archived = self.envelope(self.folder() / invalidated_name(MOMENT, 1))
        self.assertEqual(archived["files_after"][0]["sha256"], sha(VANILLA))
        # The ledger entry is added also for an invalidated carried record (D5)
        self.assertEqual(len(list((self.area / "ledger" / self.mission_key).glob("pr-*.json"))), 1)
        self.assertEqual(self.plans.load("main")["read_only_reason"], "adoption_required")
        self.assertEqual(self.restore_service().preview("main", backup)["editor"]["rows"]["applied_carried"], "invalidate")

if __name__ == "__main__":
    unittest.main()
