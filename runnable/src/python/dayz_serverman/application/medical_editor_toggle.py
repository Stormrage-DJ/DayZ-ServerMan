"""The medical loot-zone toggle on a mission that the map editor manages (leaf 8.13, A6, D4).

The toggle is one D4 operation of kind `medical`. It runs in the existing
`APPLY_MEDICAL_FEATURE` lane operation, so the tweaks page and the command line
keep their calls. Preview returns the opaque `preview_fingerprint` (customer
decision 4); Apply needs the six request fields and that exact fingerprint.

The commit step (8.4) takes the installation guard with a proved `STOPPED`
server, the folder writer side and the association lock, in the D4 lock
order. A running server refuses with the Apply reason.

Inside the guard the request is validated and reviewed again, and the
fingerprint must be equal. Only then does a first Enable store its whole-file baseline, as the ordinary
toggle does. 8.5 recovers an interrupted toggle, because it is an editor journal.

A toggle never reads or applies a plan or a draft: it starts from the files
and the current applied record only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.medical_editor_texts import (
    FINGERPRINT_MISSING_TEXT, FINGERPRINT_STALE_TEXT, ZoneReviewStale, ZoneToggleRefused,
)
from ..domain.mission_map_records import utc_text
from ..repositories.paths import PortablePaths
from .medical_editor_records import build_toggle
from .medical_editor_review import ZoneReview, ZoneTarget, fingerprint_form, review_zone_toggle
from .medical_features import NO_ZONE_CHANGE
from .mission_map_commit import MissionMapCommit

# The loot-zone feature, the only one that an editor-managed mission coordinates
FEATURE = "medical_loot_zones"


class MedicalEditorToggle:
    """Preview and apply the zone toggle of an editor-managed mission through the D4 commit step."""

    def __init__(self, paths: PortablePaths, commit: MissionMapCommit, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        """Keep the manager layout, the commit step (with the process's association locks) and the clock."""
        self._paths, self._commit = paths, commit
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def review(self, target: ZoneTarget) -> ZoneReview:
        """Return the review of one toggle; the R17 refusal raises ZoneToggleRefused."""
        return review_zone_toggle(self._paths.mission_map, target)

    def preview(self, target: ZoneTarget) -> dict[str, Any]:
        """Return the zone preview with its fingerprint; nothing is written."""
        review = self.review(target)
        answer = {"profile_id": target.profile_id, "feature": FEATURE, "enabled": target.enabled,
                  "expected_digest": target.digest, "proposed_digest": _sha(review.candidate),
                  "changed_fields": [FEATURE] if review.changed else [], "preview_fingerprint": review.fingerprint}
        return answer if review.changed else dict(answer, message=NO_ZONE_CHANGE)

    def apply(self, target: ZoneTarget, fingerprint: object, checkpoint: Callable[[str, int], None], *,
              operation_id: str, reread: Callable[[], ZoneTarget | None],
              write_baseline: Callable[[Path, bytes], None]) -> dict[str, Any]:
        """Publish the reviewed toggle in the caller's lane; a stale review raises ZoneReviewStale.

        reread validates the request again and returns its target, or None when the mission is no
        longer editor-managed. write_baseline stores the whole-file baseline of a first Enable.
        """
        if not fingerprint_form(fingerprint):
            raise ZoneToggleRefused("fingerprint_missing", FINGERPRINT_MISSING_TEXT)
        review = self.review(target)
        if review.fingerprint != fingerprint:
            raise ZoneReviewStale(FINGERPRINT_STALE_TEXT)
        checkpoint("loaded", 20)
        result = {"profile_id": target.profile_id, "feature": FEATURE, "enabled": target.enabled}
        if not review.changed:
            # Nothing changes, so nothing is written, as on an ordinary mission
            checkpoint("verified", 100)
            return dict(result, digest=target.digest, changed_fields=[], message=NO_ZONE_CHANGE)
        publication = build_toggle(review, target, operation_id=operation_id, now=utc_text(self._clock()),
                                   paths=self._paths)
        checkpoint("validated", 60)

        def revalidate() -> None:
            """Under the guard: validate the request and review again; a first Enable then stores its baseline."""
            fresh = reread()
            if fresh is None or self.review(fresh).fingerprint != fingerprint:
                raise ZoneReviewStale(FINGERPRINT_STALE_TEXT)
            if not fresh.owned_baseline.exists():
                write_baseline(fresh.owned_baseline, review.medical)

        self._commit.commit(publication.journal, publication.updates, publication.candidates,
                            revalidate=revalidate, existing_profiles={target.profile_id})
        checkpoint("verified", 100)
        return dict(result, digest=_sha(review.candidate), changed_fields=[FEATURE])


def build_medical_editor(paths: PortablePaths, commit: MissionMapCommit) -> MedicalEditorToggle:
    """Return the toggle over the one commit step of the editor (Architect 2026-10-17 13:41:03, evidence 10.31).

    Apply, the restores, adoption and the toggle share that step, its journal folder and its association locks.
    """
    return MedicalEditorToggle(paths, commit)


def _sha(data: bytes) -> str:
    """Return SHA-256 of the bytes."""
    return hashlib.sha256(data).hexdigest()
