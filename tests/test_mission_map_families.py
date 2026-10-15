"""Verify the reviewed list of verified mission families and the Apply and Restore original gate (leaf 6.11)."""

from __future__ import annotations

import json
import re
import unittest

from dayz_serverman.application.mission_map_coordinator_errors import translate
from dayz_serverman.bridge.contracts import ErrorCode, ensure_json_value
from dayz_serverman.domain.mission_map_families import (
    APPLY, FAMILY_NOT_VERIFIED, GATED_OPERATIONS, RAW_EVIDENCE_SET, RAW_EVIDENCE_SHA256, RESTORE_ORIGINAL,
    VERIFIED_FAMILIES, FamilyNotVerified, family_gate, require_verified_family,
)
from dayz_serverman.domain.mission_map_terrain import TERRAIN_FAMILY_UNKNOWN, terrain_family


# The entry texts of the Architect entry 2026-10-14 17:00:50 in evidence 6.10, word for word
ARCHITECT_CHERNARUSPLUS = (
    "Verified by the 6.10 probe (evidence 6.10; QA 2026-10-14 16:53:03; Architect 2026-10-14 15:45:25). Raw "
    "evidence: `evidence/probe-runs/2026-10-14/` (aggregate SHA-256 `cdc31c3d43e8bbf2`, Operations 2026-10-14 "
    "17:00:24). Probe: runs R0 to R4 with global abundance +50 and four zones; medical off, on and the toggle after "
    "Apply were CE-loaded. Limits: (1) The probe proves that CE loads the candidate files, not live loot quantities. "
    "(2) CE binding of case-only alias spellings was proved for one pair of truck-wreck groups; the other alias "
    "groups are covered by the 6.8 rule, not by their own census. (3) Fixed dispatch items are not governed by "
    "`lootmax`, so a group with ceiling 0 still shows its dispatch parts. (4) The candidates came from the 16 "
    "repository writer files pinned at commit `f343e48`.")
ARCHITECT_PRIPYAT = (
    "Verified by the 6.10 probe (evidence 6.10; QA 2026-10-14 16:53:03; Architect 2026-10-14 15:11:54, 15:45:25 and "
    "17:00:50). Raw evidence: `evidence/probe-runs/2026-10-14/` (aggregate SHA-256 `cdc31c3d43e8bbf2`, Operations "
    "2026-10-14 17:00:24). Probe: runs R0, R1 and R4 with global abundance 0 and four zones, loaded with "
    "`@PripyatDelta;@GSCAssets2026`. Limits: (1) Pripyat was probed in its installed state, where medical off equals "
    "on, because its stored original is unmappable (evidence 6.4). The off form with generated groups was not "
    "CE-loaded on Pripyat; medical off, on and the toggle after Apply were CE-loaded on chernarusplus only. (2) "
    "Pripyat was probed with global abundance 0 only, because its duplicate groups `Land_Jupiter_Building_Plant_1_8` "
    "and `Land_Military_building2` refuse any other global value (`source_ambiguous`). The global write on "
    "name-bound groups was CE-loaded on chernarusplus; on Pripyat it becomes possible only after the duplicates are "
    "removed outside the editor. (3) Only the terrain mods were loaded; the other eight mods of the real profile are "
    "not part of the proof. (4) The probe proves that CE loads the candidate files, not live loot quantities. (5) CE "
    "binding of case-only alias spellings was proved for one pair of truck-wreck groups; the other alias groups are "
    "covered by the 6.8 rule, not by their own census. (6) Fixed dispatch items are not governed by `lootmax`, so a "
    "group with ceiling 0 still shows its dispatch parts. (7) The candidates came from the 16 repository writer "
    "files pinned at commit `f343e48`.")
# Change 1: the moved folder is cited by set name and digest (Vigil location correction 17:02:45).
# Change 2: the entries hold plain text without the Markdown code marks (Architect 2026-10-14 17:28:16, ruling 4)
OLD_LOCATION = ("Raw evidence: `evidence/probe-runs/2026-10-14/` (aggregate SHA-256 `cdc31c3d43e8bbf2`, Operations "
                "2026-10-14 17:00:24).")
NEW_LOCATION = ("Raw evidence: the set probe-runs-2026-10-14 (aggregate SHA-256 cdc31c3d43e8bbf2, Operations "
                "2026-10-14 17:00:24; location in evidence 6.10, Vigil 2026-10-14 17:02:45).")
