"""Workshop coordinator tests for ordered sessions and context guards."""
from __future__ import annotations

import tempfile
import time
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics
from dayz_serverman.adapters.windows.steamcmd import SteamCmdPaths, SteamCmdRunResult
from dayz_serverman.adapters.windows.process_tree import ChildEvidence
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.application.settings import SettingsService
from dayz_serverman.application.workshop_coordinator import WorkshopCoordinator
from dayz_serverman.application.workshop_updates import WorkshopUpdateService
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.domain.models import SettingsInput
from dayz_serverman.domain.profiles import ProfileInput
from dayz_serverman.domain.workshop import CacheProof, WorkshopObservation
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profiles import ProfileRepository
from dayz_serverman.observability.structured_log import StructuredLogger


class FakePreflight:
    """SteamCMD path inspection and revalidation stand-in."""
    def __init__(self, paths: SteamCmdPaths) -> None:
        """Store the reported paths and the forced revalidation failure flag."""
        self.paths = paths
        self.fail_revalidation = False

    def inspect(self, _settings):
        """Return the pre-validated SteamCMD paths."""
        return self.paths

    def revalidate(self, _paths):
        """Raise on request to simulate path drift after validation."""
        if self.fail_revalidation:
            raise RuntimeError("synthetic path drift")
        return None


class FakeSteamCmd:
    """SteamCMD adapter stand-in that records the arguments it receives."""
    def __init__(self, lines=("Success. Downloaded item 111",)) -> None:
        """Initialize recorded lines, hooks, and child process flags."""
        self.lines = lines
        self.argv = None
        self.authentication_accounts: list[str] = []
        self.after_run = None
        self.exit_code = 0
        self.termination_confirmed = True
        self.before_launch_hook = None

    def authenticate_interactive(self, _paths, account_name, cancellation_requested, on_launched,
                                 before_launch):
        """Report a launched authentication child and record the account name."""
        before_launch()
        self.authentication_accounts.append(account_name)
        on_launched(ChildEvidence(77, "fake:77"))
        return SteamCmdRunResult(0, (), cancellation_requested(), True, 77,
                                 ChildEvidence(77, "fake:77"))

    def run_update(self, _paths, argv, cancellation_requested, on_launched, before_launch):
        """Report a launched update child, record argv, and run the scripted hooks."""
        if self.before_launch_hook:
            self.before_launch_hook()
        before_launch()
        self.argv = argv
        on_launched(ChildEvidence(77, "fake:77"))
        cancelled = cancellation_requested()
        if self.after_run:
            self.after_run()
        return SteamCmdRunResult(self.exit_code, tuple(self.lines), cancelled,
                                 self.termination_confirmed, 77)


class FakeVerifier:
    """Cache verifier that returns stable observations and proofs."""
    def __init__(self, _root: Path) -> None:
        """Accept the cache root even though the fake needs no state from it."""
        pass

    def observe(self, workshop_ids: tuple[str, ...]):
        """Report every requested item as installed and current."""
        return tuple(WorkshopObservation(item, "9", "9", 1, 1) for item in workshop_ids)

    def verify(self, workshop_id: str) -> CacheProof:
        """Return a fixed cache proof for the requested item."""
        return CacheProof(workshop_id, "a" * 64, "b" * 64, 1, 4,
                          "2026-09-27T00:00:00.000+00:00", "9", "d" * 64)


class DriftingVerifier(FakeVerifier):
    """Verifier whose content digest drifts after the first call."""
    calls = 0

    def verify(self, workshop_id: str) -> CacheProof:
        """Return a drifted content digest after the first verification."""
        type(self).calls += 1
        digest = "b" * 64 if self.calls == 1 else "c" * 64
        return CacheProof(workshop_id, "a" * 64, digest, 1, 4,
                          "2026-09-27T00:00:00.000+00:00", "9", "d" * 64)


class MutatingSecondVerifier(FakeVerifier):
    """Verifier whose content digest drifts only after the second call."""
    calls = 0

    def verify(self, workshop_id: str) -> CacheProof:
        """Return a stable digest for two calls, then a drifted one."""
        type(self).calls += 1
        digest = "b" * 64 if self.calls <= 2 else "c" * 64
        return CacheProof(workshop_id, "a" * 64, digest, 1, 4,
                          "2026-09-27T00:00:00.000+00:00", "9", "d" * 64)


class BlockingSteamCmd(FakeSteamCmd):
    """SteamCMD stand-in that blocks authentication until cancelled."""
    def authenticate_interactive(self, _paths, account_name, cancellation_requested, on_launched,
                                 before_launch):
        """Report a launched child and wait until the caller cancels."""
        before_launch()
        self.authentication_accounts.append(account_name)
        on_launched(ChildEvidence(88, "fake:88"))
        deadline = time.monotonic() + 2
        while not cancellation_requested() and time.monotonic() < deadline:
            time.sleep(0.01)
        return SteamCmdRunResult(None, (), True, True, 88, ChildEvidence(88, "fake:88"))


