"""Refusals of the coordinated medical toggle (8.13): fingerprint, R17 mismatch, running server, other missions."""

from __future__ import annotations

import unittest

from dayz_serverman.application.medical_features import EDITOR_MANAGED
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState
from dayz_serverman.domain.medical_editor_texts import (
    FINGERPRINT_MISSING_TEXT, FINGERPRINT_STALE_TEXT, MISMATCH_TEXT, NO_APPLIED_PLAN_TEXT, ZoneReviewStale,
    ZoneToggleRefused,
)
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.medical_editor_support import PROTO, ZONES, ToggleFixture


class FingerprintTests(ToggleFixture):
    """Customer decision 4: an editor-managed zone Apply needs the exact reviewed fingerprint."""

    def setUp(self) -> None:
        """Apply a capacity plan with medical off."""
        super().setUp()
        self.applied_with_medical()

    def assert_unchanged(self, snapshot: tuple) -> None:
        """Assert that no DayZ or manager file changed and no journal stays."""
        self.assertEqual(self.snapshot(), snapshot)
        self.assertFalse(list(self.journals.root.glob("*.json")))

    def test_missing_and_malformed_fingerprint(self) -> None:
        """None, a short text and a non-text value refuse as a request without a write."""
        snapshot = self.snapshot()
        for value in (None, "abc", "A" * 64, 7):
            with self.subTest(value=value), self.assertRaises(ZoneToggleRefused) as caught:
                self.run_toggle(True, fingerprint=value)
            self.assertEqual((caught.exception.reason, str(caught.exception)),
                             ("fingerprint_missing", FINGERPRINT_MISSING_TEXT))
        self.assert_unchanged(snapshot)

    def test_stale_fingerprint(self) -> None:
        """A fingerprint of another review, or after a context change, is stale and retryable."""
        fingerprint = self.zone_preview(True)["preview_fingerprint"]
        snapshot = self.snapshot()
        self.events.clear()
        with self.assertRaises(ZoneReviewStale) as caught:
            self.run_toggle(True, fingerprint="0" * 64)
        self.assertEqual(str(caught.exception), FINGERPRINT_STALE_TEXT)
        # The lane review refuses before the installation guard is taken
        self.assertNotIn("mutex", self.events)
        self.assertIsInstance(caught.exception, RevisionConflict)
        # Another association on the mission key changes the reviewed context
        (self.area / "profiles" / "second" / self.key()).mkdir(parents=True)
        with self.assertRaises(ZoneReviewStale):
            self.run_toggle(True, fingerprint=fingerprint)
        self.assertEqual(self.snapshot()[0], snapshot[0])
        self.assertNotEqual(self.zone_preview(True)["preview_fingerprint"], fingerprint)

    def test_equal_contexts_share_a_fingerprint(self) -> None:
        """The comparison is stateless: two reviews of one context are equal, another request differs."""
        first, second = self.zone_preview(True), self.zone_preview(True)
        self.assertEqual(first["preview_fingerprint"], second["preview_fingerprint"])

    def test_change_between_review_and_guard_is_stale(self) -> None:
        """A context change after the lane review is found again under the guard; nothing is written."""
        snapshot = self.snapshot()
        status = self.ports.status

        def changed():
            """Add an association folder when the guard reads the server state."""
            (self.area / "profiles" / "second" / self.key()).mkdir(parents=True, exist_ok=True)
            return status()
        self.ports.status = changed
        with self.assertRaises(ZoneReviewStale):
            self.run_toggle(True)
        self.assertEqual(self.snapshot()[0], snapshot[0])
        self.assertFalse(list(self.journals.root.glob("*.json")))

    def test_running_server_refuses_with_the_apply_reason(self) -> None:
        """The guard refuses a running server before any write, as for Apply."""
        snapshot = self.snapshot()
        self.ports.state = ServerState.RUNNING_MANAGED
        with self.assertRaises(LifecycleFailure) as caught:
            self.run_toggle(True)
        self.assertIn("RUNNING_MANAGED", caught.exception.safe_message)
        self.assert_unchanged(snapshot)


