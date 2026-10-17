"""Preview and Apply through the bridge (leaf 8.9): refusal envelopes and texts, never the general texts."""

from __future__ import annotations

import re
import unittest
from unittest import mock

from dayz_serverman.application.activity_wording import ERROR_TEXTS, REQUEST_TEXT, error_text
from dayz_serverman.application.mission_map_apply_choices import CHOICE_FILES_TEXT
from dayz_serverman.application.mission_configuration import MissionPathError
from dayz_serverman.application.mission_map_coordinator_errors import translate
from dayz_serverman.application.mission_map_plans import MissionPathChanged
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND
from dayz_serverman.domain.mission_map_families import NOT_VERIFIED_TEXT, FamilyNotVerified, family_gate
from dayz_serverman.domain.mission_map_preview_texts import NO_SAVED_PLAN_TEXT, PREVIEW_STALE_TEXT
from dayz_serverman.repositories.mission_map_layout import PLAN_FILE
from dayz_serverman.repositories.mission_map_incorporate import (
    OWNED_VALUE_ABSENT_TEXT, OWNED_VALUE_CHANGED, OWNED_VALUE_TEXT,
)
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_publication_support import PublicationBridgeFixture
from tests.mission_map_restore_support import PLAN

DIRT = "mapgroupdirt.xml"


class PreviewBridgeTests(PublicationBridgeFixture):
    """The Apply preview is an owner read with the 8.6 answer; its refusals keep the Product Owner text."""

    def test_preview_returns_the_review_answer_and_writes_nothing(self) -> None:
        """The bridge answer is the 8.6 answer with the same fingerprint; no file of either root changes."""
        self.save(PLAN, 25)
        before = self.snapshot()
        answer = self.value("preview_mission_map")
        self.assertEqual(answer, self.review().answer)
        self.assertRegex(answer["fingerprint"], "^[0-9a-f]{64}$")
        self.assertEqual(self.snapshot(), before)
        # Exact fields: the window cannot name a revision, a path or another field
        for extra in ({"expected_revision": 1}, {"mission_key": self.key()}):
            error = self.refused("preview_mission_map", **extra)
            self.assertEqual((error["code"], error["details"]["reason"]), ("INVALID_REQUEST", "invalid_document"))

    def test_no_saved_plan_keeps_the_product_owner_text(self) -> None:
        """No problem list and no request text: reason no_saved_plan with the text, in the bridge and the lane."""
        error = self.refused("preview_mission_map", "second")
        self.assertEqual(error, {"code": "INVALID_REQUEST", "message": NO_SAVED_PLAN_TEXT, "retryable": False,
                                 "details": {"reason": "no_saved_plan"}})
        # The window's bridge wording and the activity view both show the text unchanged
        self.assertEqual(error_text(error["code"], error["message"]), NO_SAVED_PLAN_TEXT)
        self.assertNotEqual(error_text(error["code"], error["message"]), REQUEST_TEXT)
        record = self.apply_bridge(profile="second", fingerprint="0" * 64)
        self.assertEqual((record["state"], record["terminal_error"]),
                         ("FAILED", {"code": "INVALID_REQUEST", "message": NO_SAVED_PLAN_TEXT, "retryable": False}))
        self.assertEqual(error_text("INVALID_REQUEST", NO_SAVED_PLAN_TEXT, kind=EDITOR_KIND), NO_SAVED_PLAN_TEXT)


class PreviewRefusalTests(PublicationBridgeFixture):
    """Refusals that are not a ValueError keep their 3.6 code, reason and retry flag through both previews."""

    PREVIEWS = (("preview_mission_map", {}), ("preview_mission_map_restore", {"mode": "original"}))

    def envelopes(self, profile: str = "main") -> list[tuple[str, str, bool]]:
        """Return the code, reason and retry flag of the refusal of each preview."""
        errors = [self.refused(method, profile, **extra) for method, extra in self.PREVIEWS]
        return [(error["code"], error["details"]["reason"], error["retryable"]) for error in errors]

    def test_profile_mission_and_storage_refusals(self) -> None:
        """An unknown profile, a moved or unresolved mission and a storage error; never INTERNAL_FAILURE."""
        self.save(PLAN, 25)
        self.assertEqual(self.envelopes("ghost"), [("NOT_FOUND", "profile_not_found", False)] * 2)
        cases = ((MissionPathChanged([], "mission_path_changed"),
                  ("PROFILE_CONTEXT_MISMATCH", "mission_path_changed", False)),
                 (MissionPathError("mission folder missing"), ("NOT_FOUND", "mission_unresolved", False)),
                 (OSError("disk refused"), ("STORAGE_FAILURE", "storage_failure", True)))
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), mock.patch.object(self.plans, "_resolve", side_effect=error):
                self.assertEqual(self.envelopes(), [expected] * 2)
        # A DayZ root that is not configured leaves the mission unresolved in both previews
        self.settings.dayz_root = None
        self.assertEqual(self.envelopes(), [("NOT_FOUND", "mission_unresolved", False)] * 2)

    def test_invalid_saved_data_refuses_the_apply_preview_only(self) -> None:
        """Invalid saved plan bytes refuse the Apply preview; the restore preview reads no plan."""
        self.save(PLAN, 25)
        (self.folder() / PLAN_FILE).write_bytes(b"{")
        error = self.refused("preview_mission_map")
        self.assertEqual((error["code"], error["details"]["reason"], error["retryable"]),
                         ("REVISION_CONFLICT", "invalid_saved_data", False))
        answer = self.value("preview_mission_map_restore", mode="original")
        self.assertEqual([item["reason"] for item in answer["refusals"]], ["no_original"])


