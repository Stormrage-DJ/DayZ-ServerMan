"""Structured DayZ profile schema version 2 and validation."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any, Mapping, Sequence

from ..security.sensitive import contains_sensitive_arguments


# Stable profile identifier: lowercase letters, digits, hyphens, up to 64 characters
PROFILE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
# Workshop item identifier: 1 to 20 digits without a leading zero
WORKSHOP_ID = re.compile(r"[1-9][0-9]{0,19}")
# Windows device names that cannot be used as file names
RESERVED_IDS = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
# DayZ switches owned by structured profile fields
STRUCTURED_SWITCHES = frozenset(("config", "port", "mission", "mod", "servermod", "profiles"))
# Characters that alter Windows shell parsing when unquoted
SHELL_METACHARACTERS = frozenset("&|<>^\r\n\x00")
# Command interpreters that must never appear as raw arguments
UNSAFE_COMMANDS = frozenset(("cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"))
# Characters Windows forbids in path segments
INVALID_PATH_CHARACTERS = frozenset('<>:"|?*')
# Supported mod launch scopes
LAUNCH_SCOPES = frozenset(("client", "server"))
# Supported mod source kinds
SOURCE_KINDS = frozenset(("workshop", "external"))


class ProfileValidationError(ValueError):
    """Raised when profile data violates the version 2 schema."""
    pass


def validate_profile_id(value: object) -> str:
    """Return a validated profile identifier or raise."""
    if not isinstance(value, str) or PROFILE_ID.fullmatch(value) is None:
        raise ProfileValidationError("profile_id must be a lowercase stable identifier")
    # Reject Windows device names even though the pattern would allow them
    if value.casefold() in RESERVED_IDS:
        raise ProfileValidationError("profile_id is reserved on Windows")
    return value


def validate_relative_path(value: object, field: str, *, optional: bool = False) -> str | None:
    """Return a normalized DayZ-root-relative Windows path or raise."""
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or any(ord(mark) < 32 for mark in value):
        raise ProfileValidationError(f"{field} must be a non-empty relative path")
    # Normalize Unicode and unify separators before validation
    normalized = unicodedata.normalize("NFC", value).replace("/", "\\")
    # Reject absolute paths and drive-qualified roots
    if normalized.startswith("\\") or re.match(r"^[A-Za-z]:", normalized):
        raise ProfileValidationError(f"{field} must stay relative to the DayZ root")
    raw_parts = normalized.split("\\")
    # Reject empty, current, and parent segments
    if any(part in ("", ".", "..") for part in raw_parts):
        raise ProfileValidationError(f"{field} contains an empty or traversing segment")
    path = PureWindowsPath(normalized)
    if path.is_absolute() or path.drive or path.root:
        raise ProfileValidationError(f"{field} must stay relative to the DayZ root")
    # Enforce Windows segment rules: forbidden characters and reserved names
    for part in raw_parts:
        base = part.split(".", 1)[0].casefold()
        if (
            any(character in part for character in INVALID_PATH_CHARACTERS)
            or part.endswith((" ", "."))
            or base in RESERVED_IDS
        ):
            raise ProfileValidationError(f"{field} contains an invalid Windows path segment")
    return str(PureWindowsPath(*raw_parts))


@dataclass(frozen=True)
class ModSource:
    """One mod origin: a Workshop item or an external directory."""

    kind: str
    workshop_id: str | None

    @classmethod
    def parse(cls, value: object) -> "ModSource":
        """Parse and validate a tagged mod source object."""
        if not isinstance(value, dict) or "kind" not in value:
            raise ProfileValidationError("mod source must be a tagged object")
        kind = value["kind"]
        if kind not in SOURCE_KINDS:
            raise ProfileValidationError("mod source kind must be workshop or external")
        expected = {"kind", "workshop_id"} if kind == "workshop" else {"kind"}
        if set(value) != expected:
            raise ProfileValidationError("mod source fields do not match its kind")
        # Workshop sources carry exactly one numeric identifier
        workshop_id = value.get("workshop_id")
        if kind == "workshop" and (
            not isinstance(workshop_id, str) or WORKSHOP_ID.fullmatch(workshop_id) is None
        ):
            raise ProfileValidationError("Workshop mod source requires a 1 to 20 digit ID")
        return cls(kind, workshop_id)

    def to_dict(self) -> dict[str, str]:
        """Return the source as its persisted object."""
        result = {"kind": self.kind}
        if self.workshop_id is not None:
            result["workshop_id"] = self.workshop_id
        return result


@dataclass(frozen=True)
class ModReference:
    """One mod directory bound to a launch scope and source."""

    directory: str
    launch_scope: str
    source: ModSource

    @classmethod
    def parse(cls, value: object) -> "ModReference":
        """Parse and validate one mod reference object."""
        # Require the exact per-mod field set
        fields = {"directory", "launch_scope", "source"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ProfileValidationError("each mod must contain directory, launch_scope, and source")
        scope = value["launch_scope"]
        if scope not in LAUNCH_SCOPES:
            raise ProfileValidationError("mod launch_scope must be client or server")
        directory = validate_relative_path(value["directory"], "mod directory")
        assert directory is not None
        return cls(directory, scope, ModSource.parse(value["source"]))

    def to_dict(self) -> dict[str, Any]:
        """Return the reference as its persisted object."""
        return {
            "directory": self.directory,
            "launch_scope": self.launch_scope,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class ProfileInput:
    """Validated user-supplied profile values before persistence."""

    profile_id: str
    display_name: str
    server_executable: str
    server_config: str
    mission_root: str | None
    game_port: int
    mods: tuple[ModReference, ...]
    extra_arguments: tuple[str, ...]
    runtime_profile: str | None = None

    @classmethod
    def parse(cls, value: object) -> "ProfileInput":
        """Parse and validate a raw profile object."""
        # Require the exact profile field set
        fields = {
            "profile_id", "display_name", "server_executable", "server_config",
            "runtime_profile", "mission_root", "game_port", "mods", "extra_arguments",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ProfileValidationError("profile fields are missing or unknown")
        # Keep display names bounded and free of control characters
        display_name = value["display_name"]
        if (
            not isinstance(display_name, str) or not display_name.strip()
            or len(display_name) > 100 or any(mark in display_name for mark in "\x00\r\n")
        ):
            raise ProfileValidationError("display_name must contain 1 to 100 safe characters")
        # Enforce the DayZ game port range
        port = value["game_port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ProfileValidationError("game_port must be from 1 through 65535")
        mods_value, extras_value = value["mods"], value["extra_arguments"]
        if not isinstance(mods_value, list) or not isinstance(extras_value, list):
            raise ProfileValidationError("mods and extra_arguments must be arrays")
        # Parse mod references and reject duplicates across scopes
        mods = tuple(ModReference.parse(item) for item in mods_value)
        _reject_duplicate_mods(mods)
        return cls(
            profile_id=validate_profile_id(value["profile_id"]),
            display_name=display_name.strip(),
            server_executable=_required_path(value["server_executable"], "server_executable"),
            server_config=_required_path(value["server_config"], "server_config"),
            mission_root=validate_relative_path(value["mission_root"], "mission_root", optional=True),
            game_port=port,
            mods=mods,
            extra_arguments=validate_extra_arguments(extras_value),
            runtime_profile=validate_relative_path(
                value["runtime_profile"], "runtime_profile", optional=True,
            ),
        )


def _required_path(value: object, field: str) -> str:
    """Return a validated required relative path."""
    result = validate_relative_path(value, field)
    assert result is not None
    return result


def _reject_duplicate_mods(mods: Sequence[ModReference]) -> None:
    """Reject repeated mod directories and Workshop identifiers."""
    # Compare directories case-insensitively to match Windows path semantics
    directories = [item.directory.casefold() for item in mods]
    workshop_ids = [item.source.workshop_id for item in mods if item.source.workshop_id]
    if len(directories) != len(set(directories)):
        raise ProfileValidationError("mods must not repeat a directory across launch scopes")
    # A Workshop item must appear in at most one scope
    if len(workshop_ids) != len(set(workshop_ids)):
        raise ProfileValidationError("Workshop mods must not repeat an identifier")


def validate_extra_arguments(values: Sequence[object]) -> tuple[str, ...]:
    """Return validated extra arguments, rejecting credential options."""
    validated = tuple(validate_extra_argument(value) for value in values)
    # Credential options must never reach persisted settings
    if contains_sensitive_arguments(validated):
        raise ProfileValidationError("extra arguments contain a credential option")
    return validated


def validate_extra_argument(value: object) -> str:
    """Return one validated raw argv token."""
    if not isinstance(value, str) or not value:
        raise ProfileValidationError("extra arguments must be non-empty strings")
    if any(character in value for character in SHELL_METACHARACTERS):
        raise ProfileValidationError("extra argument contains a shell metacharacter")
    # Arguments must arrive as unquoted argv tokens
    if '"' in value or "'" in value:
        raise ProfileValidationError("extra arguments must be unquoted argv tokens")
    normalized = value.casefold()
    switch = normalized.lstrip("-").split("=", 1)[0]
    # Structured switches belong to profile fields, not extra arguments
    if normalized.startswith("-") and switch in STRUCTURED_SWITCHES:
        raise ProfileValidationError(f"extra argument duplicates structured switch: {switch}")
    # Reject interpreter invocations and inline command constructs
    if normalized in UNSAFE_COMMANDS or normalized in ("/c", "/k", "-command", "-encodedcommand"):
        raise ProfileValidationError("extra argument contains an unsafe command construct")
    return value


def semantic_profile_digest(values: ProfileInput) -> str:
    """Return the canonical digest of the profile's semantic values."""
    # Only semantic fields are signed; revision bookkeeping is excluded
    semantic = {
        "server_executable": values.server_executable,
        "server_config": values.server_config,
        "runtime_profile": values.runtime_profile,
        "mission_root": values.mission_root,
        "game_port": values.game_port,
        "mods": [item.to_dict() for item in values.mods],
        "extra_arguments": list(values.extra_arguments),
    }
    encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProfileRecord:
    """Persisted profile revision paired with its validated values."""

    revision: int
    values: ProfileInput

    @property
    def semantic_digest(self) -> str:
        """Return the digest of the current semantic values."""
        return semantic_profile_digest(self.values)

    def to_dict(self) -> dict[str, Any]:
        """Return the record with revision, digest, and fields."""
        return {"revision": self.revision, "semantic_digest": self.semantic_digest, **self.fields()}

    def fields(self) -> Mapping[str, Any]:
        """Return the persisted profile field mapping."""
        values = self.values
        return {
            "profile_id": values.profile_id, "display_name": values.display_name,
            "server_executable": values.server_executable, "server_config": values.server_config,
            "runtime_profile": values.runtime_profile, "mission_root": values.mission_root,
            "game_port": values.game_port, "mods": [item.to_dict() for item in values.mods],
            "extra_arguments": list(values.extra_arguments),
        }
