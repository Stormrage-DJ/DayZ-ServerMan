"""Retired associations of adoption (D2, 10.2): the retired marker, the dated history name and the chain end.

Each writer returns D4 record updates. One adoption journal stages them with its other records, so a crash
never leaves records moved without a retired marker, or the reverse (condition (a)). Nothing is deleted: a
retired folder and a history folder keep their files and are never applied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .mission_map_commit_records import RecordUpdate
from .mission_map_journal import MapJournal
from .mission_map_layout import RETIRED_FILE, association_folder, history_name, profile_folder
from .mission_map_plans import retired_marker
from .paths import PortablePaths


# UTC time form of the retired marker, as the plan service reads it
MARKER_TIME = "%Y-%m-%dT%H:%M:%SZ"


def marker_fields(journal: MapJournal, moment: datetime) -> dict[str, str]:
    """Return the marker of D2: the adoption operation ID, the time and the new mission key."""
    if moment.tzinfo is None:
        raise ValueError("a retired marker needs an aware time")
    return {"operation_id": journal.operation_id, "retired_at": moment.astimezone(timezone.utc).strftime(MARKER_TIME),
            "new_mission_key": journal.mission_key}


def retired_update(paths: PortablePaths, journal: MapJournal, old_key: str, moment: datetime) -> RecordUpdate:
    """Return the update that retires one old association folder of the journal's profile (OP-1)."""
    folder = association_folder(paths.mission_map, journal.profile_id, old_key)
    return RecordUpdate("retired", paths.relative(folder / RETIRED_FILE), marker_fields(journal, moment))


def history_update(paths: PortablePaths, journal: MapJournal, moment: datetime) -> RecordUpdate:
    """Return the update that moves the active folder aside to its dated history name in the same parent (D-2)."""
    area = paths.mission_map
    source = association_folder(area, journal.profile_id, journal.mission_key)
    target = profile_folder(area, journal.profile_id) / history_name(journal.mission_key, moment)
    relative = paths.relative(target)
    return RecordUpdate("rename", relative, {"source": paths.relative(source), "target": relative})


def path_change_updates(paths: PortablePaths, journal: MapJournal, old_keys: list[str],
                        moment: datetime) -> list[RecordUpdate]:
    """Return the retirement updates of a move or a copy after a path change of this profile.

    An existing folder of the active key goes to its history name first (D-2). Then each matching old folder of
    this profile is retired. A folder of another profile is never named here (OP-1).
    """
    active = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
    updates = [history_update(paths, journal, moment)] if active.is_dir() else []
    return updates + [retired_update(paths, journal, key, moment) for key in sorted(set(old_keys))]


def chain_end(area: Path, profile_id: str, mission_key: str) -> str:
    """Return the newest folder that the retired markers lead to from a folder; a broken or cyclic chain raises."""
    key, seen = mission_key, set()
    while True:
        # Damaged adoption evidence never gives a chain end, and never loops
        if key in seen or not association_folder(area, profile_id, key).is_dir():
            raise ValueError("the retired chain of this association is broken or cyclic")
        seen.add(key)
        marker = retired_marker(association_folder(area, profile_id, key))
        if marker is None:
            return key
        key = marker["new_mission_key"]


def chain_end_retirement(paths: PortablePaths, journal: MapJournal,
                         moment: datetime) -> tuple[str, list[RecordUpdate]]:
    """Return the chain end of a retired active folder and the updates of steps 1 and 3 of the retired case.

    Step 1 moves the retired active folder aside to its history name. Step 3 retires the chain-end folder, which
    then names the active key. The adoption commit adds the other two steps. Step 2 is the fresh active folder
    with the records of the chain end. Step 4 is the D5 table on the original of the active key.
    """
    active = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
    if retired_marker(active) is None:
        raise ValueError("the active association folder is not retired")
    end = chain_end(paths.mission_map, journal.profile_id, journal.mission_key)
    return end, [history_update(paths, journal, moment), retired_update(paths, journal, end, moment)]
