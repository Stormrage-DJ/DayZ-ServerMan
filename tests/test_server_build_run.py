"""One newest-build run: the exit table of 14.4, the preflight, the launch and the 60 s deadline."""
from __future__ import annotations

import threading
import unittest
from types import MappingProxyType

try:
    from tests.server_build_fixtures import FakeAppInfo, FakePreflight, app_info_lines, result
except ModuleNotFoundError:
    from server_build_fixtures import FakeAppInfo, FakePreflight, app_info_lines, result

from dayz_serverman.application.server_build_run import classify_run, run_newest_build_check
from dayz_serverman.domain.models import ManagerSettings


class ExitTableTests(unittest.TestCase):
    """Detailed design 14.4: exit handling, first match wins."""

    def test_each_row(self) -> None:
        """Unproven, shutdown, deadline, exit code, connection, unreadable, success."""
        self.assertEqual(classify_run(result(confirmed=False, cancelled=True), True, 1).error_code, "STEAMCMD_EXIT_UNPROVEN")
        self.assertTrue(classify_run(result(cancelled=True), True, 1).shutdown)
        self.assertEqual(classify_run(result(cancelled=True), False, 1).error_code, "TIMEOUT")
        for code in (1, None):
            self.assertEqual(classify_run(result(exit_code=code), False, 1).error_code, "STEAMCMD_FAILED")
        offline = tuple(line for line in app_info_lines() if "Connecting anonymously" not in line)
        self.assertEqual(classify_run(result(lines=offline), False, 1).error_code, "NOT_CONNECTED")
        broken = ("Connecting anonymously to Steam Public...OK\n", '"223350"\n', "{\n")
        self.assertEqual(classify_run(result(lines=broken), False, 1).error_code, "OUTPUT_UNREADABLE")
        good = classify_run(result(), False, 1)
        self.assertEqual((good.error_code, good.branches["public"].build_id), (None, 24570360))

    def test_preflight_launch_and_deadline(self) -> None:
        """Unset paths, a failed preflight or launch, and the 60 s deadline."""
        settings = ManagerSettings(2, None, None, "C:\\S", "C:\\S\\steamcmd.exe", None, None, MappingProxyType({}))
        app, preflight, shutdown = FakeAppInfo(), FakePreflight(), threading.Event()
        unset = ManagerSettings(2, None, None, None, None, None, None, MappingProxyType({}))
        self.assertTrue(run_newest_build_check(unset, preflight, app, shutdown, lambda: 0.0).not_configured)
        preflight.error = RuntimeError("missing")
        self.assertEqual(run_newest_build_check(settings, preflight, app, shutdown, lambda: 0.0).error_code,
                         "STEAMCMD_UNAVAILABLE")
        preflight.error = None
        app.during = lambda _cancel: (_ for _ in ()).throw(OSError("launch"))
        self.assertEqual(run_newest_build_check(settings, preflight, app, shutdown, lambda: 0.0).error_code,
                         "STEAMCMD_UNAVAILABLE")
        clock = iter((0.0, 59.9, 60.0, 61.0))
        seen: list[bool] = []
        app.during = lambda cancel: (seen.append(cancel()), seen.append(cancel()))
        app.answer = result(cancelled=True, exit_code=None)
        outcome = run_newest_build_check(settings, preflight, app, shutdown, lambda: next(clock))
        self.assertEqual((seen, outcome.error_code), ([False, True], "TIMEOUT"))


if __name__ == "__main__":
    unittest.main()
