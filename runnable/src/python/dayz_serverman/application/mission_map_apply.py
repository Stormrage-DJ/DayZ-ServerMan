"""Apply the reviewed mission map plan with reconciliation choices (leaf 8.7, R11, R16, R17, D3, D4).

The work runs in the exclusive operation lane (`APPLY_MISSION_MAP`). It
reviews the saved plan again and requires the reviewed fingerprint, a
verified family and a choice for each external change and each unavailable
encounter. The commit step (8.4) then takes the installation guard with a
proved `STOPPED` server, the folder writer side and the association lock.

The commit step calls `revalidate` twice. `revalidate` reads the capabilities
of the target mission again and calls the family gate with that terrain ID.
Then it reviews again and requires the same fingerprint. So the gate runs
inside the guard, before the journal starts (Architect 2026-10-14 17:28:16).

A failure before the commit point rolls every file and record back; after it,
the commit rolls forward or recovery is required. Partial success is never
reported. An Apply that would change no file and no record writes nothing and
returns `UNCHANGED`, also under the guard.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..domain.mission_map_families import APPLY, require_verified_family
from ..domain.mission_map_preview_texts import PREVIEW_STALE_TEXT, TARGET_CHANGED_TEXT
from ..domain.mission_map_records import utc_text
from ..domain.mission_map_terrain import TerrainCapabilities
from ..domain.profiles import ProfileRecord
from ..repositories.mission_map_publication import MissionMapPublicationError
from ..repositories.paths import PortablePaths
from ..repositories.restore_paths import group_old_matches
from .mission_map_apply_choices import (
    Choices, check_choices, check_review, chosen_base, chosen_candidates, parse_choices,
)
from .mission_map_apply_records import Publication, build_publication
from .mission_map_commit import MissionMapCommit
from .mission_map_preview import MissionMapPreview, PreviewReview, PreviewStale


class ProfileList(Protocol):
    """The profile list that names the existing profiles of a capture refusal (D2)."""

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every stored profile."""
        ...


class MissionMapApply:
    """The backend of the `APPLY_MISSION_MAP` lane operation; bridge registration is leaf 8.9."""

    def __init__(self, preview: MissionMapPreview, commit: MissionMapCommit, paths: PortablePaths,
                 profiles: ProfileList, *, capabilities: Callable[[Path], TerrainCapabilities] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        """Keep the preview, the commit step (built with the process's shared association locks) and the clock."""
        self._preview, self._commit, self._paths, self._profiles = preview, commit, paths, profiles
        self._capabilities = capabilities or preview.capabilities
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def apply(self, profile_id: object, fingerprint: object, choices: object, *, operation_id: str) -> dict[str, Any]:
        """Apply the reviewed plan of the profile; the caller holds the operation lane."""
        parsed = parse_choices(choices)
        review = self._preview.review(profile_id)
        context = review.context
        assert context is not None
        # The review read the capabilities of the target mission now; the plan terrain ID is never used (6.11)
        require_verified_family(context.terrain_id, APPLY)
        if review.fingerprint != fingerprint:
            raise PreviewStale(PREVIEW_STALE_TEXT)
        check_review(review)
        check_choices(parsed, review.answer)
        publication = self._publication(review, parsed, operation_id)

        def revalidate() -> None:
            """Under the guard: a fresh capability read for the family gate, then the same fingerprint."""
            require_verified_family(self._capabilities(context.mission).terrain_id, APPLY)
            self._preview.revalidate(profile_id, review.fingerprint)

        summary = {"operation_id": operation_id, "files": [group.relative_path for group in publication.journal.groups],
                   "incorporated": parsed.incorporated, "replaced": publication.replaced,
                   "excluded_encounters": list(parsed.excluded)}
        if publication.unchanged:
            # Nothing is written, but Apply still needs the proved STOPPED server and a fresh review
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
            # A late file change gets the Product Owner text 7c outside the commit module, as a restore does
            path = next((group.relative_path for group in publication.journal.groups
                         if group.state == "READY" and not group_old_matches(group)), "a mission file")
            conflict = MissionMapPublicationError("REVISION_CONFLICT", TARGET_CHANGED_TEXT.format(path=path))
            conflict.details, conflict.retryable = dict(details), True  # type: ignore[attr-defined]
            raise conflict from error
        return {**summary, "result": result["result"]}

    def commit_point(self, operation_id: str) -> str | None:
        """Return where the journal of a failed operation stands (AR-4); the commit step owns the rule."""
        return self._commit.commit_point(operation_id)

    def _publication(self, review: PreviewReview, choices: Choices, operation_id: str) -> Publication:
        """Return the commit of the chosen base and candidates."""
        base = chosen_base(review, choices)
        candidates = chosen_candidates(review, choices, base)
        return build_publication(review, choices, candidates, base, operation_id=operation_id,
                                 now=utc_text(self._clock()), paths=self._paths)
