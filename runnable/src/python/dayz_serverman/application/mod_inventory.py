"""Read-only mod inventory for the selected server profile."""

from __future__ import annotations

from pathlib import Path

from .profiles import ProfileService
from .settings import SettingsService
from ..repositories.mod_metadata import read_mod_metadata
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier


class ModInventoryService:
    """Assemble the read-only mod inventory for one server profile."""

    def __init__(self, profiles: ProfileService, settings: SettingsService) -> None:
        """Store the profile and settings services used for the report."""
        self._profiles = profiles
        self._settings = settings

    def list(self, profile_id: str) -> list[dict[str, object]]:
        """Return per-mod rows with metadata and verified cache state."""
        # Read the profile and the configured Workshop content root
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        root = Path(settings.workshop_content_root) if settings.workshop_content_root else None
        # Collect the distinct Workshop identifiers referenced by the profile
        workshop_ids = tuple(
            mod.source.workshop_id for mod in profile.values.mods
            if mod.source.kind == "workshop" and mod.source.workshop_id is not None
        )
        # Verify the Workshop cache once for all referenced items
        observations, workshop_available = self._observations(root, workshop_ids)
        # Build one ordered row per configured mod
        rows = []
        for order, mod in enumerate(profile.values.mods, start=1):
            workshop_id = mod.source.workshop_id
            # Read cached metadata only when the Workshop item is present on disk
            metadata = read_mod_metadata(root / workshop_id) if (
                root is not None and workshop_id is not None and (root / workshop_id).is_dir()
            ) else None
            observation = observations.get(workshop_id) if workshop_id else None
            # Prefer cached metadata names and versions over the raw directory
            rows.append({
                "order": order,
                "directory": mod.directory,
                "launch_scope": mod.launch_scope,
                "source_kind": mod.source.kind,
                "workshop_id": workshop_id,
                "name": metadata.name if metadata and metadata.name else mod.directory,
                "version": metadata.version if metadata and metadata.version else None,
                "state": self._state(mod.source.kind, observation, workshop_available),
                "time_updated": observation.installed_time_updated if observation else None,
            })
        return rows

    @staticmethod
    def _observations(
        root: Path | None, ids: tuple[str, ...],
    ) -> tuple[dict[str, object], bool]:
        """Verify the Workshop cache for the given item identifiers."""
        # Treat a profile without Workshop mods as fully available
        if not ids:
            return {}, True
        # Without a configured content root every item stays unavailable
        if root is None:
            return {}, False
        # A verification failure degrades to unavailable instead of failing the list
        try:
            return ({item.workshop_id: item
                     for item in WorkshopCacheVerifier(root).observe(ids)}, True)
        except CacheVerificationError:
            return {}, False

    @staticmethod
    def _state(source_kind: str, observation: object | None, available: bool) -> str:
        """Classify one mod against its verified cache observation."""
        # Non-workshop mods are always reported as local
        if source_kind != "workshop":
            return "LOCAL"
        if not available:
            return "UNAVAILABLE"
        if observation is None or not getattr(observation, "installed", False):
            return "NOT_DOWNLOADED"
        # Compare installed and latest manifest identifiers for update state
        installed = getattr(observation, "installed_manifest_id", None)
        latest = getattr(observation, "latest_manifest_id", None)
        if latest and installed != latest:
            return "UPDATE_AVAILABLE"
        return "CURRENT" if latest else "INSTALLED"
