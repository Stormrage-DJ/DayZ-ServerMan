"""Persisted content proofs of Workshop items and server-folder copies, shared by all profiles."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import open_shared, replace_file
from ..domain.content_proofs import (
    MAX_SOURCE_RECORDS,
    MAX_TARGET_RECORDS,
    ProofDocument,
    SourceProofRecord,
    TargetProofRecord,
    parse_source_record,
    parse_target_record,
    target_directory_key,
    verified_time,
)
from ..observability.structured_log import StructuredLogger

# A larger file is treated as unreadable; the limits keep a real store far below it
MAX_FILE_BYTES = 4 * 1024 * 1024
# A reader and the writer can meet during the replace of the file on Windows: the
# refused side tries again this many times, this many seconds apart, then gives up
SHARING_RETRIES = 4
SHARING_RETRY_SECONDS = 0.02
# Exact field set of the persisted root object
_ROOT_FIELDS = {"schema_version", "sources", "targets"}


class ContentProofStore:
    """Read and atomically rewrite the proof document; only lane operations write."""

    def __init__(self, path: Path, logger: StructuredLogger | None = None) -> None:
        """Store the document path and the optional logger."""
        self._path = path
        self._logger = logger

    def load(self) -> ProofDocument:
        """Return every well-formed record; any unusable document is an empty store."""
        try:
            try:
                raw = self._read()
            except PermissionError:
                # A sharing violation during the replace: wait once, then read again
                time.sleep(SHARING_RETRY_SECONDS)
                raw = self._read()
            if len(raw) > MAX_FILE_BYTES:
                return ProofDocument()
            return _document(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError, TypeError, RecursionError):
            # A reader error means "no proof" for this read, never a failed call
            return ProofDocument()

    def _read(self) -> bytes:
        """Return the file bytes, at most one byte above the cap so an oversized file is detected."""
        with open_shared(self._path) as stream:
            return stream.read(MAX_FILE_BYTES + 1)

    def target_records(self) -> Collection[tuple[str, str, str, str]]:
        """Return (root identity, directory key, Workshop id, manifest id) of every target.

        The result serves the row rule only and authorizes nothing.
        """
        return frozenset(
            (root, directory, record.workshop_id, record.installed_manifest_id)
            for (root, directory), record in self.load().targets.items()
        )

    def record(
        self,
        sources: Mapping[str, SourceProofRecord] | None = None,
        targets: Mapping[tuple[str, str], TargetProofRecord] | None = None,
        *,
        remove_sources: Collection[str] = (),
        remove_targets: Collection[tuple[str, str]] = (),
    ) -> bool:
        """Merge the records into the document and write it; return False on a failed write.

        Target keys are (root identity, target directory); the directory is
        normalized here. The named records are removed first, then the new
        ones are merged. A failed write is logged and never raised.
        """
        try:
            # Read, change and write the whole document
            document = self.load()
            merged_sources = dict(document.sources)
            merged_targets = dict(document.targets)
            # Drop the records that a deliberate verification found untrue
            for workshop_id in remove_sources:
                merged_sources.pop(workshop_id, None)
            for root, directory in remove_targets:
                merged_targets.pop((root, target_directory_key(directory)), None)
            # A malformed new record is skipped, so the store holds only valid records
            for workshop_id, record in (sources or {}).items():
                try:
                    merged_sources[workshop_id] = parse_source_record(workshop_id, record.to_dict())
                except (ValueError, TypeError):
                    continue
            for (root, directory), record in (targets or {}).items():
                try:
                    key = (root, target_directory_key(directory))
                    merged_targets[key] = parse_target_record(*key, record.to_dict())
                except (ValueError, TypeError):
                    continue
            self._write(_serialize(
                _newest(merged_sources, MAX_SOURCE_RECORDS),
                _newest(merged_targets, MAX_TARGET_RECORDS),
            ))
            return True
        except (OSError, ValueError, TypeError) as error:
            # Losing a proof costs one later hash; the operation itself goes on
            self._log_failure(error)
            return False

    def _write(self, value: dict[str, object]) -> None:
        """Write the document atomically through a temporary sibling file."""
        # Ensure the target directory exists before publishing
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the document beside the target so the swap stays on one volume
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            # Serialize, flush, and fsync the staged bytes before the swap
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Swap the staged file into place
            self._replace(temporary)
        finally:
            # Remove the staged file when any step fails
            if temporary.exists():
                temporary.unlink()

    def _replace(self, temporary: Path) -> None:
        """Swap the staged file into place; a replace refused by an open reader is retried."""
        for attempt in range(SHARING_RETRIES + 1):
            try:
                replace_file(temporary, self._path)
                return
            except PermissionError:
                # The last refusal goes to the caller, which logs it and goes on
                if attempt == SHARING_RETRIES:
                    raise
                time.sleep(SHARING_RETRY_SECONDS)

    def _log_failure(self, error: Exception) -> None:
        """Emit the write-failure event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(
                "content_proofs.write_failed", level="WARNING",
                fields={"error": type(error).__name__},
            )
        except (OSError, TypeError, ValueError):
            return


def _document(raw: Any) -> ProofDocument:
    """Return the records of a schema 1 document; raise ValueError for a wrong root."""
    # Unknown version or wrong root shape rejects the whole document
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS:
        raise ValueError("content proof store has the wrong shape")
    version = raw["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("content proof store has an unknown version")
    if not isinstance(raw["sources"], dict) or not isinstance(raw["targets"], dict):
        raise ValueError("content proof store has the wrong shape")
    document = ProofDocument()
    # One malformed record is ignored; the next write drops it
    for workshop_id, value in raw["sources"].items():
        try:
            document.sources[workshop_id] = parse_source_record(workshop_id, value)
        except (ValueError, TypeError):
            continue
    for root, directories in raw["targets"].items():
        for directory, value in (directories.items() if isinstance(directories, dict) else ()):
            try:
                document.targets[(root, directory)] = parse_target_record(root, directory, value)
            except (ValueError, TypeError):
                continue
    return document


def _newest(records: dict, limit: int) -> dict:
    """Return at most `limit` records; the oldest verification time goes first."""
    if len(records) <= limit:
        return records
    # Order by time, then by key, so the choice is deterministic
    ordered = sorted(
        records, key=lambda key: (verified_time(records[key].verified_at), key), reverse=True,
    )
    return {key: records[key] for key in ordered[:limit]}


def _serialize(
    sources: Mapping[str, SourceProofRecord],
    targets: Mapping[tuple[str, str], TargetProofRecord],
) -> dict[str, object]:
    """Return the schema 1 document with the targets grouped by root identity."""
    grouped: dict[str, dict[str, object]] = {}
    for (root, directory), record in targets.items():
        grouped.setdefault(root, {})[directory] = record.to_dict()
    return {
        "schema_version": 1,
        "sources": {workshop_id: record.to_dict() for workshop_id, record in sources.items()},
        "targets": grouped,
    }
