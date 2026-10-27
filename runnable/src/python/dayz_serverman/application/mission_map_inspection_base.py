"""The capacity base of an Inspect object answer: the working baseline of the association (6.9 ruling).

The working baseline is read as load reads records: the published record
through an observer store, and its stored content with digest verification.
Its manifest digest joins the recheck before publication. Without a working
baseline, the read pass reads the mapgroup files of the mission instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_read_pass import BUILDING_FILES
from ..repositories.mission_map_records import AssociationRecordStore, RecordCorrupt


class BaselineReader:
    """Read the working baseline record of one association and the stored bytes of its mapgroup files."""

    def __init__(self, area: Path, profile_id: str, mission_key: str) -> None:
        """Bind the observer store of the association."""
        self._mission_key = mission_key
        self._store = AssociationRecordStore(area, profile_id, mission_key, "baseline",
                                             staging=StagingPolicy.OBSERVER)

    def digest(self) -> str | None:
        """Return the manifest digest of the published working baseline, or None without one."""
        found = self._store.read()
        return None if found is None else found[0]["manifest_sha256"]

    def read(self, buildings: bool) -> tuple[str | None, dict[str, bytes | None] | None]:
        """Return the manifest digest and, when buildings is true, the mapgroup files of the working baseline.

        Without a working baseline both are None.
        """
        found = self._store.read()
        if found is None:
            return None, None
        record = found[0]
        return record["manifest_sha256"], (self._buildings(record) if buildings else None)

    def _buildings(self, record: dict[str, Any]) -> dict[str, bytes | None]:
        """Return the stored bytes of the three mapgroup files of the mission target; None for an absent file."""
        entries = {entry["path"]: entry for entry in record["files"] if entry["target_key"] == self._mission_key}
        result: dict[str, bytes | None] = {}
        for path in BUILDING_FILES:
            entry = entries.get(path)
            if entry is None:
                raise RecordCorrupt(self._store.folder, f"the working baseline has no entry for {path}")
            # Content is read with digest verification (D2)
            result[path] = self._store.contents.read(entry["sha256"]) if entry["existed"] else None
        return result
