"""Commit rules of the staged original kinds of 10.8 (D5) and of the adoption kinds of 10.34 (AR-3).

10.8: supersede, restored copy and the original of a new key. 10.34: the plan, the retired marker and the folder
rename.

At step 7 the rename moves the active association folder to its dated history name. Then the sibling folder with
the fresh records becomes the active folder. The reverse step removes the sibling folder.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import rename_directory, rename_file
from ..domain.mission_map_plan import PlanForm, parse_plan
from ..domain.mission_map_records import PROVENANCE_FIELD, RecordShapeError, copy_matches, sealed
from ..domain.mission_map_values import IDENTIFIER, TARGET_KEY, PlanValidationError
from .backup_verification import path_has_reparse
from .json_store import StagingPolicy
from .mission_map_journal import MapJournal, MapRecord, record_staging
from .mission_map_layout import (
    CLOSED_PREFIX, INVALIDATED_PREFIX, association_folder, association_profiles, ordinal_spelling,
)
from .mission_map_plans import RETIRED_FIELDS, UTC_TIME, check_size
from .mission_map_records import ORIGINAL_KINDS, OriginalStore, RecordConflict
from .paths import PortablePaths


# Kinds of 10.34 and 10.35 without a stored manifest digest; the digest is that of D3, over the canonical JSON
# of the fields. The two move kinds publish by a rename at step 7: the folder rename and the closed record
VALUE_KINDS = frozenset(("plan", "retired", "rename", "history_file"))
MOVE_KINDS = frozenset(("rename", "history_file"))


# One staged candidate: its kind, its target path, the checked record and the published record at that path
Staged = tuple[str, str, dict[str, Any], "tuple[dict[str, Any], int] | None"]


def check_original_kinds(area: Path, journal: MapJournal, staged: list[Staged],
                         sources: Mapping[str, dict[str, Any]] | None = None) -> set[str]:
    """Check the original kinds of one commit and return the target paths of the originals that D5 replaces.

    No kind deletes a record. An original is replaced only when the same commit first keeps it as a superseded
    record. Also, no other profile may have an association folder for its target (A4 condition 3).
    `sources` maps the target path of a copied original of the case `restore` to its verified archived source.
    """
    superseded: dict[str, str] = {}
    replaced: dict[str, str] = {}
    for kind, target, record, found in staged:
        if kind not in ORIGINAL_KINDS:
            continue
        key, manifest = record["target_key"], record["manifest_sha256"]
        active = _active(area, key)
        if kind == "copied_original":
            # The original of a new key holds the content of the source original that it names
            link = record[PROVENANCE_FIELD]
            # A restore copy comes from the verified archive entry, because its key can be absent here (ruling (c))
            source = (sources or {}).get(target) if link["case"] == "restore" else _active(area, link["source_key"])
            if link["operation_id"] != journal.operation_id or source is None or not copy_matches(record, source):
                raise ValueError("a copied original differs from its source original or its operation")
        elif kind == "restored_copy":
            # D5 row "different original, shared": the backup original stays inactive beside the active one
            if active is None or active["manifest_sha256"] == manifest:
                raise ValueError("a restored copy needs a different active original")
        elif kind == "superseded":
            if set(association_profiles(area, key)) - {journal.profile_id}:
                raise RecordConflict("the original of a shared target is never replaced")
            if active is None or active["manifest_sha256"] != manifest:
                raise ValueError("a superseded record must keep the active original")
            superseded[key] = manifest
        elif found is not None and found[0]["manifest_sha256"] != manifest:
            # D5 row "different original, not shared": the replaced original is already kept by an earlier record
            if superseded.get(key) != found[0]["manifest_sha256"]:
                raise RecordConflict("a different protected original already exists for this target")
            replaced[target] = key
    # A superseded record is staged only for an original that the same commit replaces
    if set(superseded) != set(replaced.values()):
        raise ValueError("a superseded record needs the replacement of its original")
    return set(replaced)


def content_kind(kind: str) -> str:
    """Return the record kind whose stored file items a kind has: each original kind holds an original, and an
    invalidated record (10.10) is a last-applied record."""
    if kind in ("invalidated", "closed"):
        return "applied"
    return "original" if kind in ORIGINAL_KINDS else kind


def _active(area: Path, key: str) -> dict[str, Any] | None:
    """Return the published active original of a target key, or None."""
    return OriginalStore(area, key, staging=StagingPolicy.OBSERVER).read()


def parse_value(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Check the stored fields of a plan, a retired marker or a folder rename, and add the manifest digest."""
    fields = {key: value for key, value in raw.items() if key != "manifest_sha256"}
    if kind == "plan":
        if set(fields) != {"plan"}:
            raise RecordShapeError("a plan record holds only the plan")
        try:
            fields = {"plan": parse_plan(fields["plan"], PlanForm.STORED)}
            check_size(fields["plan"])
        except PlanValidationError as error:
            raise RecordShapeError(str(error)) from error
    elif kind == "retired":
        # The marker form that the plan service reads (D2): operation, UTC time and the new mission key
        if set(fields) != RETIRED_FIELDS or not all(isinstance(value, str) for value in fields.values()) or not (
                IDENTIFIER.fullmatch(fields["operation_id"]) and UTC_TIME.fullmatch(fields["retired_at"])
                and TARGET_KEY.fullmatch(fields["new_mission_key"])):
            raise RecordShapeError("a retired marker names its operation, its UTC time and the new mission key")
    elif set(fields) != {"source", "target"} or not all(isinstance(value, str) for value in fields.values()):
        raise RecordShapeError("a rename names its source and its target")
    record = sealed(fields)
    if raw.get("manifest_sha256", record["manifest_sha256"]) != record["manifest_sha256"]:
        raise RecordShapeError("the manifest digest differs from the record")
    return record