class WorkshopCoordinatorTests(unittest.TestCase):
    """Coordinated update session contracts for revisions, secrets, and classification."""
    def setUp(self) -> None:
        """Build settings, a profile, and a fake SteamCMD composition."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # Create the manager layout and the SteamCMD fixture layout
        self.paths = PortablePaths.from_root(self.root / "Manager")
        self.paths.create_layout()
        self.steam_root = self.root / "SteamCMD"
        self.steam_root.mkdir()
        self.executable = self.steam_root / "steamcmd.exe"
        self.executable.write_bytes(b"fixture")
        self.workshop = self.root / "Workshop" / "content" / "221100"
        self.workshop.mkdir(parents=True)
        # Persist path settings and a profile with one workshop and one local mod
        self.settings = SettingsService(
            VersionedJsonRepository(self.paths.manager_config), self.paths,
            WindowsPathDiagnostics(),
        )
        saved = self.settings.save(SettingsInput(
            steamcmd_root=str(self.steam_root), steamcmd_executable=str(self.executable),
            workshop_content_root=str(self.workshop), steam_account_name="operator",
            steam_authentication_mode="ACCOUNT",
        ), None)
        self.settings_revision = saved.revision
        self.profiles = ProfileService(ProfileRepository(self.paths.profiles), self.settings)
        self.profile = self.profiles.save(ProfileInput.parse({
            "profile_id": "primary", "display_name": "Primary",
            "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
            "runtime_profile": None, "mission_root": None, "game_port": 2302,
            "mods": [
                {"directory": "@Workshop", "launch_scope": "client",
                 "source": {"kind": "workshop", "workshop_id": "111"}},
                {"directory": "@Local", "launch_scope": "server",
                 "source": {"kind": "external"}},
            ], "extra_arguments": [],
        }), None)
        self.operations = OperationManager(OperationStore(self.paths.operations))
        self.steamcmd = FakeSteamCmd()
        self.preflight = FakePreflight(SteamCmdPaths(self.steam_root, self.executable, self.workshop))
        # Wire the update service and coordinator handlers behind the bridge
        service = WorkshopUpdateService(
            self.profiles, self.settings,
            self.preflight,
            self.steamcmd, FakeVerifier,
        )
        self.handlers = WorkshopCoordinator(service, self.settings, self.operations).handlers()
        self.bridge = BridgeFacade(self.handlers)

    def tearDown(self) -> None:
        """Shut the operations manager down and remove the temporary tree."""
        self.operations.shutdown(2)
        self.temporary.cleanup()

    def dispatch(self, method: str, parameters: dict[str, object]):
        """Dispatch one bridge request and return its response."""
        return self.bridge.dispatch({
            "contract_version": 1, "request_id": "test", "method": method,
            "parameters": parameters,
        })

    def wait(self, operation_id: str):
        """Wait for the operation to reach a terminal state or fail the test."""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            operation = self.operations.get(operation_id)
            if operation.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                return operation
            time.sleep(0.01)
        self.fail("operation did not finish")

    def update_parameters(self) -> dict[str, object]:
        """Build the valid update parameters for the fixture profile."""
        return {
            "profile_id": "primary", "expected_profile_revision": self.profile.revision,
            "expected_semantic_profile_digest": self.profile.semantic_digest,
            "expected_settings_revision": self.settings_revision,
            "authentication_mode": "ACCOUNT", "account_name": "operator",
            "update_all_and_start": True,
        }

    def test_update_is_one_ordered_session_and_start_stays_blocked(self) -> None:
        """One update session verifies the cache and keeps the start step blocked."""
        # Run one coordinated update against the fake SteamCMD
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        self.assertTrue(accepted["success"])
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "SUCCEEDED")
        self.assertEqual(operation.result["download_state"], "VERIFIED")
        self.assertEqual(operation.result["profile_revision"], self.profile.revision)
        self.assertEqual(operation.result["settings_revision"], self.settings_revision)
        self.assertRegex(operation.result["authentication_identity_digest"], r"^[0-9a-f]{64}$")
        self.assertNotIn("operator", str(operation.result).casefold())
        self.assertEqual(operation.result["start_error"], "PUBLICATION_REQUIRED")
        self.assertTrue(operation.result["start_requested"])
        self.assertFalse(operation.result["start_authorized"])
        # Confirm only the workshop mod was passed to SteamCMD
        self.assertIn(("+workshop_download_item", "221100", "111"), tuple(
            tuple(self.steamcmd.argv[index:index + 3])
            for index in range(len(self.steamcmd.argv) - 2)
        ))
        self.assertNotIn("@Local", self.steamcmd.argv)

    def test_secret_and_unknown_fields_are_rejected_before_queue(self) -> None:
        """Secret-shaped fields are rejected before any operation is queued."""
        parameters = self.update_parameters()
        # Smuggle a password field into otherwise valid parameters
        parameters["password"] = "never-store-me"
        rejected = self.dispatch("update_workshop_items", parameters)
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.operations.list_recent(), ())

    def test_rejected_secret_field_never_enters_structured_log(self) -> None:
        """A rejected secret field never enters the structured log."""
        log_path = self.root / "security.jsonl"
        bridge = BridgeFacade(self.handlers, StructuredLogger(log_path))
        parameters = self.update_parameters()
        # Stamp a sentinel secret and confirm it never appears in the log
        parameters["password"] = "SENTINEL-NOT-STORED"
        bridge.dispatch({"contract_version": 1, "request_id": "security-test",
                         "method": "update_workshop_items", "parameters": parameters})
        self.assertNotIn("SENTINEL-NOT-STORED", log_path.read_text(encoding="utf-8"))

    def test_stale_profile_context_fails_in_lane(self) -> None:
        """Stale profile context fails the operation before SteamCMD runs."""
        parameters = self.update_parameters()
        # Present a stale semantic profile digest
        parameters["expected_semantic_profile_digest"] = "0" * 64
        accepted = self.dispatch("update_workshop_items", parameters)
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "FAILED")
        self.assertEqual(operation.terminal_error.code, "REVISION_CONFLICT")
        self.assertIsNone(self.steamcmd.argv)

    def test_settings_revision_drift_after_child_blocks_verification(self) -> None:
        """Settings drift after the child run blocks verification."""
        def change_settings() -> None:
            """Save the current settings under a new revision."""
            current = self.settings.load()
            values = {field: getattr(current, field) for field in (
                "dayz_root", "dayz_executable", "steamcmd_root", "steamcmd_executable",
                "workshop_content_root", "custom_backup_root", "steam_account_name",
                "steam_authentication_mode",
            )}
            self.settings.save(SettingsInput(**values), current.revision)
        # Trigger the drift from the after-run hook
        self.steamcmd.after_run = change_settings
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "FAILED")
        self.assertEqual(operation.terminal_error.code, "REVISION_CONFLICT")

    def test_nonzero_exit_cannot_create_success_from_success_text(self) -> None:
        """A nonzero exit cannot be overridden by success text."""
        # Report a nonzero exit despite success output
        self.steamcmd.exit_code = 7
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.result["download_state"], "UNKNOWN")
        self.assertEqual(operation.result["items"][0]["error_code"], "UPDATE_RESULT_UNKNOWN")
        self.assertEqual(operation.result["steamcmd_exit_code"], 7)

    def test_nonzero_exit_preserves_classified_failure(self) -> None:
        """A nonzero exit preserves the classified authentication failure."""
        # Report an authentication failure message with a nonzero exit
        self.steamcmd.exit_code = 7
        self.steamcmd.lines = ("ERROR! Not logged on",)
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.result["download_state"], "FAILED")
        self.assertEqual(operation.result["items"][0]["error_code"], "AUTHENTICATION_FAILED")
        self.assertEqual(
            operation.result["steamcmd_summary"],
            "Steam authentication is required or was rejected.",
        )

    def test_interactive_auth_receives_only_account_name(self) -> None:
        """Interactive authentication receives only the account name."""
        accepted = self.dispatch("authenticate_steamcmd", {
            "expected_settings_revision": self.settings_revision,
        })
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "SUCCEEDED")
        self.assertEqual(self.steamcmd.authentication_accounts, ["operator"])

    def test_named_settings_contract_rejects_secret_and_saves_explicit_mode(self) -> None:
        """The settings contract rejects secrets and saves the explicit mode."""
        rejected = self.dispatch("save_steam_settings", {
            "expected_revision": self.settings_revision, "authentication_mode": "ACCOUNT",
            "account_name": "operator", "guard_code": "never-store-me",
        })
        self.assertFalse(rejected["success"])
        # Reject an account name that looks like a password assignment
        disguised = self.dispatch("save_steam_settings", {
            "expected_revision": self.settings_revision, "authentication_mode": "ACCOUNT",
            "account_name": "password=secret",
        })
        self.assertFalse(disguised["success"])
        # Save the anonymous mode and confirm what the settings hold
        accepted = self.dispatch("save_steam_settings", {
            "expected_revision": self.settings_revision,
            "authentication_mode": "ANONYMOUS", "account_name": None,
        })
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual(operation.state.value, "SUCCEEDED")
        loaded = self.settings.load()
        self.assertEqual(loaded.steam_authentication_mode, "ANONYMOUS")
        self.assertIsNone(loaded.steam_account_name)


if __name__ == "__main__":
    unittest.main()
