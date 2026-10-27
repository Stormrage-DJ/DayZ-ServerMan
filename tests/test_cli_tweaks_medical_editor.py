"""`tweaks medical set` on an editor-managed mission (leaf 8.13): the zone review's fingerprint goes to Apply.

Real compositions over the edit fixture. The fixture mission gets the files and the plain editor records of
one committed map editor Apply (the 2.5 and 8.7 record shapes), so the composed D4 toggle runs.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, PROFILE_ID, ChangingTerminal, EditRoot  # noqa: E402

from dayz_serverman.application.activity_wording import ERROR_TEXTS  # noqa: E402
from dayz_serverman.application.medical_features import MedicalFeatureService  # noqa: E402
from dayz_serverman.application.mission_map_apply_records import realization  # noqa: E402
from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_STALE_TEXT, MEDICAL_KIND, MISMATCH_TEXT  # noqa: E402
from dayz_serverman.repositories.medical_zone_fragments import zones_enabled  # noqa: E402
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, target_key  # noqa: E402
from dayz_serverman.repositories.mission_map_owned import WRITABLE_MISSION_FILES, owned_digest  # noqa: E402
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore  # noqa: E402
from tests.mission_map_mapgroup_support import BASELINE, PROTOTYPE, build, contents, plan, zone  # noqa: E402

ZONES = ["tweaks", "medical", "set", *PROFILE, "medical_loot_zones"]
NOW = "2026-10-15T08:00:00Z"
# The bytes that an outside edit adds to mapgroupproto.xml after the zone review (8.14)
OUTSIDE = b"\n<!-- outside -->\n"


def sha(data: bytes | None) -> str | None:
    """Return SHA-256 of bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()


