"""Baseline locations and the editor-managed check of the medical feature service.

The service keeps each profile's whole-file baselines in the manager store and
still reads the legacy `.dayz_manager_backups` copy inside the mission. A
mission is editor-managed when `mapgroupproto.xml` holds a reserved group or a
profile has an editor apply record on it (6.3); leaf 8.13 coordinates the zone
toggle there.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..repositories.atomic_file import ContentChangedError
from ..repositories.medical_zone_fragments import holds_reserved_group
from ..repositories.mission_map_layout import APPLIED_FILE, TargetClass, target_key

# Mission-relative file owned by each reversible feature
FEATURE_PATHS = {
    "medical_loot_zones": Path("mapgroupproto.xml"),
}


class MedicalFeaturePaths:
    """Baseline paths, the exclusive baseline write and the editor-managed check (a base of the service)."""

    # The manager store of the baselines and the map editor area, set by the service
    _baselines: Path
    _mission_map: Path | None

    def _editor_managed(self, mission: Path, path: Path) -> bool:
        """Return whether the file holds an editor group or any profile has an editor apply on the mission."""
        if path.is_file() and holds_reserved_group(read_bytes_shared(path)):
            return True
        return self._editor_record(mission)

    def _editor_record(self, mission: Path) -> bool:
        """Return whether any profile has an editor apply on the mission."""
        if self._mission_map is None:
            return False
        key = target_key(TargetClass.MISSION, mission)
        return any((self._mission_map / "profiles").glob(f"*/{key}/{APPLIED_FILE}"))

    def zone_baseline_path(self, profile_id: str, mission: Path) -> Path | None:
        """Return the loot-zone baseline that the toggle reads for a resolved mission, or None (8.6 base rule)."""
        found = self._baseline_source(profile_id, "medical_loot_zones", mission / FEATURE_PATHS["medical_loot_zones"])
        return found if found.is_file() else None

    def _baseline(self, profile_id: str, feature: str) -> Path:
        """Return the manager-owned baseline path for one profile and feature."""
        return self._baselines / profile_id / f"{feature}.original.xml"

    def _baseline_source(self, profile_id: str, feature: str, path: Path) -> Path:
        """Return the baseline path, accepting the legacy in-mission location."""
        owned = self._baseline(profile_id, feature)
        # Prefer the manager-owned baseline when it exists
        if owned.exists():
            return owned
        # Fall back to the legacy backup location inside the mission folder
        legacy = path.parent / ".dayz_manager_backups" / path.name
        return legacy if legacy.is_file() else owned

    @staticmethod
    def _write_baseline(path: Path, content: bytes) -> None:
        """Create the baseline file exclusively and flush it to disk."""
        # Create the parent directory, then write the baseline exactly once
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError:
            # Refuse a conflicting pre-existing baseline
            if read_bytes_shared(path) != content:
                raise ContentChangedError("medical feature baseline already exists with different content")
