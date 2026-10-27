"""The medical loot-zone toggle on an editor-managed mission (leaf 8.13): clone parity, records and restore point."""

from __future__ import annotations

import unittest
from typing import Any

from dayz_serverman.application.medical_features import NO_ZONE_CHANGE
from dayz_serverman.domain.medical_editor_texts import MEDICAL_KIND, MISMATCH_TEXT, ZoneToggleRefused
from dayz_serverman.repositories.medical_zone_fragments import zones_enabled
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_owned import WRITABLE_MISSION_FILES
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.medical_editor_support import PROTO, PROTOTYPE, ToggleFixture
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_preview_support import sha

# The hospital source keeps only its Medic usage when medical loot zones are on
TOWN = b'<usage name="Town" />'


class ToggleAfterApplyTests(ToggleFixture):
    """Medical on and off after an Apply, with clone parity; capacity, names and other files stay."""

    def setUp(self) -> None:
        """Apply a capacity plan with medical off."""
        super().setUp()
        self.applied = self.applied_with_medical()
        self.apply_record = self.applied_record()[0]

    def test_on_and_off_keep_clone_parity(self) -> None:
        """Every generated group follows its source; a fresh Apply review then changes nothing."""
        others, ceilings, names = self.other_files(), self.ceilings(), self.apply_record["generated_groups"]
        preview = self.zone_preview(True)
        self.assertEqual(preview["changed_fields"], ["medical_loot_zones"])
        self.assertRegex(preview["preview_fingerprint"], "^[0-9a-f]{64}$")
        result = self.run_toggle(True)
        self.assertEqual((result["changed_fields"], result["digest"]), (["medical_loot_zones"], sha(self.proto())))
        self.assertEqual(result["digest"], preview["proposed_digest"])
        self.assertTrue(zones_enabled(self.proto(), PROTOTYPE))
        hospital = next(group for group in self.generated() if b'<usage name="Medic" />' in group)
        self.assertNotIn(TOWN, hospital)
        self.assertEqual((self.other_files(), self.ceilings()), (others, ceilings))
        answer = self.review().answer
        self.assertEqual((answer["affected_files"], answer["external_changes"], answer["coordinated_changes"]),
                         ([], [], []))
        self.assertEqual(self.applied_record()[0]["generated_groups"], names)
        self.run_toggle(False)
        self.assertEqual(self.proto(), self.applied)
        self.assertEqual(self.review().answer["affected_files"], [])

    def test_records_of_the_toggle(self) -> None:
        """The record keeps the applied plan and realization; files before and the ledger name the change."""
        before = self.proto()
        self.run_toggle(True, operation_id="medical0001")
        record, revision = self.applied_record()
        self.assertEqual((record["operation_id"], record["operation_kind"], revision), ("medical0001", MEDICAL_KIND, 1))
        for name in ("plan_id", "plan_revision", "configuration_fingerprint", "global_abundance", "plan_objects",
                     "excluded_encounters", "baseline_revision", "baseline_manifest_sha256", "mission_root"):
            self.assertEqual(record[name], self.apply_record[name], name)
        self.assertEqual(record["generated_groups"], self.apply_record["generated_groups"])
        self.assertEqual(record["generated_groups_before"], self.apply_record["generated_groups"])
        self.assertEqual([(item["path"], item["sha256"], item["size"]) for item in record["files_before"]],
                         [(PROTO, sha(before), len(before))])
        store = AssociationRecordStore(self.area, "main", self.key(), "applied")
        self.assertEqual(store.contents.read(sha(before)), before)
        after = {item["path"]: item for item in record["files_after"]}
        old = {item["path"]: item for item in self.apply_record["files_after"]}
        self.assertEqual(after[PROTO]["sha256"], sha(self.proto()))
        self.assertNotEqual(after[PROTO]["owned_sha256"], old[PROTO]["owned_sha256"])
        self.assertEqual({path: after[path] for path in WRITABLE_MISSION_FILES if path != PROTO},
                         {path: old[path] for path in WRITABLE_MISSION_FILES if path != PROTO})
        entry = LedgerStore(self.area).read(self.key(), "medical0001")
        self.assertEqual((entry["operation_kind"], entry["output"], [item["path"] for item in entry["files"]]),
                         (MEDICAL_KIND, True, [PROTO]))
        self.assertEqual((entry["files"][0]["before_sha256"], entry["files"][0]["after_sha256"]),
                         (sha(before), sha(self.proto())))
        # The working baseline and the original stay as they were
        self.assertEqual(self.records()[1][1], 0)

    def test_restore_point_returns_the_state_before_the_toggle(self) -> None:
        """Writing the files before of the toggle record gives the bytes before the toggle (8.8 restore point)."""
        before = self.proto()
        self.run_toggle(True)
        record = self.applied_record()[0]
        store = AssociationRecordStore(self.area, "main", self.key(), "applied")
        restored = {item["path"]: store.contents.read(item["sha256"]) for item in record["files_before"]}
        self.assertEqual(restored, {PROTO: before})

    def test_draft_isolation(self) -> None:
        """A saved draft with other capacity is never applied by a toggle; its review still lists the change."""
        self.save([zone("very_rich")], 50, expected=0)
        ceilings = self.ceilings()
        self.run_toggle(True)
        self.assertEqual(self.ceilings(), ceilings)
        record = self.applied_record()[0]
        self.assertEqual((record["plan_revision"], record["global_abundance"]), (0, 25))
        answer = self.review().answer
        self.assertIn(PROTO, [item["path"] for item in answer["affected_files"]])
        self.assertEqual((answer["external_changes"], answer["coordinated_changes"]), ([], []))
        self.assertTrue(zones_enabled(self.review().candidates.contents()[PROTO], PROTOTYPE))

    def test_apply_after_a_toggle_keeps_the_state_and_its_restore_point(self) -> None:
        """An unchanged Apply after the toggle writes no file; it keeps the toggle's restore point."""
        self.run_toggle(True)
        toggled, record = self.proto(), self.applied_record()[0]
        self.run_apply()
        self.assertEqual(self.proto(), toggled)
        after = self.applied_record()[0]
        self.assertEqual((after["operation_kind"], after["files_before"], after["generated_groups_before"]),
                         ("apply", record["files_before"], record["generated_groups_before"]))


