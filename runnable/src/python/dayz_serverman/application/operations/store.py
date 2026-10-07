"""Durable operation records and append-only event evidence."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from ...adapters.windows.shared_files import replace_file
from .models import OperationEvent, OperationRecord


# Restrict identifiers to characters that are safe in file names
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class OperationStore:
    """Persist operation records and append-only events under a root directory."""
    def __init__(self, root: Path, *, create_root: bool = True) -> None:
        """Resolve the root directory and create it when missing; an observer session creates nothing."""
        self.root = root.resolve(strict=False)
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        # One lock serializes record writes and event appends
        self._lock = threading.Lock()

    def save_record(self, record: OperationRecord) -> None:
        """Write the record atomically as indented JSON named by its identifier."""
        # Reject identifiers that cannot be used as file names
        if IDENTIFIER.fullmatch(record.operation_id) is None:
            raise ValueError("operation identifier is invalid")
        path = self.root / f"{record.operation_id}.json"
        # Stage the replacement beside the target so the swap stays on one volume
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ) + "\n"
        with self._lock:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                # Flush to disk before the atomic rename
                os.fsync(stream.fileno())
            # Swap the staged file in so readers never observe a partial record
            replace_file(temporary, path)

    def append_event(self, event: OperationEvent) -> None:
        """Append one event as a single compact JSON line."""
        payload = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
        # Serialize appends so event lines never interleave
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                # Flush each append to disk before the next writer proceeds
                os.fsync(stream.fileno())

