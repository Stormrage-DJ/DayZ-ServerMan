"""Remove orphan editor commit staging and retain plan-save evidence in manager storage."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..adapters.windows.shared_files import replace_file
from ..domain.profiles import validate_profile_id
from .backup_verification import path_has_reparse
from .json_store import StagingPolicy
from .mission_map_commit_records import parse_record
from .mission_map_contents import ContentStore
from .mission_map_journal_store import MissionMapJournalRepository, journal_folder
from .mission_map_layout import PLAN_STAGING_GLOB, interrupted_name, is_target_key
from .mission_map_records import RecordFile
from .paths import PortablePaths

if TYPE_CHECKING:
    from ..application.mission_map_locks import AssociationLocks


# Only record commit staging carries an operation identifier; plan staging uses a different pattern
COMMIT_STAGE = re.compile(r"[A-Za-z0-9_-]{1,64}\.json\.staged-[A-Za-z0-9_-]{1,64}")


def cleanup_orphans(paths: PortablePaths, locks: AssociationLocks) -> None:
    """Clean only derived editor folders when every active editor journal has been retired."""
    if MissionMapJournalRepository(journal_folder(paths), create_root=False).records():
        return
    area = paths.mission_map
    if not area.exists():
        return
    _safe(area, paths.root)
    # Original and ledger folders are keyed by physical target identity
    for branch in ("originals", "ledger"):
        parent = area / branch
        _safe(parent, paths.root)
        for folder in sorted(parent.glob("*")):
            if is_target_key(folder.name):
                names = {"original"} if branch == "originals" else None
                _clean_folder(folder, paths.root, names)
    # Plan saves use the same association locks, so their active staging never appears orphaned
    parent = area / "profiles"
    _safe(parent, paths.root)
    for profile in sorted(parent.glob("*")):
        _safe(profile, paths.root)
        try:
            validate_profile_id(profile.name)
        except ValueError:
            continue
        for folder in sorted(profile.glob("*")):
            if is_target_key(folder.name):
                with locks.hold(profile.name, folder.name):
                    _clean_folder(folder, paths.root, {"baseline", "applied"})
                    _preserve_plans(folder, paths.root)


def _clean_folder(folder: Path, root: Path, names: set[str] | None) -> None:
    """Prove referenced content before deleting orphan stages or unused content."""
    _safe(folder, root)
    _safe(folder / "files", root)
    keep: set[str] = set()
    for name, kind in (("original.json", "original"), ("baseline.json", "baseline"), ("applied.json", "applied")):
        target = folder / name
        _safe(target, root)
        found = RecordFile(target, lambda raw, kind=kind: parse_record(kind, raw), StagingPolicy.OBSERVER).read()
        if found is not None:
            items = found[0].get("files_before", []) if kind == "applied" else found[0]["files"]
            keep.update(item["sha256"] for item in items if item["existed"])
    for stage in sorted(folder.glob("*.json.staged-*")):
        _safe(stage, root)
        if COMMIT_STAGE.fullmatch(stage.name) and (names is None or stage.name.split(".json.", 1)[0] in names):
            stage.unlink()
    # Check each content entry before the store can remove it
    for entry in (folder / "files").glob("*"):
        _safe(entry, root)
    ContentStore(folder).sweep(keep)


def _preserve_plans(folder: Path, root: Path) -> None:
    """Rename each plan-save leftover to dated evidence without replacing earlier evidence."""
    moment = datetime.now(timezone.utc)
    number = 1
    for leftover in sorted(folder.glob(PLAN_STAGING_GLOB)):
        _safe(leftover, root)
        while (target := folder / interrupted_name(moment, number)).exists():
            number += 1
        replace_file(leftover, target)
        number += 1


def _safe(path: Path, root: Path) -> None:
    """Refuse paths outside the manager root or through any reparse point."""
    if not path.resolve(strict=False).is_relative_to(root.resolve(strict=False)) or path_has_reparse(path):
        raise OSError("editor cleanup path is unsafe")
