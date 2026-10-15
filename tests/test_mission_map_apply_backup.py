"""Replace backups and restore points of leaf 8.7 (Architect 2026-10-15 01:18:47 in evidence 8.7)."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_apply_choices import ApplyRefused
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.domain.mission_map_records import MAX_PROVENANCE, sealed
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_incorporate import REVERSAL_FAILED
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import zone

PROTO, POS = "mapgroupproto.xml", "mapgrouppos.xml"
FARM, VILLAGE = b'<usage name="Farm" />', b'<usage name="Village" />'
# The version 2 fields that a version 1 applied record lacks
VERSION_TWO = ("global_abundance", "plan_objects", "generated_groups", "generated_groups_before")


def choices(external: dict[str, str] | None = None) -> dict:
    """Return the choices object of an Apply request."""
    return {"external_changes": external or {}, "unavailable_encounters": {}}


def sha(data: bytes) -> str:
    """Return SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


class BackupFixture(ApplyFixture):
    """Helpers over the Apply fixture."""

    def read(self, path: str) -> bytes:
        """Return the current bytes of a mission file."""
        return (self.mission / path).read_bytes()

    def owned_edit(self) -> bytes:
        """Apply global +50, then hand-edit a lootmax (an editor-owned value) and return the edited bytes."""
        self.save([], 50)
        self.run_apply()
        edited = re.sub(rb'(name="Land_Shed" lootmax=")[0-9]+', rb"\g<1>99", self.read(PROTO))
        self.write(PROTO, edited)
        return edited

    def content(self, digest: str) -> bytes | None:
        """Return the bytes of one content file of the association, or None."""
        path = self.folder() / "files" / digest
        return path.read_bytes() if path.is_file() else None

    def to_version_one(self) -> None:
        """Replace the applied record with the same record in version 1 (no version 2 fields)."""
        path = self.folder() / "applied.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {key: value for key, value in raw.items()
                  if key not in ("schema_version", "revision", "manifest_sha256", *VERSION_TWO)}
        path.unlink()
        VersionedJsonRepository(path, 1).save(sealed(fields), None)


