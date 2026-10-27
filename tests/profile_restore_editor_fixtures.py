"""A profile restore with fixture editor records for the tests of the fourth journal group (leaf 10.11).

The archive is a schema 3 backup of profile main, restored as profile `restored`. The editor files are fixture
records that the restore receives beside it: the planner of the D5 policies (10.37) does not exist yet. The
records follow the 10.8 shapes, so the group checks them as it checks real records.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.backups import BackupService
from dayz_serverman.application.profile_restores import ProfileRestoreService
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.application.restore_editor_files import ledger_entries, record_bytes
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.repositories.backups import BackupStorage
from dayz_serverman.repositories.mission_map_commit_originals import supersede_updates
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, RUNTIME_FILE_SET, TargetClass
from dayz_serverman.repositories.mission_map_records import OriginalStore
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profile_restore_editor_shapes import EditorFile
from dayz_serverman.repositories.profile_restore_journal import ProfileRestoreJournal
from dayz_serverman.repositories.profile_restore_storage import ProfileRestoreStorage
from dayz_serverman.repositories.profiles import ProfileRepository
from tests.test_backups import FakeSettings, create_runtime_profile, record

MISSION_ROOT = "mpmissions\\serverman-restored.chernarusplus"
# Target keys of the fixture: the mission and runtime targets of the new profile, and an unshared supersede target
MISSION_KEY, RUNTIME_KEY, SUPERSEDE_KEY = "a1" * 16, "b2" * 16, "c3" * 16
VANILLA, APPLIED, PREVIOUS, BANDITS = b"vanilla", b"applied", b"previous", b'{"bandits": 1}'
TIME = "2026-10-16T12:00:00Z"
AREA = "data/mission-map/"
# Phases and hooks of a restore with an editor group, in their order
PHASES = ("PLANNED", "PREPARING", "PREPARED", "PUBLISHING", "PUBLISHED_GENERATED", "PUBLISHED_MISSION",
          "HELD_EDITOR", "PUBLISHED_EDITOR_FILE", "PUBLISHED_EDITOR", "FILES_PUBLISHED", "PROFILE_PUBLISHING",
          "PUBLISHED_PROFILE", "PROFILE_PUBLISHED")


class Crash(BaseException):
    """A process end at a journal phase: no handler of the restore runs."""


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def original_fields(key: str, target_class: str, data: bytes, captured_at: str = TIME) -> dict[str, Any]:
    """Return the fields of a protected original whose first managed file holds `data`."""
    file_set = MISSION_FILE_SET if target_class == "mission" else RUNTIME_FILE_SET
    files = [{"path": path, "existed": False, "sha256": None, "size": None} for path in file_set]
    files[0] = {"path": file_set[0], "existed": True, "sha256": sha(data), "size": len(data)}
    return {"target_class": target_class, "target_key": key, "display_path": f"C:\\{target_class}",
            "mission_root": MISSION_ROOT, "terrain_id": "chernarusplus", "captured_at": captured_at,
            "profile_id": "main", "operation_id": "op-1", "files": files}


class ProfileEditorFixture(unittest.TestCase):
    """A deleted profile with a backup, a restore service whose storage knows the manager layout, and records."""

    def setUp(self) -> None:
        """Back up profile main, delete it, and keep an unshared original that the fixture supersedes."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_profile_editor_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dayz = self.root / "dayz"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname="original"; instanceId=7; steamQueryPort=2305; class Missions { class DayZ { '
                          'template="dayzOffline.chernarusplus"; }; };')
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        (mission / "storage_7").mkdir(parents=True)
        (mission / "storage_7" / "players.db").write_bytes(b"players")
        (mission / "init.c").write_bytes(b"common mission")
        create_runtime_profile(self.dayz)
        (self.dayz / "DayZServer_x64.exe").write_bytes(b"fixture")
        self.settings = FakeSettings(self.dayz, self.root / "backups")
        (self.root / "backups").mkdir()
        self.repository = ProfileRepository(self.root / "profiles")
        self.profiles = ProfileService(self.repository, self.settings)
        self.profiles.save(record().values, None)
        backup = BackupService(self.profiles, self.settings, BackupStorage()).create("main", 0, 4, lambda *_: None)
        self.profiles.delete("main", 0)
        self.paths = PortablePaths.from_root(self.root / "manager")
        self.area = self.paths.mission_map
        OriginalStore(self.area, SUPERSEDE_KEY).create(original_fields(SUPERSEDE_KEY, "mission", VANILLA),
                                                       {sha(VANILLA): VANILLA})
        self.journals = ProfileRestoreJournal(self.root / "journals")
        self.storage = self.new_storage()
        self.service = ProfileRestoreService(
            self.profiles, self.settings, BackupStorage(), self.storage,
            SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.STOPPED)),
            SimpleNamespace(guard=lambda *_: contextlib.nullcontext()), lambda: frozenset())
        self.request = {"backup_id": backup["backup_id"], "profile_id": "restored", "display_name": "Restored",
                        "storage_policy": None, "game_port": None, "steam_query_port": None}

    def new_storage(self, *, manager: bool = True) -> ProfileRestoreStorage:
        """Return a storage over the same roots, as after a restart; without `manager` it lacks the layout."""
        return ProfileRestoreStorage(self.journals, self.repository.root, self.root / "recovery",
                                     manager=self.paths if manager else None)

    def apply(self, operation_id: str, files: tuple[EditorFile, ...] | None = None) -> dict[str, Any]:
        """Preview and apply the restore; the storage receives the editor files beside the reviewed groups."""
        preview = self.service.preview(self.request)
        files = self.files(operation_id) if files is None else files
        storage = self.service.storage
        real = type(storage).restore.__get__(storage)
        storage.restore = lambda *arguments, **_keywords: real(*arguments, editor=files)  # type: ignore[method-assign]
        parameters = {**self.request, "expected_manifest_digest": preview["manifest_digest"],
                      "preview_fingerprint": preview["preview_fingerprint"], "overwrite_confirmation": None}
        return self.service.apply(parameters, operation_id, lambda *_: None)

    def files(self, operation_id: str) -> tuple[EditorFile, ...]:
        """Return the fixture records of profile restored, with the supersede and the ledger entry of the operation."""
        folder = f"{AREA}profiles/restored/{MISSION_KEY}/"
        mission = sealed(original_fields(MISSION_KEY, "mission", VANILLA))
        runtime = sealed(original_fields(RUNTIME_KEY, "runtime", BANDITS))
        baseline = sealed({
            "profile_id": "restored", "mission_key": MISSION_KEY, "mission_root": MISSION_ROOT,
            "target_keys": [MISSION_KEY, RUNTIME_KEY],
            "originals": [{"target_key": item["target_key"], "manifest_sha256": item["manifest_sha256"]}
                          for item in (mission, runtime)],
            "files": [{**item["files"][0], "target_key": item["target_key"]} for item in (mission, runtime)],
            "provenance": [{"time": TIME, "source": "original", "operation_id": "op-1", "paths": ["areaflags.map"]}]})
        applied = sealed({
            "profile_id": "restored", "mission_key": MISSION_KEY, "mission_root": MISSION_ROOT, "operation_id": "op-3",
            "operation_kind": "restore_original", "applied_at": TIME, "plan_id": None, "plan_revision": None,
            "configuration_fingerprint": None, "baseline_revision": 0, "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": MISSION_KEY, "path": "areaflags.map", "existed": True,
                             "sha256": sha(APPLIED), "owned_sha256": sha(APPLIED)}],
            "files_before": [{"target_key": MISSION_KEY, "path": "areaflags.map", "existed": True,
                              "sha256": sha(PREVIOUS), "size": len(PREVIOUS)}], "excluded_encounters": []})
        contents = {sha(data): data for data in (VANILLA, PREVIOUS, BANDITS)}
        entry = ledger_entries(applied, baseline, contents.__getitem__, {MISSION_KEY: (TargetClass.MISSION, "C:\\m")},
                               profile_id="restored", mission_root=MISSION_ROOT, operation_id=operation_id,
                               moment=datetime(2026, 10, 16, 18, 0, 0, tzinfo=UTC))[0]
        owner = SimpleNamespace(operation_id=operation_id, profile_id="restored")
        incoming = original_fields(SUPERSEDE_KEY, "mission", PREVIOUS, "2026-10-15T12:00:00Z")
        supersede = supersede_updates(self.paths, owner, incoming, {sha(PREVIOUS): PREVIOUS})  # type: ignore[arg-type]
        return (
            EditorFile("plan", folder + "plan.json", b'{"plan": "stored as it is"}\n'),
            EditorFile("baseline", folder + "baseline.json", record_bytes(baseline, 0)),
            EditorFile("applied", folder + "applied.json", record_bytes(applied, 0)),
            EditorFile("invalidated", folder + "applied.invalidated-20261016T180000Z.json", record_bytes(applied, 0)),
            *(EditorFile("content", f"{folder}files/{digest}", data) for digest, data in contents.items()),
            EditorFile("original", f"{AREA}originals/{MISSION_KEY}/original.json", record_bytes(mission, 0)),
            EditorFile("content", f"{AREA}originals/{MISSION_KEY}/files/{sha(VANILLA)}", VANILLA),
            EditorFile("original", f"{AREA}originals/{RUNTIME_KEY}/original.json", record_bytes(runtime, 0)),
            EditorFile("content", f"{AREA}originals/{RUNTIME_KEY}/files/{sha(BANDITS)}", BANDITS),
            EditorFile("ledger", f"{AREA}ledger/{MISSION_KEY}/{entry['operation_id']}.json", record_bytes(entry, 0)),
            *(EditorFile(item.kind, item.target_path, record_bytes(item.record, 0 if item.expected_revision is None
                                                                    else item.expected_revision + 1))
              for item in supersede),
            # The content that the superseded record names is already there, so it is not written again
            EditorFile("content", f"{AREA}originals/{SUPERSEDE_KEY}/files/{sha(VANILLA)}", VANILLA),
            EditorFile("content", f"{AREA}originals/{SUPERSEDE_KEY}/files/{sha(PREVIOUS)}", PREVIOUS),
        )

    def tree(self) -> dict[str, bytes]:
        """Return every file below the manager data folder by its relative path, staging included."""
        data = self.paths.data
        return {path.relative_to(data).as_posix(): path.read_bytes()
                for path in sorted(data.rglob("*")) if path.is_file()}

    def profile_exists(self) -> bool:
        """Return whether the record of profile restored is published."""
        return (self.repository.root / "restored.json").is_file()

    def folders(self) -> list[str]:
        """Return every folder below the manager data folder, staging folders included."""
        data = self.paths.data
        return sorted(path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_dir())

    def journal(self, operation_id: str) -> dict[str, Any]:
        """Return the journal document of one restore."""
        return json.loads((self.journals.root / f"{operation_id}.json").read_text(encoding="utf-8"))
