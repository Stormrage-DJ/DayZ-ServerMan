"""Adoption of a moved, copied or restored mission: the preview (10.13), the adopt step (10.35) and the load field.

The adopt step runs in the lane (`ADOPT_MISSION_MAP`). It reviews again, requires the reviewed fingerprint and
commits one `adopt` journal (AR-3). The load field comes from the records, the folders and the path state alone.
It reads no mission file and compares nothing (UX-2, Architect 2026-10-16 14:33:32 in evidence 10.1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.mission_map_adoption import MISSION_PATH_CHANGED, load_case, offer
from ..domain.mission_map_adoption_texts import ADOPTION_STALE_TEXT
from ..repositories.mission_map_layout import association_folder, has_invalidated
from ..repositories.mission_map_records import RecordCorrupt
from ..repositories.paths import PortablePaths
from .mission_map_adoption_commit import AdoptionCommit
from .mission_map_adoption_records import build_adoption
from .mission_map_adoption_review import AdoptionReader, source_facts
from .mission_map_apply_choices import ApplyRefused
from .mission_map_plans import MissionMapPlanService
from .mission_map_preview import PreviewStale


class MissionMapAdoption:
    """The adoption service of the owner session: the review, and the commit in the lane (10.35)."""

    def __init__(self, reader: AdoptionReader, commit: AdoptionCommit | None = None,
                 paths: PortablePaths | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        """Keep the adoption reader, the commit step and the clock of the dated names."""
        self.reader, self._commit, self._paths = reader, commit, paths
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(self, profile_id: object) -> dict[str, Any]:
        """Return the adoption answer with its fingerprint; nothing is written."""
        return self.reader.review(profile_id).answer

    def commit_point(self, operation_id: str) -> str | None:
        """Return where the journal of a failed adoption stands (AR-4)."""
        return None if self._commit is None else self._commit.commit_point(operation_id)

    def adopt(self, profile_id: object, fingerprint: object, *, operation_id: str) -> dict[str, Any]:
        """Adopt the reviewed case in one journaled commit; the caller holds the operation lane."""
        if self._commit is None or self._paths is None:
            raise RuntimeError("the adoption commit step is not composed")
        review = self.reader.review(profile_id)
        if review.fingerprint != fingerprint:
            raise PreviewStale(ADOPTION_STALE_TEXT)
        if review.answer["refusals"]:
            # The first refusal names the reason (nothing to adopt, no source, a file that differs) and its file
            first = review.answer["refusals"][0]
            raise ApplyRefused(first["reason"], first["message"], path=first["file"])
        profile = self.reader._plans._profiles.read(review.context.association["profile_id"])
        publication = build_adoption(review, profile, self._paths, operation_id=operation_id, moment=self._clock())
        result = self._commit.commit(publication.journal, publication.updates,
                                     revalidate=lambda: self.reader.revalidate(profile_id, review.fingerprint))
        return {"operation_id": operation_id, "case": review.context.case, "result": result["result"],
                "history_name": publication.history_name}


def load_offer(plans: MissionMapPlanService, root: Path, loaded: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the load field `adoption`: null, or `{"case": ...}` for the path state and the records of a load.

    The window and `map show` offer adoption only from this field and from `capture.adoption`, never from
    `read_only_reason` (UX-2). An unreadable source original counts as an existing folder, so the case is `copy`.
    The caller passes the load answer after its own read-only checks.
    """
    association, change = loaded["association"], loaded["path_change"]
    reason = loaded["read_only_reason"] if change is not None else None
    folder = association_folder(plans._area, association["profile_id"], association["mission_key"])
    exists, shared = True, True
    if reason == MISSION_PATH_CHANGED:
        try:
            exists, shared = source_facts(plans._area, root, plans._profiles.list(), association["profile_id"],
                                          change["shown_mission_key"])
        except RecordCorrupt:
            exists, shared = True, True
    # The path state comes first, so an invalidated record counts only without a path change. While the records
    # do not match the profile settings, restore is not offered (Architect 2026-10-17 09:30:36 in evidence 10.13)
    invalidated = has_invalidated(folder) and loaded["read_only_reason"] != "association_mismatch"
    return offer(load_case(reason, invalidated, old_folder_exists=exists, shared=shared))
