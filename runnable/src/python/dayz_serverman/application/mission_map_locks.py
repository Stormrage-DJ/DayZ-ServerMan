"""One lock for each mission map association in the owner process (D2)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class AssociationLocks:
    """Serialize plan saves and record commits of one profile and mission association.

    The association lock is the innermost lock. A commit takes it after the folder writer side; a plan
    save takes only this lock and never waits for the lane, the installation mutex or the writer side.
    Observer sessions only read, so they never take it.
    """

    def __init__(self) -> None:
        """Start with no association locks; each one is made on first use and kept for the process."""
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def lock_for(self, profile_id: str, mission_key: str) -> threading.Lock:
        """Return the one lock of an association; the same pair always returns the same lock."""
        with self._guard:
            return self._locks.setdefault((profile_id, mission_key), threading.Lock())

    @contextmanager
    def hold(self, profile_id: str, mission_key: str) -> Iterator[None]:
        """Hold the association lock for the body; it is not reentrant, so a nested take would deadlock."""
        lock = self.lock_for(profile_id, mission_key)
        with lock:
            yield
