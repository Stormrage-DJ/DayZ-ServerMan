"""Read actual mission-map record identities and fingerprints without rebinding plan references."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.mission_map_records import identity, stale_references
from ..domain.models import RevisionConflict
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_records import AssociationRecordStore, OriginalStore, RecordCorrupt


class MissionMapRecordReader:
    """Provide a published record snapshot for Draft changes and D3 freshness checks."""

    def __init__(self, area: Path) -> None:
        """Bind reads to the manager editor area; commit staging files remain invisible."""
        self._area = area

    def snapshot(self, plan: dict[str, Any]) -> tuple[str | None, list[str]]:
        """Return the actual last-applied fingerprint and stale reference names for this plan."""
        association = plan["association"]
        profile_id, key = association["profile_id"], association["mission_key"]
        identities: dict[str, dict[str, Any] | None] = {}
        fingerprint = None
        # Read published baseline and applied records, including a record absent from the plan references
        for kind in ("baseline", "applied"):
            store = AssociationRecordStore(self._area, profile_id, key, kind, staging=StagingPolicy.OBSERVER)
            found = store.read()
            if found is None:
                identities[kind] = None
                continue
            record, revision = found
            if (record["profile_id"], record["mission_key"]) != (profile_id, key):
                raise RecordCorrupt(store.folder, "the record names another association")
            identities[kind] = identity(kind, record, revision)
            if kind == "applied":
                fingerprint = record["configuration_fingerprint"]
        # D3 compares explicit references only; a null reference never silently acquires an identity
        originals = []
        for reference in plan["records"]["originals"]:
            found = OriginalStore(self._area, reference["target_key"], staging=StagingPolicy.OBSERVER).identity()
            if found is not None:
                originals.append(found)
        stale = stale_references(plan["records"], originals, identities["baseline"], identities["applied"])
        return fingerprint, stale

    def require_fresh(self, plan: dict[str, Any]) -> str | None:
        """Refuse stale references under the caller's association lock, and return the actual fingerprint."""
        fingerprint, stale = self.snapshot(plan)
        if stale:
            raise RevisionConflict("Editor records changed; reload the plan: " + ", ".join(stale))
        return fingerprint
