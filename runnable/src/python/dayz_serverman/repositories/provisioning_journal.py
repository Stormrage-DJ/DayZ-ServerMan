"""Durable recovery evidence for profile provisioning."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..adapters.windows.shared_files import read_text_shared, replace_file


@dataclass(frozen=True)
class ProvisioningJournal:
    """Paths and phase owned by one profile provisioning operation."""

    operation_id: str
    profile_id: str
    dayz_root: str
    stage_relative: str
    target_relative: str
    phase: str


class ProvisioningJournalRepository:
    """Atomically persist one journal per provisioning operation."""

    def __init__(self, root: Path) -> None:
        """Store the resolved manager-owned journal directory."""
        self.root = root.resolve(strict=False)

    def list(self) -> tuple[ProvisioningJournal, ...]:
        """Return every valid journal in stable order."""
        if not self.root.exists():
            return ()
        journals: list[ProvisioningJournal] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
            try:
                raw = json.loads(read_text_shared(path, encoding="utf-8"))
                if not isinstance(raw, dict) or set(raw) != {
                    "operation_id", "profile_id", "dayz_root", "stage_relative",
                    "target_relative", "phase",
                }:
                    raise ValueError
                journals.append(ProvisioningJournal(**raw))
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise RuntimeError("Profile provisioning recovery journal is invalid.") from error
        return tuple(journals)

    def save(self, journal: ProvisioningJournal) -> None:
        """Publish a journal with stable JSON and an atomic swap."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(journal.operation_id)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(asdict(journal), indent=2, sort_keys=True) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            replace_file(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def delete(self, operation_id: str) -> None:
        """Remove a completed journal if present."""
        self._path(operation_id).unlink(missing_ok=True)

    def _path(self, operation_id: str) -> Path:
        """Return the journal path for a hexadecimal operation identifier."""
        if not operation_id or any(mark not in "0123456789abcdef" for mark in operation_id):
            raise ValueError("provisioning operation identifier is invalid")
        return self.root / f"{operation_id}.json"
