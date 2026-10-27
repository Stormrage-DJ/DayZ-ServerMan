"""Serialize preference patches and retry one competing revision."""
from threading import RLock

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordState, RecordUnavailable, RevisionConflict


class PreferenceStore:
    """Share one transaction boundary across every preference writer."""

    def __init__(self, repository):
        """Retain one repository and its write lock."""
        self.repository = repository
        self.lock = RLock()

    def update(self, transform):
        """Merge against a fresh inspection, with one bounded conflict retry."""
        with self.lock:
            for attempt in range(2):
                inspection = self.repository.inspect()
                if inspection.state not in (RecordState.MISSING, RecordState.VALID):
                    raise ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "UI preference storage requires recovery.")
                fields = dict(inspection.document.fields) if inspection.document else {}
                result = transform(fields)
                if result is None:
                    return
                expected = inspection.document.revision if inspection.document else None
                try:
                    self.repository.save(result, expected)
                    return
                except RevisionConflict as error:
                    if attempt:
                        raise ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True) from error
                except (OSError, RecordUnavailable, ValueError) as error:
                    raise ApplicationCallError(ErrorCode.STORAGE_FAILURE, "UI preferences could not be stored.") from error
