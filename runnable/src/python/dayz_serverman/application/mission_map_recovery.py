"""Recover editor publications under the installation guard and one folder writer hold."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

from ..domain.lifecycle import LifecycleFailure
from ..domain.models import RepositoryError
from ..repositories.mission_map_commit_records import CommitRecords
from ..repositories.mission_map_journal import RESULTS, MapJournal
from ..repositories.mission_map_journal_store import MissionMapJournalRepository
from ..repositories.mission_map_journal_paths import journal_paths_safe
from ..repositories.mission_map_publication import MissionMapPublication
from ..repositories.mission_map_recovery_cleanup import cleanup_orphans
from ..repositories.paths import PortablePaths
from ..repositories.publication_contracts import RecoveryHooks, in_new_state
from ..repositories.restore_paths import group_old_matches
from .folder_writer_scope import WriterScope
from .installation_guard import InstallationGuard
from .lifecycle_ports import ServerFolderWriterPort
from .mission_map_locks import AssociationLocks
from .settings import SettingsService


# All editor journal kinds share this recovery owner; other owners' blocks remain intact
EDITOR_KIND = "APPLY_MISSION_MAP"
RECOVERY_NO_ROOT = "Mission map recovery requires a configured DayZ root."
RECOVERY_NOT_STOPPED = "Mutations are blocked by an interrupted mission map publication while the server is not proven stopped."
RECOVERY_UNRESOLVED = "Mutations are blocked by unresolved mission map recovery."
RECOVERY_UNREADABLE = "Mission map journals could not be read."
# One read of the active journals: each file with its journal, or None when it cannot be trusted
JournalRead = tuple[tuple[Path, MapJournal | None], ...]


class MissionMapRecovery:
    """Bind durable record hooks to D4 recovery using the process's shared association locks."""

    def __init__(
        self, paths: PortablePaths, settings: SettingsService, guard: InstallationGuard,
        folder_writer: ServerFolderWriterPort | None, locks: AssociationLocks,
    ) -> None:
        """Keep owner dependencies without creating folders or reading journals."""
        self.paths, self.settings, self.guard = paths, settings, guard
        self.folder_writer, self.locks = folder_writer, locks
        self.journals = MissionMapJournalRepository(paths.operations / "mission-map-journals", create_root=False)

    def recovery_pending(self) -> bool:
        """Report active journals; unreadable storage counts as pending and causes no write."""
        try:
            return bool(self.journals.records())
        except (OSError, ValueError):
            return True

    def inspect(self, *, wait_seconds: float | None = None) -> dict[str, object]:
        """Resolve safe journals, refusing every precondition before any recovery write.

        After a journal read, the envelope has the internal key `journals` for the coordinator's log lines
        (8.12 Architect decision 1). The coordinator removes it before any caller sees the envelope.
        """
        reads: list[JournalRead] = []
        result = self._resolve(reads, wait_seconds)
        if reads:
            # The last read is the state that recovery acted on, or that a refusal kept
            result["journals"] = self._outcomes(reads[-1])
        return result

    def _read(self, reads: list[JournalRead]) -> JournalRead:
        """Read the active journals and keep the read for the outcome items; a failed read is not kept."""
        pending = self.journals.records()
        reads.append(pending)
        return pending

    def _outcomes(self, pending: JournalRead) -> list[dict[str, object]]:
        """Describe each journal by its phase before recovery and its durable result now; unreadable ones by count."""
        items: list[dict[str, object]] = [
            {"operation_id": journal.operation_id, "operation_kind": journal.operation_kind,
             "phase": journal.phase, "result": self._result_now(journal)}
            for _path, journal in pending if journal is not None]
        unreadable = sum(1 for _path, journal in pending if journal is None)
        # An unreadable journal has no trusted id, so only the count is kept
        if unreadable:
            items.append({"unreadable": unreadable})
        return items

    def _result_now(self, journal: MapJournal) -> str | None:
        """Return None while the journal is active; else its retired result, or the engine's fixed direction."""
        try:
            if self.journals.path_for(journal.operation_id).exists():
                return None
        except (OSError, ValueError, RuntimeError):
            # An unknown state counts as active, so no line claims a result that did not happen
            return None
        try:
            retired = self.journals.load(self.journals.retired_path_for(journal.operation_id)).result
        except (OSError, ValueError, RuntimeError):
            retired = None
        if retired in RESULTS:
            return retired
        # The engine only rolls a COMMITTING or COMMITTED journal forward; every other journal rolls back
        return "COMMITTED" if journal.phase in ("COMMITTING", "COMMITTED") else "ROLLED_BACK"

    def _resolve(self, reads: list[JournalRead], wait_seconds: float | None) -> dict[str, object]:
        """Run the guarded recovery; every successful journal read is appended to reads."""
        try:
            pending = self._read(reads)
        except (OSError, ValueError):
            return _blocked(RECOVERY_UNREADABLE)
        # With no journal, cleanup remains within manager-owned editor storage
        if not pending:
            return self._clean_without_journals(reads, wait_seconds)
        if any(journal is None for _path, journal in pending):
            return _blocked(RECOVERY_UNREADABLE)
        try:
            configured = self.settings.load().dayz_root
            if configured is None:
                return _blocked(RECOVERY_NO_ROOT)
            root = Path(configured).resolve(strict=True)
            with self.guard.stopped(root):
                writer = WriterScope(self.folder_writer, wait_seconds)
                with writer.held_for():
                    # The initial read may expire while the guard or writer waits; prove only current journals
                    try:
                        pending = self._read(reads)
                    except (OSError, ValueError):
                        return _blocked(RECOVERY_UNREADABLE)
                    if any(journal is None for _path, journal in pending):
                        return _blocked(RECOVERY_UNREADABLE)
                    with ExitStack() as associations:
                        # Hold affected associations through compensation and record hooks
                        keys = sorted({(journal.profile_id, journal.mission_key) for _path, journal in pending})
                        for profile_id, key in keys:
                            associations.enter_context(self.locks.hold(profile_id, key))
                        # Detect touched third states before the engine can persist COMPENSATING
                        for _path, journal in pending:
                            if not journal_paths_safe(journal, root, self.paths):
                                return _blocked(RECOVERY_UNRESOLVED)
                            for group in journal.groups:
                                untouched = group.state == "READY" and journal.phase not in ("COMMITTING", "COMMITTED")
                                if not untouched and not group_old_matches(group) and not in_new_state(group):
                                    return _blocked(RECOVERY_UNRESOLVED)
                        records = CommitRecords(self.paths, self.journals, lambda _phase, _index: None)
                        publication = MissionMapPublication(self.paths, self.journals, writer)
                        result = publication.inspect(root, RecoveryHooks(records.roll_forward, records.rollback))
                    # Release association holds before sweep takes them again, retaining the same writer
                    if not result["blocked"]:
                        result["diagnostics"].extend(self._clean()["diagnostics"])
        except LifecycleFailure:
            return _blocked(RECOVERY_NOT_STOPPED)
        except (OSError, ValueError, RepositoryError, RuntimeError):
            return _blocked(RECOVERY_UNRESOLVED)
        return result

    def _clean_without_journals(self, reads: list[JournalRead], wait_seconds: float | None) -> dict[str, object]:
        """Serialize manager-only cleanup with commits, checking for journals after the writer wait."""
        try:
            with WriterScope(self.folder_writer, wait_seconds).held_for():
                try:
                    pending = self._read(reads)
                except (OSError, ValueError):
                    return _blocked(RECOVERY_UNREADABLE)
                if pending:
                    # A commit started while we waited; release the writer before a later guarded inspection
                    reason = RECOVERY_UNREADABLE if any(journal is None for _path, journal in pending) else RECOVERY_UNRESOLVED
                    return _blocked(reason)
                return self._clean()
        except (LifecycleFailure, OSError):
            # A busy manager-only cleanup never replaces an existing recovery block
            return {"blocked": False, "cleanup_deferred": True,
                    "diagnostics": [{"message": "Mission map manager cleanup is deferred while the writer is unavailable."}]}

    def require_root_match(self, candidate: Path) -> bool:
        """Check readable editor journals for the settings repair without taking locks or writing."""
        return all(journal is None or journal_paths_safe(journal, candidate, self.paths)
                   for _path, journal in self.journals.records())

    def _clean(self) -> dict[str, object]:
        """Keep failed manager-only cleanup as diagnostics; no journal means no lane block."""
        try:
            cleanup_orphans(self.paths, self.locks)
            return {"blocked": False, "diagnostics": []}
        except (OSError, ValueError, RepositoryError):
            return {"blocked": False, "diagnostics": [{"message": "Mission map manager cleanup requires attention."}]}


def _blocked(reason: str) -> dict[str, object]:
    """Return the recovery envelope without changing journals or lane state."""
    return {"blocked": True, "reason": reason, "diagnostics": [{"message": reason}]}
