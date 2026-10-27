"""Restore previous state after a coordinated medical toggle, and the toggle after it (8.13 with the 8.8 restore)."""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_restore import MissionMapRestore
from dayz_serverman.application.mission_map_restore_review import RestoreReader
from dayz_serverman.domain.medical_editor_texts import MEDICAL_KIND
from dayz_serverman.repositories.medical_zone_fragments import zones_enabled
from tests.medical_editor_support import PROTOTYPE, ToggleFixture
from tests.mission_map_mapgroup_support import zone


class RestoreAfterToggleTests(ToggleFixture):
    """The restore point of the toggle record is the state before the toggle (R16)."""

    def setUp(self) -> None:
        """Apply with medical off and build the 8.8 restore service over the fixture's commit step."""
        super().setUp()
        self.applied = self.applied_with_medical()
        reader = RestoreReader(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path)
        self.restore = MissionMapRestore(reader, self.commit, self.paths, self.profiles)
        self.restores = 0

    def restore_previous(self) -> dict:
        """Review and run Restore previous state of the main profile."""
        self.restores += 1
        fingerprint = self.restore.preview("main", "previous")["fingerprint"]
        return self.restore.restore("main", "previous", fingerprint, operation_id=f"restore{self.restores:04d}")

    def test_restore_previous_returns_the_state_before_the_toggle(self) -> None:
        """After the toggle, Restore previous state writes the bytes before it; the next toggle works again."""
        realization = self.applied_record()[0]["generated_groups"]
        self.run_toggle(True)
        self.assertEqual(self.applied_record()[0]["operation_kind"], MEDICAL_KIND)
        self.restore_previous()
        self.assertEqual(self.proto(), self.applied)
        record = self.applied_record()[0]
        self.assertEqual((record["operation_kind"], record["generated_groups"]), ("restore_previous", realization))
        # Known realization: the toggle after the restore keeps clone parity
        self.run_toggle(True)
        self.assertTrue(zones_enabled(self.proto(), PROTOTYPE))
        self.run_toggle(False)
        self.assertEqual(self.proto(), self.applied)

    def test_toggle_after_restore_of_a_first_apply(self) -> None:
        """After two Applies, Restore previous state gives the first state; its realization is known and toggles."""
        self.save([zone("very_rich")], 50, expected=0)
        self.run_apply()
        self.restore_previous()
        self.assertEqual(self.proto(), self.applied)
        self.run_toggle(True)
        self.run_toggle(False)
        self.assertEqual(self.proto(), self.applied)


if __name__ == "__main__":
    unittest.main()