def check_value_identity(paths: PortablePaths, journal: MapJournal, kind: str, target: str,
                         record: dict[str, Any]) -> None:
    """Require a kind of 10.34 to name the association, the operation and the folders of its journal."""
    if kind == "plan":
        named = record["plan"]["association"]
        if ((named["profile_id"], named["mission_key"]) != (journal.profile_id, journal.mission_key)
                or ordinal_spelling(named["mission_root"]) != ordinal_spelling(journal.mission_root)
                or named["runtime_profile_key"] not in (None, journal.runtime_profile_key)):
            raise ValueError("the plan names another association than its journal")
    elif kind == "retired":
        # Only an existing association folder is retired; the marker never creates one
        folder = (paths.root / target).parent
        if ((record["operation_id"], record["new_mission_key"]) != (journal.operation_id, journal.mission_key)
                or not folder.is_dir() or path_has_reparse(folder)):
            raise ValueError("a retired marker names another operation or key, or its folder is missing")
    elif kind == "history_file":
        source, closed = history_paths(paths, journal, target)
        if (record["source"], record["target"]) != (paths.relative(source), target) or not source.is_file() or (
                closed.exists()):
            raise ValueError("a closed record renames an existing invalidated record to a free name")
    else:
        source = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
        if (record["source"], record["target"]) != (paths.relative(source), target) or not source.is_dir() or (
                paths.root / target).exists():
            raise ValueError("a rename moves the existing active folder of the journal to a free history name")


