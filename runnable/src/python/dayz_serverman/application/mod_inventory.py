"""Read-only mod inventory for the selected server profile."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .profiles import ProfileService
from .settings import SettingsService
from .target_proofs import TargetProofLookup
from .update_check import CheckSnapshot
from ..domain.mod_row_state import RowInputs, TargetProof, row_state
from ..repositories.mod_metadata import read_mod_metadata
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier


class CheckSource(Protocol):
    """Source of the current remote facts and check state."""

    # Return a consistent view without any I/O
    def snapshot(self) -> CheckSnapshot: ...


class ModInventoryService:
    """Assemble the read-only mod inventory for one server profile."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService, *,
        check_source: CheckSource | None = None,
        target_proofs: TargetProofLookup | None = None,
    ) -> None:
        """Store the profile and settings services and the optional evidence sources."""
        self._profiles = profiles
        self._settings = settings
        self._check_source = check_source
        self._target_proofs = target_proofs

    def list(self, profile_id: str) -> list[dict[str, object]]:
        """Return per-mod rows with metadata and the merged row state."""
        return self.report(profile_id)[0]

    def report(self, profile_id: str) -> tuple[list[dict[str, object]], CheckSnapshot | None]:
        """Return the rows together with the check view they were derived from."""
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
        # Take one view of the remote facts and one target lookup for all rows
        snapshot = self._check_source.snapshot() if self._check_source is not None else None
        targets = self._targets(settings, profile, observations)
        # Build one ordered row per configured mod
        rows = []
        for order, mod in enumerate(profile.values.mods, start=1):
            workshop_id = mod.source.workshop_id
            # Read cached metadata only when the Workshop item is present on disk
            metadata = read_mod_metadata(root / workshop_id) if (
                root is not None and workshop_id is not None and (root / workshop_id).is_dir()
            ) else None
            observation = observations.get(workshop_id) if workshop_id else None
            # Merge local observation, remote fact and target proof into one state
            derived = row_state(RowInputs(
                source_kind=mod.source.kind,
                cache_readable=workshop_available,
                installed_manifest_id=getattr(observation, "installed_manifest_id", None),
                latest_manifest_id=getattr(observation, "latest_manifest_id", None),
                local_time_updated=getattr(observation, "installed_time_updated", None),
                fact=snapshot.facts.get(workshop_id) if snapshot and workshop_id else None,
                check_state=snapshot.check_state if snapshot else None,
                target=targets.get(mod.directory, TargetProof.UNKNOWN),
            ))
            # Prefer cached metadata names and versions over the raw directory
            rows.append({
                "order": order,
                "directory": mod.directory,
                "launch_scope": mod.launch_scope,
                "source_kind": mod.source.kind,
                "workshop_id": workshop_id,
                "name": metadata.name if metadata and metadata.name else mod.directory,
                "version": metadata.version if metadata and metadata.version else None,
                "state": derived.state,
                "time_updated": observation.installed_time_updated if observation else None,
                "remote_time_updated": derived.remote_time_updated,
                "remote_check": derived.remote_check,
                "pending_reason": derived.pending_reason,
            })
        return rows, snapshot

    def _targets(self, settings, profile, observations) -> dict[str, TargetProof]:
        """Look up the target proof of every installed Workshop mod of the profile."""
        if self._target_proofs is None:
            return {}
        # Only an installed item can have a proven server-folder copy
        items = [
            (mod.directory, mod.source.workshop_id,
             observations[mod.source.workshop_id].installed_manifest_id)
            for mod in profile.values.mods
            if mod.source.kind == "workshop" and mod.source.workshop_id in observations
        ]
        return self._target_proofs.resolve(getattr(settings, "dayz_root", None), items)

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
