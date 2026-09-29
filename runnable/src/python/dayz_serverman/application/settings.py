"""Manager settings validation, persistence, and diagnostic coordination."""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from ..domain.models import (
    ManagerSettings,
    PathDiagnostic,
    PathRole,
    PathStatus,
    RecordState,
    RecordUnavailable,
    RevisionConflict,
    SettingsInput,
    VersionedDocument,
)
from ..repositories.json_store import VersionedJsonRepository
from ..repositories.paths import PortablePaths, normalize_external_path


SETTINGS_FIELDS = (
    "dayz_root",
    "dayz_executable",
    "steamcmd_root",
    "steamcmd_executable",
    "workshop_content_root",
    "custom_backup_root",
    "steam_account_name",
    "steam_authentication_mode",
)
# The first six settings fields are filesystem paths checked by diagnostics
PATH_SETTINGS_FIELDS = SETTINGS_FIELDS[:6]
# Roots from which dependent executable and content paths are derived
LOCATION_ROOT_FIELDS = ("dayz_root", "steamcmd_root", "custom_backup_root")
# Path roles accepted for pre-save selection validation
PATH_SELECTION_ROLES = {
    "dayz_root": PathRole.DAYZ_ROOT,
    "steamcmd_root": PathRole.STEAMCMD_ROOT,
    "custom_backup_root": PathRole.BACKUP_ROOT,
}
AUTHENTICATION_MODES = frozenset(("ACCOUNT", "ANONYMOUS"))
# Steam account names are 1-64 letters, digits, or underscores
STEAM_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")


class SettingsValidationError(ValueError):
    """Raised when settings values fail validation."""
    pass


def expand_location_roots(values: dict[str, str | None]) -> dict[str, str | None]:
    """Derive redundant executable and Workshop paths from selected roots."""
    dayz_root = values.get("dayz_root")
    steamcmd_root = values.get("steamcmd_root")
    # 221100 is the DayZ dedicated-server app id used by the Workshop layout
    return {
        "dayz_root": dayz_root,
        "dayz_executable": str(Path(dayz_root) / "DayZServer_x64.exe") if dayz_root else None,
        "steamcmd_root": steamcmd_root,
        "steamcmd_executable": str(Path(steamcmd_root) / "steamcmd.exe") if steamcmd_root else None,
        "workshop_content_root": str(
            Path(steamcmd_root) / "steamapps/workshop/content/221100"
        ) if steamcmd_root else None,
        "custom_backup_root": values.get("custom_backup_root"),
    }


def resolved_paths_for_selection(role: str, path: str) -> dict[str, str]:
    """Return dependent paths implied by a selected settings root."""
    # Derive the expected executable beneath the selected DayZ root
    if role == "dayz_root":
        return {"dayz_executable": str(Path(path) / "DayZServer_x64.exe")}
    if role == "steamcmd_root":
        root = Path(path)
        # The Workshop content root sits under the DayZ app id 221100
        return {
            "steamcmd_executable": str(root / "steamcmd.exe"),
            "workshop_content_root": str(root / "steamapps/workshop/content/221100"),
        }
    return {}


class DiagnosticsPort(Protocol):
    """Port for settings path diagnostics."""

    # Inspect every configured path and return one diagnostic per role.
    def inspect(
        self,
        settings: ManagerSettings,
        default_backup_root: Path,
    ) -> tuple[PathDiagnostic, ...]: ...

    # Inspect one candidate path and return its diagnostic before saving.
    def inspect_path(self, role: PathRole, path: str) -> PathDiagnostic: ...


def _optional_absolute(value: str | None, field: str) -> str | None:
    """Normalize an optional path, treating blank values as unset."""
    if value is None or not value.strip():
        return None
    try:
        return normalize_external_path(value)
    except ValueError as error:
        raise SettingsValidationError(f"{field}: {error}") from error


def _is_within(root: str | None, child: str | None) -> bool:
    """Report whether a child path stays inside an optional root."""
    if root is None or child is None:
        return True
    try:
        Path(child).relative_to(Path(root))
    except ValueError:
        return False
    return True


