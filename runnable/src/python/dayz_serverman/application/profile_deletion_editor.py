"""Remove the map editor records of a deleted profile and keep the records that other profiles share (D2, R20).

The association folders of the profile go before the profile record. A protected original goes only when the
same deletion removes its target folder and no other profile shares that target. The output ledger always stays.
Each removal starts with a rename into the deleted folder of the editor area. So an interruption never leaves a
half-removed record set in the live layout, and what stays in the deleted folder is reported as a remnant.
"""

from __future__ import annotations

import shutil
from contextlib import suppress
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from ..adapters.windows.shared_files import rename_directory
from ..repositories.mission_map_layout import (
    DELETED_FOLDER, TargetClass, association_profiles, deleted_name, original_folder, profile_folder, target_key,
)
from .mission_configuration import MissionPathError, _contained

# Highest number of dated names with the same stamp that one deleted folder can hold
NAME_ATTEMPTS = 100


@dataclass(frozen=True)
class EditorCleanup:
    """The editor folders that one deletion removes or keeps, decided before its first change."""

    profile_id: str
    # The folder of every association of the profile, when it exists
    association: Path | None
    # Target keys of the originals that go, and of the originals of the profile's targets that stay
    originals: tuple[str, ...]
    kept: tuple[str, ...]
    # Profile IDs that keep their profile record after this deletion
    known: frozenset[str]


class ProfileDeletionEditor:
    """Decide and remove the editor records of one profile deletion in the editor area."""

    def __init__(self, area: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Keep the editor area (PortablePaths.mission_map) and the clock of the dated names."""
        self.area = area
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare(self, profile: Any, root: Path, mission: Path, *, mission_removed: bool,
                others: Iterable[Any]) -> EditorCleanup:
        """Decide the removals; `mission_removed` tells if the deletion removes the resolved mission folder.

        The runtime profile folder is inside the generated folder, which the deletion always removes.
        """
        profile_id = profile.values.profile_id
        others = tuple(other for other in others if other.values.profile_id != profile_id)
        association = profile_folder(self.area, profile_id)
        removed, kept = [], []
        # The mission original stays while its folder stays or another profile has an association for it
        mission_key = _key(TargetClass.MISSION, mission)
        if mission_key is not None and original_folder(self.area, mission_key).is_dir():
            shared = set(association_profiles(self.area, mission_key)) - {profile_id}
            (removed if mission_removed and not shared else kept).append(mission_key)
        # The runtime original stays while another profile resolves to the same runtime profile folder
        runtime_key = _runtime_key(root, profile.values.runtime_profile)
        if runtime_key is not None and original_folder(self.area, runtime_key).is_dir():
            shared = any(_runtime_shared(root, other.values.runtime_profile, runtime_key) for other in others)
            (kept if shared else removed).append(runtime_key)
        return EditorCleanup(profile_id, association if association.is_dir() else None, tuple(removed),
                             tuple(kept), frozenset(other.values.profile_id for other in others))

    def paths(self, cleanup: EditorCleanup) -> tuple[Path, ...]:
        """Return the existing folders that the removal touches, for the link checks before the first change."""
        folders = [cleanup.association, *(original_folder(self.area, key) for key in cleanup.originals),
                   self.area / DELETED_FOLDER]
        return tuple(folder for folder in folders if folder is not None and folder.exists())

    def remove(self, cleanup: EditorCleanup) -> dict[str, Any]:
        """Move the decided folders away, remove the deleted folder's content and report what stays.

        A failed rename raises OSError and keeps that folder in place. A failed removal afterwards only leaves a
        remnant, because the folder is already out of the live layout.
        """
        moment = self._clock()
        deleted = self.area / DELETED_FOLDER
        # The association folders go first, so no original goes while a record of this profile can name it
        moves = [] if cleanup.association is None else [(cleanup.association, "profile", cleanup.profile_id)]
        moves += [(original_folder(self.area, key), "original", key) for key in cleanup.originals]
        for source, kind, name in moves:
            deleted.mkdir(exist_ok=True)
            _move(source, deleted, deleted_name(kind, name, moment))
        # Remove each moved folder, also the leftovers of an earlier interrupted deletion
        if deleted.is_dir():
            for entry in sorted(deleted.iterdir()):
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
            # An empty deleted folder goes too; one that cannot go yet is harmless
            if not any(deleted.iterdir()):
                with suppress(OSError):
                    deleted.rmdir()
        return {"removed_association_folders": cleanup.association is not None,
                "removed_originals": list(cleanup.originals), "kept_originals": list(cleanup.kept),
                "preserved_ledger": True, "remnants": self.remnants(cleanup.known)}

    def remnants(self, known: Iterable[str]) -> list[str]:
        """Return the deleted folders that stay and the profile folders of profiles without a record."""
        known = set(known)
        deleted, profiles = self.area / DELETED_FOLDER, self.area / "profiles"
        found = [f"{DELETED_FOLDER}/{entry.name}" for entry in sorted(deleted.iterdir())] if deleted.is_dir() else []
        if profiles.is_dir():
            found += [f"profiles/{entry.name}" for entry in sorted(profiles.iterdir()) if entry.name not in known]
        return found


def _move(source: Path, deleted: Path, name: str) -> None:
    """Rename a folder into the deleted folder under a free dated name; an existing name is never replaced."""
    for number in range(1, NAME_ATTEMPTS + 1):
        target = deleted / (name if number == 1 else f"{name}-{number}")
        if not target.exists():
            rename_directory(source, target)
            return
    raise OSError("the deleted editor folder has no free name")


def _key(target_class: TargetClass, resolved: Path) -> str | None:
    """Return the target key of a resolved folder; a path without a key form can have no editor records."""
    try:
        return target_key(target_class, resolved)
    except ValueError:
        return None


def _runtime_key(root: Path, relative: str | None) -> str | None:
    """Return the target key of a runtime profile folder, or None when it does not exist or has no key."""
    if relative is None:
        return None
    try:
        return _key(TargetClass.RUNTIME, _contained(root, root.joinpath(*PureWindowsPath(relative).parts),
                                                    directory=True))
    except MissionPathError:
        return None


def _runtime_shared(root: Path, relative: str | None, key: str) -> bool:
    """Return whether another profile's runtime profile folder can be the target of the key."""
    if relative is None:
        return False
    candidate = root.joinpath(*PureWindowsPath(relative).parts)
    try:
        return _key(TargetClass.RUNTIME, _contained(root, candidate, directory=True)) == key
    except MissionPathError:
        # An existing folder that cannot be proven separate keeps the original
        return candidate.exists()
