"""Editor publication through the shared engine: the editor policy, the A13 writer-side guard and the phases (D4)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .journaled_publication import JournaledPublication
from .mission_map_journal import MapJournal, MissionMapJournalError
from .mission_map_journal_paths import journal_paths_safe
from .mission_map_journal_store import MissionMapJournalRepository, recovery_folder
from .paths import PortablePaths
from .publication_contracts import PublicationPolicy, RecoveryHooks


class MissionMapPublicationError(RuntimeError):
    """Raised when an editor publication step is refused or fails."""

    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        """Store the error code and whether recovery is required."""
        self.code = code
        self.recovery_required = recovery_required
        super().__init__(message)


class WriterHold(Protocol):
    """The A13 server-folder writer side as the caller holds it, for example a WriterScope."""

    @property
    def held(self) -> bool:
        """Return whether the writer side is held now."""


# Error types, operator messages and checks of the editor journal kind for the shared engine
EDITOR_POLICY = PublicationPolicy(
    error_type=MissionMapPublicationError,
    journal_error_type=MissionMapJournalError,
    cleanup_message="Mission map temporary files could not be removed.",
    invalid_message="A mission map publication journal is invalid or unsafe.",
    unresolved_message="An interrupted mission map publication could not prove the prior or the new state.",
    verify_message="published mission map target failed verification",
    # D4 step 5: each group's old state is checked again just before its replace
    recheck_old_state=True,
    changed_message="A mission file changed after it was checked, so the publication stopped.",
)
# D4 steps 6 and 8: the phase that follows each phase the caller may advance from
NEXT_PHASE = {"PUBLISHING": "PUBLISHED", "PUBLISHED": "COMMITTING", "COMMITTING": "COMMITTED"}


class MissionMapPublication:
    """The shared publication engine bound to the editor journal; every step requires the held writer side.

    The caller keeps the A3 lock order and holds the writer side for the whole publication or recovery (Architect
    condition of 2026-10-10 16:18:24). A refusal raises before any write.
    """

    def __init__(
        self, paths: PortablePaths, repository: MissionMapJournalRepository, writer: WriterHold,
        *, fault_hook: Callable[[str, int], None] | None = None,
    ) -> None:
        """Bind the manager layout, the journal store and the writer side that the caller holds."""
        self._paths = paths
        self._repository = repository
        self._writer = writer
        # Default the fault hook to a no-op for callers without injection
        self._engine = JournaledPublication(fault_hook or (lambda _phase, _index: None), EDITOR_POLICY)

    @property
    def recovery_root(self) -> Path:
        """Return the folder of the recovery copies, one folder for each operation."""
        return recovery_folder(self._paths)

    def publish_groups(self, journal: MapJournal) -> None:
        """D4 steps 4 and 5: publish every DayZ-side group; the journal stays in phase PUBLISHING."""
        self._require_writer()
        self._engine.publish_groups(journal, self._repository)

    def advance(self, journal: MapJournal) -> str:
        """D4 steps 6 and 8: move to the next phase and save it durably; the saved COMMITTING is the commit point."""
        self._require_writer()
        phase = NEXT_PHASE.get(journal.phase)
        if phase is None:
            raise MissionMapPublicationError("PHASE_INVALID", "The publication cannot advance from this phase.")
        before = (journal.phase, journal.resolved, journal.result)
        journal.phase = phase
        if phase == "COMMITTED":
            journal.resolved, journal.result = True, "COMMITTED"
        try:
            self._repository.save(journal)
        except MissionMapJournalError:
            # The store refuses a state that D4 cannot produce, such as COMMITTED with an unpublished record
            journal.phase, journal.resolved, journal.result = before
            raise
        return phase

    def compensate(self, journal: MapJournal) -> bool:
        """Return every group to its old state in reverse order; False leaves the journal for recovery."""
        self._require_writer()
        return self._engine.compensate(journal, self._repository)

    def cleanup(self, journal: MapJournal) -> None:
        """Remove the DayZ-side staging files and the recovery copies of a resolved publication."""
        self._require_writer()
        self._engine.cleanup(journal, self.recovery_root)

    def retire(self, journal: MapJournal) -> Path:
        """Archive a resolved journal into completed/."""
        self._require_writer()
        return self._repository.retire(journal)

    def inspect(self, dayz_root: Path, hooks: RecoveryHooks[MapJournal]) -> dict[str, object]:
        """Resolve interrupted journals by the D4 recovery table and report whether any still block.

        Every path is derived again first. At COMMITTING the hooks roll forward only from a proven new state;
        otherwise the journal blocks without a write.
        """
        self._require_writer()
        return self._engine.inspect(
            self._repository, self.recovery_root,
            lambda journal: journal_paths_safe(journal, dayz_root, self._paths), hooks,
        )

    def _require_writer(self) -> None:
        """Refuse every engine step outside the A13 writer side."""
        if not self._writer.held:
            raise MissionMapPublicationError(
                "WRITER_SIDE_REQUIRED", "Mission map publication needs the server-folder writer side.",
            )