def stored_fields(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return the fields that a staged record file holds: a kind of 10.34 stores no manifest digest."""
    return {key: value for key, value in record.items() if kind not in VALUE_KINDS or key != "manifest_sha256"}


def sibling_folder(paths: PortablePaths, journal: MapJournal) -> str:
    """Return the sibling staging folder of the active folder, manager-relative: <active>.staged-<operation_id>."""
    active = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
    return record_staging(paths.relative(active), journal.operation_id)


def move_entries(paths: PortablePaths, journal: MapJournal, entries: list[MapRecord]) -> list[MapRecord]:
    """Return the entries in publish order, with the second folder move (Architect 00:21:00 in evidence 10.34).

    The records of the sibling folder publish first, still out of sight. Then the active folder moves to its
    history name, and the sibling folder becomes the active folder. The other records follow, so the retired
    markers publish last. Until step 7, load therefore shows the path-change state as before the commit.
    """
    sibling = sibling_folder(paths, journal)
    active = paths.relative(association_folder(paths.mission_map, journal.profile_id, journal.mission_key))
    staged = [entry for entry in entries if entry.target_path.startswith(sibling + "/")]
    history = [entry for entry in entries if entry.kind == "rename"]
    move = MapRecord("rename", active, sibling, False, None,
                     sealed({"source": sibling, "target": active})["manifest_sha256"])
    return staged + history + [move] + [entry for entry in entries if entry not in staged + history]


def rename_paths(paths: PortablePaths, journal: MapJournal, entry: MapRecord) -> tuple[Path, Path]:
    """Return the source and the target folder of a move entry, derived again.

    The history move takes the active folder. The second move takes the sibling folder, which is the staging
    path of the entry that targets the active folder.
    """
    active = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
    target = paths.root / entry.target_path
    source = paths.root / entry.staging_path if entry.target_path == paths.relative(active) else active
    if path_has_reparse(source) or path_has_reparse(target):
        raise OSError("an association folder move crosses a reparse point")
    return source, target


def located(paths: PortablePaths, journal: MapJournal, entry: MapRecord) -> Path:
    """Return where a record of the entry is now: a sibling record is in the active folder after the move."""
    sibling = paths.root / sibling_folder(paths, journal)
    target = paths.root / entry.target_path
    if target.parent == sibling and not sibling.exists():
        return association_folder(paths.mission_map, journal.profile_id, journal.mission_key) / target.name
    return target


def history_paths(paths: PortablePaths, journal: MapJournal, target: str) -> tuple[Path, Path]:
    """Return the invalidated record and its closed name in the active folder, derived again from the target."""
    folder = association_folder(paths.mission_map, journal.profile_id, journal.mission_key)
    closed = paths.root / target
    if closed.parent != folder or path_has_reparse(closed):
        raise OSError("a closed record is outside the active association folder")
    return folder / (INVALIDATED_PREFIX + closed.name.removeprefix(CLOSED_PREFIX)), closed


def move_record(paths: PortablePaths, journal: MapJournal, entry: MapRecord) -> bool:
    """Run the forward step of a move kind at step 7: a folder move, or the rename of an invalidated record."""
    if entry.kind == "rename":
        return move_folder(*rename_paths(paths, journal, entry))
    source, target = history_paths(paths, journal, entry.target_path)
    # An earlier rename counts as done; any other state is no proof (Architect 00:21:00 in evidence 10.34)
    if target.is_file() and not source.exists():
        return True
    if not source.is_file() or target.exists():
        return False
    rename_file(source, target)
    return target.is_file()


def move_folder(source: Path, target: Path) -> bool:
    """Forward step at step 7: move one folder; an earlier move counts as done, any other state is no proof."""
    if target.is_dir() and not source.exists():
        return True
    if not source.is_dir() or target.exists():
        return False
    rename_directory(source, target)
    return target.is_dir()


def discard_sibling(folder: Path, operation_id: str) -> bool:
    """Reverse step before the commit point: remove the sibling folder; False when that is not proven safe.

    Nothing moved before the commit point. The sibling folder may hold only staging of this operation and an
    empty content folder. Anything else is never deleted, and the journal then blocks for recovery.
    """
    if not folder.exists():
        return True
    mark = record_staging("", operation_id)
    for item in list(folder.iterdir()):
        if item.is_file() and item.name.endswith(mark):
            item.unlink()
        elif item.name == "files" and item.is_dir() and not any(item.iterdir()):
            item.rmdir()
        else:
            return False
    folder.rmdir()
    return True
