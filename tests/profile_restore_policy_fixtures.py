"""A profile with editor records, a schema 4 backup of it and a profile restore with the editor plan (10.37).

The backup service binds the 10.9 collector as `composition.py` now does. The restore service gets the editor
plan of 10.37 and a storage with the manager layout, as `profile_restore_composition.py` builds them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.backups import BackupService
from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.application.profile_restore_editor_plan import ProfileEditorPlanner
from dayz_serverman.application.profile_restores import ProfileRestoreService
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.repositories.backup_editor_records import parse_record
from dayz_serverman.repositories.backups import BackupStorage
from dayz_serverman.repositories.mission_map_layout import TargetClass, target_key
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profile_restore_journal import ProfileRestoreJournal
from dayz_serverman.repositories.profile_restore_storage import ProfileRestoreStorage
from dayz_serverman.repositories.profiles import ProfileRepository
from tests.profile_restore_editor_fixtures import original_fields
from tests.test_backups import FakeSettings, create_runtime_profile, record
from tests.test_mission_map_plan import stored_plan

MISSION_ROOT = "mpmissions\\dayzOffline.chernarusplus"
VANILLA, APPLIED, PREVIOUS, BANDITS, OTHER = b"vanilla", b"applied", b"previous", b'{"bandits": 1}', b"other"
TIME = "2026-10-16T12:00:00Z"
MOMENT = datetime(2026, 10, 17, 8, 0, 0, tzinfo=UTC)


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


class PolicyFixture(unittest.TestCase):
    """Profile main with a plan, both originals, a baseline and a last-applied record, and its schema 4 backup."""

    def setUp(self) -> None:
        """Write the DayZ tree and the editor records of main; the tests back it up and restore it."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_policy_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dayz = self.root / "dayz"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname="original"; instanceId=7; steamQueryPort=2305; class Missions { class DayZ { '
                          'template="dayzOffline.chernarusplus"; }; };')
        self.mission = self.dayz.joinpath(*MISSION_ROOT.split("\\"))
        (self.mission / "storage_7").mkdir(parents=True)
        (self.mission / "storage_7" / "players.db").write_bytes(b"players")
        (self.mission / "areaflags.map").write_bytes(APPLIED)
        self.runtime = create_runtime_profile(self.dayz)
        (self.dayz / "DayZServer_x64.exe").write_bytes(b"fixture")
        (self.root / "backups").mkdir()
        self.settings = FakeSettings(self.dayz, self.root / "backups")
        self.repository = ProfileRepository(self.root / "profiles")
        self.profiles = ProfileService(self.repository, self.settings)
        self.profiles.save(record().values, None)
        self.paths = PortablePaths.from_root(self.root / "manager")
        self.paths.backup_recovery.mkdir(parents=True)
        self.area = self.paths.mission_map
        self.locks = AssociationLocks()
        self.plans = MissionMapPlanService(self.profiles, self.settings, self.area, self.locks)  # type: ignore[arg-type]
        self.mission_key = target_key(TargetClass.MISSION, self.mission.resolve())
        self.runtime_key = target_key(TargetClass.RUNTIME, self.runtime.resolve())
        self.journals = ProfileRestoreJournal(self.root / "journals")
        self.storage = ProfileRestoreStorage(self.journals, self.repository.root, self.paths.backup_recovery,
                                             manager=self.paths)
        self.service = ProfileRestoreService(
            self.profiles, self.settings, BackupStorage(), self.storage,
            SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.STOPPED)),
            SimpleNamespace(guard=lambda *_: contextlib.nullcontext()), lambda: frozenset(),
            editor=ProfileEditorPlanner(self.paths, clock=lambda: MOMENT))

    def write_records(self, after: bytes = APPLIED) -> None:
        """Save a plan, both originals, the baseline on them and a last-applied record whose "files after" is `after`."""
        plan = stored_plan([])
        plan["association"] = self.plans.active_association("main")
        self.plans.save("main", plan, None)
        mission = self.write_original(self.mission_key, "mission", VANILLA)
        runtime = self.write_original(self.runtime_key, "runtime", BANDITS)
        AssociationRecordStore(self.area, "main", self.mission_key, "baseline").save({
            "profile_id": "main", "mission_key": self.mission_key, "mission_root": MISSION_ROOT,
            "target_keys": [self.mission_key, self.runtime_key],
            "originals": [{"target_key": item["target_key"], "manifest_sha256": item["manifest_sha256"]}
                          for item in (mission, runtime)],
            "files": [{**item["files"][0], "target_key": item["target_key"]} for item in (mission, runtime)],
            "provenance": [{"time": TIME, "source": "original", "operation_id": "op-1", "paths": ["areaflags.map"]}]},
            {sha(VANILLA): VANILLA, sha(BANDITS): BANDITS}, None)
        AssociationRecordStore(self.area, "main", self.mission_key, "applied").save({
            "profile_id": "main", "mission_key": self.mission_key, "mission_root": MISSION_ROOT, "operation_id": "op-3",
            "operation_kind": "restore_original", "applied_at": TIME, "plan_id": None, "plan_revision": None,
            "configuration_fingerprint": None, "baseline_revision": 0, "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": self.mission_key, "path": "areaflags.map", "existed": True,
                             "sha256": sha(after), "owned_sha256": sha(after)}],
            "files_before": [{"target_key": self.mission_key, "path": "areaflags.map", "existed": True,
                              "sha256": sha(PREVIOUS), "size": len(PREVIOUS)}], "excluded_encounters": []},
            {sha(PREVIOUS): PREVIOUS}, None)

    def write_original(self, key: str, target_class: str, data: bytes, captured_at: str = TIME) -> dict[str, Any]:
        """Create the protected original of one target, where only its first managed file exists."""
        fields = original_fields(key, target_class, data, captured_at)
        fields["display_path"], fields["mission_root"] = str(self.mission if target_class == "mission" else self.runtime), MISSION_ROOT
        return OriginalStore(self.area, key).create(fields, {sha(data): data})

    def backup(self) -> str:
        """Back up profile main with the bound collector and return the backup ID."""
        service = BackupService(self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
                                clock=lambda: datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC))
        service.bind_editor(self.plans, self.locks, self.area)
        return str(service.create("main", self.profiles.read("main").revision, 4, lambda *_: None)["backup_id"])

    def request(self, backup_id: str, policy: str | None) -> dict[str, Any]:
        """Return the restore request of profile `restored` with one destination policy."""
        return {"backup_id": backup_id, "profile_id": "restored", "display_name": "Restored",
                "storage_policy": policy, "game_port": None, "steam_query_port": None}

    def restore(self, backup_id: str, policy: str | None, operation_id: str = "pr-1") -> tuple[dict, dict]:
        """Preview and apply the restore; return the preview and the result."""
        request = self.request(backup_id, policy)
        preview = self.service.preview(request)
        confirmation = ({"preview_fingerprint": preview["preview_fingerprint"],
                         "affected_profile_ids": preview["affected_profile_ids"]}
                        if preview["storage_policy"] == "replace_existing" else None)
        result = self.service.apply({**request, "expected_manifest_digest": preview["manifest_digest"],
                                     "preview_fingerprint": preview["preview_fingerprint"],
                                     "overwrite_confirmation": confirmation}, operation_id, lambda *_: None)
        return preview, result

    def delete_main(self, *, mission: bool = False, records: bool = False) -> None:
        """Delete the profile record of main; also its mission folder, its editor records, or both."""
        self.profiles.delete("main", self.profiles.read("main").revision)
        if mission:
            shutil.rmtree(self.mission)
        if records:
            shutil.rmtree(self.area)

    def destination(self, preview: dict[str, Any]) -> tuple[str, str]:
        """Return the mission key and the runtime key of the restored profile."""
        mission = self.dayz.joinpath(*preview["mission_root"].split("\\")).resolve()
        runtime = self.dayz.joinpath(*preview["profile"]["runtime_profile"].split("\\")).resolve()
        return target_key(TargetClass.MISSION, mission), target_key(TargetClass.RUNTIME, runtime)

    def folder(self, key: str, profile_id: str = "restored") -> Path:
        """Return an association folder."""
        return self.area / "profiles" / profile_id / key


