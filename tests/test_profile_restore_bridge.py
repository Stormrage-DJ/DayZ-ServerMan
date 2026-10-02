"""Named direct restore bridge contracts and queued publication evidence."""

import time
import unittest

import tests.test_profile_restore_service as fixtures
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.profile_restore_coordinator import ProfileRestoreCoordinator
from dayz_serverman.bridge.facade import BridgeFacade


class DirectRestoreBridgeTests(unittest.TestCase):
    """Exercise exact requests through a real mutation lane and production handlers."""

    def setUp(self):
        """Compose the disposable archive fixture with the real bridge coordinator."""
        self.fixture = fixtures.DirectRestoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.operations = OperationManager(OperationStore(self.fixture.root / "operations"))
        self.addCleanup(self.operations.shutdown, 5)
        coordinator = ProfileRestoreCoordinator(self.fixture.service, self.operations)
        self.bridge = BridgeFacade(coordinator.handlers())

    def dispatch(self, method, parameters):
        """Build the exact version-one named bridge envelope."""
        return self.bridge.dispatch({"contract_version": 1, "request_id": "direct-test", "method": method, "parameters": parameters})

    def test_invalid_fields_and_optional_types_are_rejected(self):
        """Extra fields and invalid port/storage choices never publish records."""
        for patch in ({"unexpected": True}, {"game_port": True}, {"storage_policy": "unsafe"}):
            result = self.dispatch("preview_profile_restore", {**self.fixture.request, **patch})
            self.assertFalse(result["success"])
            self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        parameters = self.fixture.parameters()
        result = self.dispatch("restore_profile_from_backup", {**parameters, "game_port": True})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.fixture.profiles.list(), ())

    def test_named_apply_reconstructs_profile_in_shared_lane(self):
        """The queued operation creates a revision-zero profile and reports readiness."""
        preview = self.dispatch("preview_profile_restore", self.fixture.request)
        self.assertTrue(preview["success"])
        parameters = {**self.fixture.request, "expected_manifest_digest": preview["value"]["manifest_digest"],
                      "preview_fingerprint": preview["value"]["preview_fingerprint"], "overwrite_confirmation": None}
        result = self.dispatch("restore_profile_from_backup", parameters)
        self.assertTrue(result["success"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            operation = self.operations.get(result["value"]["operation_id"])
            if operation.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                break
            time.sleep(.01)
        self.assertEqual(operation.state.value, "SUCCEEDED", operation.to_dict())
        self.assertEqual(operation.kind, "RESTORE_PROFILE_FROM_BACKUP")
        self.assertEqual(operation.result["profile"]["revision"], 0)
        self.assertTrue(operation.result["readiness"]["ready"])
        self.assertFalse(operation.result["readiness"]["server_started"])
