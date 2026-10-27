"""The stale texts of leaf 8.14 in the Python catalogue and the "Manager activity" view.

A stale medical zone review shows the 8.13 stale-review text before and under the guard, never the
general REVISION_CONFLICT text. An ordinary-mission REVISION_CONFLICT keeps the general text. A stale
restore and a late file change show the 8.8 stale-restore text under both restore kinds.
The window catalogue runs the same cases in the Edge module of this leaf (`stale_texts_wording` with the Edge suffix).
"""

from __future__ import annotations

import unittest
from typing import Any

from dayz_serverman.application.activity_wording import (
    KIND_ERROR_TEXTS, KIND_TEXTS, SHOWN_HOST_TEXTS, error_text,
)
from dayz_serverman.application.log_activity import format_manager_record
from dayz_serverman.application.medical_feature_coordinator import _error
from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_STALE_TEXT
from dayz_serverman.domain.mission_map_restore_texts import LANE_KINDS, RESTORE_STALE_TEXT
from tests.stale_wording_cases import (
    ALL_CASES, CHANGED_TEXT, GENERAL_TEXT, MEDICAL_KIND, ORDINARY_CASES, RESTORE_CASES, SETTINGS_TEXT,
    STALE_REVIEW_CASES,
)
from tests.test_medical_editor_before_guard import OTHER_FINGERPRINT, BeforeGuardFixture
from tests.test_medical_editor_reread import RereadFixture


def activity(kind: str | None, code: str, message: str) -> str:
    """Return the activity sentence of a failed operation, or of a refused bridge call without a kind."""
    event, fields = (("operation.state", {"kind": kind, "state": "FAILED", "error_code": code,
                                          "error_message": message}) if kind else
                     ("bridge.failure", {"method": "apply_medical_feature", "error_code": code, "message": message}))
    line = format_manager_record({"event": event, "level": "ERROR", "fields": fields,
                                  "occurred_at": "2026-10-15T10:00:00Z"})
    return line.split("  ", 2)[2].strip() if line else ""


class CatalogueTests(unittest.TestCase):
    """`error_text` and the activity view word each case; the general text shows only for an ordinary mission."""

    def test_each_case_in_the_python_catalogue(self) -> None:
        """The shown text of each case, also in the activity line of a failed operation or a refused call."""
        for label, kind, code, message, shown in ALL_CASES:
            with self.subTest(label, message=message[:40]):
                self.assertEqual(error_text(code, message, kind=kind), shown)
                ending = f"{KIND_TEXTS[kind][2]} {shown}" if kind else f": {shown}"
                self.assertTrue(activity(kind, code, message).endswith(ending), activity(kind, code, message))

    def test_the_general_text_shows_only_for_an_ordinary_mission(self) -> None:
        """No stale-review or restore case shows the general text; every ordinary case does."""
        for _label, kind, code, message, _shown in STALE_REVIEW_CASES + RESTORE_CASES:
            self.assertNotIn(GENERAL_TEXT, activity(kind, code, message))
        for _label, kind, code, message, _shown in ORDINARY_CASES:
            self.assertEqual(error_text(code, message, kind=kind), GENERAL_TEXT)

    def test_the_mechanisms_of_the_two_texts(self) -> None:
        """The stale review is a shown opening of its own text; the stale restore is a kind row of each kind."""
        openings = [opening for opening in SHOWN_HOST_TEXTS if FINGERPRINT_STALE_TEXT.startswith(opening)]
        self.assertEqual(len(openings), 1)
        for message in (CHANGED_TEXT, SETTINGS_TEXT, RESTORE_STALE_TEXT):
            self.assertFalse(message.startswith(SHOWN_HOST_TEXTS), message)
        for kind in LANE_KINDS.values():
            self.assertEqual(KIND_ERROR_TEXTS[kind]["REVISION_CONFLICT"], RESTORE_STALE_TEXT)
            # Leaf 10.32 adds the commit-point row (Product Owner 2026-10-17 02:14:51); no other row
            self.assertEqual(set(KIND_ERROR_TEXTS[kind]), {"REVISION_CONFLICT", "PUBLICATION_FAILED"})
        # The medical kind has no row, so its ordinary REVISION_CONFLICT keeps the general text
        self.assertNotIn(MEDICAL_KIND, KIND_ERROR_TEXTS)
        # A restore refusal of another code keeps its own sentence
        self.assertEqual(error_text("INVALID_REQUEST", "This restore choice is not valid. Choose Restore original "
                                    "or Restore previous state.", kind=LANE_KINDS["original"]),
                         "This restore choice is not valid. Choose Restore original or Restore previous state.")


class StaleReviewEnvelopeTests(RereadFixture):
    """The real refusals of a stale zone review give the case envelope at both times (Product Owner 14:59:22)."""

    def envelope(self, error: BaseException) -> tuple[str, str]:
        """Return the bridge code and message of a refusal, as the lane record carries them."""
        call = _error(error)  # type: ignore[arg-type]
        return call.code.value, call.safe_message

    def before_the_guard(self) -> BaseException:
        """Review, change the settings revision, then apply: the refusal comes before the guard."""
        request = self.request(True)
        fingerprint = self.medical.preview(*request)["preview_fingerprint"]
        self.settings_revision()
        try:
            self.medical.apply(*request, lambda *_: None, preview_fingerprint=fingerprint, operation_id="early0001")
        except Exception as error:
            return error
        self.fail("the toggle did not refuse")

    def test_both_times_show_the_stale_review_text(self) -> None:
        """Before and under the guard: REVISION_CONFLICT and the 8.13 text, shown unchanged in the activity view."""
        found: dict[str, Any] = {"before the guard": self.envelope(self.before_the_guard())}
        self.tearDown(); self.setUp()
        done = self.inject("guard", self.settings_revision)
        found["under the guard"] = self.envelope(self.refusal())
        self.assertTrue(done[0])
        for timing, (code, message) in found.items():
            with self.subTest(timing):
                self.assertEqual((code, message), ("REVISION_CONFLICT", FINGERPRINT_STALE_TEXT))
                self.assertEqual(error_text(code, message, kind=MEDICAL_KIND), FINGERPRINT_STALE_TEXT)
                self.assertEqual(activity(MEDICAL_KIND, code, message),
                                 f"{KIND_TEXTS[MEDICAL_KIND][2]} {FINGERPRINT_STALE_TEXT}")


class OrdinaryMissionEnvelopeTests(BeforeGuardFixture):
    """The real refusals of an ordinary mission give the ordinary case envelopes and the general text."""

    def test_ordinary_refusals_keep_the_general_text(self) -> None:
        """A changed loot-zone file and a changed settings revision, with a fingerprint, as in 6.3."""
        self.set_medical_baseline()
        for change, text in ((self.outside_edit, CHANGED_TEXT), (self.settings_revision, SETTINGS_TEXT)):
            with self.subTest(text=text):
                request = self.request(True)
                change()
                call = _error(self.refused(request, OTHER_FINGERPRINT))  # type: ignore[arg-type]
                self.assertEqual((call.code.value, call.safe_message), ("REVISION_CONFLICT", text))
                self.assertEqual(error_text(call.code.value, call.safe_message, kind=MEDICAL_KIND), GENERAL_TEXT)


if __name__ == "__main__":
    unittest.main()
