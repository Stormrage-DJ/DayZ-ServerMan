"""The projection rule of a restore (Architect 2026-10-15 07:07:28 in evidence 8.8, A88-F1 and A88-F2).

The owned-value rule of the commit step applies to the file and to the state that the commit writes.
It never applies to the bytes that a restored absence removes. A target state without a projection is refused by the
review. A captured-only file stays refused as a journal group in both forms. A failure before the commit
point removes a folder that the restore created (QA-8.8-F4).
"""

from __future__ import annotations

import hashlib
import shutil
import unittest

from dayz_serverman.application.mission_map_restore_records import _group, build_restore
from dayz_serverman.application.mission_map_restore_review import RestoreRefused
from dayz_serverman.domain.mission_map_restore_texts import TARGET_UNPROJECTABLE, TARGET_UNPROJECTABLE_TEXT
from dayz_serverman.repositories.mission_map_infected import MARKER_BEGIN
from dayz_serverman.repositories.mission_map_journal_paths import target_folder
from tests.mission_map_restore_support import CAPTURED_ONLY, PLAN, RestoreFixture, sha

DIRT, TERRITORY = "mapgroupdirt.xml", "env/zombie_territories.xml"
# A Dirt file whose owned block the projection cannot read (QA probe qa88_damaged.py)
BROKEN = b'<group name="broken'


