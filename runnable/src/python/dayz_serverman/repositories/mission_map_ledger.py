"""Editor output ledger: create-once entries stored by target key and operation ID, looked up by file (D2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.mission_map_ledger import file_entry, matches, parse_entry
from ..domain.mission_map_records import RecordShapeError, sealed
from ..domain.mission_map_values import IDENTIFIER
from .json_store import StagingPolicy
from .mission_map_layout import FILE_SETS, TargetClass, is_target_key, ledger_folder
from .mission_map_records import RecordConflict, RecordCorrupt, RecordFile


class LedgerStore:
    """All output ledger entries of one editor area; profile or association deletion never removes one."""

    def __init__(self, area: Path, *, staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the store to the ledger folder of the editor area."""
        self.root = area / "ledger"
        self._area = area
        self._staging = staging

    def create(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Write one entry once; an identical rewrite does nothing and a different one is refused."""
        entry = _parse(sealed(raw))
        record = self._file(entry["target_key"], entry["operation_id"])
        found = record.read()
        if found is not None:
            if found[0]["manifest_sha256"] != entry["manifest_sha256"]:
                raise RecordConflict("a different ledger entry already exists for this target and operation")
            return found[0]
        record.save(entry, None)
        return entry

    def read(self, target_key: str, operation_id: str) -> dict[str, Any] | None:
        """Return one entry by its storage key, or None; an entry stored under other keys is corrupt."""
        record = self._file(target_key, operation_id)
        found = record.read()
        if found is not None and (found[0]["target_key"], found[0]["operation_id"]) != (target_key, operation_id):
            raise RecordCorrupt(record.path, "the entry names another target or operation than its path")
        return None if found is None else found[0]

    def entries(self, target_class: TargetClass, path: str) -> list[dict[str, Any]]:
        """Return the entries of every target of the class that changed the relative path, oldest first."""
        if not self.root.is_dir():
            return []
        found = []
        # Each target folder holds one record for each operation; an unreadable one raises RecordCorrupt
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not is_target_key(folder.name):
                continue
            for record in sorted(folder.glob("*.json")):
                if IDENTIFIER.fullmatch(record.stem) is None:
                    continue
                entry = self.read(folder.name, record.stem)
                if entry is not None and entry["target_class"] == target_class.value and file_entry(entry, path):
                    found.append(entry)
        return sorted(found, key=lambda entry: (entry["recorded_at"], entry["operation_id"]))

    def capture_matches(self, target_class: TargetClass, path: str, current_sha256: str | None,
                        current_owned_sha256: str | None) -> list[dict[str, Any]]:
        """Return the entries whose output the candidate file still is; any match refuses the capture."""
        return [entry for entry in self.entries(target_class, path)
                if matches(entry, path, current_sha256, current_owned_sha256)]

    def _file(self, target_key: str, operation_id: str) -> RecordFile:
        """Return the record file of one entry; the key and the operation ID are safe single names."""
        if IDENTIFIER.fullmatch(operation_id) is None:
            raise ValueError("a ledger operation ID must be an identifier")
        return RecordFile(ledger_folder(self._area, target_key) / f"{operation_id}.json", _parse, self._staging)


def _parse(raw: dict[str, Any]) -> dict[str, Any]:
    """Check an entry against the managed file set of its target class."""
    try:
        file_set = FILE_SETS[TargetClass(raw.get("target_class"))]
    except ValueError as error:
        raise RecordShapeError("ledger target_class must be mission or runtime") from error
    return parse_entry(raw, file_set)
