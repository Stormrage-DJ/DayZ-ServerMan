"""Leaf 10.13: the 8.6 preview and the commit-step capture check name the same source entry.

One shared function chooses the entry (Architect 2026-10-17 09:30:36 in evidence 10.13, point 2). An entry of a
key with an active original comes first, else the oldest match.
"""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_preview_targets import capture_check
from dayz_serverman.repositories.mission_map_commit_capture import OriginalCaptureRefused, capture_entry
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, TargetClass
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.test_mission_map_commit_fixture import CommitFixture
from tests.test_mission_map_records import VANILLA, original, sha

# A key without an original whose output is older, and a key with an active original whose output is newer
PLAIN, ADOPTED = "f" * 32, "e" * 32


class CaptureSelectionTests(CommitFixture):
    """Two ledger entries of two keys match the current vanilla file."""

    def write_entries(self, updates: list) -> None:
        """Write one output entry for each key; both end in the current bytes of mapgroupproto.xml."""
        for key, operation, moment in ((PLAIN, "older-output", "2026-10-10T10:00:00Z"),
                                       (ADOPTED, "copied-output", "2026-10-11T10:00:00Z")):
            entry = dict(updates[-1].record, target_key=key, display_path=f"D:\\{operation}",
                         mission_root="mpmissions\\other", operation_id=operation, recorded_at=moment)
            entry["files"] = [dict(entry["files"][0], before_sha256=sha(b"earlier"), baseline_sha256=sha(b"earlier"),
                                   after_sha256=sha(VANILLA), owned_after_sha256=sha(VANILLA),
                                   owned_before_sha256=sha(b"earlier"), owned_baseline_sha256=sha(b"earlier"))]
            LedgerStore(self.paths.mission_map).create(entry)

    def test_preview_and_commit_name_the_same_entry(self) -> None:
        """Both checks name the entry of the key with an active original, and both offer adoption."""
        journal, updates, candidates = self.candidates()
        self.write_entries(updates)
        OriginalStore(self.paths.mission_map, ADOPTED).create(original(ADOPTED), {sha(VANILLA): VANILLA})
        current = {path: (self.mission / path).read_bytes() if (self.mission / path).is_file() else None
                   for path in MISSION_FILE_SET}
        preview = capture_check(self.paths.mission_map, journal.mission_key, current, {"main"})
        self.assertEqual((preview["reason"], preview["match"]["operation_id"], preview["adoption"]),
                         ("editor_output", "copied-output", {"case": "copy"}))
        with self.assertRaises(OriginalCaptureRefused) as caught:
            self.run_commit(journal, updates, candidates)
        details = caught.exception.details
        self.assertEqual((details["match"]["operation_id"], details["adoption_required"]), ("copied-output", True))
        self.assertEqual(details["match"], preview["match"])

    def test_without_an_original_the_oldest_entry_is_named(self) -> None:
        """Without an active original the oldest match is named, and nothing offers adoption."""
        journal, updates, _candidates = self.candidates()
        self.write_entries(updates)
        matches = LedgerStore(self.paths.mission_map).capture_matches(
            TargetClass.MISSION, "mapgroupproto.xml", sha(VANILLA), sha(VANILLA))
        entry, adoption = capture_entry(self.paths.mission_map, matches)
        self.assertEqual((entry["operation_id"], adoption), ("older-output", False))


if __name__ == "__main__":
    unittest.main()