def normalize_settings(values: SettingsInput) -> SettingsInput:
    """Validate and normalize raw settings input into a canonical record."""
    # Normalize each field through the shared validators
    normalized = SettingsInput(
        dayz_root=_optional_absolute(values.dayz_root, "dayz_root"),
        dayz_executable=_optional_absolute(values.dayz_executable, "dayz_executable"),
        steamcmd_root=_optional_absolute(values.steamcmd_root, "steamcmd_root"),
        steamcmd_executable=_optional_absolute(
            values.steamcmd_executable,
            "steamcmd_executable",
        ),
        workshop_content_root=_optional_absolute(
            values.workshop_content_root,
            "workshop_content_root",
        ),
        custom_backup_root=_optional_absolute(
            values.custom_backup_root,
            "custom_backup_root",
        ),
        steam_account_name=validate_steam_account_name(values.steam_account_name),
        steam_authentication_mode=_normalize_authentication_mode(
            values.steam_authentication_mode,
        ),
    )
    # Require derived executables to stay inside their selected roots
    if not _is_within(normalized.dayz_root, normalized.dayz_executable):
        raise SettingsValidationError("dayz_executable must be inside dayz_root")
    if not _is_within(normalized.steamcmd_root, normalized.steamcmd_executable):
        raise SettingsValidationError("steamcmd_executable must be inside steamcmd_root")
    # Reject account and mode combinations that cannot authenticate
    _validate_auth_pair(
        normalized.steam_account_name,
        normalized.steam_authentication_mode,
    )
    return normalized


def validate_steam_account_name(value: str | None) -> str | None:
    """Validate an optional Steam account name and return its trimmed form."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsValidationError("steam_account_name must be a string or null")
    if not value.strip():
        return None
    checked = value.strip()
    if STEAM_ACCOUNT_NAME.fullmatch(checked) is None:
        raise SettingsValidationError(
            "steam_account_name must contain only letters, digits, or underscore",
        )
    return checked


def _normalize_authentication_mode(value: str | None) -> str | None:
    """Validate an optional authentication mode value."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in AUTHENTICATION_MODES:
        raise SettingsValidationError("steam_authentication_mode must be ACCOUNT or ANONYMOUS")
    return value


def _validate_auth_pair(account: str | None, mode: str | None) -> None:
    """Reject account and mode combinations that contradict each other."""
    if mode == "ACCOUNT" and account is None:
        raise SettingsValidationError("steam_account_name is required in ACCOUNT mode")
    if mode == "ANONYMOUS" and account:
        raise SettingsValidationError("steam_account_name must be empty in ANONYMOUS mode")
    if mode is None and account:
        raise SettingsValidationError("select an authentication mode for the Steam account")