# The Product Owner texts of 2026-10-14 17:28:16 in evidence 6.11, word for word
ENOCH_TEXT = ("Apply and Restore original are not available for this mission. This version of DayZ-ServerMan allows "
              "them only for terrain families that passed a test with a DayZ server. The terrain family enoch is not "
              "one of them. You can still change, save, import and export the plan.")
UNKNOWN_TEXT = ("Apply and Restore original are not available for this mission. This version of DayZ-ServerMan "
                "allows them only for terrain families that passed a test with a DayZ server. The mission folder name "
                "has no terrain family after a dot, as in dayzOffline.chernarusplus. You can still change, save, "
                "import and export the plan.")


class VerifiedListTests(unittest.TestCase):
    """Each entry records its probe evidence, its probe scope and its limits."""

    def test_the_list_starts_with_the_families_that_passed_6_10(self) -> None:
        """chernarusplus and Pripyat, in this order, once each; enoch and sakhal wait for 6.12."""
        self.assertEqual([entry.family for entry in VERIFIED_FAMILIES], ["chernarusplus", "Pripyat"])

    def test_each_entry_has_its_evidence_reference_and_limit_text(self) -> None:
        """Evidence 6.10 with the QA verdict, the raw set by name and digest, a probe scope and numbered limits."""
        self.assertRegex(RAW_EVIDENCE_SHA256, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(RAW_EVIDENCE_SHA256,
                         "cdc31c3d43e8bbf2be681713a9fd4b25620e0a4ba477bb4556edd6d811176e16")
        for entry in VERIFIED_FAMILIES:
            with self.subTest(family=entry.family):
                self.assertTrue(entry.evidence.startswith("Verified by the 6.10 probe (evidence 6.10; QA 2026-10-14 "
                                                          "16:53:03; Architect 2026-10-14 "))
                self.assertIn(f"the set {RAW_EVIDENCE_SET} ", entry.raw_evidence)
                self.assertIn(f"aggregate SHA-256 {RAW_EVIDENCE_SHA256[:16]},", entry.raw_evidence)
                self.assertTrue(entry.scope.startswith("Probe: runs R0"))
                self.assertGreaterEqual(len(entry.limits), 4)
                self.assertTrue(all(limit.strip() and limit.endswith(".") for limit in entry.limits))
                text = entry.text()
                self.assertIn(" Limits: (1) ", text)
                self.assertIn(f"({len(entry.limits)}) {entry.limits[-1]}", text)
                # Shipped text names no machine path, not the old package folder, and has no Markdown code marks
                self.assertNotRegex(text, r"[A-Za-z]:\\|evidence/probe-runs|session|`")

    def test_entry_texts_are_the_architect_wording_with_the_moved_location(self) -> None:
        """The texts equal the Architect entry 17:00:50 without code marks; only the raw evidence location changes."""
        expected = {"chernarusplus": ARCHITECT_CHERNARUSPLUS, "Pripyat": ARCHITECT_PRIPYAT}
        for entry in VERIFIED_FAMILIES:
            with self.subTest(family=entry.family):
                self.assertEqual(expected[entry.family].count(OLD_LOCATION), 1)
                plain = expected[entry.family].replace(OLD_LOCATION, NEW_LOCATION).replace("`", "")
                self.assertEqual(entry.text(), plain)

    def test_the_pripyat_entry_states_its_two_probe_limits(self) -> None:
        """Installed state where medical off equals on, and global abundance 0 only (leaf 6.11)."""
        pripyat = VERIFIED_FAMILIES[1].text()
        for phrase in ("probed in its installed state, where medical off equals on",
                       "medical off, on and the toggle after Apply were CE-loaded on chernarusplus only",
                       "probed with global abundance 0 only, because its duplicate groups",
                       "The global write on name-bound groups was CE-loaded on chernarusplus"):
            self.assertIn(phrase, pripyat)
        self.assertNotIn("installed state", VERIFIED_FAMILIES[0].text())


class FamilyGateTests(unittest.TestCase):
    """Verified, unverified and custom missions of a verified family."""

    def test_verified_families_and_their_custom_missions(self) -> None:
        """The family is the folder suffix, so a custom mission of a verified family is verified too."""
        for folder in ("dayzOffline.chernarusplus", "serverman-test20261001.chernarusplus", "dayzOffline.Pripyat",
                       "plannerProbe.Pripyat", "banditValidation.Pripyat", "custom.pripyat", "x.CHERNARUSPLUS"):
            with self.subTest(folder=folder):
                gate = family_gate(terrain_family(folder))
                self.assertTrue(gate.verified)
                self.assertEqual(gate.to_dict(), {"terrain_id": terrain_family(folder), "verified": True,
                                                  "reason": None, "text": None, "refused": []})
                for operation in GATED_OPERATIONS:
                    self.assertIs(require_verified_family(gate.terrain_id, operation).entry, gate.entry)

    def test_unverified_families_are_refused_with_the_visible_reason(self) -> None:
        """Every other family, a near name and a mission without a family give a reason code and a text."""
        cases = [("dayzOffline.enoch", "enoch", FAMILY_NOT_VERIFIED), ("dayzOffline.sakhal", "sakhal",
                 FAMILY_NOT_VERIFIED), ("dayzOffline.chernarusplus2", "chernarusplus2", FAMILY_NOT_VERIFIED),
                 ("chernarusplus.Pripyatx", "Pripyatx", FAMILY_NOT_VERIFIED), ("chernarusplus", None,
                 TERRAIN_FAMILY_UNKNOWN), ("dayzOffline.", None, TERRAIN_FAMILY_UNKNOWN)]
        for folder, family, reason in cases:
            with self.subTest(folder=folder):
                gate = family_gate(terrain_family(folder))
                snapshot = gate.to_dict()
                ensure_json_value(snapshot)
                self.assertEqual((snapshot["terrain_id"], snapshot["verified"], snapshot["reason"]),
                                 (family, False, reason))
                self.assertEqual(snapshot["refused"], ["apply", "restore_original"])
                self.assertEqual(snapshot["text"], UNKNOWN_TEXT if family is None else ENOCH_TEXT.replace(
                    "enoch", family))
                for operation in (APPLY, RESTORE_ORIGINAL):
                    with self.assertRaises(FamilyNotVerified) as caught:
                        require_verified_family(family, operation)
                    self.assertEqual((caught.exception.operation, str(caught.exception)), (operation, snapshot["text"]))

    def test_only_apply_and_restore_original_are_gated(self) -> None:
        """Save, import, export, Inspect and Restore previous state are no gate operations, on any family."""
        self.assertEqual(GATED_OPERATIONS, ("apply", "restore_original"))
        for operation in ("save", "import", "export", "copy", "inspect", "load", "restore_previous", "Apply", ""):
            for family in ("enoch", "chernarusplus", None):
                with self.subTest(operation=operation, family=family), self.assertRaises(LookupError):
                    require_verified_family(family, operation)

    def test_the_refusal_is_a_closed_bridge_refusal(self) -> None:
        """translate gives UNSUPPORTED_SNAPSHOT_CONTENT, the reason code, the text and the refused operation."""
        for family, reason in (("enoch", FAMILY_NOT_VERIFIED), (None, TERRAIN_FAMILY_UNKNOWN)):
            with self.subTest(family=family):
                try:
                    require_verified_family(family, RESTORE_ORIGINAL)
                except FamilyNotVerified as error:
                    refusal = translate(error)
                self.assertEqual((refusal.code, refusal.retryable), (ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, False))
                self.assertEqual(dict(refusal.details), {"reason": reason, "operation": "restore_original",
                                                         "terrain_id": family})
                self.assertEqual(str(refusal), family_gate(family).text)
                json.dumps(dict(refusal.details))

    def test_reason_texts_word_for_word_with_the_family_as_written(self) -> None:
        """Both Product Owner texts, word for word; the family keeps the case of the folder name."""
        self.assertEqual(family_gate("enoch").text, ENOCH_TEXT)
        self.assertEqual(family_gate(None).text, UNKNOWN_TEXT)
        for folder, family in (("dayzOffline.Enoch", "Enoch"), ("my.SAKHAL", "SAKHAL"), ("a.b.deerIsle", "deerIsle")):
            with self.subTest(folder=folder):
                expected = ENOCH_TEXT.replace(" enoch ", f" {family} ")
                self.assertEqual(family_gate(terrain_family(folder)).text, expected)
        # Plain sentences: no quotes, code marks, paths or other Markdown
        for text in (ENOCH_TEXT, UNKNOWN_TEXT):
            self.assertIsNone(re.search(r"[`'\"\\/<>*_#\[\]]", text))


if __name__ == "__main__":
    unittest.main()
