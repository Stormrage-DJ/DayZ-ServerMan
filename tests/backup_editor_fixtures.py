"""Temporary DayZ root, editor area and bound backup service for the editor backup tests (10.9)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dayz_serverman.application.backups import BackupService
from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.domain.backup_inventory import MISSION_INVENTORY
from dayz_serverman.domain.backups import manifest_digest
from dayz_serverman.repositories.backups import BackupStorage
from dayz_serverman.repositories.mission_map_layout import (
    MISSION_FILE_SET, RUNTIME_FILE_SET, TargetClass, target_key,
)
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from tests.backup_fixtures import FakeProfiles, FakeSettings, create_runtime_profile, record
from tests.test_mission_map_plan import stored_plan


MISSION_ROOT = "mpmissions\\dayzOffline.chernarusplus"
# File bytes of the records: original and baseline, runtime provider, Replace backup, files before an apply
VANILLA = b"<prototype>vanilla</prototype>"
BANDITS = b'{"bandits": 1}'
REPLACED = b"<prototype>admin edit kept by Replace</prototype>"
PREVIOUS = b"<prototype>before the last apply</prototype>"
TIME = "2026-10-16T12:00:00Z"


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def stored(key: str, path: str, data: bytes) -> dict[str, Any]:
    """Return a stored file item of one existing file."""
    return {"target_key": key, "path": path, "existed": True, "sha256": sha(data), "size": len(data)}


class EditorBackupFixture(unittest.TestCase):
    """A profile with a mission, a runtime profile folder, an editor area and a backup service bound to it."""

    def setUp(self) -> None:
        """Write the DayZ tree and bind the backup service to the plan service and its locks."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_editor_backup_")
        base = Path(self.temporary.name)
        self.dayz = base / "DayZ Root"
        self.backups = base / "manager" / "backups"
        self.backups.mkdir(parents=True)
        self.area = base / "manager" / "data" / "mission-map"
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True)
        server.write_text('class Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n',
                          encoding="utf-8")
        self.runtime = create_runtime_profile(self.dayz)
        self.mission = self.dayz.joinpath(*MISSION_ROOT.split("\\"))
        for item in MISSION_INVENTORY:
            target = self.mission.joinpath(*item.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{item}\n", encoding="utf-8")
        self.profiles = FakeProfiles(record())
        self.settings = FakeSettings(self.dayz, self.backups)
        self.locks = AssociationLocks()
        self.plans = MissionMapPlanService(self.profiles, self.settings, self.area, self.locks)  # type: ignore[arg-type]
        self.storage = BackupStorage()
        self.service = self.backup_service()
        self.service.bind_editor(self.plans, self.locks, self.area)
        self.mission_key = target_key(TargetClass.MISSION, self.mission.resolve())
        self.runtime_key = target_key(TargetClass.RUNTIME, self.runtime.resolve())

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temporary.cleanup()

    def backup_service(self) -> BackupService:
        """Return a backup service over the fixture, without editor records bound."""
        return BackupService(self.profiles, self.settings, self.storage,  # type: ignore[arg-type]
                             clock=lambda: datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC),
                             local_timezone=timezone(timedelta(hours=2)))

    def create(self, service: BackupService | None = None) -> dict[str, Any]:
        """Create one backup of profile main and return its summary."""
        return (service or self.service).create("main", 3, 4, lambda _phase, _percent: None)

    def archive(self, result: dict[str, Any]) -> Path:
        """Return the archive path of a created backup."""
        return self.backups / f"{result['backup_id']}.zip"

    def save_plan(self) -> dict[str, Any]:
        """Save a valid plan of the active association; its background stays a reference to an image path."""
        plan = stored_plan([])
        plan["association"] = self.plans.active_association("main")
        return self.plans.save("main", plan, None)

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

    def write_records(self, profile_id: str = "main", key: str | None = None) -> None:
        """Write the baseline, with a Replace backup in its provenance, and the last-applied record."""
        key = key or self.mission_key
        mission, runtime = OriginalStore(self.area, key).read(), OriginalStore(self.area, self.runtime_key).read()
        AssociationRecordStore(self.area, profile_id, key, "baseline").save({
            "profile_id": profile_id, "mission_key": key, "mission_root": MISSION_ROOT,
            "target_keys": [key, self.runtime_key],
            "originals": [{"target_key": item["target_key"], "manifest_sha256": item["manifest_sha256"]}
                          for item in (mission, runtime) if item is not None],
            "files": [stored(key, "areaflags.map", VANILLA), stored(self.runtime_key, RUNTIME_FILE_SET[0], BANDITS)],
            "provenance": [{"time": TIME, "source": "original", "operation_id": "op-1", "paths": ["areaflags.map"]},
                           {"time": TIME, "source": "replaced", "operation_id": "op-2", "paths": ["areaflags.map"],
                            "files": [stored(key, "areaflags.map", REPLACED)]}]},
            {sha(data): data for data in (VANILLA, BANDITS, REPLACED)}, None)
        AssociationRecordStore(self.area, profile_id, key, "applied").save({
            "profile_id": profile_id, "mission_key": key, "mission_root": MISSION_ROOT, "operation_id": "op-3",
            "operation_kind": "restore_original", "applied_at": TIME, "plan_id": None, "plan_revision": None,
            "configuration_fingerprint": None, "baseline_revision": 0, "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": key, "path": "areaflags.map", "existed": True, "sha256": sha(VANILLA),
                             "owned_sha256": None}],
            "files_before": [stored(key, "areaflags.map", PREVIOUS)], "excluded_encounters": []},
            {sha(PREVIOUS): PREVIOUS}, None)

    def write_all(self) -> None:
        """Write a plan, both originals, the baseline and the last-applied record of the active association."""
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records()

    def association_folder(self) -> Path:
        """Return the active association folder."""
        return self.area / "profiles" / "main" / self.mission_key


def rewrite_archive(path: Path, change: Callable[[dict[str, Any], dict[str, bytes]], None]) -> None:
    """Rewrite an archive after `change` edits its manifest and member bytes; the manifest is signed again."""
    with zipfile.ZipFile(path) as source:
        infos = {info.filename: info for info in source.infolist()}
        members = {name: source.read(info) for name, info in infos.items()}
    manifest = json.loads(members.pop("manifest.json"))
    change(manifest, members)
    manifest["manifest_digest"] = manifest_digest({name: value for name, value in manifest.items()
                                                   if name != "manifest_digest"})
    path.unlink()
    with zipfile.ZipFile(path, "x") as target:
        target.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            target.writestr(infos.get(name, name), data)
