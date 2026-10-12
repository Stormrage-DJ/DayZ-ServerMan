"""Refuse protected-original capture of recognized editor output or reserved elements."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_ledger import refusal
from .mission_map_commit_records import RecordUpdate
from .mission_map_journal import MapJournal
from .mission_map_journal_paths import target_folder
from .mission_map_layout import TargetClass, original_folder
from .mission_map_ledger import LedgerStore
from .mission_map_publication import MissionMapPublicationError
from .paths import PortablePaths


class OriginalCaptureRefused(MissionMapPublicationError):
    """Readable capture refusal naming the prior output and its physical target."""

    def __init__(self, report: dict[str, object], *, adoption_required: bool) -> None:
        """Expose the ledger provenance and whether an original exists for adoption."""
        self.details = {"reason": "editor_output", "match": report, "adoption_required": adoption_required}
        profile = f" (profile {report['profile_id']})" if report["profile_id"] is not None else ""
        super().__init__("ORIGINAL_CAPTURE_REFUSED",
                         f"Original capture refused: editor output from operation {report['operation_id']} at "
                         f"{report['display_path']} [{report['mission_root']}]{profile} was found.")


def check_capture(
    paths: PortablePaths, journal: MapJournal, updates: list[RecordUpdate], existing_profiles: set[str],
    owned_digest: Callable[[TargetClass, str, bytes], str | None],
    check_reserved: Callable[[TargetClass, str, bytes], None],
) -> None:
    """Run the ledger and adapter D8 checks before staging any new original."""
    root = Path(journal.dayz_root).resolve(strict=True)
    folders = {journal.mission_key: (TargetClass.MISSION, target_folder(root, journal.mission_root))}
    if journal.runtime_profile is not None:
        folders[journal.runtime_profile_key] = (TargetClass.RUNTIME, target_folder(root, journal.runtime_profile))
    ledger = LedgerStore(paths.mission_map)
    for update in updates:
        if update.kind not in ("original", "baseline"):
            continue
        record = update.record
        # Baseline files also refuse reserved names before the adapter writes from them
        if update.kind == "baseline":
            for item in record["files"]:
                if item["existed"]:
                    check_reserved(folders[item["target_key"]][0], item["path"], update.contents[item["sha256"]])
            continue
        target_class = TargetClass(record["target_class"])
        published = original_folder(paths.mission_map, record["target_key"]) / "original.json"
        if published.exists():
            continue
        folder = folders[record["target_key"]][1]
        # The original captures every current managed file, including absence
        for item in record["files"]:
            target = folder / item["path"]
            data = read_bytes_shared(target) if target.exists() else None
            digest = None if data is None else hashlib.sha256(data).hexdigest()
            if (data is not None, digest) != (item["existed"], item["sha256"]):
                raise MissionMapPublicationError("REVISION_CONFLICT", "Original capture files changed after the preview.")
            if data is None:
                continue
            owned = owned_digest(target_class, item["path"], data)
            matches = ledger.capture_matches(target_class, item["path"], digest, owned)
            if matches:
                report = refusal(matches[0], existing_profiles)
                original = original_folder(paths.mission_map, report["target_key"]) / "original.json"
                raise OriginalCaptureRefused(report, adoption_required=original.exists())
            check_reserved(target_class, item["path"], data)