class PolicyAssertions:
    """Checks of the records that a restore as a new profile installs."""

    def assert_remapped(self, mission_key: str, runtime_key: str, *, applied: str, baseline_source: str = "original"
                        ) -> None:
        """The plan, baseline and last-applied record name profile restored and its two destination keys.

        `baseline_source` is "original" for the remapped archived baseline, "profile_restore" for a rebuilt one.
        """
        folder = self.folder(mission_key)  # type: ignore[attr-defined]
        plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))["plan"]
        self.assertEqual(plan["association"]["profile_id"], "restored")  # type: ignore[attr-defined]
        self.assertEqual((plan["association"]["mission_key"], plan["association"]["runtime_profile_key"]),  # type: ignore[attr-defined]
                         (mission_key, runtime_key))
        baseline = parse_record("baseline", (folder / "baseline.json").read_bytes())
        self.assertEqual(baseline["target_keys"], [mission_key, runtime_key])  # type: ignore[attr-defined]
        self.assertEqual(baseline["provenance"][-1]["source"], baseline_source)  # type: ignore[attr-defined]
        area = self.area  # type: ignore[attr-defined]
        self.assertEqual([item["manifest_sha256"] for item in baseline["originals"]],  # type: ignore[attr-defined]
                         [OriginalStore(area, key).read()["manifest_sha256"] for key in (mission_key, runtime_key)])
        if applied == "install":
            record = parse_record("applied", (folder / "applied.json").read_bytes())
            self.assertEqual((record["profile_id"], record["files_after"][0]["target_key"]),  # type: ignore[attr-defined]
                             ("restored", mission_key))
            self.assertEqual(record["baseline_manifest_sha256"], baseline["manifest_sha256"])  # type: ignore[attr-defined]
            self.assertEqual(len(list((area / "ledger" / mission_key).iterdir())), 1)  # type: ignore[attr-defined]
