"""Read-only Workshop identity discovery for legacy profile imports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..adapters.windows.shared_files import read_text_shared
from .backup_verification import is_reparse, path_has_reparse


# Steam application id for DayZ; Workshop content lives under this key
DAYZ_APP_ID = "221100"
# Metadata files larger than this byte bound are treated as unsafe and skipped
MAX_METADATA_SIZE = 256 * 1024
# Metadata assignments to harvest: the published id and the display name
_ID_PATTERN = re.compile(r"publishedid\s*=\s*([0-9]+)", re.IGNORECASE)
_NAME_PATTERN = re.compile(r'name\s*=\s*"([^"]+)"', re.IGNORECASE)
# Profile field names, punctuation-folded, that configure mod folders
_MOD_FIELDS = frozenset(("mod", "mods", "servermod", "servermods"))


def resolve_legacy_workshop_ids(
    root: Path, profiles: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[tuple[tuple[str, str], ...], tuple[Path, ...]]:
    """Resolve configured folders exactly as the legacy mod inventory did."""
    configured = _configured_folders(profiles)
    evidence: set[Path] = set()
    cache_names: dict[str, set[str]] = {}
    # Index downloaded Workshop item names for later fallback matches
    cache_root = root / "steamcmd" / "steamapps" / "workshop" / "content" / DAYZ_APP_ID
    if cache_root.is_dir() and not path_has_reparse(cache_root):
        for candidate in sorted(cache_root.iterdir(), key=lambda item: item.name):
            if not candidate.is_dir() or not candidate.name.isdigit() or is_reparse(candidate):
                continue
            _published, display_name = _read_metadata(root, candidate, evidence)
            if display_name:
                cache_names.setdefault(_normalized_name(display_name), set()).add(candidate.name)

    resolved: list[tuple[str, str]] = []
    # Resolve each configured folder, preferring its live metadata
    for folder in configured:
        live = root / folder
        published_id, display_name = _read_metadata(root, live, evidence)
        if not published_id:
            # Fall back only when exactly one cache item matches the folder name
            candidates: set[str] = set()
            for name in (display_name, folder.removeprefix("@")):
                if name:
                    candidates.update(cache_names.get(_normalized_name(name), ()))
            if len(candidates) == 1:
                published_id = candidates.pop()
        if published_id:
            resolved.append((folder, published_id))
    # Return the resolved pairs and the metadata files that served as evidence
    return tuple(resolved), tuple(sorted(evidence, key=lambda item: item.as_posix().casefold()))


def _configured_folders(
    profiles: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[str, ...]:
    """Collect distinct @-prefixed mod folder names from legacy profile fields."""
    folders: set[str] = set()
    for _relative, profile in profiles:
        for key, value in profile.items():
            # Match field names with case and punctuation folded away
            folded = "".join(mark for mark in key.casefold() if mark.isalnum())
            if folded not in _MOD_FIELDS or not isinstance(value, str):
                continue
            # Split multi-folder values and accept only plain folder names
            for raw in value.split(";"):
                folder = raw.strip().strip('"')
                if folder.startswith("@") and Path(folder).name == folder:
                    folders.add(folder)
    # Sort case-insensitively so resolution order stays deterministic
    return tuple(sorted(folders, key=str.casefold))


def _read_metadata(root: Path, folder: Path, evidence: set[Path]) -> tuple[str | None, str | None]:
    """Read the published id and display name from a mod folder's metadata files."""
    published_id = None
    display_name = None
    # Only the two known metadata files are considered, in fixed order
    for name in ("meta.cpp", "mod.cpp"):
        path = folder / name
        if not _safe_metadata(root, path):
            continue
        evidence.add(path)
        # Read tolerantly; undecodable bytes still yield best-effort text
        text = read_text_shared(path, encoding="utf-8", errors="ignore")
        if name == "meta.cpp":
            match = _ID_PATTERN.search(text)
            # Id zero marks an unpublished mod and cannot be a valid identity
            if match and match.group(1) != "0":
                published_id = match.group(1)
        else:
            match = _NAME_PATTERN.search(text)
            if match:
                display_name = match.group(1).strip()
    return published_id, display_name


def _safe_metadata(root: Path, path: Path) -> bool:
    """Return whether the metadata file is a bounded regular file inside the root."""
    try:
        return (
            path.is_file() and path.stat().st_size <= MAX_METADATA_SIZE
            and not is_reparse(path) and not path_has_reparse(path.parent)
            and path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
        )
    except OSError:
        return False


def _normalized_name(value: str) -> str:
    """Fold a display name to lowercase letters and digits for matching."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
