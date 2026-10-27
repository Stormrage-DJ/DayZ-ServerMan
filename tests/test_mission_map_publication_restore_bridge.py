"""Restore preview, restore and recovery through the bridge (leaf 8.9): the 8.8 texts come back unchanged."""

from __future__ import annotations

import unittest

from dayz_serverman.application.activity_wording import REQUEST_TEXT, error_text
from dayz_serverman.domain.mission_map_restore_texts import (
    INVALID_MODE, INVALID_MODE_TEXT, LANE_KINDS, NO_ORIGINAL, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE,
    NO_PREVIOUS_STATE_TEXT, RESTORE_STALE_TEXT, SHARED_ORIGINAL_TEXT,
)
from tests.mission_map_publication_support import PublicationBridgeFixture
from tests.mission_map_restore_support import PLAN

# Modes that no restore has: an unknown word, another type, a list and null
INVALID_MODES = ("sideways", "ORIGINAL", 1, ["original"], None)


class RestoreBridgeTests(PublicationBridgeFixture):
    """The restore preview is an owner read with the 8.8 answer; the lane keeps the 8.8 texts."""

    def test_refusals_before_any_apply_keep_the_8_8_texts(self) -> None:
        """No original and no previous state: the preview lists the texts, and the restore fails with them."""
        for mode, reason, text in (("original", NO_ORIGINAL, NO_ORIGINAL_TEXT),
                                   ("previous", NO_PREVIOUS_STATE, NO_PREVIOUS_STATE_TEXT)):
            with self.subTest(mode=mode):
                answer = self.value("preview_mission_map_restore", mode=mode)
                self.assertEqual(answer, self.restore_review(mode).answer)
                self.assertEqual([(item["reason"], item["message"]) for item in answer["refusals"]], [(reason, text)])
                handle = self.value("restore_mission_map", mode=mode, preview_fingerprint=answer["fingerprint"])
                record = self.finished(handle)
                self.assertEqual((record["kind"], record["state"], record["terminal_error"]), (
                    LANE_KINDS[mode], "FAILED", {"code": "INVALID_REQUEST", "message": text, "retryable": False}))
                shown = error_text("INVALID_REQUEST", text, kind=LANE_KINDS[mode])
                self.assertEqual(shown, text)
                self.assertNotEqual(shown, REQUEST_TEXT)

    def test_an_unknown_mode_gets_the_8_8_text_in_both_methods(self) -> None:
        """Preview and restore refuse an unknown mode with reason invalid_mode and the text, before any read."""
        for mode in INVALID_MODES:
            with self.subTest(mode=mode):
                expected = {"code": "INVALID_REQUEST", "message": INVALID_MODE_TEXT, "retryable": False,
                            "details": {"reason": INVALID_MODE}}
                self.assertEqual(self.refused("preview_mission_map_restore", mode=mode), expected)
                self.assertEqual(self.refused("restore_mission_map", mode=mode, preview_fingerprint="0" * 64),
                                 expected)
        self.assertEqual(self.lane.list_recent(), ())
        # A missing or extra field is a malformed request, not a mode
        for parameters in ({}, {"mode": "original", "extra": 1}):
            error = self.refused("preview_mission_map_restore", **parameters)
            self.assertEqual((error["code"], error["details"]["reason"]), ("INVALID_REQUEST", "invalid_document"))

    def test_restore_after_apply_stale_and_reviewed(self) -> None:
        """A stale fingerprint fails with the stale-restore text; the reviewed one commits."""
        self.save(PLAN, 25)
        self.assertEqual(self.apply_bridge()["state"], "SUCCEEDED")
        answer = self.value("preview_mission_map_restore", mode="previous")
        self.assertEqual((answer, answer["refusals"]), (self.restore_review("previous").answer, []))
        before = self.stores()
        record = self.finished(self.value("restore_mission_map", mode="previous", preview_fingerprint="0" * 64))
        self.assertEqual((record["state"], record["terminal_error"]),
                         ("FAILED", {"code": "REVISION_CONFLICT", "message": RESTORE_STALE_TEXT, "retryable": True}))
        self.assertEqual(self.stores(), before)
        record = self.finished(self.value("restore_mission_map", mode="previous",
                                          preview_fingerprint=answer["fingerprint"]))
        self.assertEqual((record["kind"], record["state"]), (LANE_KINDS["previous"], "SUCCEEDED"))
        self.assertEqual(record["result"]["result"], "COMMITTED")

    def test_the_shared_target_text_through_the_bridge(self) -> None:
        """A second profile on the applied folder gets the shared-original text in its Restore original preview."""
        self.save(PLAN, 25)
        self.assertEqual(self.apply_bridge()["state"], "SUCCEEDED")
        original = self.value("preview_mission_map_restore", "second", mode="original")["original"]
        self.assertEqual((original["shared"], original["text"]), (True, SHARED_ORIGINAL_TEXT))


class RecoverBridgeTests(PublicationBridgeFixture):
    """The 8.5 on-demand recovery is the owner method `inspect_mission_map_recovery`."""

    def test_recover_takes_no_parameters_and_reports_the_state(self) -> None:
        """Without a journal nothing is blocked; a parameter, also the profile ID, is refused."""
        self.assertEqual(self.value("inspect_mission_map_recovery", None)["blocked"], False)
        error = self.refused("inspect_mission_map_recovery")
        self.assertEqual(error["code"], "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
