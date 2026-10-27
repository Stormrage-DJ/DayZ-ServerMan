"""8.13 test strength (QA-8.13-F2): literal Product Owner texts, the fingerprint basis and a missing "files after" entry.

Also the stored original at a stale first check, and an outside edit between the two checks (Architect 08:21:10).
"""

from __future__ import annotations

import unittest

from dayz_serverman.domain import medical_editor_texts as texts
from dayz_serverman.domain.medical_editor_texts import ZoneReviewStale, ZoneToggleRefused
from dayz_serverman.repositories.mission_map_layout import ledger_folder
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.medical_editor_support import PROTO, ToggleFixture
from tests.mission_map_mapgroup_support import zone

# The four texts of the Product Owner entry of 2026-10-15 06:27:10 in evidence 8.13, word for word
PRODUCT_OWNER_TEXTS = {
    "MISMATCH_TEXT": "Medical loot zones cannot change because the mission files do not match the map editor records "
                     "of this profile. No file was changed. Apply the map plan of this profile again in the map "
                     "editor, then change this setting.",
    "NO_APPLIED_PLAN_TEXT": "Medical loot zones cannot change because the map editor manages this mission, but this "
                            "profile has no applied map plan for it. No file was changed. If another profile applied "
                            "its map plan to this mission, change this setting in that profile. Otherwise apply a map "
                            "plan of this profile in the map editor, then change this setting.",
    "FINGERPRINT_MISSING_TEXT": "The map editor manages this mission, so a change of medical loot zones must include "
                                "the fingerprint of its reviewed preview. No file was changed. Review the change, "
                                "then apply it with that fingerprint.",
    "FINGERPRINT_STALE_TEXT": "The profile, the settings, the mission files or the map editor records changed after "
                              "the review of medical loot zones. No file was changed. Review the change again, then "
                              "apply it.",
}


class LiteralTextTests(unittest.TestCase):
    """The constants are the Product Owner texts, pinned as literals."""

    def test_texts(self) -> None:
        """Each of the four constants equals its literal text."""
        for name, text in PRODUCT_OWNER_TEXTS.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(texts, name), text)


class BasisTests(ToggleFixture):
    """Each input of the fingerprint basis changes the fingerprint; a missing "files after" entry refuses."""

    def setUp(self) -> None:
        """Apply a capacity plan with medical off and a stored loot-zone original."""
        super().setUp()
        self.applied = self.applied_with_medical()

    def fingerprint(self) -> str:
        """Return the fingerprint of the zone review that turns medical on."""
        return self.zone_preview(True)["preview_fingerprint"]

    def test_ledger_entries_are_bound(self) -> None:
        """Another ledger file of the target makes the review stale."""
        before = self.fingerprint()
        folder = ledger_folder(self.area, self.key())
        entry = sorted(folder.glob("*.json"))[0]
        (folder / "extra0001.json").write_bytes(entry.read_bytes())
        self.assertNotEqual(self.fingerprint(), before)

    def test_off_source_is_bound(self) -> None:
        """A byte change of the stored original that keeps the candidate makes the review stale."""
        before, candidate = self.fingerprint(), self.zone_preview(True)["proposed_digest"]
        original = self.medical_baseline_path()
        original.write_bytes(original.read_bytes() + b"\r\n")
        self.assertEqual(self.zone_preview(True)["proposed_digest"], candidate)
        self.assertNotEqual(self.fingerprint(), before)

    def test_request_is_bound(self) -> None:
        """A request with another settings revision, in an otherwise equal context, has another fingerprint."""
        before = self.fingerprint()
        self.settings.revision = 2
        self.assertNotEqual(self.fingerprint(), before)
        with self.assertRaises(ZoneReviewStale):
            self.run_toggle(True, fingerprint=before)

    def test_writable_file_without_files_after_entry(self) -> None:
        """A record whose "files after" lack a writable file refuses with the R17 text."""
        record, revision = self.applied_record()
        store = AssociationRecordStore(self.area, "main", self.key(), "applied")
        fields = {name: value for name, value in record.items() if name != "manifest_sha256"}
        fields["files_after"] = [item for item in record["files_after"] if item["path"] != "mapgroupdirt.xml"]
        kept = {item["sha256"]: store.contents.read(item["sha256"]) for item in record["files_before"] if item["existed"]}
        store.save(fields, kept, revision)
        with self.assertRaises(ZoneToggleRefused) as caught:
            self.zone_preview(True)
        self.assertEqual((caught.exception.reason, str(caught.exception)),
                         ("files_differ", PRODUCT_OWNER_TEXTS["MISMATCH_TEXT"]))


class FirstEnableTests(ToggleFixture):
    """A first Enable stores its original only after the first check proved the review current."""

    def setUp(self) -> None:
        """Apply a capacity plan without a stored loot-zone original."""
        super().setUp()
        self.save([zone("rich")], 25)
        self.run_apply()
        self.applied = self.proto()
        self.assertFalse(self.medical_baseline_path().exists())

    def test_stale_first_check_stores_no_original(self) -> None:
        """A context change seen at the first check under the guard refuses and stores no original."""
        status = self.ports.status

        def changed():
            """Add an association folder when the guard reads the server state."""
            (self.area / "profiles" / "second" / self.key()).mkdir(parents=True, exist_ok=True)
            return status()
        self.ports.status = changed
        with self.assertRaises(ZoneReviewStale) as caught:
            self.run_toggle(True)
        self.assertEqual(str(caught.exception), PRODUCT_OWNER_TEXTS["FINGERPRINT_STALE_TEXT"])
        self.assertFalse(self.medical_baseline_path().exists())
        self.assertEqual(self.proto(), self.applied)

    def test_outside_edit_between_the_two_checks(self) -> None:
        """The stale refusal keeps the outside bytes and the reviewed original; the next review gives R17."""
        others = self.other_files()
        outside = self.applied.replace(b"keep this comment", b"outside comment")
        self.assertNotEqual(outside, self.applied)

        def edit(phase: str, _index: int) -> None:
            """Edit mapgroupproto.xml from outside after the first check."""
            if phase == "AFTER_REVALIDATE":
                (self.mission / PROTO).write_bytes(outside)
        self.fault = edit
        with self.assertRaises(ZoneReviewStale) as caught:
            self.run_toggle(True)
        self.fault = None
        self.assertEqual(str(caught.exception), PRODUCT_OWNER_TEXTS["FINGERPRINT_STALE_TEXT"])
        self.assertEqual((self.proto(), self.other_files()), (outside, others))
        self.assertEqual(self.medical_baseline_path().read_bytes(), self.applied)
        self.assertFalse(list(self.journals.root.glob("*.json")))
        with self.assertRaises(ZoneToggleRefused) as refused:
            self.zone_preview(True)
        self.assertEqual(str(refused.exception), PRODUCT_OWNER_TEXTS["MISMATCH_TEXT"])


if __name__ == "__main__":
    unittest.main()
