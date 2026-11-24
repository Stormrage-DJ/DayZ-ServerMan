"""Crash restart host API (plan 3.1): the window method that cancels a pending automatic restart."""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.host.api import HostApi  # noqa: E402
from tests.test_readonly_host_api import RecordingFacade  # noqa: E402


class CancelHostApiTests(unittest.TestCase):
    """The method sends one request without parameters."""

    def test_the_method_builds_a_request_without_parameters(self) -> None:
        """The request names the bridge method, has no parameters and uses contract version 1."""
        facade = RecordingFacade()
        HostApi(facade).cancel_crash_restart()
        request = facade.requests[-1]
        self.assertEqual(request["method"], "cancel_crash_restart")
        self.assertEqual(request["parameters"], {})
        self.assertEqual(request["contract_version"], 1)

    def test_the_method_takes_no_argument(self) -> None:
        """The signature has self only."""
        self.assertEqual(list(inspect.signature(HostApi.cancel_crash_restart).parameters), ["self"])


if __name__ == "__main__":
    unittest.main()
