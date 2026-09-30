"""Recoverable creation of a profile and its DayZ operational files."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from ..domain.models import RecordUnavailable
from ..domain.profiles import ProfileInput, ProfileValidationError
from ..repositories.backup_verification import path_has_reparse
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from ..repositories.server_configuration import load_server_configuration
from ..repositories.provisioning_journal import (
    ProvisioningJournal,
    ProvisioningJournalRepository,
)
from .mission_catalog import MissionCatalog
from .profiles import ProfileService
from .server_config_template import render_server_config
from .settings import SettingsService, SettingsValidationError


REQUEST_FIELDS = {
    "profile_id", "display_name", "server_executable", "mission_root",
    "game_port", "mods", "extra_arguments",
}


class ProfileProvisioningError(RuntimeError):
    """Raised when a new profile cannot be provisioned safely."""

    def __init__(self, message: str, *, recovery_required: bool = False) -> None:
        """Store the safe message and recovery severity."""
        self.recovery_required = recovery_required
        super().__init__(message)


class ProfileProvisioningService:
    """Create DayZ operational files and a profile as one recoverable unit."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        missions: MissionCatalog,
        journals: ProvisioningJournalRepository,
    ) -> None:
        """Store collaborating services and durable recovery storage."""
        self._profiles = profiles
        self._settings = settings
        self._missions = missions
        self._journals = journals

    def list_missions(self) -> dict[str, object]:
        """Return installed mission choices under the current settings revision."""
        settings = self._settings.load()
        if settings.dayz_root is None or settings.revision is None:
            raise ProfileValidationError("DayZ root is not configured")
        return {
            "settings_revision": settings.revision,
            "missions": [item.to_dict() for item in self._missions.list(settings.dayz_root)],
        }

    def provision(
        self,
        raw: object,
        expected_settings_revision: object,
        operation_id: str,
        checkpoint,
    ) -> dict[str, object]:
        """Create a profile and return its record plus launch readiness."""
        request = self._parse_request(raw)
        settings = self._settings.load()
        if settings.revision != expected_settings_revision or settings.dayz_root is None:
            raise ProfileProvisioningError("Manager settings changed; reload profile creation.")
        root = self._safe_root(Path(settings.dayz_root))
        mission = self._missions.validate(str(root), request["mission_root"])
        profile_id = str(request["profile_id"])
        target_relative = str(PureWindowsPath("serverman", profile_id))
        stage_relative = str(PureWindowsPath("serverman", f".{profile_id}.{operation_id}.stage"))
        target = root.joinpath(*PureWindowsPath(target_relative).parts)
        stage = root.joinpath(*PureWindowsPath(stage_relative).parts)
        if stage.exists():
            raise ProfileProvisioningError("Profile staging files already exist; restart the manager.")
        record = None
        try:
            self._profiles.read(profile_id)
        except ProfileNotFound:
            pass
        else:
            raise ProfileProvisioningError("A profile with this ID already exists.")
        values = ProfileInput.parse({
            **request,
            "server_config": str(PureWindowsPath(target_relative, "serverDZ.cfg")),
            "runtime_profile": str(PureWindowsPath(target_relative, "profile")),
            "mission_root": mission.relative_path,
        })
        if target.exists():
            self._validate_reusable_target(target)
            checkpoint("reuse_profile_files", 55)
            record = self._profiles.save(values, None)
            checkpoint("verify_launch", 90)
            return self._result(profile_id, record, reused_files=True)
        instance_id = self._allocate_instance_id(root)
        journal = ProvisioningJournal(
            operation_id, profile_id, str(root), stage_relative, target_relative, "PLANNED",
        )
        self._journals.save(journal)
        try:
            checkpoint("stage_profile", 20)
            stage.mkdir(parents=True, exist_ok=False)
            (stage / "profile").mkdir()
            (stage / "serverDZ.cfg").write_bytes(
                render_server_config(
                    values.display_name,
                    values.game_port,
                    values.mission_root,
                    instance_id,
                )
            )
            self._write_marker(stage, operation_id)
            journal = self._phase(journal, "STAGED")
            checkpoint("publish_profile_files", 55)
            os.replace(stage, target)
            journal = self._phase(journal, "PUBLISHED")
            checkpoint("save_profile", 75)
            record = self._profiles.save(values, None)
            journal = self._phase(journal, "PROFILE_SAVED")
        except Exception:
            # A persisted profile owns its files even if final journal cleanup fails.
            if record is None:
                self._compensate(journal, root)
            raise
        # Once the profile is saved, its files are committed user data.
        self._remove_marker(target)
        self._journals.delete(operation_id)
        checkpoint("verify_launch", 90)
        return self._result(profile_id, record, reused_files=False)

    def _result(self, profile_id: str, record, *, reused_files: bool) -> dict[str, object]:
        """Return launch readiness for a newly stored profile."""
        try:
            command = self._profiles.preview_launch(profile_id).to_dict()
            readiness: dict[str, object] = {"ready": True, "command": command, "reasons": []}
        except ProfileValidationError as error:
            readiness = {"ready": False, "command": None, "reasons": [str(error)]}
        return {
            "profile": record.to_dict(), "readiness": readiness,
            "reused_files": reused_files,
        }

    def _allocate_instance_id(self, root: Path) -> int:
        """Return the lowest positive DayZ instance identifier not already in use."""
        used: set[int] = set()
        for record in self._profiles.list():
            config = root.joinpath(*PureWindowsPath(record.values.server_config).parts)
            try:
                config.relative_to(root)
                value = load_server_configuration(config).values.get("instanceId", 1)
            except (OSError, UnicodeError, ValueError) as error:
                raise ProfileProvisioningError(
                    "Existing server configurations must be readable before assigning an instance ID."
                ) from error
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ProfileProvisioningError(
                    "An existing server configuration has an invalid instance ID."
                )
            used.add(value)
        missions = root / "mpmissions"
        if missions.is_dir():
            for path in missions.glob("*/storage_*"):
                if path.is_dir():
                    suffix = path.name.removeprefix("storage_")
                    if suffix.isdecimal() and int(suffix) > 0:
                        used.add(int(suffix))
        candidate = 1
        while candidate in used:
            candidate += 1
        return candidate

    @staticmethod
    def _validate_reusable_target(target: Path) -> None:
        """Accept only an unambiguous generated profile folder without overwriting it."""
        config = target / "serverDZ.cfg"
        runtime = target / "profile"
        marker = target / ".serverman-provision.json"
        if (not target.is_dir() or path_has_reparse(target) or marker.exists()
                or not config.is_file() or not runtime.is_dir()):
            raise ProfileProvisioningError(
                "The profile folder exists but is not reusable. Rename or remove it, then try again."
            )

    def recover(self) -> tuple[str, ...]:
        """Recover incomplete journals or report an ambiguous unsafe state."""
        recovered: list[str] = []
        for journal in self._journals.list():
            root = self._safe_root(Path(journal.dayz_root))
            stage = self._journal_path(root, journal.stage_relative)
            target = self._journal_path(root, journal.target_relative)
            try:
                record = self._profiles.read(journal.profile_id)
            except ProfileNotFound:
                record = None
            if record is not None:
                expected = str(PureWindowsPath(journal.target_relative, "serverDZ.cfg"))
                if record.values.server_config != expected:
                    raise ProfileProvisioningError(
                        "Profile provisioning recovery requires review.", recovery_required=True,
                    )
                self._remove_marker(target)
            else:
                self._remove_owned(stage, journal.operation_id)
                self._remove_owned(target, journal.operation_id)
            self._journals.delete(journal.operation_id)
            recovered.append(journal.profile_id)
        return tuple(recovered)

    @staticmethod
    def _parse_request(raw: object) -> dict[str, Any]:
        """Validate the exact provisioning payload shape."""
        if not isinstance(raw, dict) or set(raw) != REQUEST_FIELDS:
            raise ProfileValidationError("profile provisioning fields are missing or unknown")
        return dict(raw)

    @staticmethod
    def _safe_root(root: Path) -> Path:
        """Resolve an existing local DayZ root without reparse points."""
        if not root.is_absolute() or path_has_reparse(root):
            raise ProfileProvisioningError("The configured DayZ root is unsafe.")
        resolved = root.resolve(strict=False)
        if not resolved.is_dir():
            raise ProfileProvisioningError("The configured DayZ root is unavailable.")
        return resolved

    def _phase(self, journal: ProvisioningJournal, phase: str) -> ProvisioningJournal:
        """Persist and return the journal at a new phase."""
        updated = ProvisioningJournal(
            journal.operation_id, journal.profile_id, journal.dayz_root,
            journal.stage_relative, journal.target_relative, phase,
        )
        self._journals.save(updated)
        return updated

    def _compensate(self, journal: ProvisioningJournal, root: Path) -> None:
        """Remove only artifacts bearing this operation's ownership marker."""
        stage = self._journal_path(root, journal.stage_relative)
        target = self._journal_path(root, journal.target_relative)
        self._remove_owned(stage, journal.operation_id)
        self._remove_owned(target, journal.operation_id)
        self._journals.delete(journal.operation_id)

    @staticmethod
    def _journal_path(root: Path, relative: str) -> Path:
        """Resolve and constrain a journal path to the manager's DayZ subtree."""
        path = root.joinpath(*PureWindowsPath(relative).parts).resolve(strict=False)
        try:
            path.relative_to(root / "serverman")
        except ValueError as error:
            raise ProfileProvisioningError(
                "Profile provisioning recovery requires review.", recovery_required=True,
            ) from error
        return path

    @staticmethod
    def _write_marker(directory: Path, operation_id: str) -> None:
        """Write proof that the operation exclusively owns a staged directory."""
        (directory / ".serverman-provision.json").write_text(
            json.dumps({"operation_id": operation_id}), encoding="utf-8",
        )

    @staticmethod
    def _remove_marker(directory: Path) -> None:
        """Remove an ownership marker after a committed profile is authoritative."""
        (directory / ".serverman-provision.json").unlink(missing_ok=True)

    @staticmethod
    def _remove_owned(directory: Path, operation_id: str) -> None:
        """Remove a directory only when its marker proves operation ownership."""
        if not directory.exists():
            return
        marker = directory / ".serverman-provision.json"
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProfileProvisioningError(
                "Profile provisioning recovery requires review.", recovery_required=True,
            ) from error
        if raw != {"operation_id": operation_id}:
            raise ProfileProvisioningError(
                "Profile provisioning recovery requires review.", recovery_required=True,
            )
        shutil.rmtree(directory)
