"""Durable configuration journal storage, separate from map and restore journals."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared, replace_file
from .atomic_file import AtomicFilePublisher
from .backup_verification import path_has_reparse
from .configuration_common import digest_bytes
from .configuration_journal import ConfigurationJournal, parse_journal


def durable_create(path: Path, content: bytes) -> None:
    """Create and flush a new owner artifact without replacing an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path_has_reparse(path.parent) or path.exists():
        raise OSError("configuration staging path is unsafe or already present")
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class ConfigurationJournalStore:
    """Persist active records atomically and retain resolved records for diagnosis."""

    def __init__(self, root: Path, retirement_guard=None) -> None:
        """Keep the manager-owned root without creating it during reads."""
        self.root = root
        self.retirement_guard = retirement_guard

    def path(self, operation_id: str) -> Path:
        """Return the active journal path for a validated operation identity."""
        if len(operation_id) != 32 or any(mark not in "0123456789abcdef" for mark in operation_id):
            raise ValueError("configuration operation identity is invalid")
        return self.root / (operation_id + ".json")

    def save(self, journal: ConfigurationJournal) -> None:
        """Validate the full record and replace its durable state atomically."""
        value = journal.document()
        parse_journal(value)
        content = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        target = self.path(journal.operation_id)
        if path_has_reparse(target):
            raise OSError("configuration journal path is unsafe")
        if target.exists():
            AtomicFilePublisher().publish(target, content, digest_bytes(read_bytes_shared(target)))
        else:
            stage = target.with_suffix(".journal-stage")
            durable_create(stage, content)
            replace_file(stage, target)

    def load(self, operation_id: str) -> ConfigurationJournal:
        """Read and validate the persisted phase after an uncertain save result."""
        path = self.path(operation_id)
        if path_has_reparse(path):
            raise OSError("configuration journal path is unsafe")
        return parse_journal(json.loads(read_bytes_shared(path).decode("utf-8")))

    def records(self) -> list[tuple[Path, ConfigurationJournal | None]]:
        """Return unreadable active records as blocking entries, without changing bytes."""
        if path_has_reparse(self.root):
            raise OSError("configuration journal root is unsafe")
        records = []
        for path in sorted(self.root.glob("*")):
            if path.name == "resolved":
                continue
            try:
                if path.suffix != ".json":
                    raise ValueError("unrecognized configuration journal artifact")
                journal = self.load(path.stem)
                if journal.operation_id != path.stem:
                    raise ValueError("configuration journal filename differs from its identity")
            except (OSError, ValueError, TypeError, KeyError):
                journal = None
            records.append((path, journal))
        return records

    def retire(self, journal: ConfigurationJournal) -> None:
        """Archive a resolved record only after its final state is validated."""
        if not journal.resolved:
            raise ValueError("configuration journal is unresolved")
        if self.retirement_guard is not None:
            self.retirement_guard(journal)
        target = self.root / "resolved" / (journal.operation_id + ".json")
        if path_has_reparse(target):
            raise OSError("configuration journal archive is unsafe")
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_file(self.path(journal.operation_id), target)
