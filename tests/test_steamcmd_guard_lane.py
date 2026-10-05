"""The Workshop update and the sign-in hold the SteamCMD guard from preflight to the end of the run."""
from __future__ import annotations

import time
import unittest

try:
    from tests import server_build_fixtures  # noqa: F401
    from tests import test_workshop_coordinator as workshop_base
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401
    import test_workshop_coordinator as workshop_base

# The module is imported, not the class, so the loader does not run the base cases here again
FakeVerifier = workshop_base.FakeVerifier

from dayz_serverman.adapters.windows.steamcmd import SteamCmdRunResult
from dayz_serverman.application.steamcmd_guard import SteamCmdRunGuard
from dayz_serverman.application.workshop_coordinator import WorkshopCoordinator
from dayz_serverman.application.workshop_updates import WorkshopUpdateService
from dayz_serverman.bridge.facade import BridgeFacade


class GuardedLaneTests(workshop_base.WorkshopCoordinatorTests):
    """Detailed design 14.5 with Architect changes 1 and 2; fakes only, no SteamCMD runs."""

    def setUp(self) -> None:
        """Wire the update service with a guard and record the order of the SteamCMD steps."""
        super().setUp()
        self.guard = SteamCmdRunGuard(poll_seconds=0.01)
        self.order: list[str] = []
        inspect, revalidate = self.preflight.inspect, self.preflight.revalidate
        self.preflight.inspect = lambda settings: (self.order.append(f"inspect:{self.guard.available()}"),
                                                   inspect(settings))[1]
        self.preflight.revalidate = lambda paths: (self.order.append(f"revalidate:{self.guard.available()}"),
                                                   revalidate(paths))[1]
        self.steamcmd.after_run = lambda: self.order.append(f"run:{self.guard.available()}")
        service = WorkshopUpdateService(self.profiles, self.settings, self.preflight, self.steamcmd,
                                        FakeVerifier, steamcmd_guard=self.guard)
        self.handlers = WorkshopCoordinator(service, self.settings, self.operations).handlers()
        self.bridge = BridgeFacade(self.handlers)

    def run_update(self):
        """Submit one update and wait for its terminal record."""
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        self.assertTrue(accepted["success"], accepted)
        return self.wait(accepted["value"]["operation_id"])

    def test_the_update_holds_the_guard_from_inspect_to_the_end_of_the_run(self) -> None:
        """No check can take the guard between preflight, revalidation and launch."""
        operation = self.run_update()
        self.assertEqual(operation.state.value, "SUCCEEDED")
        self.assertEqual(self.order[:3], ["inspect:False", "revalidate:False", "run:False"])
        self.assertTrue(self.guard.available())

    def test_the_sign_in_holds_the_guard(self) -> None:
        """Interactive sign-in takes the guard before the preflight and releases it after the run."""
        accepted = self.dispatch("authenticate_steamcmd", {"expected_settings_revision": self.settings_revision})
        self.assertEqual(self.wait(accepted["value"]["operation_id"]).state.value, "SUCCEEDED")
        self.assertEqual(self.order[:2], ["inspect:False", "revalidate:False"])
        self.assertTrue(self.guard.available())

    def test_every_failure_path_releases_the_guard(self) -> None:
        """A drifted path and a failed launch end the operation and free the guard."""
        self.preflight.fail_revalidation = True
        self.assertEqual(self.run_update().state.value, "FAILED")
        self.assertTrue(self.guard.available())
        self.preflight.fail_revalidation = False

        def explode(*_args, **_kwargs):
            """Fail the launch."""
            raise OSError("launch failed")

        self.steamcmd.run_update = explode
        self.assertEqual(self.run_update().state.value, "FAILED")
        self.assertTrue(self.guard.available())

    def test_an_unproven_exit_poisons_the_guard_and_later_holders_fail_before_preflight(self) -> None:
        """The update reports the recovery; the next SteamCMD operation fails at once."""
        self.steamcmd.termination_confirmed = False
        operation = self.run_update()
        self.assertEqual((operation.state.value, operation.terminal_error.code),
                         ("RECOVERY_REQUIRED", "UPDATE_RESULT_UNKNOWN"))
        self.assertTrue(self.guard.poisoned)
        # A fresh service on the same poisoned guard: the next update fails before its preflight
        self.order.clear()
        service = WorkshopUpdateService(self.profiles, self.settings, self.preflight, self.steamcmd,
                                        FakeVerifier, steamcmd_guard=self.guard)
        operations = type(self.operations)(type(self.operations._store)(self.paths.operations / "second"))
        try:
            bridge = BridgeFacade(WorkshopCoordinator(service, self.settings, operations).handlers())
            accepted = bridge.dispatch({"contract_version": 1, "request_id": "t", "method": "update_workshop_items",
                                        "parameters": self.update_parameters()})
            deadline = time.monotonic() + 3
            record = operations.get(accepted["value"]["operation_id"])
            while record.state.value not in ("SUCCEEDED", "FAILED", "RECOVERY_REQUIRED") and time.monotonic() < deadline:
                time.sleep(0.01)
                record = operations.get(accepted["value"]["operation_id"])
            self.assertEqual((record.state.value, record.terminal_error.code), ("FAILED", "STEAMCMD_EXIT_UNPROVEN"))
            self.assertEqual(self.order, [])
        finally:
            operations.shutdown(2)

    def test_a_held_guard_shows_the_wait_phase_and_the_cancellation_stops_there(self) -> None:
        """While a check holds the guard the update waits at its safe point and can be cancelled."""
        self.assertTrue(self.guard.try_hold())
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        operation_id = accepted["value"]["operation_id"]
        deadline = time.monotonic() + 3
        while self.operations.get(operation_id).progress_phase != "wait_steamcmd" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.operations.get(operation_id).progress_phase, "wait_steamcmd")
        self.assertEqual(self.order, [])
        self.operations.request_cancellation(operation_id)
        self.assertEqual(self.wait(operation_id).state.value, "CANCELLED")
        self.assertEqual(self.order, [])
        self.guard.release()

    def test_a_guard_freed_during_the_wait_lets_the_update_run(self) -> None:
        """The update takes the guard as soon as the check releases it."""
        self.assertTrue(self.guard.try_hold())
        accepted = self.dispatch("update_workshop_items", self.update_parameters())
        time.sleep(0.1)
        self.guard.release()
        self.assertEqual(self.wait(accepted["value"]["operation_id"]).state.value, "SUCCEEDED")
        self.assertEqual(self.order[0], "inspect:False")


class GuardedSignInTests(GuardedLaneTests):
    """QF-051: a Steam sign-in whose SteamCMD exit is unproven poisons the guard."""

    def test_an_unproven_sign_in_poisons_the_guard(self) -> None:
        """The sign-in reports the recovery; the next SteamCMD holder fails before its preflight."""
        self.steamcmd.authenticate_interactive = lambda *_args: SteamCmdRunResult(0, (), False, False, 77)
        accepted = self.dispatch("authenticate_steamcmd", {"expected_settings_revision": self.settings_revision})
        operation = self.wait(accepted["value"]["operation_id"])
        self.assertEqual((operation.state.value, operation.terminal_error.code),
                         ("RECOVERY_REQUIRED", "UPDATE_RESULT_UNKNOWN"))
        self.assertTrue(self.guard.poisoned)
        self.assertFalse(self.guard.try_hold())


# The inherited cases of the base class run in their own module only
for _name in [name for name in vars(workshop_base.WorkshopCoordinatorTests) if name.startswith("test_")]:
    setattr(GuardedLaneTests, _name, None)
for _name in [name for name in vars(GuardedLaneTests) if name.startswith("test_")]:
    setattr(GuardedSignInTests, _name, None)


if __name__ == "__main__":
    unittest.main()
