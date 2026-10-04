"""Steps of one remote check run: choose the ids and fetch them in bounded batches."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from ..domain.profiles import WORKSHOP_ID
from ..domain.update_check import (
    MAX_IDS_PER_REQUEST,
    RemoteBatchFailure,
    RemoteFact,
    RemoteItem,
    RemoteItemResult,
)
from ..domain.update_check_rules import MAX_IDS_PER_RUN, utc_text
from .update_check_ports import WorkshopRemoteCatalogPort


def select_ids(
    configured: Iterable[object], extra: Iterable[object] = (),
) -> tuple[tuple[str, ...], int]:
    """Return the ids of one run in numeric order and the count of ids left out."""
    # Keep each well-formed id once; a malformed id never reaches the request
    valid = {
        item for item in (*configured, *extra)
        if isinstance(item, str) and WORKSHOP_ID.fullmatch(item) is not None
    }
    ordered = sorted(valid, key=int)
    # Ids above the run limit get no fact in this run
    return tuple(ordered[:MAX_IDS_PER_RUN]), max(0, len(ordered) - MAX_IDS_PER_RUN)


def fetch_facts(
    catalog: WorkshopRemoteCatalogPort, ids: tuple[str, ...], *,
    clock: Callable[[], datetime], monotonic: Callable[[], float],
    deadline_seconds: float,
    on_answer: Callable[[tuple[str, ...]], None] | None = None,
) -> tuple[dict[str, RemoteFact], RemoteBatchFailure | None]:
    """Fetch the ids in sequence and return the facts with the first failure code.

    `on_answer` receives the ids of each batch at the moment its answer arrived.
    """
    facts: dict[str, RemoteFact] = {}
    expires = monotonic() + deadline_seconds
    # Send groups of at most 200 ids; zero ids send nothing
    for start in range(0, len(ids), MAX_IDS_PER_REQUEST):
        batch = ids[start:start + MAX_IDS_PER_REQUEST]
        # Each batch gets what is left of the run deadline
        remaining = expires - monotonic()
        if not remaining > 0:
            return facts, RemoteBatchFailure.TIMEOUT
        result = catalog.fetch(batch, remaining)
        # The first failed batch ends the run; earlier facts are kept
        if result.failure is not None:
            return facts, result.failure
        # Stamp the answers of this batch with the time they arrived
        checked_at = utc_text(clock())
        requested = set(batch)
        answered = []
        for item in result.items:
            if item.workshop_id not in requested:
                continue
            facts[item.workshop_id] = _fact(item, checked_at)
            answered.append(item.workshop_id)
        # Tell the caller which ids this batch answered, while the answer is new
        if on_answer is not None:
            on_answer(tuple(answered))
    return facts, None


def _fact(item: RemoteItem, checked_at: str) -> RemoteFact:
    """Return the storable fact of one answer in the strict form of the cache."""
    # An OK answer without a positive update time is not a usable fact
    if item.result is RemoteItemResult.OK and not (_integer(item.time_updated)
                                                   and item.time_updated > 0):
        return RemoteFact(RemoteItemResult.INVALID, None, None, checked_at)
    # Only an OK answer carries a time and a size
    if item.result is not RemoteItemResult.OK:
        return RemoteFact(item.result, None, None, checked_at)
    size = item.file_size if _integer(item.file_size) and item.file_size >= 0 else None
    return RemoteFact(item.result, item.time_updated, size, checked_at)


def _integer(value: object) -> bool:
    """Return whether the value is an integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)