class ToggleLifecycleTests(ToggleFixture):
    """The toggle before an Apply, a first Enable without a baseline, a toggle without a change."""

    def test_ordinary_toggle_then_apply_then_coordinated_toggle(self) -> None:
        """Before any Apply the six-field toggle stays; after the Apply the coordinated toggle keeps parity."""
        self.set_medical_baseline()
        preview = self.zone_preview(True)
        self.assertNotIn("preview_fingerprint", preview)
        self.medical.apply(*self.request(True), lambda *_: None)
        self.save([zone("rich")], 25)
        self.run_apply()
        hospital = next(group for group in self.generated() if b'<usage name="Medic" />' in group)
        self.assertNotIn(TOWN, hospital)
        self.run_toggle(False)
        self.assertFalse(zones_enabled(self.proto(), PROTOTYPE))
        self.assertTrue(all(TOWN in group or b"Farm" in group for group in self.generated()))
        self.assertEqual(self.review().answer["affected_files"], [])

    def test_first_enable_stores_the_baseline_inside_the_guard(self) -> None:
        """Without a stored baseline the current file is the off state; the commit stores it once."""
        self.save([zone("rich")], 25)
        self.run_apply()
        applied = self.proto()
        self.assertFalse(self.medical_baseline_path().exists())
        self.events.clear()
        self.run_toggle(True)
        self.assertEqual(self.medical_baseline_path().read_bytes(), applied)
        self.assertEqual(self.events[:2], ["mutex", "stopped"])
        self.run_toggle(False)
        self.assertEqual(self.proto(), applied)

    def test_toggle_without_a_changing_group(self) -> None:
        """No qualified group: the review has a fingerprint and the no-change sentence; Apply writes nothing."""
        self.write(PROTO, PROTOTYPE.replace(b'<usage name="Medic" />', b'<usage name="Village" />'))
        self.save([zone("rich")], 25)
        self.run_apply()
        snapshot = self.snapshot()
        preview = self.zone_preview(False)
        self.assertEqual((preview["changed_fields"], preview["message"]), ([], NO_ZONE_CHANGE))
        self.assertIn("preview_fingerprint", preview)
        result = self.run_toggle(False)
        self.assertEqual((result["changed_fields"], result["message"]), ([], NO_ZONE_CHANGE))
        self.assertEqual(self.snapshot(), snapshot)


