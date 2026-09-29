"""Build deterministic legacy migration publication targets."""

from __future__ import annotations

from typing import Any, Iterable

from ..domain.migrations import ConvertedProfile
from ..repositories.legacy_source import LegacyInventory
from ..repositories.migration_publication import PublicationTarget
from .migration_outputs import (
    legacy_index_output,
    profile_output,
    report_output,
    settings_output,
)


def build_publication_targets(
    migration_id: str,
    state: Any,
    selected: set[str],
    profiles: tuple[ConvertedProfile, ...],
    refreshed: LegacyInventory,
    selectable: set[str],
    *,
    settings: Any,
    settings_repository: Any,
    profile_repository: Any,
    backup_indexes: Any,
    storage: Any,
) -> tuple[list[dict[str, Any]], tuple[PublicationTarget, ...]]:
    """Build the publication targets for every selected migration item."""
    # Collect the published summary entries alongside the files to publish
    published: list[dict[str, Any]] = []
    targets: list[PublicationTarget] = []
    # Publish manager settings only when the installation item was selected
    if "settings:dayz-installation" in selected:
        current = settings.load()
        payload, revision = settings_output(
            settings, current, state.settings_updates, state.settings_revision,
        )
        targets.append(PublicationTarget(
            "SETTINGS", "Manager settings", settings_repository.path, payload,
        ))
        published.append({"kind": "SETTINGS", "revision": revision})
    # Publish converted profiles in a fixed identifier order
    for item in sorted(
        profiles,
        key=lambda value: (
            value.profile.profile_id.casefold() if value.profile else "",
            value.profile.profile_id if value.profile else "",
        ),
    ):
        payload, profile_id = profile_output(item)
        path = profile_repository.root / f"{profile_id}.json"
        targets.append(PublicationTarget("PROFILE", profile_id, path, payload))
        published.append({"kind": "PROFILE", "profile_id": profile_id, "revision": 0})
    # Publish the reviewed external backup index when it was selected
    if "backups:external-index" in selected:
        if state.backup_index is None:
            raise ValueError("Reviewed legacy backup index is unavailable.")
        payload, revision = legacy_index_output(
            state.backup_index, state.backup_index_revision,
        )
        targets.append(PublicationTarget(
            "LEGACY_BACKUP_INDEX", "Legacy backup index", backup_indexes.path, payload,
        ))
        published.append({
            "kind": "LEGACY_BACKUP_INDEX",
            "revision": revision,
            "entry_count": len(state.backup_index.entries),
            "inventory_digest": state.backup_index.inventory_digest,
        })
    # Always publish the migration report as review evidence
    report = report_output(
        migration_id, refreshed.source_digest,
        (item.to_dict() for item in refreshed.files), selected,
        selectable.difference(selected), published,
        _profile_warnings(profiles), state.backup_index,
        "backups:external-index" in selected,
    )
    targets.append(PublicationTarget(
        "REPORT", migration_id, storage.report_path(migration_id), report,
    ))
    return published, tuple(targets)


def _profile_warnings(profiles: Iterable[ConvertedProfile]) -> Iterable[str]:
    """Yield the profile warnings carried by converted legacy profiles."""
    return (warning for item in profiles for warning in item.warnings)
