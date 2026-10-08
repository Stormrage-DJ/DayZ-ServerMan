"""Observer sessions (A4): a read-only composition, the reader side per bridge call (A13) and pending recoveries."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.windows.instance_lock import InstanceLockUnsupported
from .adapters.windows.server_folder_lock import (
    NO_FOLDER_LOCK,
    FolderLockFile,
    FolderLockUnsupported,
    FolderReader,
)
from .adapters.windows.shared_files import read_text_shared
from .bridge.contracts import CONTRACT_VERSION
from .bridge_composition import OBSERVER_READ_METHODS, READER_EXEMPT_METHODS
from .composition import build_composition
from .composition_model import ApplicationComposition, SessionMode
from .repositories.migration_journal import MigrationJournalRepository
from .repositories.paths import PortablePaths
from .repositories.profile_restore_journal import ProfileRestoreJournal
from .repositories.restore_journal import RestoreJournalRepository
from .repositories.workshop_recovery import NONTERMINAL
from .session import FOLDER_LOCK_FILE, CompositionBuilder, resolve_paths


# Wait of one observer call for an owner's swap step, then CONTROL_CONFLICT (A13)
READER_WAIT_SECONDS = 30.0
# Exempt reads of the backup root: exempt only while that root lies outside the DayZ root (A13, 2.3 residual)
BACKUP_ROOT_METHODS = frozenset(("list_backups", "list_backup_catalog"))


class BridgeCallFailed(RuntimeError):
    """A read handler answered with a bridge error; `error` is the error object of the envelope."""

    def __init__(self, error: Mapping[str, Any]) -> None:
        """Keep the bridge error object; the message is its safe message."""
        self.error = dict(error)
        super().__init__(str(self.error.get("message", "")))


class _UnusableReader:
    """Reader of a lock file that exists but cannot be opened: every reader-side call refuses."""

    def __init__(self, error: OSError) -> None:
        """Keep the open error as the cause of each refusal."""
        self._error = error

    @contextmanager
    def shared(self, bound_seconds: float) -> Iterator[None]:
        """Refuse like a folder without byte-range locks."""
        raise FolderLockUnsupported(None, "server-folder lock file could not be opened") from self._error
        yield  # pragma: no cover - never reached

    def close(self) -> None:
        """Nothing to close."""
        return


@dataclass
class ObserverSession:
    """A read-only composition whose bridge calls each run under the reader side of the folder lock."""

    paths: PortablePaths
    composition: ApplicationComposition
    reader: Any
    _sequence: int = field(default=0, repr=False)

    def call(self, method: str, parameters: Mapping[str, Any] | None = None) -> Any:
        """Dispatch one read and return its value; raise BridgeCallFailed without any retry (4.4)."""
        if method not in OBSERVER_READ_METHODS:
            raise ValueError(f"{method} is not an observer read method")
        self._sequence += 1
        envelope = {
            "contract_version": CONTRACT_VERSION, "request_id": f"observer-{self._sequence}",
            "method": method, "parameters": dict(parameters or {}),
        }
        if method in READER_EXEMPT_METHODS and not (method in BACKUP_ROOT_METHODS
                                                    and backup_root_in_dayz_root(self.composition.settings)):
            result = self.composition.bridge.dispatch(envelope)
        else:
            try:
                with self.reader.shared(READER_WAIT_SECONDS):
                    result = self.composition.bridge.dispatch(envelope)
            except FolderLockUnsupported as error:
                # The folder cannot hold the reader side: the call refuses like an unsupported owner (3.2)
                raise InstanceLockUnsupported() from error
        if result["success"]:
            return result["value"]
        raise BridgeCallFailed(result["error"])

    def close(self) -> None:
        """Close the reader handle; the composition started no thread and holds no lock."""
        self.reader.close()


def backup_root_in_dayz_root(settings: Any) -> bool:
    """Report whether the effective backup root is the DayZ root or below it, from the settings of this call.

    The comparison is lexical (absolute, normalized, case-folded): settings normalization stored
    both roots resolved, and a resolve here would open a handle on the backup root before the
    reader side is held (S6). Settings that cannot be read count as inside, so the call takes the
    reader side (fail closed) and reports its own error.
    """
    try:
        current = settings.load()
        backup_root = settings.backup_root(current)
    except Exception:
        return True
    if current.dayz_root is None:
        return False
    child, parent = _comparable(backup_root), _comparable(current.dayz_root)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _comparable(path: Any) -> str:
    """Return the lexical comparison form of a Windows path, without touching the file system."""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def open_observer_session(
    manager_root: Path | None = None, *, build: CompositionBuilder = build_composition,
) -> ObserverSession:
    """Open a session that creates no folder, file or lock and writes nothing."""
    paths = resolve_paths(manager_root)
    reader = _reader(paths)
    try:
        built = build(paths.root, mode=SessionMode.OBSERVER)
    except BaseException:
        reader.close()
        raise
    return ObserverSession(paths, built, reader)


def _reader(paths: PortablePaths) -> Any:
    """Open the lock file for read; without it no owner of this build opened the root yet (accepted gap)."""
    try:
        lock_file = FolderLockFile.open_existing(paths.data / FOLDER_LOCK_FILE)
    except OSError as error:
        return _UnusableReader(error)
    return NO_FOLDER_LOCK if lock_file is None else FolderReader(lock_file)


def pending_recoveries(paths: PortablePaths) -> tuple[str, ...]:
    """Return the kinds of interrupted work that an owner would recover, by read-only checks (4.5)."""
    pending: list[str] = []
    # Backup restore: any restore journal
    if RestoreJournalRepository(paths.operations / "restore-journals", create_root=False).records():
        pending.append("RESTORE_BACKUP")
    # Mod publication: an active journal; retired ones are in "retired/"
    if _has_json(paths.publication_journals):
        pending.append("PUBLISH_MODS_AND_KEYS")
    # Profile creation: a provisioning journal
    if _has_json(paths.operations / "profile-provisioning"):
        pending.append("PROVISION_PROFILE")
    # Profile restore: the pending rule of the startup recovery, where an unreadable set counts
    if _profile_restore_pending(paths.operations / "profile-restore-journals"):
        pending.append("RESTORE_PROFILE_FROM_BACKUP")
    # Legacy import: an active migration journal
    if MigrationJournalRepository(paths.migrations / "publication-journals").active_paths():
        pending.append("IMPORT_LEGACY")
    # Mod update: a SteamCMD update record that never reached a terminal state
    if _update_interrupted(paths.operations):
        pending.append("UPDATE_WORKSHOP_ITEMS")
    return tuple(pending)


def _has_json(folder: Path) -> bool:
    """Report whether a folder lists at least one record file; a missing folder lists none."""
    return any(folder.glob("*.json"))


def _profile_restore_pending(folder: Path) -> bool:
    """Apply the startup rule: an unfinished or uncleaned journal, or a set that cannot be read."""
    try:
        records = ProfileRestoreJournal(folder).records()
    except Exception:
        return True
    return any(
        record["phase"] not in {"COMMITTED", "ROLLED_BACK"}
        or (record["phase"] == "COMMITTED" and not record["cleanup_complete"])
        for record in records
    )


def _update_interrupted(operations: Path) -> bool:
    """Report an UPDATE_WORKSHOP_ITEMS record in a non-terminal state, read through the shared opener."""
    for path in operations.glob("*.json"):
        try:
            document = json.loads(read_text_shared(path, encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if (isinstance(document, dict) and document.get("kind") == "UPDATE_WORKSHOP_ITEMS"
                and document.get("state") in NONTERMINAL):
            return True
    return False