class ToggleAfterRestoreTests(ToggleFixture):
    """The toggle after Restore previous state, with a known and with a null realization."""

    def setUp(self) -> None:
        """Apply plan A, then plan B, with medical off."""
        super().setUp()
        self.first = self.applied_with_medical()
        self.first_groups = self.applied_record()[0]["generated_groups"]
        self.save([zone("very_rich")], 50, expected=0)
        self.run_apply()

    def restore_previous(self, groups: Any) -> None:
        """Write the restore point of the current record and a restore record with the realization of its bytes."""
        record, revision = self.applied_record()
        store = AssociationRecordStore(self.area, "main", self.key(), "applied")
        for item in record["files_before"]:
            self.write(item["path"], store.contents.read(item["sha256"]) if item["existed"] else None)
        after = [{"target_key": self.key(), "path": path, "existed": data is not None, "sha256": sha(data),
                  "owned_sha256": self.owned(path, data)}
                 for path, data in ((path, self.current(path)) for path in WRITABLE_MISSION_FILES)]
        fields = {name: value for name, value in record.items() if name != "manifest_sha256"}
        fields.update(operation_id="restore0001", operation_kind="restore_previous", plan_id=None,
                      plan_revision=None, configuration_fingerprint=None, global_abundance=None, plan_objects=None,
                      files_after=after, files_before=[], generated_groups=groups,
                      generated_groups_before=record["generated_groups"])
        store.save(fields, {}, revision)

    def current(self, path: str) -> bytes | None:
        """Return the bytes of one mission file, or None."""
        target = self.mission / path
        return target.read_bytes() if target.exists() else None

    def test_known_realization(self) -> None:
        """The restored bytes toggle on and off; the toggle record keeps the restore record's null plan."""
        self.restore_previous(self.first_groups)
        self.assertEqual(self.proto(), self.first)
        self.run_toggle(True)
        self.assertTrue(zones_enabled(self.proto(), PROTOTYPE))
        record = self.applied_record()[0]
        self.assertEqual((record["operation_kind"], record["plan_id"], record["global_abundance"]),
                         (MEDICAL_KIND, None, None))
        self.assertEqual((record["generated_groups"], record["generated_groups_before"]),
                         (self.first_groups, self.first_groups))
        self.run_toggle(False)
        self.assertEqual(self.proto(), self.first)

    def test_null_realization_refuses(self) -> None:
        """A restore record whose realization is unknown refuses with the R17 reason and writes nothing."""
        self.restore_previous(None)
        snapshot = self.snapshot()
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.zone_preview(True)
        self.assertEqual((caught.exception.reason, str(caught.exception)), ("realization_unknown", MISMATCH_TEXT))
        with self.assertRaises(ZoneToggleRefused):
            self.run_toggle(True, fingerprint="a" * 64)
        self.assertEqual(self.snapshot(), snapshot)

    def test_wrong_known_realization_refuses(self) -> None:
        """A realization that names other generated groups than the restored file refuses."""
        self.restore_previous(self.applied_record()[0]["generated_groups"])
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.zone_preview(True)
        self.assertEqual(caught.exception.reason, "generated_names_differ")


if __name__ == "__main__":
    unittest.main()