class ReplaceBackupTests(BackupFixture):
    """Replace keeps the external version as a `replaced` provenance entry with its content."""

    def test_backup_survives_later_applies_and_the_manager_cleanup(self) -> None:
        """A records-only Apply, an Apply that changes files and the no-journal cleanup keep the backup."""
        edited = self.owned_edit()
        result = self.run_apply(choices({PROTO: "replace"}))
        self.assertEqual(result["replaced"], [{"path": PROTO, "sha256": sha(edited)}])
        raw = json.loads((self.folder() / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 2)
        entry = raw["provenance"][-1]
        self.assertEqual((entry["source"], entry["paths"], entry["operation_id"]), ("replaced", [PROTO], "apply0002"))
        self.assertEqual(entry["files"][0], {"target_key": self.key(), "path": PROTO, "existed": True,
                                             "sha256": sha(edited), "size": len(edited)})
        # A records-only Apply, then an Apply that changes files: the backup and its content stay
        self.save([dict(zone("normal", 50.0, 50.0, 10.0), id="empty")], 50, expected=0)
        self.assertEqual(self.run_apply()["files"], [])
        self.save([], 75, expected=1)
        self.assertEqual(self.run_apply()["files"], [PROTO])
        _original, (baseline, revision), (applied, _revision) = self.records()
        self.assertEqual((revision, baseline["provenance"][-1]["source"]), (1, "replaced"))
        # A later incorporation changes the baseline again; the new record keeps the backup entry and content
        self.write(PROTO, self.read(PROTO).replace(FARM, VILLAGE))
        self.run_apply(choices({PROTO: "incorporate"}))
        _original, (baseline, revision), (applied, _revision) = self.records()
        self.assertEqual((revision, [item["source"] for item in baseline["provenance"]]),
                         (2, ["original", "replaced", "adoption"]))
        self.assertNotIn(sha(edited), [item["sha256"] for item in applied["files_before"]])
        self.assertEqual(self.content(sha(edited)), edited)
        recovery = MissionMapRecovery(self.paths, SimpleNamespace(load=lambda: self.settings),  # type: ignore[arg-type]
                                      InstallationGuard(self.ports, self.ports), self.ports, self.plans._locks)
        self.assertFalse(recovery.inspect()["blocked"])
        self.assertEqual(self.content(sha(edited)), edited)

    def test_rolled_back_replace_leaves_no_entry_and_no_content(self) -> None:
        """A failure after the first group restores the file; the baseline and the content store keep no backup."""
        edited = self.owned_edit()

        def failure(phase: str, index: int) -> None:
            """Fail after the first group is published."""
            if (phase, index) == ("AFTER_PUBLISH", 0):
                raise OSError("phase fault")
        self.fault = failure
        with self.assertRaises(OSError):
            self.run_apply(choices({PROTO: "replace"}))
        self.assertEqual(self.read(PROTO), edited)
        _original, (baseline, revision), _applied = self.records()
        self.assertEqual((revision, [item["source"] for item in baseline["provenance"]]), (0, ["original"]))
        self.assertIsNone(self.content(sha(edited)))

    def test_replace_that_overwrites_nothing_adds_no_entry(self) -> None:
        """When the candidate equals the current file, Replace writes nothing and keeps no backup."""
        self.save([], 50)
        self.run_apply()
        self.save([], 75, expected=0)
        self.write(PROTO, self.review().candidates.contents()[PROTO])
        self.assertEqual(self.review().answer["choices"]["external_changes"], [PROTO])
        result = self.run_apply(choices({PROTO: "replace"}))
        self.assertEqual((result["files"], result["replaced"]), ([], []))
        _original, (baseline, revision), _applied = self.records()
        self.assertEqual((revision, [item["source"] for item in baseline["provenance"]]), (0, ["original"]))

    def test_provenance_keeps_the_50_newest_entries(self) -> None:
        """An incorporation on a full list drops the oldest entry."""
        self.save([], 50)
        self.run_apply()
        _original, (baseline, revision), _applied = self.records()
        filler = [dict(baseline["provenance"][0], source="adoption", paths=[POS], operation_id=f"old{index:02d}")
                  for index in range(MAX_PROVENANCE - 1)]
        full = sealed({**{key: value for key, value in baseline.items() if key != "manifest_sha256"},
                       "provenance": baseline["provenance"] + filler})
        path = self.folder() / "baseline.json"
        path.unlink()
        VersionedJsonRepository(path, 1).save(full, None)
        self.write(PROTO, self.read(PROTO).replace(FARM, VILLAGE))
        self.run_apply(choices({PROTO: "incorporate"}))
        _original, (baseline, _revision), _applied = self.records()
        self.assertEqual(len(baseline["provenance"]), MAX_PROVENANCE)
        self.assertEqual(baseline["provenance"][0]["operation_id"], "old00")
        self.assertEqual(baseline["provenance"][-1]["source"], "adoption")


class RealizationBeforeTests(BackupFixture):
    """`generated_groups_before` names the realization of the "files before" state, or null when unknown."""

    def test_values_after_a_reapply_a_shared_target_and_a_version_one_record(self) -> None:
        """A reapply keeps the replaced record's groups; another association and a version 1 record give null."""
        self.save([zone("rich")], 25)
        self.run_apply()
        _original, _baseline, (first, _revision) = self.records()
        self.save([zone("very_rich")], 50, expected=0)
        self.run_apply()
        _original, _baseline, (second, _revision) = self.records()
        self.assertEqual(second["generated_groups_before"], first["generated_groups"])
        # Profile second applies on the same target: an original exists, so the realization is unknown
        self.save([zone("rich")], 25, identifier="second")
        answer = self.review("second").answer
        replace = {item["path"]: "replace" for item in answer["external_changes"]}
        self.run_apply(choices(replace), identifier="second")
        _original, _baseline, (other, _revision) = self.records("second")
        self.assertIsNone(other["generated_groups_before"])
        # A version 1 record has no realization
        self.to_version_one()
        self.save([zone("rich")], 25, expected=1)
        self.run_apply(choices({item["path"]: "replace" for item in self.review().answer["external_changes"]}))
        _original, _baseline, (third, _revision) = self.records()
        self.assertIsNone(third["generated_groups_before"])
        self.assertEqual(json.loads((self.folder() / "applied.json").read_text(encoding="utf-8"))["schema_version"], 2)

    def test_version_one_record_refuses_incorporation_of_detached_placements(self) -> None:
        """Without a realization the reversal cannot put detached placements back: reversal_failed, then Replace."""
        self.save([zone("rich")], 25)
        self.run_apply()
        self.to_version_one()
        self.write(POS, self.read(POS).replace(b"<map>\r\n", b"<map>\r\n<!-- admin pos -->\r\n"))
        change = self.review().answer["external_changes"][0]
        self.assertEqual((change["path"], change["incorporate"]["available"], change["incorporate"]["reason"]),
                         (POS, False, REVERSAL_FAILED))
        before = self.snapshot()
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply(choices({POS: "incorporate"}))
        self.assertEqual((refused.exception.reason, refused.exception.path), ("incorporation_refused", POS))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.run_apply(choices({POS: "replace"}))["result"], "COMMITTED")


if __name__ == "__main__":
    unittest.main()
