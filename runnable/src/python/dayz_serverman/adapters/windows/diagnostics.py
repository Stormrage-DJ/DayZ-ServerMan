"""Non-mutating Windows path diagnostics for configured installations."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PureWindowsPath
from typing import Callable

from ...domain.models import (
    ManagerSettings,
    PathDiagnostic,
    PathRole,
    PathStatus,
)


# Roles whose path must accept writes for the manager to operate
WRITABLE_ROLES = frozenset(
    (
        PathRole.DAYZ_ROOT,
        PathRole.STEAMCMD_ROOT,
        PathRole.WORKSHOP_CONTENT_ROOT,
        PathRole.BACKUP_ROOT,
    )
)
# Roles that must point at an executable file
FILE_ROLES = frozenset((PathRole.DAYZ_EXECUTABLE, PathRole.STEAMCMD_EXECUTABLE))


def _is_reparse(path: Path) -> bool:
    """Return whether the path entry carries the Windows reparse-point attribute."""
    # Inspect the entry itself so a junction or symlink is not followed
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    # 0x400 is FILE_ATTRIBUTE_REPARSE_POINT when the running Python omits the stat constant
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _is_network(path: Path) -> bool:
    """Return whether the path uses a UNC network anchor."""
    # A leading double backslash marks a UNC share rather than a local drive
    return PureWindowsPath(str(path)).anchor.startswith("\\\\")


class WindowsPathDiagnostics:
    """Classify configured path roles and explain any corrective action."""

    def __init__(
        self,
        writable_check: Callable[[Path], bool] | None = None,
        reparse_check: Callable[[Path], bool] | None = None,
        network_check: Callable[[Path], bool] | None = None,
    ) -> None:
        """Store the injectable checks used to probe each path."""
        self._writable = writable_check or (lambda path: os.access(path, os.W_OK))
        self._reparse = reparse_check or _is_reparse
        self._network = network_check or _is_network

    def inspect(
        self,
        settings: ManagerSettings,
        default_backup_root: Path,
    ) -> tuple[PathDiagnostic, ...]:
        """Inspect every configured role and return one diagnostic per role."""
        # Bind each role to its configured value; the backup root may fall back to the default
        configured = {
            PathRole.DAYZ_ROOT: settings.dayz_root,
            PathRole.DAYZ_EXECUTABLE: settings.dayz_executable,
            PathRole.STEAMCMD_ROOT: settings.steamcmd_root,
            PathRole.STEAMCMD_EXECUTABLE: settings.steamcmd_executable,
            PathRole.WORKSHOP_CONTENT_ROOT: settings.workshop_content_root,
            PathRole.BACKUP_ROOT: settings.custom_backup_root or str(default_backup_root),
        }
        # Re-check each role against the path recorded by the last validation
        results = [
            self._inspect_role(
                role,
                value,
                settings.last_validated_paths.get(role.value),
            )
            for role, value in configured.items()
        ]
        return tuple(results)

    def inspect_path(self, role: PathRole, path: str) -> PathDiagnostic:
        """Inspect one already normalized native selection without mutation."""
        return self._inspect_role(role, path, None)

    def _inspect_role(
        self,
        role: PathRole,
        configured: str | None,
        last_validated: str | None,
    ) -> PathDiagnostic:
        """Classify one role path and describe any corrective action."""
        # Human-readable role label used in every message
        label = role.value.replace("_", " ")
        # Report an unconfigured role immediately; no location is guessed
        if configured is None:
            return PathDiagnostic(
                role,
                PathStatus.UNCONFIGURED,
                None,
                f"{label} is not configured",
                f"Select the {label} location.",
            )
        path = Path(configured)
        # Apply the disqualifiers in cheap-to-expensive order and stop at the first failure
        try:
            if self._network(path):
                return self._result(role, PathStatus.UNSUPPORTED_NETWORK, path)
            if not path.exists():
                status = PathStatus.MOVED if last_validated == str(path) else PathStatus.MISSING
                return self._result(role, status, path)
            if self._reparse(path):
                return self._result(role, PathStatus.UNSUPPORTED_REPARSE, path)
            if role in FILE_ROLES and not path.is_file():
                return self._result(role, PathStatus.NOT_FILE, path)
            if role not in FILE_ROLES and not path.is_dir():
                return self._result(role, PathStatus.NOT_DIRECTORY, path)
            if role in WRITABLE_ROLES and not self._writable(path):
                return self._result(role, PathStatus.NOT_WRITABLE, path)
        except (OSError, PermissionError) as error:
            return PathDiagnostic(
                role,
                PathStatus.INACCESSIBLE,
                str(path),
                f"{label} cannot be inspected: {error}",
                f"Check access to the configured {label}; no alternative was selected.",
            )
        return self._result(role, PathStatus.READY, path)

    @staticmethod
    def _result(role: PathRole, status: PathStatus, path: Path) -> PathDiagnostic:
        """Build the diagnostic for one role from its final classification."""
        label = role.value.replace("_", " ")
        # Role-specific message for each classification
        messages = {
            PathStatus.READY: f"{label} is ready",
            PathStatus.MISSING: f"configured {label} does not exist",
            PathStatus.MOVED: f"previously validated {label} is missing or moved",
            PathStatus.NOT_FILE: f"configured {label} is not a file",
            PathStatus.NOT_DIRECTORY: f"configured {label} is not a directory",
            PathStatus.NOT_WRITABLE: f"configured {label} is not writable",
            PathStatus.UNSUPPORTED_NETWORK: f"network {label} is unsupported",
            PathStatus.UNSUPPORTED_REPARSE: f"reparse-point {label} is unsupported",
        }
        # Matching corrective action for each classification
        actions = {
            PathStatus.READY: "No action is required.",
            PathStatus.MISSING: f"Select the correct {label}; no alternative was guessed.",
            PathStatus.MOVED: f"Reconnect or select the moved {label}.",
            PathStatus.NOT_FILE: f"Select a file for {label}.",
            PathStatus.NOT_DIRECTORY: f"Select a directory for {label}.",
            PathStatus.NOT_WRITABLE: f"Choose a writable {label} or correct its permissions.",
            PathStatus.UNSUPPORTED_NETWORK: f"Choose a supported local {label}.",
            PathStatus.UNSUPPORTED_REPARSE: f"Choose a direct, non-reparse {label}.",
        }
        return PathDiagnostic(role, status, str(path), messages[status], actions[status])
