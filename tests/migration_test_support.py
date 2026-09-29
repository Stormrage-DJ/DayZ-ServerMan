"""Shared temp-tree harness for the legacy migration service tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics
from dayz_serverman.application.migrations import MigrationService
from dayz_serverman.application.operations.models import OperationCancelled
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.application.settings import SettingsService
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.migrations import MigrationStorage
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profiles import ProfileRepository


class FakeContext:
    """Progress context that records phases and injects cancellation."""
    operation_id = "a" * 32

    def __init__(self, cancel_at: str | None = None) -> None:
        """Store the phase that must raise a cancellation, if any."""
        self.cancel_at = cancel_at
        self.phases: list[str] = []

    def checkpoint(self, phase: str, _percent: int) -> None:
        """Record the reported phase and raise when it triggers cancellation."""
        self.phases.append(phase)
        if phase == self.cancel_at:
            raise OperationCancelled("synthetic cancellation")


class MigrationTestCase(unittest.TestCase):
    """Prepare a portable manager root and a legacy source tree per test."""
    def setUp(self) -> None:
        """Create the portable manager root and the legacy source tree."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_import_")
        self.root = Path(self.temporary.name)
        self.manager = self.root / "Portable Mánager"
        self.paths = PortablePaths.from_root(self.manager)
        self.paths.create_layout()
        self.settings_repository = VersionedJsonRepository(self.paths.manager_config)
        self.settings = SettingsService(
            self.settings_repository, self.paths, WindowsPathDiagnostics(),
        )
        self.profile_repository = ProfileRepository(self.paths.profiles)
        self.profiles = ProfileService(self.profile_repository, self.settings)
        self.legacy = self.root / "Légacy Server"
        # Recreate the legacy manager layout that the importer expects
        (self.legacy / "dayz_server_manager").mkdir(parents=True)
        (self.legacy / "dayz_server_manager-profiles").mkdir()
        (self.legacy / "DayZServer_x64.exe").write_bytes(b"synthetic")
        self.service = self.make_service()

    def tearDown(self) -> None:
        """Remove the temporary root and its legacy source tree."""
        self.temporary.cleanup()

    def make_service(self, hook=None) -> MigrationService:
        """Build a migration service over the test paths and ports."""
        return MigrationService(
            self.paths, self.settings, self.settings_repository, self.profiles,
            self.profile_repository, MigrationStorage(self.paths.migrations), hook,
        )

    def write_profile(self, values: dict[str, object], name: str = "Main.json") -> Path:
        """Write a legacy profile JSON file into the legacy profiles folder."""
        path = self.legacy / "dayz_server_manager-profiles" / name
        path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        return path

    def usable_profile(self, **overrides: object) -> dict[str, object]:
        """Return legacy profile values accepted by the importer."""
        values: dict[str, object] = {
            "name": "Máin Server", "config": "serverDZ.cfg", "port": "2402",
            "profiles": "runtime profile", "mission": r"mpmissions\dayzOffline.test",
            "mods": "@Client One;@Ünicode", "serverMod": "@Server Tools",
            "args": (
                '-config=serverDZ.cfg -port=2402 -profiles="runtime profile" '
                r'-mission=mpmissions\dayzOffline.test '
                '-mod="@Client One;@Ünicode" -serverMod="@Server Tools" '
                "-doLogs -adminLog"
            ),
        }
        values.update(overrides)
        return values

    def select_preview(self) -> tuple[dict[str, object], str]:
        """Select the legacy root and return its preview with the first item id."""
        selected = self.service.select_root(str(self.legacy))
        preview = self.service.preview(selected["selection_id"])
        return preview, preview["profiles"][0]["item_id"]

    def create_launch_paths(self) -> None:
        """Create the launch targets referenced by the usable profile."""
        (self.legacy / "serverDZ.cfg").write_text("hostname = test;", encoding="utf-8")
        for relative in (
            "runtime profile", r"mpmissions\dayzOffline.test",
            "@Client One", "@Ünicode", "@Server Tools",
        ):
            self.legacy.joinpath(*relative.split("\\")).mkdir(parents=True, exist_ok=True)
