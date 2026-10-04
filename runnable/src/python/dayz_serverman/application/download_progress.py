"""Per-item progress of the update operation, and the observer of the SteamCMD download folders.

Everything here is advisory. Outcomes come only from the exit classification
and the content proofs; a wrong or missing sample changes no result.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from ..domain.update_check import RemoteFact, RemoteItemResult
from ..repositories.backup_verification import is_reparse

# Limits of one observer: items per run, entries and seconds per item and sample
MAX_ITEMS = 200
MAX_ENTRIES = 5000
MAX_SECONDS = 0.5
# Seconds between two samples, and the longest wait for the observer to end
SAMPLE_INTERVAL = 2.0
STOP_TIMEOUT = 2.0
# Sample values that are no byte count: no folder, and a folder above a bound or unreadable
ABSENT = "absent"
UNKNOWN = "unknown"


class SizeRecordingCheck:
    """Check source wrapper that remembers the file size of each fresh fact."""

    def __init__(self, source) -> None:
        """Store the wrapped check source and start with no sizes."""
        self._source = source
        self.sizes: dict[str, int | None] = {}

    def check_now(self, ids: Iterable[str]) -> Mapping[str, RemoteFact]:
        """Run the wrapped check and keep the size of every answered item."""
        facts = self._source.check_now(ids)
        self.sizes = {key: fact.file_size for key, fact in dict(facts).items()
                      if fact.result is RemoteItemResult.OK}
        return facts


class ItemProgress:
    """Item phases of one update operation, published as the operation's progress detail."""

    def __init__(self, publish: Callable[[list[dict[str, object]]], None]) -> None:
        """Store the publication callback; entries appear in the order they are created."""
        self._publish = publish
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, object]] = {}
        self._seen: set[str] = set()

    def queued(self, workshop_ids: Iterable[str], sizes: Mapping[str, int | None]) -> None:
        """Enter every sent item as waiting for its download, with its size when known."""
        for workshop_id in workshop_ids:
            self._set(workshop_id, "queued", None, sizes.get(workshop_id), publish=False)
        self._emit()

    def sample(self, workshop_id: str, value: object) -> None:
        """Apply one sample of the download folder of a sent item."""
        with self._lock:
            entry = self._entries.get(workshop_id)
            # Only an item that still waits or downloads follows the folder
            if entry is None or entry["phase"] not in ("queued", "downloading"):
                return
            total = entry["total_bytes"]
            seen = workshop_id in self._seen
            if value == ABSENT and not seen:
                return
            if value == ABSENT:
                # The folder was seen and is gone: the download of the item is complete
                done = total
            elif value == UNKNOWN:
                if not seen:
                    return
                done = None
            else:
                self._seen.add(workshop_id)
                done = min(value, total) if isinstance(total, int) else value
            entry.update(phase="downloading", done_bytes=done)
        self._emit()

    def verifying(self, workshop_id: str) -> None:
        """Mark the item whose proof step begins; an unsent item gets its entry here."""
        self._set(workshop_id, "verifying", None, None)

    def finished(self, workshop_id: str, success: bool) -> None:
        """Mark the item whose result is built."""
        self._set(workshop_id, "done" if success else "failed", None, None)

    def fail_unfinished(self) -> None:
        """After a cancelled run every entry that is not done becomes failed."""
        with self._lock:
            for entry in self._entries.values():
                if entry["phase"] != "done":
                    entry.update(phase="failed", done_bytes=None, total_bytes=None)
        self._emit()

    def _set(self, workshop_id: str, phase: str, done: int | None, total: int | None,
             publish: bool = True) -> None:
        """Replace the entry of one item."""
        with self._lock:
            self._entries[workshop_id] = {
                "workshop_id": workshop_id, "phase": phase, "done_bytes": done, "total_bytes": total,
            }
        if publish:
            self._emit()

    def _emit(self) -> None:
        """Publish a copy of all entries; a failed publication changes nothing."""
        with self._lock:
            items = [dict(entry) for entry in self._entries.values()]
        try:
            self._publish(items)
        except Exception:
            return


def measure_folder(folder: Path, clock: Callable[[], float] = time.monotonic) -> object:
    """Return the sum of the file sizes under the folder, or ABSENT or UNKNOWN.

    The walk is read-only and follows no link and no other reparse point, such
    as a directory junction. Above 5,000 entries or 500 ms, and after any read
    error, the value is UNKNOWN.
    """
    started = clock()
    total = entries = 0
    pending = [folder]
    try:
        # An item folder that is a link or a junction is not a download folder
        if not folder.is_dir() or is_reparse(folder):
            return ABSENT
        while pending:
            with os.scandir(pending.pop()) as scan:
                for entry in scan:
                    entries += 1
                    if entries > MAX_ENTRIES or clock() - started > MAX_SECONDS:
                        return UNKNOWN
                    if entry.is_dir(follow_symlinks=False):
                        # A junction is a directory to the scan; its target is never measured
                        if not is_reparse(Path(entry.path)):
                            pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
    except OSError:
        return UNKNOWN
    return total


class DownloadObserver:
    """Sample the download folder of each sent item on its own worker while SteamCMD runs."""

    def __init__(
        self, workshop_root: Path, progress: ItemProgress, workshop_ids: Iterable[str],
        interval: float = SAMPLE_INTERVAL,
    ) -> None:
        """Derive the download folder from the Workshop root and bound the item list."""
        # <library>/workshop/content/221100 has its downloads at <library>/workshop/downloads/221100
        self._downloads = workshop_root.parent.parent / "downloads" / workshop_root.name
        self._progress = progress
        self._ids = tuple(workshop_ids)[:MAX_ITEMS]
        self._interval = interval
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="download-observer", daemon=True)

    def __enter__(self) -> "DownloadObserver":
        """Start the worker before the SteamCMD launch."""
        self._worker.start()
        return self

    def __exit__(self, *_error: object) -> None:
        """Signal the worker and wait at most two seconds for it."""
        self._stop.set()
        self._worker.join(STOP_TIMEOUT)

    def sample_once(self) -> None:
        """Take one sample of every item; stop early when the run has ended."""
        for workshop_id in self._ids:
            if self._stop.is_set():
                return
            self._progress.sample(workshop_id, measure_folder(self._downloads / workshop_id))

    def _run(self) -> None:
        """Sample until the stop signal; an error never leaves the worker."""
        while not self._stop.wait(self._interval):
            try:
                self.sample_once()
            except Exception:
                continue
