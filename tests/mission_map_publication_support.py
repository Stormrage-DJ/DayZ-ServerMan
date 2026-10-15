"""The 8.9 owner methods behind a real bridge facade and a real operation lane, over the 8.8 restore fixture."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationState
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.mission_map_publication_composition import build_mission_map_publication
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.mission_map_restore_support import RestoreFixture
from tests.test_mission_map_preview import tree

# States after which an operation does not change again
TERMINAL = (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED, OperationState.RECOVERY_REQUIRED)


class PublicationBridgeFixture(RestoreFixture):
    """The publication builder of the composition over the fixture's services, served by BridgeFacade."""

    def setUp(self) -> None:
        """Build the lane, the 8.5 recovery and the five owner methods; `get_operation` reads the results."""
        super().setUp()
        # An unknown profile raises ProfileNotFound, as ProfileService.read does, not the stand-in's KeyError
        known = self.profiles.read

        def read(profile_id: object) -> Any:
            """Return a known profile record; refuse any other identifier."""
            if profile_id not in self.profiles.records:
                raise ProfileNotFound(f"profile {profile_id!r} does not exist")
            return known(profile_id)
        self.profiles.read = read  # type: ignore[method-assign]
        self.lane = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(self.lane.shutdown, 5)
        guard = InstallationGuard(self.ports, self.ports)
        settings = SimpleNamespace(load=lambda: self.settings)
        recovery = MissionMapRecovery(self.paths, settings, guard, self.ports, self.plans._locks)  # type: ignore[arg-type]
        # The builder needs only the lock set, the plan service and the recovery coordinator of the parts
        parts = SimpleNamespace(locks=self.plans._locks, plans=self.plans,
                                recovery_coordinator=MissionMapRecoveryCoordinator(recovery, self.lane))
        self.publication = build_mission_map_publication(
            self.paths, self.profiles, parts, guard, self.ports, self.lane,  # type: ignore[arg-type]
            medical_baseline=self.medical.zone_baseline_path, create_root=True)
        operations = ApplicationCoordinator(None, self.lane, None)  # type: ignore[arg-type]
        self.bridge = BridgeFacade({**self.publication.handlers(),
                                    "get_operation": operations.handlers()["get_operation"]})
        self.requests = 0

    def call(self, method: str, profile: str | None = "main", **parameters: Any) -> dict[str, Any]:
        """Dispatch one request; the profile ID is added as the editor API adds it, unless it is None."""
        self.requests += 1
        if profile is not None:
            parameters["profile_id"] = profile
        return self.bridge.dispatch({"contract_version": 1, "request_id": f"r{self.requests}", "method": method,
                                     "parameters": parameters})

    def value(self, method: str, profile: str | None = "main", **parameters: Any) -> Any:
        """Dispatch one request that must succeed and return its value."""
        answer = self.call(method, profile, **parameters)
        self.assertTrue(answer["success"], answer)
        return answer["value"]

    def refused(self, method: str, profile: str | None = "main", **parameters: Any) -> dict[str, Any]:
        """Dispatch one request that the bridge must refuse and return its error envelope."""
        answer = self.call(method, profile, **parameters)
        self.assertFalse(answer["success"], answer)
        return answer["error"]

    def finished(self, handle: dict[str, Any]) -> dict[str, Any]:
        """Wait until the queued operation ends and the lane is drained; return its record through the bridge."""
        operation_id = handle["operation_id"]
        for _ in range(1500):
            if self.lane.get(operation_id).state in TERMINAL:
                break
            time.sleep(0.02)
        for _ in range(500):
            if self.lane.is_drained():
                break
            time.sleep(0.02)
        return self.value("get_operation", None, operation_id=operation_id)

    def apply_bridge(self, choices: dict[str, Any] | None = None, profile: str = "main",
                     fingerprint: str | None = None) -> dict[str, Any]:
        """Preview through the bridge, queue Apply with that fingerprint unless given, and return the record."""
        if fingerprint is None:
            fingerprint = self.value("preview_mission_map", profile)["fingerprint"]
        handle = self.value("apply_mission_map", profile, preview_fingerprint=fingerprint,
                            choices=choices or {"external_changes": {}, "unavailable_encounters": {}})
        return self.finished(handle)

    def stores(self) -> tuple[dict[str, bytes], dict[str, bytes]]:
        """Return every file of the DayZ root and of the editor area; operation records are left out."""
        return tree(self.root), tree(self.area)

    def blocks(self) -> list[Any]:
        """Return every recovery block of the lane."""
        return list(self.lane._recovery_blocks._entries)
