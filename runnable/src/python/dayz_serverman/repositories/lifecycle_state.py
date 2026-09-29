"""Durable audit evidence for the current-session server launch."""

from __future__ import annotations

from ..domain.lifecycle import LaunchEvidence, LifecycleFailure
from ..domain.models import RecordState, RecordUnavailable, RevisionConflict
from .json_store import VersionedJsonRepository


class LifecycleStateRepository:
    """Persist and guard the launch evidence of the current session."""

    def __init__(self, repository: VersionedJsonRepository, session_id: str) -> None:
        """Bind the repository and the session whose evidence is stored."""
        self._repository = repository
        self._session_id = session_id

    def ensure_writable(self) -> int | None:
        """Return the current revision, or fail closed when recovery is required."""
        inspection = self._repository.inspect()
        # A missing record starts a fresh lifecycle without a revision
        if inspection.state == RecordState.MISSING:
            return None
        # Only a valid record with well-formed fields may be overwritten
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise LifecycleFailure(
                "RECOVERY_REQUIRED",
                "Lifecycle state requires recovery before server control.",
                recovery_required=True,
            )
        if not _valid_fields(inspection.document.fields):
            raise LifecycleFailure(
                "RECOVERY_REQUIRED",
                "Lifecycle state contains invalid fields.",
                recovery_required=True,
            )
        # Hand back the revision so the next save is conflict-checked
        return inspection.document.revision

    def record_launch(self, evidence: LaunchEvidence) -> None:
        """Persist launch evidence for the active session."""
        # Refuse to record when the stored state needs recovery first
        revision = self.ensure_writable()
        try:
            # Store the session id together with the launch proof
            self._repository.save(
                {
                    "session_id": self._session_id,
                    "launch_evidence": evidence.to_dict(),
                },
                revision,
            )
        except (RecordUnavailable, RevisionConflict) as error:
            raise LifecycleFailure(
                "RECOVERY_REQUIRED",
                "Launch evidence could not be recorded safely.",
                recovery_required=True,
            ) from error
        except OSError as error:
            raise LifecycleFailure(
                "STORAGE_FAILURE",
                "Launch evidence could not be stored.",
                recovery_required=True,
            ) from error

    def record_stopped(self) -> None:
        """Persist the stopped state that clears stored launch evidence."""
        # Refuse to record when the stored state needs recovery first
        revision = self.ensure_writable()
        try:
            self._repository.save(
                {"session_id": self._session_id, "launch_evidence": None},
                revision,
            )
        except (OSError, RecordUnavailable, RevisionConflict) as error:
            raise LifecycleFailure(
                "RECOVERY_REQUIRED",
                "Stopped state could not be recorded safely.",
                recovery_required=True,
            ) from error


def _valid_fields(fields: object) -> bool:
    """Return whether stored lifecycle fields satisfy the state schema."""
    # Stored documents may arrive as plain dicts or mapping-like objects
    if not isinstance(fields, dict) and not hasattr(fields, "items"):
        return False
    values = dict(fields)
    # The record must hold exactly the session id and optional launch evidence
    if set(values) != {"session_id", "launch_evidence"}:
        return False
    if not isinstance(values["session_id"], str) or not values["session_id"]:
        return False
    evidence = values["launch_evidence"]
    # Absent evidence is the valid stopped state
    if evidence is None:
        return True
    if not isinstance(evidence, dict) or set(evidence) != {
        "pid", "executable_path", "creation_time_ns", "launch_token", "handle_token"
    }:
        return False
    # Check each launch field; booleans must not pass as integer ids
    pid = evidence["pid"]
    creation = evidence["creation_time_ns"]
    handle = evidence["handle_token"]
    return (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and isinstance(evidence["executable_path"], str)
        and bool(evidence["executable_path"])
        and (creation is None or isinstance(creation, int) and not isinstance(creation, bool))
        and isinstance(evidence["launch_token"], str)
        and bool(evidence["launch_token"])
        and (handle is None or isinstance(handle, str) and bool(handle))
    )
