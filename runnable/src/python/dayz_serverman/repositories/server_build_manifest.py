"""Read the installed DayZ server build from its Steam manifest and collect ownership signals.

Only the allowlisted fields of detailed design 14.2 are read. The parsed
document stays local to `_read_manifest` and is dropped there; `LastOwner`
and every other field are never accessed, returned, stored or logged.
"""

from __future__ import annotations

import ntpath
import os
import re
import stat
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import open_shared
from ..domain.server_build import (
    BRANCH_PATTERN,
    FLAG_FULLY_INSTALLED,
    MAX_BUILD_ID,
    SERVER_APP_ID,
    InstalledBuild,
    InstalledRead,
    OwnershipSignals,
    pending_reason,
)
from .steam_vdf import VdfError, field, parse_vdf

# A manifest or library marker above this size is not read
MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_NAME = f"appmanifest_{SERVER_APP_ID}.acf"
_BRANCH = re.compile(BRANCH_PATTERN)
_DECIMAL = re.compile(r"[0-9]{1,10}")


class ManifestUnreadable(ValueError):
    """Raised inside the reader for any manifest that breaks a rule; it carries no value."""


def read_installed(dayz_root: str | None, steamcmd_root: str | None) -> InstalledRead:
    """Find and read the manifest of the configured DayZ folder; never raises."""
    if not dayz_root:
        return InstalledRead(None, "NO_DAYZ_FOLDER")
    try:
        root = Path(dayz_root)
        candidate_a = root / "steamapps" / MANIFEST_NAME
        found_a = _regular_file(candidate_a)
        library_layout = (root.parent.name.casefold() == "common"
                          and root.parent.parent.name.casefold() == "steamapps")
        candidate_b = root.parent.parent / MANIFEST_NAME
        found_b = library_layout and _regular_file(candidate_b)
        build_b = None
        if found_b:
            # Candidate B counts only when its installdir names this folder
            build_b, installdir = _read_manifest(candidate_b)
            found_b = installdir.casefold() == root.name.casefold()
        if found_a and found_b:
            return InstalledRead(None, "MANIFEST_AMBIGUOUS")
        if not found_a and not found_b:
            return InstalledRead(None, "NO_MANIFEST")
        if found_a:
            build, _installdir = _read_manifest(candidate_a)
            signals = OwnershipSignals(layout="A", launcher_steam=build[1] == "steam.exe",
                                       launcher_steamcmd=build[1] == "steamcmd.exe")
        else:
            assert build_b is not None
            build = build_b
            signals = _library_signals(root.parent.parent.parent, steamcmd_root, build[1])
    except ManifestUnreadable:
        return InstalledRead(None, "MANIFEST_UNREADABLE")
    except (OSError, ValueError, RuntimeError):
        return InstalledRead(None, "MANIFEST_UNREADABLE")
    installed = build[0]
    # Without a pending reason the manifest must report a full installation
    if pending_reason(installed) is None and not installed.state_flags & FLAG_FULLY_INSTALLED:
        return InstalledRead(None, "NOT_INSTALLED", signals)
    return InstalledRead(installed, None, signals)


def _read_manifest(path: Path) -> tuple[tuple[InstalledBuild, str | None], str]:
    """Return ((build, launcher file name), installdir) from the allowlisted fields only."""
    data = _bounded_text(path)
    try:
        document = parse_vdf(data)
        state = field(document, "AppState")
        if not isinstance(state, dict) or field(state, "appid") != SERVER_APP_ID:
            raise ManifestUnreadable()
        installdir = _text(field(state, "installdir"))
        build_id = _build_id(field(state, "buildid"))
        target = _optional(state, "TargetBuildID")
        target_id = None if target in (None, "0") else _build_id(target)
        flags = _decimal(field(state, "StateFlags"))
        launcher = _optional(state, "LauncherPath")
        launcher_name = ntpath.basename(_text(launcher)).casefold() if launcher is not None else None
        mounted, selected = _branch(state, "MountedConfig"), _branch(state, "UserConfig")
    except VdfError as error:
        # The parser message names a field, never a value; it is still not kept
        raise ManifestUnreadable() from error
    # The mounted branch is what is installed; the selected one is what Steam will install
    branch = mounted if mounted is not None else selected if selected is not None else "public"
    change = mounted is not None and selected is not None and mounted != selected
    build = InstalledBuild(build_id, target_id, flags, branch, change)
    return (build, launcher_name), installdir


def _library_signals(library: Path, steamcmd_root: str | None, launcher: str | None) -> OwnershipSignals:
    """Collect the signals of the library layout (candidate B)."""
    configured = False
    if steamcmd_root:
        configured = (os.path.normcase(str(library.resolve(strict=False)))
                      == os.path.normcase(str(Path(steamcmd_root).resolve(strict=False))))
    return OwnershipSignals(
        layout="B", steamcmd_root=configured,
        steam_exe=_regular_file(library / "steam.exe", limit=None),
        client_library=_client_library(library / "libraryfolder.vdf"),
        launcher_steam=launcher == "steam.exe", launcher_steamcmd=launcher == "steamcmd.exe",
    )


def _client_library(path: Path) -> bool:
    """Return whether a secondary-library marker names a Steam client launcher."""
    try:
        if not _regular_file(path):
            return False
        marker = field(parse_vdf(_bounded_text(path)), "libraryfolder")
        launcher = _optional(marker, "launcher") if isinstance(marker, dict) else None
        return launcher is not None and ntpath.basename(_text(launcher)).casefold() == "steam.exe"
    except (OSError, ValueError, VdfError):
        return False


def _regular_file(path: Path, *, limit: int | None = MAX_MANIFEST_BYTES) -> bool:
    """Return whether the path is a regular file that is neither a link nor a reparse point.

    A file above the limit raises ManifestUnreadable, because it exists but cannot be read.
    """
    try:
        value = os.lstat(path)
    except OSError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISREG(value.st_mode) or getattr(value, "st_file_attributes", 0) & marker:
        return False
    if limit is not None and value.st_size > limit:
        raise ManifestUnreadable()
    return True


def _bounded_text(path: Path) -> str:
    """Read at most the size cap as strict UTF-8 with an optional byte order mark."""
    with open_shared(path) as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestUnreadable()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise ManifestUnreadable() from error


def _optional(mapping: dict[str, Any], name: str) -> Any:
    """Return the one field of that name, or None when the manifest has none."""
    matches = [key for key in mapping if key.casefold() == name.casefold()]
    return field(mapping, name) if matches else None


def _branch(state: dict[str, Any], section: str) -> str | None:
    """Return the normalized beta key of one configuration object, or None without the object."""
    config = _optional(state, section)
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ManifestUnreadable()
    key = _optional(config, "betakey")
    if key is None or key == "":
        return "public"
    if not isinstance(key, str) or _BRANCH.fullmatch(key) is None:
        raise ManifestUnreadable()
    return key


def _text(value: Any) -> str:
    """Return a scalar text value; an object in its place breaks the format."""
    if not isinstance(value, str):
        raise ManifestUnreadable()
    return value


def _decimal(value: Any) -> int:
    """Return an unsigned 32-bit decimal value."""
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None or int(value) > MAX_BUILD_ID:
        raise ManifestUnreadable()
    return int(value)


def _build_id(value: Any) -> int:
    """Return a build id from 1 to the 32-bit maximum."""
    number = _decimal(value)
    if number < 1:
        raise ManifestUnreadable()
    return number
