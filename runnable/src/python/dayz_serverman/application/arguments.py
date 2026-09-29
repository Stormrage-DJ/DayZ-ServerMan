"""Deterministic direct-process DayZ argv generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..domain.profiles import ProfileRecord, ProfileValidationError
from ..repositories.backup_verification import path_has_reparse


@dataclass(frozen=True)
class LaunchCommand:
    """Resolved executable, working directory, and argv for a direct DayZ launch."""
    executable: Path
    working_directory: Path
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the launch command into bridge-friendly primitive values."""
        return {
            "executable": str(self.executable),
            "working_directory": str(self.working_directory),
            "argv": list(self.argv),
        }


def build_launch_command(record: ProfileRecord, dayz_root: str) -> LaunchCommand:
    """Resolve every profile path, then compose the direct DayZ launch command."""
    # Reject a linked or non-absolute root before resolving anything inside it
    raw_root = Path(dayz_root)
    if not raw_root.is_absolute() or path_has_reparse(raw_root):
        raise ProfileValidationError("configured DayZ root contains a link or reparse point")
    # Confirm the resolved root is a real directory
    root = raw_root.resolve(strict=False)
    if not root.is_dir():
        raise ProfileValidationError("configured DayZ root is not a safe accessible directory")
    values = record.values
    # Verify the executable and server configuration first
    executable = _resolve_existing(root, values.server_executable, "server executable", file=True)
    _resolve_existing(root, values.server_config, "server configuration", file=True)
    # Verify the optional runtime profile and mission directories
    if values.runtime_profile is not None:
        _resolve_existing(root, values.runtime_profile, "runtime profile", file=False)
    if values.mission_root is not None:
        _resolve_existing(root, values.mission_root, "mission root", file=False)
    # Verify every mod directory referenced by the profile
    for mod in values.mods:
        _resolve_existing(root, mod.directory, "mod directory", file=False)

    # Compose the DayZ argv from the validated profile values
    argv = [str(executable), f"-config={values.server_config}", f"-port={values.game_port}"]
    if values.runtime_profile is not None:
        argv.append(f"-profiles={values.runtime_profile}")
    if values.mission_root is not None:
        argv.append(f"-mission={values.mission_root}")
    # Split mods by launch scope so client and server lists stay separate
    client_mods = [mod.directory for mod in values.mods if mod.launch_scope == "client"]
    server_mods = [mod.directory for mod in values.mods if mod.launch_scope == "server"]
    if client_mods:
        argv.append(f"-mod={';'.join(client_mods)}")
    if server_mods:
        argv.append(f"-serverMod={';'.join(server_mods)}")
    # Append user extra arguments last, after manager-generated arguments
    argv.extend(values.extra_arguments)
    return LaunchCommand(executable, root, tuple(argv))


def _resolve_existing(root: Path, relative: str, role: str, *, file: bool) -> Path:
    """Resolve one profile-controlled path and reject links, escapes, or wrong types."""
    # Build the candidate from Windows-style profile segments
    candidate = root.joinpath(*PureWindowsPath(relative).parts)
    # Refuse links so a profile cannot smuggle in a reparse point
    if path_has_reparse(candidate):
        raise ProfileValidationError(f"profile {role} contains a link or reparse point")
    resolved = candidate.resolve(strict=False)
    # Require the resolved path to stay inside the DayZ root
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ProfileValidationError(f"profile {role} resolves outside the DayZ root") from error
    # Confirm the target has the required file or directory type
    valid = resolved.is_file() if file else resolved.is_dir()
    if not valid:
        expected = "file" if file else "directory"
        raise ProfileValidationError(f"profile {role} is not an accessible {expected}")
    return resolved
