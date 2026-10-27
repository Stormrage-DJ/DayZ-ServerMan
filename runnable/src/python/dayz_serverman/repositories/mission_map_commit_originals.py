"""Build the staged original kinds of 10.8 for a restore or an adoption journal; the commit step checks them (D5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.mission_map_records import copied_original, sealed
from .json_store import StagingPolicy
from .mission_map_commit_records import RecordUpdate
from .mission_map_journal import MapJournal
from .mission_map_layout import TargetClass, original_folder
from .mission_map_records import ORIGINAL_FILE, OriginalStore, restored_copy_file, superseded_file
from .paths import PortablePaths


def supersede_updates(paths: PortablePaths, journal: MapJournal, incoming: dict[str, Any],
                      contents: Mapping[str, bytes]) -> list[RecordUpdate]:
    """Return the D5 updates that set an incoming original active on a target that is not shared.

    The first update keeps the current original as a superseded record beside it; nothing is deleted. The second
    replaces the active original under the revision that this read found. The caller proves that no other profile
    resolves to the target; the commit refuses a target with an association folder of another profile.
    """
    record = sealed(incoming)
    store = OriginalStore(paths.mission_map, record["target_key"], staging=StagingPolicy.OBSERVER)
    found = store.read_revision()
    if found is None or found[0]["manifest_sha256"] == record["manifest_sha256"]:
        raise ValueError("only a different active original can be superseded")
    current, revision = found
    kept = superseded_file(paths.mission_map, store.target_key, journal.operation_id)
    return [
        RecordUpdate("superseded", paths.relative(kept), current, contents=store.stored_contents(current)),
        RecordUpdate("original", paths.relative(store.folder / ORIGINAL_FILE), record, revision, contents),
    ]


def restored_copy_update(paths: PortablePaths, journal: MapJournal, incoming: dict[str, Any],
                         contents: Mapping[str, bytes]) -> RecordUpdate:
    """Return the update that keeps an incoming original as an inactive copy in the restoring profile's folder.

    It is the D5 row "different original, shared": the active original of the target stays unchanged.
    """
    record = sealed(incoming)
    target = restored_copy_file(paths.mission_map, journal.profile_id, journal.mission_key,
                                TargetClass(record["target_class"]), journal.operation_id)
    return RecordUpdate("restored_copy", paths.relative(target), record, contents=contents)


def copied_original_update(paths: PortablePaths, journal: MapJournal, source_key: str, *, display_path: str,
                           mission_root: str, case: str) -> RecordUpdate:
    """Return the update that gives a new key of the journal the content of a source original (10.2 OP-3).

    It serves a true move and a copy. The source original stays in place, and its content is read with digest
    verification.
    """
    source_store = OriginalStore(paths.mission_map, source_key, staging=StagingPolicy.OBSERVER)
    source = source_store.read()
    if source is None:
        raise ValueError("the source key has no protected original")
    # The new key is the journal key of the source class: the mission folder or the runtime profile
    mission = TargetClass(source["target_class"]) is TargetClass.MISSION
    key = journal.mission_key if mission else journal.runtime_profile_key
    if key is None:
        raise ValueError("the journal has no target key of the source class")
    record = copied_original(source, target_key=key, display_path=display_path, mission_root=mission_root, case=case,
                             operation_id=journal.operation_id)
    target = original_folder(paths.mission_map, key) / ORIGINAL_FILE
    return RecordUpdate("copied_original", paths.relative(target), record,
                        contents=source_store.stored_contents(source))