class SettingsService:
    """Own settings validation, persistence, and diagnostic evidence."""

    def __init__(
        self,
        repository: VersionedJsonRepository,
        paths: PortablePaths,
        diagnostics: DiagnosticsPort,
    ) -> None:
        """Store the repository, portable paths, and diagnostics port."""
        self._repository = repository
        self._paths = paths
        self._diagnostics = diagnostics

    def load(self) -> ManagerSettings:
        """Return current settings, or unconfigured defaults when none exist."""
        inspection = self._repository.inspect()
        # Missing storage means the manager has never been configured
        if inspection.state == RecordState.MISSING:
            return ManagerSettings.unconfigured()
        # Any other invalid state is unavailable instead of silently defaulted
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise RecordUnavailable(inspection)
        return self._from_document(inspection.document)

    def save(
        self,
        values: SettingsInput,
        expected_revision: int | None,
    ) -> ManagerSettings:
        """Persist validated settings against the expected revision."""
        # Validate first so invalid input never reaches storage
        checked = self.prepare_save(values, expected_revision)
        document = self._repository.save(
            self.storage_fields(checked),
            expected_revision,
        )
        return self._from_document(document)

    def prepare_save(
        self, values: SettingsInput, expected_revision: int | None,
    ) -> ManagerSettings:
        """Validate values and attach current path-validation evidence."""
        normalized = normalize_settings(values)
        # Compare against the stored revision so stale writes fail
        previous = self.load()
        if previous.revision != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, current revision is {previous.revision}"
            )
        # Build the candidate record and refresh its validation evidence
        candidate = ManagerSettings(
            revision=expected_revision,
            **{field: getattr(normalized, field) for field in SETTINGS_FIELDS},
            last_validated_paths=previous.last_validated_paths,
        )
        return self._with_validation_evidence(candidate)

    def diagnostics(self, settings: ManagerSettings | None = None) -> tuple[PathDiagnostic, ...]:
        """Return path diagnostics for the given or current settings."""
        current = settings if settings is not None else self.load()
        return self._diagnostics.inspect(current, self._paths.backups)

    def validate_selection(self, role: str, value: str) -> PathDiagnostic:
        """Validate one path selection before it is saved."""
        # Reject roles that cannot be selected from the UI
        path_role = PATH_SELECTION_ROLES.get(role)
        if path_role is None:
            raise SettingsValidationError("settings path role is unsupported")
        normalized = _optional_absolute(value, role)
        if normalized is None:
            raise SettingsValidationError(f"{role} must not be empty")
        return self._diagnostics.inspect_path(path_role, normalized)

    def backup_root(self, settings: ManagerSettings | None = None) -> Path:
        """Return the effective backup root for the given or current settings."""
        current = settings if settings is not None else self.load()
        if current.custom_backup_root is None:
            return self._paths.backups
        return Path(current.custom_backup_root)

    def portable_backup_root(self) -> Path:
        """Return the manager-owned default independently of effective settings."""
        return self._paths.backups

    def _with_validation_evidence(self, settings: ManagerSettings) -> ManagerSettings:
        """Attach ready or previously proven path evidence to a settings record."""
        evidence: dict[str, str] = {}
        prior = settings.last_validated_paths
        # Score every configured role from current diagnostics
        for diagnostic in self.diagnostics(settings):
            if diagnostic.role == PathRole.BACKUP_ROOT and settings.custom_backup_root is None:
                continue
            role = diagnostic.role.value
            path = diagnostic.configured_path
            if path is not None and diagnostic.status == PathStatus.READY:
                evidence[role] = path
            elif path is not None and prior.get(role) == path:
                evidence[role] = path
        return ManagerSettings(
            revision=settings.revision,
            **{field: getattr(settings, field) for field in SETTINGS_FIELDS},
            last_validated_paths=MappingProxyType(evidence),
        )

    def _from_document(self, document: VersionedDocument) -> ManagerSettings:
        """Rebuild and validate a settings record from stored fields."""
        fields = document.fields
        # Reject records with unknown or wrongly typed fields
        unknown = set(fields).difference((*SETTINGS_FIELDS, "last_validated_paths"))
        if unknown:
            raise SettingsValidationError(f"unknown settings fields: {sorted(unknown)}")
        values: dict[str, str | None] = {}
        for field in SETTINGS_FIELDS:
            value = fields.get(field)
            if value is not None and not isinstance(value, str):
                raise SettingsValidationError(f"{field} must be a string or null")
            values[field] = value
        # Apply the same validation used for new saves
        values["steam_account_name"] = validate_steam_account_name(values["steam_account_name"])
        values["steam_authentication_mode"] = _normalize_authentication_mode(
            values["steam_authentication_mode"],
        )
        _validate_auth_pair(
            values["steam_account_name"],
            values["steam_authentication_mode"],
        )
        raw_evidence = fields.get("last_validated_paths", {})
        if not isinstance(raw_evidence, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_evidence.items()
        ):
            raise SettingsValidationError("last_validated_paths must map strings to strings")
        return ManagerSettings(
            revision=document.revision,
            **values,
            last_validated_paths=MappingProxyType(dict(raw_evidence)),
        )

    @staticmethod
    def storage_fields(settings: ManagerSettings) -> dict[str, object]:
        """Return the persistable field mapping for a settings record."""
        return {
            **{field: getattr(settings, field) for field in SETTINGS_FIELDS},
            "last_validated_paths": dict(settings.last_validated_paths),
        }
