"""Workshop rework tests for drift, absence proof, and item classification."""
from __future__ import annotations

import time
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workshop_coordinator as fixtures

from dayz_serverman.application.workshop_coordinator import WorkshopCoordinator
from dayz_serverman.application.workshop_updates import WorkshopUpdateService
from dayz_serverman.bridge.facade import BridgeFacade


class WorkshopReworkTests(unittest.TestCase):
    """Negative and classification paths of the workshop update coordinator."""
    def setUp(self) -> None:
        """Build the coordinator fixture case without running its own tests."""
        self.case = fixtures.WorkshopCoordinatorTests(methodName="runTest")
        self.case.setUp()

    def tearDown(self) -> None:
        """Tear the fixture case down."""
        self.case.tearDown()

    def test_path_identity_drift_blocks_before_child_evidence(self) -> None:
        """Path identity drift blocks before any child evidence exists."""
        case = self.case
        # Force revalidation to fail before the child launches
        case.preflight.fail_revalidation = True
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.terminal_error.code, "STEAMCMD_PATH_CHANGED")
        self.assertIsNone(case.steamcmd.argv)
        self.assertIsNone(operation.result)

    def test_settings_drift_at_child_boundary_blocks_before_launch(self) -> None:
        """Settings drift at the child boundary blocks the launch."""
        case = self.case
        # Change the settings revision as the launch hook fires
        def drift() -> None:
            """Save the loaded settings under a fresh revision."""
            current = case.settings.load()
            values = {field: getattr(current, field) for field in (
                "dayz_root", "dayz_executable", "steamcmd_root", "steamcmd_executable",
                "workshop_content_root", "custom_backup_root", "steam_account_name",
                "steam_authentication_mode",
            )}
            case.settings.save(fixtures.SettingsInput(**values), current.revision)
        # Arm the drift hook and run the update
        case.steamcmd.before_launch_hook = drift
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.terminal_error.code, "REVISION_CONFLICT")
        self.assertIsNone(operation.result)

    def test_unproven_tree_exit_blocks_same_process_mutations(self) -> None:
        """An unproven child exit blocks later mutations."""
        case = self.case
        # Report an unconfirmed tree exit so recovery is required
        case.steamcmd.termination_confirmed = False
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "RECOVERY_REQUIRED")
        self.assertEqual(operation.terminal_error.code, "UPDATE_RESULT_UNKNOWN")
        self.assertEqual(operation.result["child_state"], "CHILD_LAUNCHED")
        # Confirm later mutations are refused while recovery is required
        rejected = case.dispatch("save_steam_settings", {
            "expected_revision": case.settings_revision,
            "authentication_mode": "ACCOUNT", "account_name": "operator",
        })
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["error"]["code"], "MUTATION_CONFLICT")

    def test_each_successful_item_is_verified_once(self) -> None:
        """Each successful item is verified exactly once."""
        case = self.case
        # Build a service whose digest drifts after the first verification
        fixtures.DriftingVerifier.calls = 0
        service = WorkshopUpdateService(
            case.profiles, case.settings, case.preflight, case.steamcmd,
            fixtures.DriftingVerifier,
        )
        case.handlers.update(WorkshopCoordinator(service, case.settings, case.operations).handlers())
        case.bridge = BridgeFacade(case.handlers)
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(fixtures.DriftingVerifier.calls, 1)
        self.assertEqual(operation.result["download_state"], "VERIFIED")
        self.assertEqual(operation.result["items"][0]["outcome"], "VERIFIED_CURRENT")

    def test_missing_item_before_run_is_classified_as_downloaded(self) -> None:
        """An item missing before the run is classified as downloaded."""
        case = self.case
        # Verify with an observation that the item was absent before the run
        class DownloadedVerifier(fixtures.FakeVerifier):
            """Verifier that reports the item absent before the run."""
            def observe(self, workshop_ids):
                """Report each requested item without an installed manifest."""
                return tuple(fixtures.WorkshopObservation(item, None, "9", None, 1)
                             for item in workshop_ids)

        # Wire the downloaded-item verifier into a fresh service and bridge
        service = WorkshopUpdateService(
            case.profiles, case.settings, case.preflight, case.steamcmd,
            DownloadedVerifier,
        )
        case.handlers.update(WorkshopCoordinator(service, case.settings, case.operations).handlers())
        case.bridge = BridgeFacade(case.handlers)
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.result["download_state"], "VERIFIED")
        self.assertEqual(operation.result["items"][0]["outcome"], "DOWNLOADED_VERIFIED")

    def test_manifest_change_is_classified_as_updated(self) -> None:
        """An advanced manifest is classified as updated."""
        case = self.case
        # Verify with an observation that reports an outdated installed manifest
        class UpdatedVerifier(fixtures.FakeVerifier):
            """Verifier that reports an outdated installed manifest."""
            def observe(self, workshop_ids):
                """Report each requested item with an outdated installed manifest."""
                return tuple(fixtures.WorkshopObservation(item, "8", "9", 1, 2)
                             for item in workshop_ids)

        # Wire the updated-item verifier into a fresh service and bridge
        service = WorkshopUpdateService(
            case.profiles, case.settings, case.preflight, case.steamcmd,
            UpdatedVerifier,
        )
        case.handlers.update(WorkshopCoordinator(service, case.settings, case.operations).handlers())
        case.bridge = BridgeFacade(case.handlers)
        accepted = case.dispatch("update_workshop_items", case.update_parameters())
        operation = case.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.result["items"][0]["outcome"], "UPDATED_VERIFIED")

    def test_interactive_auth_cancellation_reaches_cancelled_terminal_state(self) -> None:
        """A cancelled interactive authentication reaches the cancelled state."""
        case = self.case
        # Run authentication with a blocking SteamCMD stand-in
        service = WorkshopUpdateService(
            case.profiles, case.settings, case.preflight,
            fixtures.BlockingSteamCmd(), fixtures.FakeVerifier,
        )
        case.handlers.update(WorkshopCoordinator(service, case.settings, case.operations).handlers())
        case.bridge = BridgeFacade(case.handlers)
        accepted = case.dispatch("authenticate_steamcmd", {
            "expected_settings_revision": case.settings_revision,
        })
        operation_id = accepted["value"]["operation_id"]
        # Wait until the blocking authentication is reported as running
        deadline = time.monotonic() + 2
        while case.operations.get(operation_id).state.value != "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.01)
        # Cancel the running operation and confirm the cancelled terminal state
        case.operations.request_cancellation(operation_id)
        self.assertEqual(case.wait(operation_id).state.value, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
