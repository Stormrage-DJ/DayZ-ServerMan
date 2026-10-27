"""8.13 QA-8.13-F5: a change after the zone review is a stale review also before the guard.

On an editor-managed mission, an Apply with a fingerprint can refuse before the guard because the
profile, the settings or the loot-zone file changed. That refusal is the stale review. A ValueError and
a malformed request keep their errors. An Apply without a fingerprint and an ordinary mission keep their
own refusals.

A malformed fingerprint counts as missing (QA-8.13-F8). When the mission class cannot be read, the
refusal keeps its error (QA-8.13-F9).

Source: the Product Owner entry of 2026-10-15 14:59:22 and the Architect entry of 15:02:04 (evidence 8.13).
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from dayz_serverman.application.medical_feature_coordinator import _error
from dayz_serverman.application.settings import SettingsValidationError
from dayz_serverman.domain.medical_editor_texts import (
    FINGERPRINT_MISSING_TEXT, FINGERPRINT_STALE_TEXT, ZoneReviewStale, ZoneToggleRefused,
)
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.atomic_file import ContentChangedError
from tests.medical_editor_support import PROTO, ToggleFixture

# The 6.3 messages of a changed loot-zone file and of a changed or malformed settings revision
CHANGED_TEXT = "medical feature target changed after it was loaded"
SETTINGS_TEXT = "expected settings revision does not match"
# A well-formed fingerprint that no review returned
OTHER_FINGERPRINT = "0" * 64


class BeforeGuardFixture(ToggleFixture):
    """The toggle fixture with the changes that the tests make after a review."""

    def outside_edit(self) -> None:
        """Edit mapgroupproto.xml from outside."""
        (self.mission / PROTO).write_bytes(self.proto() + b"\n<!-- outside -->\n")

    def settings_revision(self) -> None:
        """Change the settings revision."""
        self.settings.revision += 1

    def profile_revision(self) -> None:
        """Change the revision of the main profile."""
        record = self.profiles.records["main"]
        self.profiles.records["main"] = dataclasses.replace(record, revision=record.revision + 1)

    def refused(self, request: tuple[Any, ...], fingerprint: object) -> Exception:
        """Apply the request with the fingerprint and return its error; the guard must not be taken."""
        self.events.clear()
        try:
            self.medical.apply(*request, lambda *_: None, preview_fingerprint=fingerprint, operation_id="early0001")
        except Exception as error:
            self.assertNotIn("mutex", self.events)
            self.assertFalse(list(self.journals.root.glob("*.json")))
            return error
        self.fail("the toggle did not refuse")

    def assert_kept(self, error: Exception, kind: type[Exception], text: str, code: str) -> None:
        """The refusal keeps its 6.3 type, message and bridge code."""
        self.assertNotIsInstance(error, ZoneReviewStale)
        self.assertIsInstance(error, kind)
        self.assertEqual((str(error), _error(error).code.value), (text, code))


class EditorManagedTests(BeforeGuardFixture):
    """An editor-managed mission after an Apply with medical off and a stored loot-zone original."""

    def setUp(self) -> None:
        """Apply a capacity plan."""
        super().setUp()
        self.applied = self.applied_with_medical()

    def reviewed(self) -> tuple[tuple[Any, ...], str]:
        """Return the request of a toggle to on and the fingerprint of its review."""
        request = self.request(True)
        return request, self.medical.preview(*request)["preview_fingerprint"]

    def test_change_after_the_review_is_stale(self) -> None:
        """An outside edit, a settings or a profile revision change gives the stale review; nothing is written."""
        for name, cause in (("outside_edit", ContentChangedError), ("settings_revision", RevisionConflict),
                            ("profile_revision", RevisionConflict)):
            with self.subTest(case=name):
                self.tearDown(); self.setUp()
                request, fingerprint = self.reviewed()
                getattr(self, name)()
                expected, manager = self.proto(), self.snapshot()[1]
                error = self.refused(request, fingerprint)
                self.assertIs(type(error), ZoneReviewStale)
                self.assertEqual(str(error), FINGERPRINT_STALE_TEXT)
                self.assertIsInstance(error.__cause__, cause)
                call = _error(error)
                self.assertEqual((call.code.value, call.safe_message, call.retryable),
                                 ("REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, True))
                self.assertEqual((self.proto(), self.snapshot()[1]), (expected, manager))

    def test_value_errors_keep_invalid_request(self) -> None:
        """An unset DayZ root and a same-state request keep their ValueError and INVALID_REQUEST."""
        request, fingerprint = self.reviewed()
        self.settings.dayz_root = None
        self.assert_kept(self.refused(request, fingerprint), SettingsValidationError, "DayZ root is not configured",
                         "INVALID_REQUEST")
        self.settings.dayz_root = str(self.root)
        self.assert_kept(self.refused(self.request(False), fingerprint), ValueError,
                         "medical feature already has the requested state", "INVALID_REQUEST")

    def test_malformed_request_keeps_its_error(self) -> None:
        """A malformed digest or settings revision is a request error, not a stale review."""
        request, fingerprint = self.reviewed()
        malformed = request[:5] + ("f" * 63,)
        self.assert_kept(self.refused(malformed, fingerprint), ContentChangedError,
                         "expected mission file digest is invalid", "REVISION_CONFLICT")
        malformed = request[:4] + (str(request[4]),) + request[5:]
        self.assert_kept(self.refused(malformed, fingerprint), RevisionConflict, SETTINGS_TEXT, "REVISION_CONFLICT")
        malformed = request[:4] + (True,) + request[5:]
        self.assert_kept(self.refused(malformed, fingerprint), RevisionConflict, SETTINGS_TEXT, "REVISION_CONFLICT")

    def test_malformed_fingerprint_keeps_its_refusal(self) -> None:
        """A malformed fingerprint counts as missing: without a change the missing text, with one the 6.3 error."""
        for value in ("", "A" * 64, "a" * 63, "not-a-fingerprint"):
            with self.subTest(value=value):
                self.tearDown(); self.setUp()
                request = self.reviewed()[0]
                error = self.refused(request, value)
                self.assertIs(type(error), ZoneToggleRefused)
                self.assertEqual((error.reason, str(error)), ("fingerprint_missing", FINGERPRINT_MISSING_TEXT))
                self.outside_edit()
                self.assert_kept(self.refused(request, value), ContentChangedError, CHANGED_TEXT, "REVISION_CONFLICT")

    def test_too_long_fingerprint_counts_as_missing(self) -> None:
        """More than 64 hexadecimal digits is malformed, also when the first 64 are the reviewed fingerprint."""
        for name, extend in (("65 digits", lambda reviewed: "a" * 65),
                             ("one more digit", lambda reviewed: reviewed + "0"),
                             ("128 digits", lambda reviewed: reviewed * 2)):
            with self.subTest(case=name):
                self.tearDown(); self.setUp()
                request, fingerprint = self.reviewed()
                value = extend(fingerprint)
                # Without a change the toggle refuses with the missing text and writes nothing
                expected = self.proto()
                error = self.refused(request, value)
                self.assertIs(type(error), ZoneToggleRefused)
                self.assertEqual((error.reason, str(error)), ("fingerprint_missing", FINGERPRINT_MISSING_TEXT))
                self.assertEqual(self.proto(), expected)
                # With a change before the guard the 6.3 error stays
                self.outside_edit()
                self.assert_kept(self.refused(request, value), ContentChangedError, CHANGED_TEXT, "REVISION_CONFLICT")

    def test_failed_class_read_keeps_its_error(self) -> None:
        """A settings change and a deleted loot-zone file: the class read fails, so the 6.3 error stays."""
        request, fingerprint = self.reviewed()
        self.settings_revision()
        (self.mission / PROTO).unlink()
        manager = self.snapshot()[1]
        error = self.refused(request, fingerprint)
        self.assertIs(type(error), RevisionConflict)
        self.assert_kept(error, RevisionConflict, SETTINGS_TEXT, "REVISION_CONFLICT")
        self.assertEqual(self.snapshot()[1], manager)
        self.assertFalse((self.mission / PROTO).exists())

    def test_apply_without_fingerprint_keeps_its_refusal(self) -> None:
        """Without a fingerprint, a change before the guard keeps its 6.3 error and message."""
        request = self.request(True)
        self.settings_revision()
        self.assert_kept(self.refused(request, None), RevisionConflict, SETTINGS_TEXT, "REVISION_CONFLICT")
        request = self.request(True)
        self.outside_edit()
        self.assert_kept(self.refused(request, None), ContentChangedError, CHANGED_TEXT, "REVISION_CONFLICT")

    def test_unbound_service_keeps_its_refusal(self) -> None:
        """A service without a bound toggle keeps the 6.3 refusal, also with a fingerprint."""
        request, fingerprint = self.reviewed()
        self.medical._editor = None
        self.settings_revision()
        self.assert_kept(self.refused(request, fingerprint), RevisionConflict, SETTINGS_TEXT, "REVISION_CONFLICT")


class OrdinaryMissionTests(BeforeGuardFixture):
    """A mission without an editor group and without an Apply record, with a bound toggle."""

    def test_ordinary_mission_keeps_its_refusals(self) -> None:
        """With a fingerprint, a changed file and a changed settings revision keep their 6.3 errors."""
        self.set_medical_baseline()
        self.assertIsNone(self.medical.preview(*self.request(True)).get("preview_fingerprint"))
        request = self.request(True)
        self.outside_edit()
        self.assert_kept(self.refused(request, OTHER_FINGERPRINT), ContentChangedError, CHANGED_TEXT,
                         "REVISION_CONFLICT")
        request = self.request(True)
        self.settings_revision()
        self.assert_kept(self.refused(request, OTHER_FINGERPRINT), RevisionConflict, SETTINGS_TEXT,
                         "REVISION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
