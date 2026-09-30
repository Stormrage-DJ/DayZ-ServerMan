"""Complete, ownership-checked deletion of one manager-created profile."""

from __future__ import annotations

import shutil
from pathlib import Path, PureWindowsPath
from typing import Any

from ..domain.lifecycle import ServerState
from ..domain.models import RevisionConflict
from ..repositories.applied_mod_state import AppliedModStateRepository
from ..repositories.backup_verification import path_has_reparse
from ..repositories.backups import BackupStorage
from ..repositories.server_configuration import load_server_configuration
from .mission_configuration import MissionPathError, resolve_profile_mission
from .preferences import PreferenceCoordinator
from .profiles import ProfileService
from .schedules import ScheduleCoordinator
from .settings import SettingsService


class ProfileDeletionError(RuntimeError):
    """Raised when complete deletion cannot be proved safe."""


class ProfileDeletionService:
    """Delete manager-owned profile files and metadata after a full preflight."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService, lifecycle: Any,
        backups: BackupStorage, preferences: PreferenceCoordinator,
        schedules: ScheduleCoordinator, applied_mod_state: AppliedModStateRepository,
    ) -> None:
        self._profiles = profiles
        self._settings = settings
        self._lifecycle = lifecycle
        self._backups = backups
        self._preferences = preferences
        self._schedules = schedules
        self._applied_mod_state = applied_mod_state

    def delete(self, profile_id: str, expected_revision: int) -> dict[str, object]:
        """Delete all exclusively owned data for a manager-created profile."""
        profile = self._profiles.read(profile_id)
        if profile.revision != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, current revision is {profile.revision}"
            )
        if self._lifecycle.status().state != ServerState.STOPPED:
            raise ProfileDeletionError("Stop the DayZ server before deleting a profile.")
        settings = self._settings.load()
        if settings.dayz_root is None:
            raise ProfileDeletionError("The DayZ root is not configured.")
        root = Path(settings.dayz_root).resolve(strict=True)
        generated = (root / "serverman" / profile_id).resolve(strict=False)
        self._require_generated_ownership(root, generated, profile)
        mission_root, mission = self._mission(root, profile)
        instance_id = self._instance_id(root, profile)
        storage = mission / f"storage_{instance_id}"
        storage_is_exclusive = self._storage_is_exclusive(
            root, mission_root, instance_id, profile_id,
        )
        self._preflight_directory(generated, root)
        if storage_is_exclusive:
            self._preflight_directory(storage, mission)

        removed_backups = self._backups.delete_profile(
            self._settings.backup_root(settings), profile_id,
        )
        self._schedules.delete_profile(profile_id)
        self._preferences.delete_profile(profile_id)
        self._applied_mod_state.delete_profile(profile_id)
        removed_storage = self._remove_directory(storage) if storage_is_exclusive else 0
        removed_generated = self._remove_directory(generated)
        self._profiles.delete(profile_id, expected_revision)
        return {
            "profile_id": profile_id,
            "removed_backups": removed_backups,
            "removed_generated_files": removed_generated,
            "removed_mission_storage_files": removed_storage,
            "preserved_shared_mission_storage": not storage_is_exclusive,
        }

    @staticmethod
    def _require_generated_ownership(root: Path, generated: Path, profile: Any) -> None:
        """Require config and runtime paths to belong to the generated profile folder."""
        try:
            generated.relative_to(root)
            config = root.joinpath(*PureWindowsPath(profile.values.server_config).parts).resolve(False)
            runtime_value = profile.values.runtime_profile
            if runtime_value is None:
                raise ValueError
            runtime = root.joinpath(*PureWindowsPath(runtime_value).parts).resolve(False)
            config.relative_to(generated)
            runtime.relative_to(generated)
        except (OSError, ValueError) as error:
            raise ProfileDeletionError(
                "This profile does not exclusively own a generated serverman folder. "
                "Its files were not deleted."
            ) from error

    @staticmethod
    def _mission(root: Path, profile: Any) -> tuple[str, Path]:
        try:
            return resolve_profile_mission(root, profile)
        except MissionPathError as error:
            raise ProfileDeletionError("The profile mission cannot be resolved safely.") from error

    @staticmethod
    def _instance_id(root: Path, profile: Any) -> int:
        config = root.joinpath(*PureWindowsPath(profile.values.server_config).parts)
        try:
            value = load_server_configuration(config).values.get("instanceId", 1)
        except (OSError, UnicodeError, ValueError) as error:
            raise ProfileDeletionError("The profile instance ID cannot be read safely.") from error
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ProfileDeletionError("The profile instance ID is invalid.")
        return value

    def _storage_is_exclusive(
        self, root: Path, mission_root: str, instance_id: int, profile_id: str,
    ) -> bool:
        """Return whether no other profile may own the same world storage."""
        target = (str(PureWindowsPath(mission_root)).casefold(), instance_id)
        for other in self._profiles.list():
            if other.values.profile_id == profile_id:
                continue
            try:
                other_mission, _path = resolve_profile_mission(root, other)
                other_target = (
                    str(PureWindowsPath(other_mission)).casefold(),
                    self._instance_id(root, other),
                )
            except (MissionPathError, ProfileDeletionError):
                # Ambiguous storage is preserved, but it does not own the profile's
                # generated folder or prevent removal of the profile record.
                return False
            if other_target == target:
                return False
        return True

    @staticmethod
    def _preflight_directory(path: Path, owner: Path) -> None:
        if not path.exists():
            return
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(owner.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise ProfileDeletionError("A profile directory escapes its owned location.") from error
        if not resolved.is_dir() or path_has_reparse(path):
            raise ProfileDeletionError("A profile directory contains an unsafe link or reparse point.")
        for descendant in resolved.rglob("*"):
            if path_has_reparse(descendant):
                raise ProfileDeletionError(
                    "A profile directory contains an unsafe link or reparse point."
                )

    @staticmethod
    def _remove_directory(path: Path) -> int:
        if not path.exists():
            return 0
        count = sum(1 for item in path.rglob("*") if item.is_file())
        shutil.rmtree(path)
        return count