class EditorManagedMedicalSetTests(EditRoot):
    """The composed zone toggle of an editor-managed mission, through the command line."""

    def setUp(self) -> None:
        """Write the applied mapgroup files, the medical baseline and the editor records of one Apply."""
        super().setUp()
        self.add_medical_files()
        result = build(plan(25, zone("rich")), BASELINE, medical_baseline=PROTOTYPE)
        files = {path: None for path in MISSION_FILE_SET}
        files.update(contents(result))
        for path, data in files.items():
            if data is not None:
                (self.mission / path).write_bytes(data)
        self.applied = files["mapgroupproto.xml"]
        baselines = self.manager / "data" / "tweak-baselines" / PROFILE_ID
        baselines.mkdir(parents=True)
        (baselines / "medical_loot_zones.original.xml").write_bytes(PROTOTYPE)
        self.write_records(files, [realization(clone) for clone in result.clones])

    def write_records(self, files: dict[str, bytes | None], groups: list) -> None:
        """Store the original, the working baseline and a version 2 last-applied record."""
        area, key = self.manager / "data" / "mission-map", target_key(TargetClass.MISSION, self.mission.resolve())
        self.key = key
        root = "mpmissions\\dayzOffline.enoch"
        base = {path: BASELINE.get(path) for path in MISSION_FILE_SET}
        items = [{"path": path, "existed": data is not None, "sha256": sha(data),
                  "size": None if data is None else len(data)} for path, data in base.items()]
        stored = {sha(data): data for data in base.values() if data is not None}
        OriginalStore(area, key).create({"target_class": "mission", "target_key": key,
                                         "display_path": str(self.mission), "mission_root": root,
                                         "terrain_id": "enoch", "captured_at": NOW, "profile_id": PROFILE_ID,
                                         "operation_id": "apply0001", "files": items}, stored)
        original = OriginalStore(area, key).read()
        baseline = AssociationRecordStore(area, PROFILE_ID, key, "baseline")
        baseline.save({"profile_id": PROFILE_ID, "mission_key": key, "mission_root": root, "target_keys": [key],
                       "originals": [{"target_key": key, "manifest_sha256": original["manifest_sha256"]}],
                       "files": [dict(item, target_key=key) for item in items],
                       "provenance": [{"time": NOW, "source": "original", "operation_id": "apply0001",
                                       "paths": list(MISSION_FILE_SET)}]}, stored, None)
        found = baseline.read()
        AssociationRecordStore(area, PROFILE_ID, key, "applied").save({
            "profile_id": PROFILE_ID, "mission_key": key, "mission_root": root, "operation_id": "apply0001",
            "operation_kind": "apply", "applied_at": NOW, "plan_id": "plan0001", "plan_revision": 0,
            "configuration_fingerprint": "f" * 64, "baseline_revision": found[1],
            "baseline_manifest_sha256": found[0]["manifest_sha256"],
            "files_after": [{"target_key": key, "path": path, "existed": files[path] is not None,
                             "sha256": sha(files[path]),
                             "owned_sha256": owned_digest(TargetClass.MISSION, path, files[path])}
                            for path in WRITABLE_MISSION_FILES],
            "files_before": [], "excluded_encounters": [], "global_abundance": 25, "plan_objects": [],
            "generated_groups": groups, "generated_groups_before": []}, {}, None)

    def test_on_then_off_with_the_reviewed_fingerprint(self) -> None:
        """The review carries the fingerprint; the toggle commits a medical record; off gives the applied bytes."""
        code, stdout, stderr = self.cli(*ZONES, "on", "--yes", "--json")
        self.assertEqual(code, 0, stderr)
        value = json.loads(stdout)["value"]
        self.assertRegex(value["review"]["preview_fingerprint"], "^[0-9a-f]{64}$")
        self.assertEqual(value["operations"][0]["kind"], "APPLY_MEDICAL_FEATURE")
        proto = (self.mission / "mapgroupproto.xml").read_bytes()
        self.assertTrue(zones_enabled(proto, PROTOTYPE))
        clone = proto[proto.index(b'<group name="ServerManMap_G_'):]
        self.assertNotIn(b'<usage name="Town" />', clone)
        area = self.manager / "data" / "mission-map"
        record = AssociationRecordStore(area, PROFILE_ID, self.key, "applied").read()[0]
        self.assertEqual(record["operation_kind"], MEDICAL_KIND)
        code, _stdout, stderr = self.cli(*ZONES, "off", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), self.applied)

    def test_records_that_do_not_match_refuse_with_the_r17_text(self) -> None:
        """An external edit refuses in the review (exit 2) and names the way out; no file changes."""
        positions = self.mission / "mapgrouppos.xml"
        positions.write_bytes(positions.read_bytes() + b"\r\n")
        before = (self.mission / "mapgroupproto.xml").read_bytes()
        code, stdout, _stderr = self.cli(*ZONES, "on", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (2, "INVALID_REQUEST"))
        self.assertEqual(error["message"], MISMATCH_TEXT)
        code, _stdout, stderr = self.cli(*ZONES, "on", "--yes")
        self.assertEqual(code, 2)
        self.assertIn(MISMATCH_TEXT, stderr)
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), before)

    def outside_edit(self) -> None:
        """Edit mapgroupproto.xml from outside, after the zone review."""
        (self.mission / "mapgroupproto.xml").write_bytes(self.applied + OUTSIDE)

    def assert_stale(self, code: int, stderr: str, exit_code: int) -> None:
        """The CLI prints the 8.13 stale-review text, never the general text, and no file changes (8.14).

        The exit code follows the 6.4 rule, which 8.14 does not change: a refusal before the first phase
        keeps exit 3, and a refusal after it (under the guard) is exit 1.
        """
        self.assertEqual(code, exit_code, stderr)
        self.assertIn(FINGERPRINT_STALE_TEXT, stderr)
        self.assertNotIn(ERROR_TEXTS["REVISION_CONFLICT"], stderr)
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), self.applied + OUTSIDE)

    def test_stale_review_before_the_guard_prints_the_stale_text(self) -> None:
        """The loot-zone file changes while the question waits: the Apply refuses before the guard."""
        code, _stdout, stderr = self.cli(*ZONES, "on", stdin=ChangingTerminal(self.outside_edit))
        self.assert_stale(code, stderr, 3)

    def test_stale_review_under_the_guard_prints_the_stale_text(self) -> None:
        """The loot-zone file changes when the guard validates again: text and JSON carry the stale text."""
        original = MedicalFeatureService._reread

        def changed(service: MedicalFeatureService, request: tuple[object, ...]) -> object:
            """Edit the file from outside, then validate again under the guard."""
            self.outside_edit()
            return original(service, request)

        with patch.object(MedicalFeatureService, "_reread", changed):
            code, _stdout, stderr = self.cli(*ZONES, "on", "--yes")
            self.assert_stale(code, stderr, 1)
            (self.mission / "mapgroupproto.xml").write_bytes(self.applied)
            code, stdout, _stderr = self.cli(*ZONES, "on", "--yes", "--json")
        error = json.loads(stdout)["error"]
        # The JSON message is the worded failure line: the failure sentence of the kind, then the stale text
        self.assertEqual((code, error["code"], error["message"]),
                         (1, "REVISION_CONFLICT", f"The medical loot setting could not be applied. {FINGERPRINT_STALE_TEXT}"))


if __name__ == "__main__":
    unittest.main()
