"""Temporary manager root, DayZ root and editor records for the same-profile editor restore tests (10.10).

The backups are built by the 10.9 collector, bound here as in its own tests; `composition.py` does not wire it
yet (10.37). The restore service gets the editor part of 10.10 over the same plan service and locks.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dayz_serverman.application.backups import BackupService
from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.application.restore_editor import RestoreEditor
from dayz_serverman.application.restores import RestoreService
from dayz_serverman.domain.backup_inventory import MISSION_INVENTORY
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.backups import BackupStorage
from dayz_serverman.repositories.mission_map_layout import (
    MISSION_FILE_SET, RUNTIME_FILE_SET, TargetClass, target_key,
)
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository
from dayz_serverman.repositories.restore_storage import RestoreStorage
from tests.backup_fixtures import FakeSettings, create_runtime_profile, record
from tests.test_mission_map_plan import stored_plan
from tests.test_restores import FakeLifecycle, FakeMutex

MISSION_ROOT = "mpmissions\\dayzOffline.chernarusplus"
# Mission bytes: the pre-editor state, the state that the last Apply wrote and the state before that Apply
VANILLA = b"<areaflags>vanilla</areaflags>"
APPLIED = b"<areaflags>applied</areaflags>"
PREVIOUS = b"<areaflags>previous</areaflags>"
BANDITS = b'{"bandits": 1}'
TIME = "2026-10-16T12:00:00Z"
# The restore clock gives the dated names
MOMENT = datetime(2026, 10, 16, 18, 0, 0, tzinfo=UTC)


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def other_profile(profile_id: str, runtime_profile: str | None, mission_root: str | None = MISSION_ROOT) -> ProfileRecord:
    """Return another profile record that names a mission and a runtime profile folder."""
    return ProfileRecord(1, ProfileInput(profile_id, profile_id, "DayZServer_x64.exe", "Config Files\\serverDZ.cfg",
                                         mission_root, 2402, (), (), runtime_profile))


class Profiles:
    """Profile port with the restoring profile and any other profiles."""

    def __init__(self, main: ProfileRecord) -> None:
        """Keep the restoring profile; tests add other profiles."""
        self.main, self.others = main, []

    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the restoring profile."""
        return self.main

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every profile."""
        return (self.main, *self.others)


class RestoreEditorFixture(unittest.TestCase):
    """A profile with a mission, a runtime profile folder, editor records, a backup service and a restore service."""

    def setUp(self) -> None:
        """Write the DayZ tree and bind the backup and restore services to one plan service and its locks."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_editor_restore_")
        base = Path(self.temporary.name)
        self.dayz, self.paths = base / "DayZ Root", PortablePaths.from_root(base / "manager")
        self.paths.backup_recovery.mkdir(parents=True)
        self.area = self.paths.mission_map
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True)
        server.write_text('class Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n',
                          encoding="utf-8")
        self.runtime = create_runtime_profile(self.dayz)
        (self.runtime / "AI_Bandits").mkdir()
        (self.runtime / "AI_Bandits" / "DynamicAIB.json").write_bytes(BANDITS)
        self.mission = self.dayz.joinpath(*MISSION_ROOT.split("\\"))
        for item in MISSION_INVENTORY:
            target = self.mission.joinpath(*item.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{item}\n", encoding="utf-8")
        self.write_mission(APPLIED)
        self.profiles = Profiles(record())
        self.settings = FakeSettings(self.dayz, self.paths.backups)
        self.locks = AssociationLocks()
        self.plans = MissionMapPlanService(self.profiles, self.settings, self.area, self.locks)  # type: ignore[arg-type]
        self.backups = BackupService(self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
                                     clock=lambda: datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC),
                                     local_timezone=timezone(timedelta(hours=2)))
        self.backups.bind_editor(self.plans, self.locks, self.area)
        self.mission_key = target_key(TargetClass.MISSION, self.mission.resolve())
        self.runtime_key = target_key(TargetClass.RUNTIME, self.runtime.resolve())
        self.journals = RestoreJournalRepository(self.paths.operations / "restore-journals")

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temporary.cleanup()

    def restore_service(self, storage: RestoreStorage | None = None, *, editor: bool = True) -> RestoreService:
        """Return the restore service; without `editor`, it has no editor part."""
        part = RestoreEditor(self.paths, self.plans, self.locks, self.profiles, clock=lambda: MOMENT)  # type: ignore[arg-type]
        return RestoreService(self.profiles, self.settings, BackupStorage(), storage or RestoreStorage(),  # type: ignore[arg-type]
                              self.journals, self.paths.backup_recovery, FakeLifecycle(), FakeMutex(),
                              editor=part if editor else None)

    def backup(self, *, editor: bool = True) -> str:
        """Create one backup of profile main and return its ID; without `editor`, it stays at schema 3."""
        service = self.backups
        if not editor:
            service = BackupService(self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
                                    clock=lambda: datetime(2026, 10, 16, 12, 0, 1, tzinfo=UTC))
        return str(service.create("main", 3, 4, lambda _phase, _percent: None)["backup_id"])

    def restore(self, backup_id: str, operation_id: str = "restore-1", service: RestoreService | None = None,
                checkpoint: Any = None) -> dict[str, Any]:
        """Preview and apply one restore of profile main."""
        service = service or self.restore_service()
        preview = service.preview("main", backup_id)
        manifest = service._backups.verified_manifest(self.paths.backups, backup_id, "main")
        return service.apply("main", backup_id, 3, 4, manifest.manifest_digest, preview["fingerprint"], operation_id,
                             checkpoint or (lambda _phase, _percent: None))

    def write_mission(self, areaflags: bytes) -> None:
        """Write the managed areaflags.map file of the mission."""
        (self.mission / "areaflags.map").write_bytes(areaflags)

    def save_plan(self, expected: int | None = None, abundance: int = 0) -> dict[str, Any]:
        """Save a valid plan of the active association with a global capacity step."""
        plan = stored_plan([])
        plan["association"], plan["global_abundance"] = self.plans.active_association("main"), abundance
        return self.plans.save("main", plan, expected)

    def write_original(self, key: str, target_class: str = "mission", data: bytes = VANILLA) -> dict[str, Any]:
        """Create the protected original of one target, where only the first managed file exists."""
        file_set = MISSION_FILE_SET if target_class == "mission" else RUNTIME_FILE_SET
        files = [{"path": path, "existed": False, "sha256": None, "size": None} for path in file_set]
        files[0] = {"path": file_set[0], "existed": True, "sha256": sha(data), "size": len(data)}
        display = str(self.mission if target_class == "mission" else self.runtime)
        return OriginalStore(self.area, key).create(
            {"target_class": target_class, "target_key": key, "display_path": display, "mission_root": MISSION_ROOT,
             "terrain_id": "chernarusplus", "captured_at": TIME, "profile_id": "main", "operation_id": "op-1",
             "files": files}, {sha(data): data})

    def write_records(self, after: bytes = APPLIED) -> None:
        """Write the baseline on the active originals and the last-applied record whose "files after" is `after`."""
        key = self.mission_key
        originals = [found for item in (key, self.runtime_key) if (found := OriginalStore(self.area, item).read())]
        AssociationRecordStore(self.area, "main", key, "baseline").save({
            "profile_id": "main", "mission_key": key, "mission_root": MISSION_ROOT,
            "target_keys": [item["target_key"] for item in originals],
            "originals": [{"target_key": item["target_key"], "manifest_sha256": item["manifest_sha256"]}
                          for item in originals],
            "files": [{**item["files"][0], "target_key": item["target_key"]} for item in originals],
            "provenance": [{"time": TIME, "source": "original", "operation_id": "op-1", "paths": ["areaflags.map"]}]},
            {sha(data): data for data in (VANILLA, BANDITS)}, None)
        AssociationRecordStore(self.area, "main", key, "applied").save({
            "profile_id": "main", "mission_key": key, "mission_root": MISSION_ROOT, "operation_id": "op-3",
            "operation_kind": "restore_original", "applied_at": TIME, "plan_id": None, "plan_revision": None,
            "configuration_fingerprint": None, "baseline_revision": 0, "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": key, "path": "areaflags.map", "existed": True, "sha256": sha(after),
                             "owned_sha256": sha(after)}],
            "files_before": [{"target_key": key, "path": "areaflags.map", "existed": True, "sha256": sha(PREVIOUS),
                              "size": len(PREVIOUS)}], "excluded_encounters": []},
            {sha(PREVIOUS): PREVIOUS}, None)

    def write_all(self) -> None:
        """Write a plan, both originals, the baseline and the last-applied record of the active association."""
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records()

    def folder(self) -> Path:
        """Return the active association folder."""
        return self.area / "profiles" / "main" / self.mission_key

    def tree(self) -> dict[str, bytes]:
        """Return every file of the editor area by its relative path."""
        return {path.relative_to(self.area).as_posix(): path.read_bytes()
                for path in sorted(self.area.rglob("*")) if path.is_file()}

    def clear_area(self) -> None:
        """Remove every editor record, as after a lost manager data folder."""
        shutil.rmtree(self.area)

    def envelope(self, path: Path) -> dict[str, Any]:
        """Return the parsed record file."""
        return json.loads(path.read_text(encoding="utf-8"))
