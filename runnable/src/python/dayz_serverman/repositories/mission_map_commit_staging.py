"""Write DayZ-side candidate staging and verified recovery copies after the durable journal."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .mission_map_journal import MapJournal
from .mission_map_publication import MissionMapPublicationError
from .restore_paths import copy_verified, group_old_matches, matches


def stage_groups(journal: MapJournal, candidates: Mapping[tuple[str, str], bytes], fault: Callable[[str, int], None]) -> None:
    """Stage every candidate and recovery copy, without replacing any target."""
    for index, group in enumerate(journal.groups):
        # Refuse a stale target before taking its recovery copy
        if not group_old_matches(group):
            raise MissionMapPublicationError("TARGET_CHANGED", f"Mission file {group.relative_path} changed after the preview.")
        if group.old_existed:
            copy_verified(Path(group.target_path), Path(group.recovery_path or ""), group.old_digest)
        if group.new_exists:
            data = candidates[(group.target_class, group.relative_path)]
            if hashlib.sha256(data).hexdigest() != group.new_digest:
                raise ValueError("candidate bytes differ from the preview digest")
            stage = Path(group.staging_path)
            stage.parent.mkdir(parents=True, exist_ok=True)
            with stage.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if not matches(stage, group.new_digest):
                raise OSError("staged mission map target failed verification")
        fault("AFTER_STAGE_GROUP", index)
