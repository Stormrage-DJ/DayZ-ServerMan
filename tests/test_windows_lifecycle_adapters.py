"""Windows lifecycle adapter tests for launcher, mutex, and graceful stop."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.adapters.windows.graceful_stop import WindowsGracefulStop  # noqa: E402
from dayz_serverman.adapters.windows.launcher import WindowsProcessLauncher  # noqa: E402
from dayz_serverman.adapters.windows.mutex import installation_mutex_name  # noqa: E402
from dayz_serverman.application.lifecycle_ports import LaunchRequest  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    InventorySnapshot,
    LaunchEvidence,
    LifecycleFailure,
    ProcessObservation,
    canonical_process_path,
)


class FakePopen:
    """Launched process stand-in that never exits on its own."""
    pid = 321

    def poll(self):
        """Report that the fake process is still running."""
        return None


class SequencedInventory:
    """Inventory stub that returns scripted snapshots in sequence."""
    def __init__(self, *snapshots: InventorySnapshot) -> None:
        """Store the scripted snapshots in call order."""
        self.snapshots = list(snapshots)

    def candidates(self, _expected_executable: str) -> InventorySnapshot:
        """Return the next scripted snapshot for the expected executable."""
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


class WindowsLifecycleAdapterTests(unittest.TestCase):
    """Windows launcher, mutex, and graceful stop adapter contracts."""
    def test_launcher_uses_vector_cwd_allowed_environment_and_redirected_output(self) -> None:
        """The launcher uses vector argv, a filtered environment, and redirected output."""
        # Build a launch request inside a temporary server root
        with tempfile.TemporaryDirectory(prefix="serverman_launcher_") as temporary:
            root = Path(temporary)
            request = LaunchRequest(
                (str(root / "DayZ Server.exe"), "-port=2302", "-name=Árvíztűrő"),
                root,
                root / "logs" / "server.log",
                "launch-token",
            )
            with (
                patch.dict(
                    os.environ,
                    {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows", "SECRET": "no"},
                    clear=True,
                ),
                patch("dayz_serverman.adapters.windows.launcher.subprocess.Popen", return_value=FakePopen()) as popen,
                patch("dayz_serverman.adapters.windows.launcher._query_creation_time", return_value=123),
            ):
                launcher = WindowsProcessLauncher()
                receipt = launcher.launch(request)

            # Confirm the launch vectors, environment, and handle lifecycle
            arguments, keywords = popen.call_args
            self.assertEqual(arguments[0], list(request.argv))
            self.assertEqual(keywords["cwd"], str(root))
            self.assertFalse(keywords["shell"])
            self.assertEqual(keywords["stdin"], subprocess.DEVNULL)
            self.assertEqual(keywords["stderr"], subprocess.STDOUT)
            self.assertNotIn("SECRET", keywords["env"])
            self.assertEqual(keywords["env"]["DAYZ_SERVERMAN_LAUNCH_TOKEN"], "launch-token")
            self.assertTrue(request.log_path.exists())
            self.assertTrue(launcher.retains_handle(receipt.handle_token, receipt.pid))
            launcher.release_handle(receipt.handle_token)
            self.assertFalse(launcher.retains_handle(receipt.handle_token, receipt.pid))

    def test_mutex_identity_is_installation_scoped_and_case_insensitive(self) -> None:
        """The installation mutex identity is installation scoped and case insensitive."""
        first = installation_mutex_name(r"D:\Servers\Primary\.")
        second = installation_mutex_name(r"d:\servers\primary")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(r"Local\DayZ-ServerMan-"))

    def test_stop_verifies_identity_uses_non_forced_taskkill_and_waits_for_exit(self) -> None:
        """The stop adapter proves identity, avoids force, and waits for exit."""
        # Build launch evidence and a probe that observes the process stopping
        executable = r"D:\Synthetic\server.exe"
        evidence = LaunchEvidence(321, canonical_process_path(executable), 100, "token", "handle")
        running = InventorySnapshot((ProcessObservation(321, executable, 100),))
        stopped = InventorySnapshot(())
        runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        adapter = WindowsGracefulStop(
            SequencedInventory(running, running, stopped),
            runner=runner,
            clock=unittest.mock.Mock(side_effect=(0.0, 0.0, 0.5)),
            sleeper=unittest.mock.Mock(),
        )
        # Stop the process and inspect the issued taskkill command
        adapter.request_stop(evidence)
        command = runner.call_args.args[0]
        self.assertEqual(command, ["taskkill.exe", "/PID", "321"])
        self.assertNotIn("/F", command)
        self.assertNotIn("/T", command)

    def test_stop_rejects_changed_process_identity_before_taskkill(self) -> None:
        """A changed process identity is rejected before any taskkill call."""
        executable = r"D:\Synthetic\server.exe"
        evidence = LaunchEvidence(321, canonical_process_path(executable), 100, "token", "handle")
        changed = InventorySnapshot((ProcessObservation(321, executable, 200),))
        runner = unittest.mock.Mock()
        adapter = WindowsGracefulStop(SequencedInventory(changed), runner=runner)
        # A changed creation identity must fail the ownership proof
        with self.assertRaises(LifecycleFailure) as raised:
            adapter.request_stop(evidence)
        self.assertEqual(raised.exception.code, "PROCESS_OWNERSHIP_UNPROVEN")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
