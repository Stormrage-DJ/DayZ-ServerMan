"""Restore original and Restore previous state of the map editor (leaf 8.8, R16, R17, A4, D2 to D5).

The preview is a read. The restore runs in the exclusive operation lane
(`RESTORE_MISSION_MAP_ORIGINAL` or `RESTORE_MISSION_MAP_PREVIOUS`). It reviews
again and requires the reviewed fingerprint.

Restore original also requires a
verified family. The gate runs on the review's own capability read before the
guard. It runs again inside the guard on a fresh read, before the journal
starts (Architect 2026-10-14 17:28:16). Restore previous state never consults it.

The commit step (8.4) then takes the installation guard with a proved
`STOPPED` server, the folder writer side and the association lock. A failure
before the commit point rolls every file and record back; after it, the commit
rolls forward or recovery is required. Partial success is never reported. A
restore that would change nothing writes nothing and returns `UNCHANGED`,
also under the guard.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..domain.mission_map_families import RESTORE_ORIGINAL, require_verified_family
from ..domain.mission_map_records import utc_text
from ..domain.mission_map_restore_texts import ORIGINAL, RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT
from ..repositories.mission_map_publication import MissionMapPublicationError
from ..repositories.paths import PortablePaths
from ..repositories.restore_paths import group_old_matches
from .mission_map_commit import MissionMapCommit
from .mission_map_preview import PreviewStale
from .mission_map_restore_records import build_restore
from .mission_map_restore_review import ProfileList, RestoreReader, RestoreRefused


class MissionMapRestore:
    """The backend of the two restore lane operations and of their preview; bridge registration is leaf 8.9."""

    def __init__(self, reader: RestoreReader, commit: MissionMapCommit, paths: PortablePaths, profiles: ProfileList,
                 *, clock: Callable[[], datetime] | None = None) -> None:
        """Keep the review, the commit step (built with the process's shared association locks) and the clock."""
        self._reader, self._commit, self._paths, self._profiles = reader, commit, paths, profiles
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(self, profile_id: object, mode: object) -> dict[str, Any]:
        """Return the restore answer with its fingerprint; the preview writes nothing."""
        return self._reader.review(profile_id, mode).answer

    def commit_point(self, operation_id: str) -> str | None:
        """Return where the journal of a failed restore stands (AR-4); the rule of the shared commit step."""
        return self._commit.commit_point(operation_id)

    def restore(self, profile_id: object, mode: object, fingerprint: object, *, operation_id: str) -> dict[str, Any]:
        """Restore the reviewed state of the profile's association; the caller holds the operation lane."""
        review = self._reader.review(profile_id, mode)
        context = review.context
        if context.mode == ORIGINAL:
            # The review read the capabilities of the target mission now; the plan terrain ID is never used (6.11)
            require_verified_family(context.terrain_id, RESTORE_ORIGINAL)
        if review.fingerprint != fingerprint:
            raise PreviewStale(RESTORE_STALE_TEXT)
        if review.answer["refusals"]:
            # The first refusal names the reason (no original, no previous state, an unprojectable file) and its file
            first = review.answer["refusals"][0]
            raise RestoreRefused(first["reason"], first["message"], path=first["file"])
        publication = build_restore(review, operation_id=operation_id, now=utc_text(self._clock()), paths=self._paths)

        def revalidate() -> None:
            """Under the guard: a fresh capability read for the gate of Restore original, then the same fingerprint."""
            if context.mode == ORIGINAL:
                require_verified_family(self._reader.capabilities(context.mission).terrain_id, RESTORE_ORIGINAL)
            self._reader.revalidate(profile_id, mode, review.fingerprint)

        summary = {"operation_id": operation_id, "mode": context.mode,
                   "files": [group.relative_path for group in publication.journal.groups],
                   "backups": publication.backups}
        if publication.unchanged:
            # Nothing is written, but a restore still needs the proved STOPPED server and a fresh review
            with self._commit.guard.stopped(context.root):
                revalidate()
            return {**summary, "result": "UNCHANGED"}
        existing = {record.values.profile_id for record in self._profiles.list()}
        try:
            result = self._commit.commit(publication.journal, publication.updates, publication.candidates,
                                         revalidate=revalidate, existing_profiles=existing)
        except MissionMapPublicationError as error:
            details = getattr(error, "details", None) or {}
            if error.code != "REVISION_CONFLICT" or details.get("reason") != "target_changed":
                raise
            # The commit step words a late file change for Apply; a restore names itself
            path = next((group.relative_path for group in publication.journal.groups
                         if group.state == "READY" and not group_old_matches(group)), "a mission file")
            conflict = MissionMapPublicationError("REVISION_CONFLICT", TARGET_CHANGED_TEXT.format(path=path))
            conflict.details, conflict.retryable = dict(details), True  # type: ignore[attr-defined]
            raise conflict from error
        return {**summary, "result": result["result"]}
