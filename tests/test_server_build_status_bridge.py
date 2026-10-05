"""Bridge fields of the server build check: `server_build`, the null profile and the scopes (14.8)."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import MappingProxyType

try:
    from tests import server_build_fixtures as fakes
    from tests.server_build_fixtures import OWNER_MARKER, library_install, manifest
    from tests.update_check_fixtures import Harness
except ModuleNotFoundError:
    import server_build_fixtures as fakes
    from server_build_fixtures import OWNER_MARKER, library_install, manifest
    from update_check_fixtures import Harness

from dayz_serverman.application.server_build import ServerBuildService
from dayz_serverman.application.steamcmd_guard import SteamCmdRunGuard
from dayz_serverman.application.update_check_coordinator import UpdateCheckCoordinator
from dayz_serverman.bridge.contracts import CONTRACT_VERSION
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.domain.models import ManagerSettings

FIELDS = {"state", "reason", "ownership", "installed_build", "installed_branch", "target_build",
          "available_build", "available_time", "check_state", "checked_at", "last_success_at",
          "error_code", "checking", "waiting", "revision", "paused"}


class FakeInventory:
    """Inventory stand-in with two rows, one of them with an update."""

    def report(self, profile_id):
        """Return the rows and no derived view."""
        return [{"state": "UPDATE_AVAILABLE"}, {"state": "CURRENT"}], None


class BridgeTests(unittest.TestCase):
    """Additive fields only; the mod answers stay unchanged; CONTRACT_VERSION stays 1."""

    def setUp(self) -> None:
        """Wire the coordinator with a real build service over fakes."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        dayz = library_install(root, manifest())
        settings = ManagerSettings(2, str(dayz), None, str(root / "S"), str(root / "S" / "steamcmd.exe"),
                                   None, None, MappingProxyType({}))
        self.app = fakes.FakeAppInfo()
        self.release = threading.Event()
        self.release.set()
        self.app.during = lambda _cancel: self.release.wait(5)
        self.drained = True
        self.build = ServerBuildService(fakes.FakePreflight(), self.app, fakes.FakeCache(), lambda: settings,
                                        SteamCmdRunGuard(), lambda: self.drained,
                                        start_worker=lambda work: work())
        self.mods = Harness()
        coordinator = UpdateCheckCoordinator(self.mods.service, FakeInventory(), self.build)
        self.bridge = BridgeFacade(coordinator.handlers())

    def call(self, method: str, parameters: dict) -> dict:
        """Dispatch one request and return its envelope."""
        return self.bridge.dispatch({"contract_version": CONTRACT_VERSION, "request_id": "b",
                                     "method": method, "parameters": parameters})

    def test_status_fields(self) -> None:
        """`server_build` has the exact field set; top-level fields keep their mod meaning."""
        self.assertEqual(CONTRACT_VERSION, 1)
        value = self.call("get_update_status", {"profile_id": "main"})["value"]
        self.assertEqual(set(value), {"mods", "server_build", "checking", "revision"})
        self.assertEqual(value["mods"]["update_count"], 1)
        self.assertEqual(set(value["server_build"]), FIELDS)
        self.assertEqual((value["server_build"]["state"], value["server_build"]["reason"]), ("COULD_NOT_CHECK", "NEVER"))
        self.call("request_update_check", {"scope": "server_build", "force": True})
        build = self.call("get_update_status", {"profile_id": "main"})["value"]["server_build"]
        self.assertEqual((build["state"], build["installed_build"], build["available_build"], build["ownership"]),
                         ("CURRENT", 24570360, 24570360, "STEAM_CLIENT"))
        self.assertNotIn(OWNER_MARKER, json.dumps(build))

    def test_null_profile_returns_the_server_build_only(self) -> None:
        """Without a profile the mods part is null and no profile is read."""
        value = self.call("get_update_status", {"profile_id": None})["value"]
        self.assertIsNone(value["mods"])
        self.assertEqual(set(value["server_build"]), FIELDS)
        refused = self.call("get_update_status", {"profile_id": None, "extra": 1})
        self.assertEqual(refused["error"]["code"], "INVALID_REQUEST")

    def test_scopes(self) -> None:
        """mods unchanged; server_build and all carry the build answer; anything else is refused."""
        mods = self.call("request_update_check", {"scope": "mods", "force": False})["value"]
        self.assertEqual(set(mods), {"accepted", "checking"})
        self.assertEqual(self.app.calls, 0)
        self.drained = False
        build = self.call("request_update_check", {"scope": "server_build", "force": True})["value"]
        self.assertEqual(build, {"accepted": False, "checking": False,
                                 "server_build": {"accepted": False, "checking": False, "waiting": True}})
        self.drained = True
        both = self.call("request_update_check", {"scope": "all", "force": True})["value"]
        self.assertEqual(set(both), {"accepted", "checking", "server_build"})
        self.assertTrue(both["server_build"]["accepted"])
        self.assertEqual(self.app.calls, 1)
        for scope in ("ALL", "server", None, 1):
            with self.subTest(scope=scope):
                self.assertEqual(self.call("request_update_check", {"scope": scope, "force": True})["error"]["code"],
                                 "INVALID_REQUEST")
        # A request that is not forced starts no build run
        self.call("request_update_check", {"scope": "server_build", "force": False})
        self.assertEqual(self.app.calls, 1)

    def test_scope_mods_never_starts_a_build_run(self) -> None:
        """QF-053: a forced or a plain mods request never reaches the build service."""
        calls: list[bool] = []
        request = self.build.request
        self.build.request = lambda force: (calls.append(force), request(force))[1]
        for force in (True, False):
            with self.subTest(force=force):
                answer = self.call("request_update_check", {"scope": "mods", "force": force})["value"]
                self.assertNotIn("server_build", answer)
        self.assertEqual((calls, self.app.calls), ([], 0))
        self.call("request_update_check", {"scope": "all", "force": True})
        self.assertEqual(calls, [True])

    def test_status_is_not_blocked_by_a_running_build_check(self) -> None:
        """The status call returns at once while a check waits inside SteamCMD."""
        self.release.clear()
        self.build._start_worker = lambda work: threading.Thread(target=work, daemon=True).start()
        self.assertTrue(self.call("request_update_check", {"scope": "server_build", "force": True})["value"]["accepted"])
        started = time.perf_counter()
        value = self.call("get_update_status", {"profile_id": None})["value"]
        elapsed = time.perf_counter() - started
        self.release.set()
        # Let the worker finish before the temporary tree is removed
        deadline = time.monotonic() + 5
        while self.build.check_view()["checking"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(value["server_build"]["checking"])
        self.assertLess(elapsed, 0.1)


if __name__ == "__main__":
    unittest.main()