class ApplyBridgeTests(PublicationBridgeFixture):
    """Apply is a lane operation; its failures carry the 6.11 and Product Owner texts unchanged."""

    def test_apply_on_an_unverified_family_returns_the_6_11_envelope_unchanged(self) -> None:
        """The preview lists the gate refusal; Apply fails with the gate code, text and retry flag; nothing changes."""
        self.save(PLAN, 25, identifier="other")
        text = NOT_VERIFIED_TEXT.format(family="deerisle")
        preview = self.value("preview_mission_map", "other")
        self.assertIn(("family_not_verified", text), [(item["reason"], item["message"]) for item in preview["refusals"]])
        before = self.stores()
        record = self.apply_bridge(profile="other", fingerprint=preview["fingerprint"])
        gate = translate(FamilyNotVerified(family_gate("deerisle"), "apply"))
        self.assertEqual(record["terminal_error"], {"code": gate.code.value, "message": gate.safe_message,
                                                    "retryable": gate.retryable})
        self.assertEqual((record["state"], record["kind"], record["terminal_error"]),
                         ("FAILED", EDITOR_KIND, {"code": "UNSUPPORTED_SNAPSHOT_CONTENT", "message": text,
                                                  "retryable": False}))
        self.assertEqual(gate.details, {"reason": "family_not_verified", "operation": "apply", "terrain_id": "deerisle"})
        self.assertEqual((self.stores(), self.blocks()), (before, []))
        # The activity view shows the gate text, never the general backup text of the code
        shown = error_text(record["terminal_error"]["code"], record["terminal_error"]["message"], kind=EDITOR_KIND)
        self.assertEqual(shown, text)
        self.assertNotEqual(shown, ERROR_TEXTS["UNSUPPORTED_SNAPSHOT_CONTENT"])

    def test_a_stale_preview_fails_with_the_preview_stale_text(self) -> None:
        """A save after the preview: REVISION_CONFLICT, retryable, the Product Owner text; nothing is written."""
        self.save(PLAN, 25)
        fingerprint = self.value("preview_mission_map")["fingerprint"]
        self.save(PLAN, 50, expected=0)
        before = self.stores()
        record = self.apply_bridge(fingerprint=fingerprint)
        self.assertEqual((record["state"], record["terminal_error"]),
                         ("FAILED", {"code": "REVISION_CONFLICT", "message": PREVIEW_STALE_TEXT, "retryable": True}))
        self.assertEqual(self.stores(), before)
        shown = error_text("REVISION_CONFLICT", record["terminal_error"]["message"], kind=EDITOR_KIND)
        self.assertEqual(shown, PREVIEW_STALE_TEXT)
        self.assertNotEqual(shown, ERROR_TEXTS["REVISION_CONFLICT"])
        # The reviewed fingerprint of the current plan applies
        self.assertEqual(self.apply_bridge()["state"], "SUCCEEDED")

    def test_choice_required_and_refused_keep_the_change(self) -> None:
        """An owned-value edit needs a choice and cannot be kept; a deleted file gets the text without the copy."""
        self.save([zone("rich")])
        self.assertEqual(self.apply_bridge()["state"], "SUCCEEDED")
        dirt = (self.mission / DIRT).read_bytes()
        line = re.search(rb"[^\n]*ServerManMap_G_[^\n]*\n", dirt).group(0)
        self.write(DIRT, dirt.replace(line, b"", 1))
        change = self.value("preview_mission_map")["external_changes"][0]
        self.assertEqual((change["path"], change["incorporate"]), (DIRT, {
            "available": False, "reason": OWNED_VALUE_CHANGED, "message": OWNED_VALUE_TEXT.format(path=DIRT)}))
        cases = ((None, CHOICE_FILES_TEXT), ({"external_changes": {DIRT: "incorporate"}, "unavailable_encounters": {}},
                                             OWNED_VALUE_TEXT.format(path=DIRT)))
        for choices, text in cases:
            with self.subTest(text=text[:30]):
                record = self.apply_bridge(choices)
                self.assertEqual((record["state"], record["terminal_error"]),
                                 ("FAILED", {"code": "INVALID_REQUEST", "message": text, "retryable": False}))
                self.assertEqual(error_text("INVALID_REQUEST", text, kind=EDITOR_KIND), text)
        # A deleted file: no copy exists, so the text ends after the replace sentence (PO 07:10:16, evidence 8.8)
        self.write(DIRT, None)
        absent = OWNED_VALUE_ABSENT_TEXT.format(path=DIRT)
        self.assertEqual(self.value("preview_mission_map")["external_changes"][0]["incorporate"]["message"], absent)
        record = self.apply_bridge({"external_changes": {DIRT: "incorporate"}, "unavailable_encounters": {}})
        self.assertEqual(record["terminal_error"]["message"], absent)
        self.assertTrue(absent.endswith("You can still replace the file."))
        self.assertEqual(error_text("INVALID_REQUEST", absent, kind=EDITOR_KIND), absent)
        # Replace stays available and writes the file again
        record = self.apply_bridge({"external_changes": {DIRT: "replace"}, "unavailable_encounters": {}})
        self.assertEqual((record["state"], (self.mission / DIRT).read_bytes()), ("SUCCEEDED", dirt))


if __name__ == "__main__":
    unittest.main()
