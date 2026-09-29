"""Legacy profile conversion and immutable migration previews."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from .legacy_arguments import convert_legacy_arguments, tokenize_windows_arguments
from .profiles import ModReference, ModSource, ProfileInput, ProfileValidationError, validate_profile_id
from ..security.sensitive import contains_sensitive_arguments


class MigrationValidationError(ValueError):
    """Raised when legacy profile data cannot be converted."""
    pass


@dataclass(frozen=True)
class MigrationConflict:
    """One blocking conflict found while converting a legacy profile."""

    code: str
    item_id: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Return the conflict as its persisted object."""
        return {"code": self.code, "item_id": self.item_id, "message": self.message}


@dataclass(frozen=True)
class ConvertedProfile:
    """Conversion result for one legacy profile entry."""

    item_id: str
    profile: ProfileInput | None
    warnings: tuple[str, ...]
    conflicts: tuple[MigrationConflict, ...]

    @property
    def selectable(self) -> bool:
        """Return whether the converted profile can be selected."""
        return self.profile is not None and not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        """Return the conversion result with its profile view."""
        value: dict[str, Any] = {
            "item_id": self.item_id,
            "selectable": self.selectable,
            "warnings": list(self.warnings),
            "conflicts": [item.to_dict() for item in self.conflicts],
        }
        if self.profile is not None:
            value["profile"] = ProfileRecordView.from_input(self.profile)
        return value


class ProfileRecordView:
    """Read-only profile view emitted by conversion results."""

    @staticmethod
    def from_input(profile: ProfileInput) -> dict[str, Any]:
        """Return the persisted view of a converted profile input."""
        return {
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "server_executable": profile.server_executable,
            "server_config": profile.server_config,
            "runtime_profile": profile.runtime_profile,
            "mission_root": profile.mission_root,
            "game_port": profile.game_port,
            "mods": [item.to_dict() for item in profile.mods],
            "extra_arguments": list(profile.extra_arguments),
        }


def convert_legacy_profile(
    relative_source: str,
    raw: Mapping[str, Any],
    dayz_root: Path,
    workshop_ids: Mapping[str, str] | None = None,
) -> ConvertedProfile:
    """Convert one legacy profile record into a version 2 converted profile."""
    item_id = f"profile:{relative_source}"
    conflicts: list[MigrationConflict] = []
    warnings: list[str] = []
    try:
        # Canonicalize field spellings and collect duplicates and unknown keys
        canonical, duplicate_conflicts, unknown = _canonical_fields(raw, item_id)
        conflicts.extend(duplicate_conflicts)
        identifier = validate_profile_id(Path(relative_source).stem.casefold())
        display_name = _text(canonical.get("name", Path(relative_source).stem), "name")
        structured = _structured_values(canonical)
        raw_arguments = canonical.get("args", "")
        # Detect credential options before conversion retains anything
        try:
            sensitive = contains_sensitive_arguments(tokenize_windows_arguments(raw_arguments))
        except (OSError, ValueError):
            sensitive = False
        converted = convert_legacy_arguments(raw_arguments, structured)
        for conflict in converted.conflicts:
            conflicts.append(MigrationConflict(conflict["code"], item_id, conflict["message"]))
        if sensitive:
            conflicts.append(MigrationConflict(
                "SENSITIVE_ARGUMENT", item_id,
                "A credential option needs manual review. Its value was not retained.",
            ))
        warnings.extend(converted.warnings)
        fields = converted.structured
        # Build the version 2 profile from the converted fields
        profile = ProfileInput.parse({
            "profile_id": identifier,
            "display_name": display_name,
            "server_executable": "DayZServer_x64.exe",
            "server_config": _relative(fields.get("config_path", "serverDZ.cfg"), dayz_root, "config"),
            "runtime_profile": _relative(fields.get("profiles_path", "profile"), dayz_root, "profiles"),
            "mission_root": _optional_relative(fields.get("mission_path"), dayz_root, "mission"),
            "game_port": _port(fields.get("game_port", "2302")),
            "mods": _mod_references(fields, dayz_root, workshop_ids or {}),
            "extra_arguments": list(converted.extra_arguments),
        })
        # Any recorded conflict makes the result non-selectable
        if conflicts:
            profile = None
    # Invalid data becomes a needs-review conflict instead of raising
    except (MigrationValidationError, ProfileValidationError, ValueError) as error:
        conflicts.append(MigrationConflict("NEEDS_REVIEW", item_id, str(error)))
        profile = None
    # Ignored and disposable legacy fields only produce warnings
    if unknown:
        warnings.append("Ignored unsupported legacy profile fields.")
    # Disposable editor tweaks never block selection
    if "tweaks" in canonical:
        warnings.append("Legacy disposable editor state was ignored.")
    return ConvertedProfile(item_id, profile, tuple(warnings), tuple(conflicts))