class MismatchTests(ToggleFixture):
    """R17: an applied record that does not match the files refuses and asks for a new Apply."""

    def setUp(self) -> None:
        """Apply a capacity plan with medical off."""
        super().setUp()
        self.applied = self.applied_with_medical()

    def refused(self, reason: str) -> None:
        """Assert that preview and apply refuse with the reason and the R17 text, and write nothing."""
        snapshot = self.snapshot()
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.zone_preview(True)
        self.assertEqual((caught.exception.reason, str(caught.exception)), (reason, MISMATCH_TEXT))
        with self.assertRaises(ZoneToggleRefused):
            self.run_toggle(True, fingerprint="a" * 64)
        self.assertEqual(self.snapshot(), snapshot)

    def test_external_edit_of_any_writable_file(self) -> None:
        """An edit of mapgrouppos.xml, of a generated ceiling or a removed file refuses."""
        for path, change in (("mapgrouppos.xml", lambda data: data + b"\r\n"),
                             (PROTO, lambda data: data.replace(b'lootmax="7"', b'lootmax="8"', 1)),
                             ("mapgroupdirt.xml", lambda data: None)):
            with self.subTest(path=path):
                original = (self.mission / path).read_bytes()
                self.write(path, change(original))
                self.refused("files_differ")
                self.write(path, original)

    def test_generated_names_differ_from_the_record(self) -> None:
        """A record whose realization lacks a generated group of the file refuses, also with equal digests."""
        record, revision = self.applied_record()
        store = AssociationRecordStore(self.area, "main", self.key(), "applied")
        fields = {name: value for name, value in record.items() if name != "manifest_sha256"}
        fields["generated_groups"] = record["generated_groups"][:1]
        kept = {item["sha256"]: store.contents.read(item["sha256"]) for item in record["files_before"] if item["existed"]}
        store.save(fields, kept, revision)
        self.refused("generated_names_differ")

    def test_record_without_realization_refuses(self) -> None:
        """Another profile's Apply makes the mission editor-managed; a profile without a record refuses."""
        loaded = self.medical.load("second")
        snapshot = self.snapshot()
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.medical.preview("second", ZONES, True, loaded["profile_revision"], loaded["settings_revision"],
                                 loaded["features"][ZONES]["digest"])
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         ("applied_record_missing", NO_APPLIED_PLAN_TEXT))
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.medical.apply("second", ZONES, True, loaded["profile_revision"], loaded["settings_revision"],
                               loaded["features"][ZONES]["digest"], lambda *_: None, preview_fingerprint="a" * 64,
                               operation_id="medical0099")
        self.assertEqual(str(caught.exception), NO_APPLIED_PLAN_TEXT)
        self.assertEqual(self.snapshot(), snapshot)


class OrdinaryMissionTests(ToggleFixture):
    """Ordinary missions and the item-spawn calls stay unchanged."""

    def test_ordinary_zone_calls_keep_six_fields(self) -> None:
        """No fingerprint in the preview; Apply with the six fields; a fingerprint there is stale."""
        self.set_medical_baseline()
        preview = self.zone_preview(True)
        self.assertEqual(set(preview), {"profile_id", "feature", "enabled", "expected_digest", "proposed_digest",
                                        "changed_fields"})
        with self.assertRaises(ZoneReviewStale):
            self.medical.apply(*self.request(True), lambda *_: None, preview_fingerprint="a" * 64)
        result = self.medical.apply(*self.request(True), lambda *_: None)
        self.assertEqual(result["changed_fields"], [ZONES])
        self.assertFalse(list(self.journals.root.glob("*.json")))
        self.assertIsNone(self.records()[2])

    def test_item_spawns_on_an_editor_managed_mission(self) -> None:
        """Item spawns keep their whole-file toggle and never get a fingerprint."""
        self.applied_with_medical()
        loaded = self.medical.load("main")
        request = ("main", "medical_item_spawns", True, loaded["profile_revision"], loaded["settings_revision"],
                   loaded["features"]["medical_item_spawns"]["digest"])
        self.assertNotIn("preview_fingerprint", self.medical.preview(*request))
        record = self.applied_record()
        self.medical.apply(*request, lambda *_: None)
        self.assertEqual(self.applied_record(), record)

    def test_an_unbound_service_keeps_the_editor_managed_refusal(self) -> None:
        """Without a bound D4 toggle the 6.3 refusal stays, so nothing writes outside the journal."""
        self.applied_with_medical()
        self.medical._editor = None
        with self.assertRaisesRegex(ValueError, EDITOR_MANAGED):
            self.zone_preview(True)


if __name__ == "__main__":
    unittest.main()