class ProjectionTests(RestoreFixture):
    """Damaged owned blocks: removed by a restored absence, never written back by a restore."""

    def setUp(self) -> None:
        """Apply the plan once."""
        super().setUp()
        self.save(PLAN, 25)
        self.run_apply()

    @staticmethod
    def records_area(snapshot: tuple) -> dict[str, bytes]:
        """Return the manager files outside the journal folders."""
        return {name: data for name, data in snapshot[1].items() if "operations" not in name}

    def test_restored_absence_removes_a_damaged_file(self) -> None:
        """A file that a restore created and an admin damaged is removed by Restore previous state (A88-F1)."""
        self.write(DIRT, None)
        self.run_restore("original")
        damaged = (self.mission / DIRT).read_bytes() + BROKEN
        self.write(DIRT, damaged)
        review = self.restore_review("previous")
        self.assertFalse(review.answer["refused"])
        self.assertIn((DIRT, False), [(item["path"], item["exists_after"]) for item in review.answer["affected_files"]])
        result = self.run_restore("previous", review.fingerprint)
        self.assertEqual((result["result"], result["backups"]), ("COMMITTED", [{"path": DIRT, "sha256": sha(damaged)}]))
        self.assertFalse((self.mission / DIRT).exists())
        _original, (baseline, _revision), (applied, _applied_revision) = self.records()
        # The removed bytes stay in "files before" and in the `replaced` backup, with content
        before = next(item for item in applied["files_before"] if item["path"] == DIRT)
        self.assertEqual(before["sha256"], sha(damaged))
        backup = baseline["provenance"][-1]
        self.assertEqual((backup["source"], backup["files"][0]["sha256"]), ("replaced", sha(damaged)))
        self.assertEqual((self.folder() / "files" / sha(damaged)).read_bytes(), damaged)
        # The ledger item has a null owned-before digest, which the ledger parser accepts
        entry = self.ledger_entries()["restore0002"]
        item = next(file for file in entry["files"] if file["path"] == DIRT)
        self.assertEqual((item["before_sha256"], item["owned_before_sha256"], item["after_sha256"]),
                         (sha(damaged), None, None))

    def test_unprojectable_target_is_refused_by_the_review(self) -> None:
        """Restore previous state back to a damaged version refuses in the preview and in the lane (A88-F2)."""
        damaged = (self.mission / DIRT).read_bytes() + BROKEN
        self.write(DIRT, damaged)
        self.assertEqual(self.run_restore("original")["result"], "COMMITTED")
        review = self.restore_review("previous")
        refusal = {"reason": TARGET_UNPROJECTABLE, "file": DIRT, "source": None, "field": None, "placements": [],
                   "message": TARGET_UNPROJECTABLE_TEXT.format(path=DIRT), "object_id": None}
        self.assertEqual((review.answer["refused"], review.answer["refusals"]), (True, [refusal]))
        before = self.snapshot()
        with self.assertRaises(RestoreRefused) as refused:
            self.run_restore("previous", review.fingerprint)
        self.assertEqual((refused.exception.reason, refused.exception.path, str(refused.exception)),
                         (TARGET_UNPROJECTABLE, DIRT, TARGET_UNPROJECTABLE_TEXT.format(path=DIRT)))
        self.assertEqual(self.snapshot(), before)
        # The damaged version stays in the `replaced` backup of Restore original
        kept = [item["sha256"] for entry in self.records()[1][0]["provenance"] if entry["source"] == "replaced"
                for item in entry["files"]]
        self.assertIn(sha(damaged), kept)
        self.assertEqual((self.folder() / "files" / sha(damaged)).read_bytes(), damaged)

    def test_damaged_territory_markers_are_refused_by_the_review(self) -> None:
        """A territory file whose markers raise the adapter refusal is unprojectable too, not a crash of the review."""
        damaged = (self.mission / TERRITORY).read_bytes() + MARKER_BEGIN
        self.write(TERRITORY, damaged)
        self.assertEqual(self.run_restore("original")["result"], "COMMITTED")
        review = self.restore_review("previous")
        self.assertEqual([(item["reason"], item["file"]) for item in review.answer["refusals"]],
                         [(TARGET_UNPROJECTABLE, TERRITORY)])

    def test_captured_only_group_is_refused_in_both_forms(self) -> None:
        """The commit step refuses a wolf file as an absence group and as a written group, before any write."""
        wolves = (self.mission / CAPTURED_ONLY[0]).read_bytes()
        for written in (None, b"<territory-type/>"):
            with self.subTest(absence=written is None):
                review = self.restore_review("original")
                publication = build_restore(review, operation_id=f"wolf{len(written or b'')}", now="2026-10-15T08:00:00Z",
                                            paths=self.paths)
                folder = target_folder(self.root.resolve(), review.context.association["mission_root"])
                publication.journal.groups.append(_group(self.paths, folder, self.root.resolve(),
                                                         publication.journal.operation_id, CAPTURED_ONLY[0],
                                                         wolves, written))
                candidates = dict(publication.candidates)
                if written is not None:
                    candidates[("mission", CAPTURED_ONLY[0])] = written
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "owned-value projection"):
                    self.commit.commit(publication.journal, publication.updates, candidates,
                                       revalidate=lambda: None, existing_profiles={"main"})
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(hashlib.sha256((self.mission / CAPTURED_ONLY[0]).read_bytes()).hexdigest(),
                                 sha(wolves))

    def test_failure_before_the_commit_point_removes_a_created_folder(self) -> None:
        """Restore original recreates a deleted folder; a failure before COMMITTING removes it again (QA-8.8-F4)."""
        shutil.rmtree(self.mission / "env")
        for boundary in (("AFTER_PUBLISH", 3), ("AFTER_PUBLISHED", -1)):
            with self.subTest(boundary=boundary):
                before = self.snapshot()

                def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """Fail once the territory file and its folder exist again."""
                    if (phase, index) == boundary:
                        self.assertTrue((self.mission / "env" / "zombie_territories.xml").exists())
                        raise OSError("phase fault")
                self.fault = failure
                with self.assertRaises(OSError):
                    self.run_restore("original")
                self.assertFalse((self.mission / "env").exists())
                # The DayZ side and every editor record are as before; only the retired journal is new
                self.assertEqual(self.snapshot()[0], before[0])
                self.assertEqual(self.records_area(self.snapshot()), self.records_area(before))
        self.fault = None
        self.run_restore("original")
        self.assertTrue((self.mission / "env" / "zombie_territories.xml").exists())


if __name__ == "__main__":
    unittest.main()
