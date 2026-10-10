"""Cover the shapes, manifest digests and identities of the original, baseline and last-applied records (D3)."""

from __future__ import annotations

import copy
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from dayz_serverman.domain.mission_map_records import (
    RecordShapeError, identity, manifest_digest, parse_applied, parse_baseline, parse_original, sealed,
    stale_references, utc_text,
)
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET


# Keys and file bytes of a test mission target and runtime target
MISSION_KEY = "0123456789abcdef0123456789abcdef"
RUNTIME_KEY = "fedcba9876543210fedcba9876543210"
VANILLA = b"<prototype>vanilla</prototype>"
OUTPUT = b"<prototype>editor output</prototype>"


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def original(key: str = MISSION_KEY, data: bytes = VANILLA) -> dict[str, Any]:
    """Return an unsealed mission original where only mapgroupproto.xml exists."""
    files = [{"path": path, "existed": False, "sha256": None, "size": None} for path in MISSION_FILE_SET]
    files[1] = {"path": "mapgroupproto.xml", "existed": True, "sha256": sha(data), "size": len(data)}
    return {"target_class": "mission", "target_key": key, "display_path": "C:\\DayZ\\mpmissions\\dayzOffline.test",
            "mission_root": "mpmissions\\dayzOffline.test", "terrain_id": "chernarusplus",
            "captured_at": "2026-10-10T12:00:00Z", "profile_id": "main", "operation_id": "op-1", "files": files}


def baseline(profile_id: str = "main", key: str = MISSION_KEY, data: bytes = VANILLA) -> dict[str, Any]:
    """Return an unsealed working baseline built on the original above."""
    return {"profile_id": profile_id, "mission_key": key, "mission_root": "mpmissions\\dayzOffline.test",
            "target_keys": [key], "originals": [{"target_key": key, "manifest_sha256": sealed(original(key))[
                "manifest_sha256"]}],
            "files": [{"target_key": key, "path": "mapgroupproto.xml", "existed": True, "sha256": sha(data),
                       "size": len(data)}],
            "provenance": [{"time": "2026-10-10T12:00:00Z", "source": "original", "operation_id": "op-1",
                            "paths": ["mapgroupproto.xml"]}]}


def applied(profile_id: str = "main", key: str = MISSION_KEY, kind: str = "apply") -> dict[str, Any]:
    """Return an unsealed last-applied record of an apply, or of a restore without plan identity."""
    plan = ("plan01", 4, "c" * 64) if kind == "apply" else (None, None, None)
    return {"profile_id": profile_id, "mission_key": key, "mission_root": "mpmissions\\dayzOffline.test",
            "operation_id": "op-2", "operation_kind": kind, "applied_at": "2026-10-10T12:05:00Z",
            "plan_id": plan[0], "plan_revision": plan[1], "configuration_fingerprint": plan[2],
            "baseline_revision": 0, "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": key, "path": "mapgroupproto.xml", "existed": True, "sha256": sha(OUTPUT),
                             "owned_sha256": "e" * 64}],
            "files_before": [{"target_key": key, "path": "mapgroupproto.xml", "existed": True, "sha256": sha(VANILLA),
                              "size": len(VANILLA)}],
            "excluded_encounters": ["e1"]}


class ManifestTests(unittest.TestCase):
    """Verify the manifest digest and the record times."""

    def test_manifest_excludes_itself_and_detects_changes(self) -> None:
        """The digest covers the content without the manifest field; any later change breaks it."""
        record = sealed(original())
        self.assertEqual(record["manifest_sha256"], manifest_digest(original()))
        self.assertEqual(sealed(record), record)
        self.assertEqual(parse_original(record, MISSION_FILE_SET), record)
        tampered = dict(record, terrain_id="enoch")
        self.assertRaisesRegex(RecordShapeError, "manifest", parse_original, tampered, MISSION_FILE_SET)

    def test_utc_text(self) -> None:
        """Record times are UTC texts of aware times."""
        moment = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(utc_text(moment), "2026-10-10T12:00:00Z")
        self.assertRaises(ValueError, utc_text, datetime(2026, 10, 10))


