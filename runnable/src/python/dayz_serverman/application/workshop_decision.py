"""Decide, before SteamCMD starts, which Workshop items must be sent to it."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ..domain.update_check import RemoteFact, RemoteItemResult
from ..domain.workshop import RequiredWorkshopItem, WorkshopObservation


class FreshCheckSource(Protocol):
    """Source of remote facts checked during the call; a failure yields no fact."""

    # Join or start a check and return only the facts checked during this call
    def check_now(self, ids: Iterable[str]) -> Mapping[str, RemoteFact]: ...


@dataclass(frozen=True)
class LocalCacheState:
    """Local facts of the Workshop cache that the decision needs."""

    observations: Mapping[str, WorkshopObservation]
    steam_reports_complete: bool
    existing_source_directories: Collection[str]


def is_unchanged(
    fact: RemoteFact | None, observation: WorkshopObservation | None, *,
    steam_reports_complete: bool, source_directory_exists: bool,
) -> bool:
    """Return True only when every "unchanged" condition holds for one item."""
    # A missing fact or a non-success answer never proves "unchanged"
    if fact is None or fact.result is not RemoteItemResult.OK:
        return False
    # The item must have an installed record
    if observation is None or observation.installed_manifest_id is None:
        return False
    # The remote update time must equal the local update time
    if (fact.time_updated is None or observation.installed_time_updated is None
            or fact.time_updated != observation.installed_time_updated):
        return False
    # A known latest manifest id must equal the installed one
    if (observation.latest_manifest_id is not None
            and observation.latest_manifest_id != observation.installed_manifest_id):
        return False
    # Steam must report no pending work, and the content directory must exist
    return steam_reports_complete is True and source_directory_exists is True


def items_to_send(
    items: tuple[RequiredWorkshopItem, ...],
    facts: Mapping[str, RemoteFact],
    local: LocalCacheState,
) -> tuple[RequiredWorkshopItem, ...]:
    """Return the items SteamCMD must process, in profile order."""
    return tuple(
        item for item in items
        if not is_unchanged(
            facts.get(item.workshop_id), local.observations.get(item.workshop_id),
            steam_reports_complete=local.steam_reports_complete,
            source_directory_exists=item.workshop_id in local.existing_source_directories,
        )
    )


def decide_sent_items(
    check_source: FreshCheckSource | None,
    items: tuple[RequiredWorkshopItem, ...],
    observations: Mapping[str, WorkshopObservation],
    verifier: object,
) -> tuple[RequiredWorkshopItem, ...]:
    """Run the fresh check and return the sent set; any doubt sends the item."""
    # Without a check source every item goes to SteamCMD, as before
    if check_source is None:
        return items
    try:
        facts = dict(check_source.check_now(tuple(item.workshop_id for item in items)))
    except Exception:
        # A failed check yields the full path
        return items
    if not facts:
        return items
    try:
        # Read the local cache state; an unreadable state proves nothing
        local = LocalCacheState(
            observations,
            verifier.steam_reports_complete(),
            frozenset(item.workshop_id for item in items
                      if verifier.source_directory_exists(item.workshop_id)),
        )
    except Exception:
        return items
    return items_to_send(items, facts, local)
