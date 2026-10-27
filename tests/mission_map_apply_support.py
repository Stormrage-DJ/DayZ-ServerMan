"""The Apply service of leaf 8.7 over the synthetic missions of the 8.6 preview tests, with recording guard ports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_apply import MissionMapApply
from dayz_serverman.application.mission_map_apply_records import owned_or_none
from dayz_serverman.application.mission_map_commit import MissionMapCommit
from dayz_serverman.repositories.json_store import StagingPolicy
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository, journal_folder
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.mission_map_owned import check_reserved
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from dayz_serverman.repositories.paths import PortablePaths
from tests.mission_map_preview_support import PreviewFixture
from tests.test_mission_map_commit_fixture import RecordingLocks, RecordingPorts
from tests.test_mission_map_preview import tree

# Fixed clock of every Apply
NOW = datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc)
# Choices of a review without external changes and unavailable encounters
NO_CHOICES: dict[str, Any] = {"external_changes": {}, "unavailable_encounters": {}}


class ApplyFixture(PreviewFixture):
    """Temporary DayZ root and manager root with the preview, the commit step and the Apply service."""

    def setUp(self) -> None:
        """Build the commit step over the plan service's association locks and recording guard ports."""
        super().setUp()
        self.paths = PortablePaths.from_root(Path(self.temp.name) / "manager")
        self.events: list[str] = []
        self.ports = RecordingPorts(self.events)
        # The plan service and the commit step share one recording association lock set (T2.3-F9)
        self.plans._locks = RecordingLocks(self.events)
        self.journals = MissionMapJournalRepository(journal_folder(self.paths))
        self.fault = None
        self.capability_reads: list[Path] = []
        self.commit = MissionMapCommit(
            self.paths, self.journals, InstallationGuard(self.ports, self.ports), self.ports, self.plans._locks,
            owned_digest=owned_or_none, check_reserved=check_reserved, fault_hook=self.on_fault)
        self.service = MissionMapApply(self.preview, self.commit, self.paths, self.profiles, clock=lambda: NOW)
        self.counter = 0

    def on_fault(self, phase: str, index: int) -> None:
        """Run the fault of a test at one commit boundary."""
        if self.fault is not None:
            self.fault(phase, index)

    def run_apply(self, choices: dict[str, Any] | None = None, fingerprint: str | None = None,
                  identifier: str = "main") -> dict[str, Any]:
        """Review the saved plan and apply it with the reviewed fingerprint unless a test gives another."""
        self.counter += 1
        if fingerprint is None:
            fingerprint = self.review(identifier).fingerprint
        return self.service.apply(identifier, fingerprint, NO_CHOICES if choices is None else choices,
                                  operation_id=f"apply{self.counter:04d}")

    def records(self, identifier: str = "main") -> tuple[Any, Any, Any]:
        """Return the original, the baseline and the last-applied record with revisions."""
        def store(kind: str) -> AssociationRecordStore:
            return AssociationRecordStore(self.area, identifier, self.key(), kind, staging=StagingPolicy.OBSERVER)
        return OriginalStore(self.area, self.key()).read(), store("baseline").read(), store("applied").read()

    def folder(self, identifier: str = "main") -> Path:
        """Return the association folder of the profile on the verified mission."""
        return association_folder(self.area, identifier, self.key())

    def snapshot(self) -> tuple[dict[str, bytes], dict[str, bytes]]:
        """Return every file of the DayZ root and of the manager root."""
        return tree(self.root), tree(Path(self.temp.name) / "manager")