def _mod_references(
    fields: Mapping[str, object], root: Path, workshop_ids: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Return mod references for both launch scopes."""
    # Index known Workshop directories case-insensitively
    known = {directory.casefold(): workshop_id for directory, workshop_id in workshop_ids.items()}
    result: list[dict[str, Any]] = []
    # Scan client then server mod lists
    for scope, key in (("client", "mods"), ("server", "server_mods")):
        for directory in _mod_directories(fields.get(key, ""), root, key):
            workshop_id = known.get(directory.casefold())
            # Known directories become Workshop sources; the rest stay external
            source = ModSource("workshop", workshop_id) if workshop_id else ModSource("external", None)
            result.append(ModReference(directory, scope, source).to_dict())
    return result


def migration_fingerprint(value: Mapping[str, Any]) -> str:
    """Return the canonical fingerprint of a migration payload."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _structured_values(raw: Mapping[str, Any]) -> dict[str, object]:
    """Return structured switch values from recognized legacy fields."""
    values: dict[str, object] = {}
    # Map legacy switch spellings to structured field names
    mapping = {
        "config": "config_path", "port": "game_port", "profiles": "profiles_path",
        "mission": "mission_path", "mods": "mods",
    }
    for source, target in mapping.items():
        if source in raw and raw[source] not in (None, ""):
            values[target] = raw[source]
    server_mod = raw.get("servermod")
    if server_mod not in (None, ""):
        values["server_mods"] = server_mod
    return values


def _text(value: object, field: str) -> str:
    """Return a trimmed legacy text value or raise."""
    if not isinstance(value, str) or not value.strip() or any(mark in value for mark in "\x00\r\n"):
        raise MigrationValidationError(f"Legacy {field} is invalid.")
    return value.strip()


def _port(value: object) -> int:
    """Return the legacy port as an integer or raise."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MigrationValidationError("Legacy port is invalid.")
    text = str(value)
    if re.fullmatch(r"[0-9]+", text) is None:
        raise MigrationValidationError("Legacy port is not an integer.")
    return int(text)


def _optional_relative(value: object, root: Path, field: str) -> str | None:
    """Return the relative form of an optional legacy path."""
    return None if value in (None, "") else _relative(value, root, field)


def _relative(value: object, root: Path, field: str) -> str:
    """Return the DayZ-root-relative form of a legacy path."""
    if not isinstance(value, str) or not value.strip():
        raise MigrationValidationError(f"Legacy {field} path is invalid.")
    text = value.strip().strip('"')
    path = PureWindowsPath(text)
    # Absolute legacy paths must resolve inside the configured root
    if path.is_absolute() or path.drive or path.root:
        try:
            relative = Path(text).resolve(strict=False).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise MigrationValidationError(
                f"Legacy {field} path is outside the configured DayZ root."
            ) from error
        text = str(relative)
    # Validate through the shared profile path rules
    try:
        from .profiles import validate_relative_path

        normalized = validate_relative_path(text, field)
    except ProfileValidationError as error:
        raise MigrationValidationError(str(error)) from error
    assert normalized is not None
    return normalized


def _mod_directories(value: object, root: Path, field: str) -> tuple[str, ...]:
    """Return validated mod directories from a semicolon-separated list."""
    if value in (None, ""):
        return ()
    if not isinstance(value, str):
        raise MigrationValidationError(f"Legacy {field} must be text.")
    entries = value.split(";")
    if any(not item.strip() for item in entries):
        raise MigrationValidationError(f"Legacy {field} contains an empty mod entry.")
    # Every entry must convert to a valid relative directory
    return tuple(_relative(item.strip(), root, field) for item in entries)


# Legacy field names folded to canonical names, ignoring case and separators
_RECOGNIZED_FIELDS = {
    "name": "name", "config": "config", "port": "port", "profiles": "profiles",
    "mod": "mods", "mods": "mods", "servermod": "servermod",
    "servermods": "servermod", "mission": "mission", "args": "args", "tweaks": "tweaks",
}


def _canonical_fields(
    raw: Mapping[str, Any], item_id: str,
) -> tuple[dict[str, Any], tuple[MigrationConflict, ...], tuple[str, ...]]:
    """Split legacy profile fields into canonical values, conflicts, and unknown keys."""
    result: dict[str, Any] = {}
    conflicts: list[MigrationConflict] = []
    unknown: list[str] = []
    # Walk raw fields in order so conflicts stay deterministic
    for original, value in raw.items():
        # Fold each key to compare spellings without case or separators
        folded = "".join(mark for mark in original.casefold() if mark.isalnum())
        canonical = _RECOGNIZED_FIELDS.get(folded)
        if canonical is None:
            unknown.append(original)
            continue
        # The first recognized spelling wins; repeats become conflicts
        if canonical in result:
            conflicts.append(MigrationConflict(
                "DUPLICATE_RECOGNIZED_FIELD", item_id,
                f"Legacy profile repeats the recognized {canonical} field.",
            ))
            continue
        result[canonical] = value
    return result, tuple(conflicts), tuple(sorted(unknown, key=str.casefold))