class OriginalShapeTests(unittest.TestCase):
    """Verify that an original captures the whole managed file set, absent files included."""

    def test_file_set_rules(self) -> None:
        """Missing, reordered and extra entries, and digests of absent files, are refused."""
        edits = [
            lambda raw: raw["files"].pop(),
            lambda raw: raw["files"].reverse(),
            lambda raw: raw["files"][0].update(sha256="a" * 64),
            lambda raw: raw["files"][1].update(size=None),
            lambda raw: raw["files"][1].update(target_key=MISSION_KEY),
            lambda raw: raw.update(target_class="profile"),
            lambda raw: raw.update(captured_at="2026-10-10 12:00"),
            lambda raw: raw.update(profile_id="Main"),
            lambda raw: raw.update(extra=1),
        ]
        for edit in edits:
            raw = copy.deepcopy(original())
            edit(raw)
            with self.subTest(raw=raw), self.assertRaises(RecordShapeError):
                parse_original(sealed(raw), MISSION_FILE_SET)


class AssociationRecordShapeTests(unittest.TestCase):
    """Verify the baseline and last-applied shapes."""

    def test_baseline_rules(self) -> None:
        """Target keys start with the mission key; originals and files stay inside them; provenance is bounded."""
        self.assertEqual(parse_baseline(sealed(baseline()))["target_keys"], [MISSION_KEY])
        both = dict(baseline(), target_keys=[MISSION_KEY, RUNTIME_KEY])
        self.assertEqual(len(parse_baseline(sealed(both))["target_keys"]), 2)
        entry = {"time": "2026-10-10T12:00:00Z", "source": "adoption", "operation_id": "op", "paths": []}
        edits = [
            lambda raw: raw.update(target_keys=[RUNTIME_KEY]),
            lambda raw: raw["originals"][0].update(target_key=RUNTIME_KEY),
            lambda raw: raw["files"][0].update(target_key=RUNTIME_KEY),
            lambda raw: raw.update(provenance=[entry] * 51),
            lambda raw: raw["provenance"][0].update(source="guess"),
            lambda raw: raw["files"][0].update(path="../escape.xml"),
            lambda raw: raw.pop("mission_root"),
        ]
        self.assertEqual(len(parse_baseline(sealed(dict(baseline(), provenance=[entry] * 50)))["provenance"]), 50)
        for edit in edits:
            raw = copy.deepcopy(baseline())
            edit(raw)
            with self.subTest(raw=raw), self.assertRaises(RecordShapeError):
                parse_baseline(sealed(raw))

    def test_applied_rules(self) -> None:
        """An apply names its plan revision and fingerprint; a restore names none."""
        self.assertEqual(parse_applied(sealed(applied()))["configuration_fingerprint"], "c" * 64)
        for kind in ("restore_original", "restore_previous"):
            self.assertIsNone(parse_applied(sealed(applied(kind=kind)))["configuration_fingerprint"])
        edits = [
            lambda raw: raw.update(configuration_fingerprint=None),
            lambda raw: raw.update(operation_kind="profile_restore"),
            lambda raw: raw["files_after"][0].update(existed=False),
            lambda raw: raw["files_before"][0].pop("size"),
            lambda raw: raw.update(excluded_encounters=["bad id"]),
            lambda raw: raw.update(plan_revision=-1),
        ]
        for edit in edits:
            raw = copy.deepcopy(applied())
            edit(raw)
            with self.subTest(raw=raw), self.assertRaises(RecordShapeError):
                parse_applied(sealed(raw))
        restore = dict(applied(kind="restore_original"), configuration_fingerprint="c" * 64)
        self.assertRaises(RecordShapeError, parse_applied, sealed(restore))


class IdentityTests(unittest.TestCase):
    """Verify the reference forms of the plan's records object and the stale check."""

    def test_identities_and_stale_references(self) -> None:
        """A reference that differs from the stored identity is stale; absent references are not checked."""
        first, last = sealed(original()), sealed(applied())
        originals = [identity("original", first)]
        current_baseline = identity("baseline", sealed(baseline()), 2)
        current_applied = identity("applied", last)
        self.assertEqual(current_applied, {"operation_id": "op-2", "manifest_sha256": last["manifest_sha256"]})
        self.assertEqual(current_baseline["revision"], 2)
        records = {"originals": originals, "baseline": current_baseline, "applied": current_applied}
        self.assertEqual(stale_references(records, originals, current_baseline, current_applied), [])
        older = dict(records, baseline=dict(current_baseline, revision=1), applied=None)
        self.assertEqual(stale_references(older, originals, current_baseline, current_applied), ["baseline"])
        self.assertEqual(stale_references(records, [], current_baseline, None),
                         [f"original {MISSION_KEY}", "applied"])


if __name__ == "__main__":
    unittest.main()
