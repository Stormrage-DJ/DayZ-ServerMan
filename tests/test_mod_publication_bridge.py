"""Cover the named mod publication bridge methods and terminal codes."""

from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.application.mod_publication_coordinator import ModPublicationCoordinator
from dayz_serverman.application.mod_publication import ModPublicationService
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.repositories.mod_publication_stage import PublicationStorageError
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalError
from dayz_serverman.repositories.mod_publication_inventory import PublicationInventoryError
from dayz_serverman.adapters.windows.publication_paths import PublicationPathError
from test_mod_publication_application import PublicationApplicationFixture


class ModPublicationBridgeTests(PublicationApplicationFixture):
    """Verify bridge dispatch for publication preview and publish requests."""

    def setUp(self) -> None:
        """Build the publication bridge over the shared fixture."""
        super().setUp()
        self.bridge = BridgeFacade(
            ModPublicationCoordinator(self.service, self.operations).handlers(),
        )

    def call(self, method: str, parameters: dict[str, object]):
        """Dispatch one named bridge request with a fixed request identifier."""
        return self.bridge.dispatch({"contract_version": 1, "request_id": "publication-test",
                                     "method": method, "parameters": parameters})

    def parameters(self) -> dict[str, object]:
        """Build standard publication parameters bound to the verified gate."""
        return {
            "profile_id": "main", "expected_profile_revision": self.profile.revision,
            "expected_semantic_profile_digest": self.profile.semantic_digest,
            "expected_settings_revision": self.settings_revision,
            "update_operation_id": self.gate_id,
        }

    def test_named_preview_and_publish_complete_without_start_enablement(self) -> None:
        """Verify preview and publish succeed without start enablement."""
        preview = self.call("preview_mod_publication", self.parameters())
        self.assertTrue(preview["success"])
        publish = self.parameters()
        publish["publication_fingerprint"] = preview["value"]["publication_fingerprint"]
        accepted = self.call("publish_mods_and_keys", publish)
        terminal = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(terminal.state.value, "SUCCEEDED")
        self.assertEqual(terminal.result["publication_state"], "VERIFIED")
        self.assertFalse(terminal.result["start_authorized"])
        self.assertEqual(terminal.result["start_state"], "NOT_REQUESTED")
        self.assertIsNone(terminal.result["start_error"])

    def test_raw_paths_proofs_and_unknown_fields_are_rejected_before_queue(self) -> None:
        """Verify raw paths and unknown fields are rejected before queueing."""
        # Require rejection before any operation is queued
        before = len(self.operations.list_recent())
        parameters = self.parameters()
        parameters["target_path"] = "C:\\forbidden"
        rejected = self.call("preview_mod_publication", parameters)
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(len(self.operations.list_recent()), before)

    def test_stale_review_fingerprint_fails_in_operation_lane(self) -> None:
        """Verify a stale review fingerprint fails in the operation lane."""
        parameters = self.parameters()
        parameters["publication_fingerprint"] = "0" * 64
        accepted = self.call("publish_mods_and_keys", parameters)
        terminal = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(terminal.state.value, "FAILED")
        self.assertEqual(terminal.terminal_error.code, "PUBLICATION_PREVIEW_STALE")

    def test_verified_publication_can_retry_with_a_new_publication_identity(self) -> None:
        """Verify a verified publication can retry under a new identity."""
        preview = self.call("preview_mod_publication", self.parameters())["value"]
        parameters = self.parameters()
        parameters["publication_fingerprint"] = preview["publication_fingerprint"]
        first = self.wait(self.call("publish_mods_and_keys", parameters)["value"]["operation_id"])
        second = self.wait(self.call("publish_mods_and_keys", parameters)["value"]["operation_id"])
        self.assertEqual((first.state.value, second.state.value), ("SUCCEEDED", "SUCCEEDED"))
        # Both publications must retire their journals
        retired = list(self.paths.publication_journals.joinpath("retired").glob("*.json"))
        self.assertEqual(len(retired), 2)

    def test_dayz_root_replacement_after_preview_is_stale(self) -> None:
        """Verify a root replacement after preview is stale."""
        preview = self.call("preview_mod_publication", self.parameters())["value"]
        # Replace the root after preview so publish must refuse
        self.dayz.rename(self.root / "Original DayZ Root")
        self.dayz.mkdir()
        parameters = self.parameters()
        parameters["publication_fingerprint"] = preview["publication_fingerprint"]
        terminal = self.wait(self.call("publish_mods_and_keys", parameters)["value"]["operation_id"])
        self.assertEqual(terminal.state.value, "FAILED")
        self.assertEqual(terminal.terminal_error.code, "PUBLICATION_PREVIEW_STALE")

    def test_recovery_required_and_path_failures_keep_stable_terminal_codes(self) -> None:
        """Verify storage, path, journal, and inventory failures keep stable codes."""
        cases = (
            (PublicationStorageError("PUBLICATION_FAILED", "safe failure",
                                     recovery_required=True), "RECOVERY_REQUIRED", "PUBLICATION_FAILED"),
            (PublicationPathError("safe path failure"), "FAILED", "PATH_INVALID"),
            (PublicationJournalError("unsafe detail"), "RECOVERY_REQUIRED", "RECOVERY_REQUIRED"),
            (PublicationInventoryError("KEY_COLLISION", "safe collision"),
             "FAILED", "PUBLICATION_FAILED"),
        )
        # Probe each failure family and require its stable terminal code
        for error, expected_state, expected_code in cases:
            with self.subTest(expected=expected_code):
                service = ModPublicationService(
                    self.profiles, self.settings, self.operations,
                    lambda _checkpoint, failure=error: _FailingStorage(failure),
                    PublicationJournalRepository(self.paths.publication_journals),
                    self.service._lifecycle,
                )
                bridge = BridgeFacade(ModPublicationCoordinator(service, self.operations).handlers())
                preview = bridge.dispatch({"contract_version": 1, "request_id": "preview",
                    "method": "preview_mod_publication", "parameters": self.parameters()})["value"]
                parameters = self.parameters()
                parameters["publication_fingerprint"] = preview["publication_fingerprint"]
                accepted = bridge.dispatch({"contract_version": 1, "request_id": "publish",
                    "method": "publish_mods_and_keys", "parameters": parameters})
                terminal = self.wait(accepted["value"]["operation_id"])
                self.assertEqual(terminal.state.value, expected_state)
                self.assertEqual(terminal.terminal_error.code, expected_code)
                if expected_state == "RECOVERY_REQUIRED":
                    self.assertIsNotNone(self.operations.recovery_block)
                    self.operations.clear_recovery_block()


class _FailingStorage:
    """Raise a configured error from the staging entry point."""

    def __init__(self, error: Exception) -> None:
        """Store the error raised by every stage call."""
        self.error = error

    def stage(self, _intent, _root):
        """Refuse staging by raising the configured error."""
        raise self.error


if __name__ == "__main__":
    unittest.main()
