"""Restore original and Restore previous state of leaf 8.8 over the Apply fixture of leaf 8.7."""

from __future__ import annotations

import json
from typing import Any

from dayz_serverman.application.mission_map_restore import MissionMapRestore
from dayz_serverman.application.mission_map_restore_review import RestoreReader, RestoreReview
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, ledger_folder
from tests.mission_map_apply_support import NOW, ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_preview_support import sha
from tests.test_mission_map_infected import encounter

__all__ = ["CAPTURED_ONLY", "INFECTED", "PLAN", "RestoreFixture", "sha"]

# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)
# A plan whose Apply writes generated groups, detached placements and a territory block
PLAN = [zone("rich"), INFECTED]
# Files that no restore writes: the captured-only files (Architect 2026-10-14 12:16:06)
CAPTURED_ONLY = ("env/wolf_territories.xml", "env/bear_territories.xml")


class RestoreFixture(ApplyFixture):
    """The Apply fixture with the restore review and the restore service over the same commit step."""

    def setUp(self) -> None:
        """Build the restore review and service with the fixed clock of the Apply fixture."""
        super().setUp()
        self.reader = RestoreReader(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path)
        self.restorer = MissionMapRestore(self.reader, self.commit, self.paths, self.profiles, clock=lambda: NOW)
        self.restores = 0

    def restore_review(self, mode: str, identifier: str = "main") -> RestoreReview:
        """Return a complete restore review of the profile."""
        return self.reader.review(identifier, mode)

    def run_restore(self, mode: str, fingerprint: str | None = None, identifier: str = "main") -> dict[str, Any]:
        """Review and restore with the reviewed fingerprint unless a test gives another."""
        self.restores += 1
        if fingerprint is None:
            fingerprint = self.restore_review(mode, identifier).fingerprint
        return self.restorer.restore(identifier, mode, fingerprint, operation_id=f"restore{self.restores:04d}")

    def mission_state(self) -> dict[str, bytes | None]:
        """Return the bytes or absence of every managed file of the verified mission."""
        return {path: (self.mission / path).read_bytes() if (self.mission / path).exists() else None
                for path in MISSION_FILE_SET}

    def ledger_entries(self) -> dict[str, dict[str, Any]]:
        """Return every ledger entry of the verified mission by operation ID."""
        folder = ledger_folder(self.area, self.key())
        entries = [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]
        return {entry["operation_id"]: entry for entry in entries}
