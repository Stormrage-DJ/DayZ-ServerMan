"""Holder file of the instance lock (A5): who holds a manager root, for the refusal sentence only."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .launcher import _query_creation_time
from .shared_files import read_text_shared, write_bytes_atomically


# The exact field set of schema 1
FIELDS = frozenset(("schema_version", "holder", "command", "pid", "process_created_ns", "started_at"))
# Holder kinds: the window of the GUI, or one CLI command
HOLDERS = frozenset(("window", "command"))


@dataclass(frozen=True)
class HolderInfo:
    """Details of the live holder that a refusal may show."""

    holder: str
    command: str | None
    pid: int
    started_at: str

    def to_details(self) -> dict[str, object]:
        """Return the JSON details of an INSTANCE_ACTIVE refusal."""
        return {"holder": self.holder, "command": self.command, "pid": self.pid, "started_at": self.started_at}


def process_created_ns(pid: int) -> int | None:
    """Return the creation time of a live process in nanoseconds, or None when it cannot be read."""
    return _query_creation_time(pid)


def write_holder(
    path: Path, holder: str, command: str | None = None, *,
    created_ns: Callable[[int], int | None] = process_created_ns,
) -> None:
    """Write the holder file of the new holder through one atomic replace; a failure is ignored."""
    pid = os.getpid()
    document = {
        "schema_version": 1, "holder": holder, "command": command, "pid": pid,
        "process_created_ns": created_ns(pid),
        "started_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }
    # The file only improves a refusal sentence; the lock itself never depends on it
    try:
        write_bytes_atomically(path, (json.dumps(document, sort_keys=True) + "\n").encode("utf-8"))
    except (OSError, TypeError, ValueError):
        return


def read_holder(
    path: Path, *, created_ns: Callable[[int], int | None] = process_created_ns,
) -> HolderInfo | None:
    """Return the holder details when the file is valid and its process still runs; otherwise None."""
    # Read through the shared opener, so the holder can still replace the file
    try:
        document = json.loads(read_text_shared(path, encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not _valid(document):
        return None
    # Show a holder only while a live process has the recorded pid and creation time
    try:
        live = created_ns(document["pid"])
    except OSError:
        return None
    if live is None or live != document["process_created_ns"]:
        return None
    return HolderInfo(document["holder"], document["command"], document["pid"], document["started_at"])


def _valid(document: object) -> bool:
    """Check the exact field set and the field types of schema 1."""
    if not isinstance(document, dict) or set(document) != FIELDS or document["schema_version"] != 1:
        return False
    pid, created = document["pid"], document["process_created_ns"]
    return (
        document["holder"] in HOLDERS
        and (document["command"] is None or isinstance(document["command"], str))
        and type(pid) is int and pid > 0
        and type(created) is int and created > 0
        and isinstance(document["started_at"], str)
    )
