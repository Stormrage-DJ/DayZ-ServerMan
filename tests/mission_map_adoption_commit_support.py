"""The adoption commit of leaf 10.35 over the 10.13 adoption fixture: manager paths, journals, guard and service."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_adoption import MissionMapAdoption
from dayz_serverman.application.mission_map_adoption_commit import AdoptionCommit
from dayz_serverman.application.mission_map_apply_records import owned_or_none
from dayz_serverman.application.mission_map_commit import MissionMapCommit
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository, journal_folder
from dayz_serverman.repositories.mission_map_owned import check_reserved
from dayz_serverman.repositories.mission_map_publication import MissionMapPublication
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.publication_contracts import RecoveryHooks
from tests.mission_map_adoption_support import MOMENT, AdoptionFixture

# The A13 writer side as a test holds it during recovery
HELD = SimpleNamespace(held=True)


class Mutex:
    """An installation mutex that records each hold, so a test sees the lock order."""

    def __init__(self, events: list[str]) -> None:
        """Keep the shared event list."""
        self.events = events

    @contextmanager
    def guard(self, dayz_root: str) -> Iterator[None]:
        """Record the hold and its end."""
        self.events.append("mutex")
        try:
            yield
        finally:
            self.events.append("mutex released")


class CommitFixture(AdoptionFixture):
    """The adoption service with a commit step over the manager root of the fixture.

    The server state is RUNNING_MANAGED: adoption needs no STOPPED proof (AR-3).
    """

    def setUp(self) -> None:
        """Build the journals, the guard with a running server, the commit step and the adoption service."""
        super().setUp()
        self.paths = PortablePaths.from_root(self.base / "manager")
        self.events: list[str] = []
        self.faults: Callable[[str, int], None] = lambda _phase, _index: None
        self.journals = MissionMapJournalRepository(journal_folder(self.paths))
        lifecycle = SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.RUNNING_MANAGED))
        guard = InstallationGuard(lifecycle, Mutex(self.events))  # type: ignore[arg-type]
        self.base_commit = MissionMapCommit(self.paths, self.journals, guard, None, self.plans._locks,
                                            owned_digest=owned_or_none, check_reserved=check_reserved,
                                            fault_hook=lambda phase, index: self.faults(phase, index))
        self.adoption = MissionMapAdoption(self.reader, AdoptionCommit(self.base_commit), self.paths,
                                           clock=lambda: MOMENT)
        self.count = 0

    def adopt(self, identifier: str = "main", fingerprint: str | None = None) -> dict[str, Any]:
        """Review and adopt; return the result of the adopt step."""
        self.count += 1
        reviewed = fingerprint or self.reader.review(identifier).fingerprint
        return self.adoption.adopt(identifier, reviewed, operation_id=f"adopt-op{self.count}")

    def recover(self) -> dict[str, object]:
        """Resolve the editor journals by the D4 recovery table, as the 8.5 recovery does."""
        records = CommitRecords(self.paths, self.journals, lambda _phase, _index: None)
        return MissionMapPublication(self.paths, self.journals, HELD).inspect(
            Path(self.settings.dayz_root), RecoveryHooks(records.roll_forward, records.rollback))

    def folders(self, identifier: str = "main") -> list[str]:
        """Return the names in the profile folder of the editor area."""
        folder = self.area / "profiles" / identifier
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []
