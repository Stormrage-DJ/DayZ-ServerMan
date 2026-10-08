"""The anonymous app-information adapter: its only command, the narrow preflight and the launch."""
from __future__ import annotations

import inspect
import re
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock, patch

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.adapters.windows import steamcmd_app
from dayz_serverman.adapters.windows.steamcmd import SteamCmdPreflight, SteamCmdPreflightError
from dayz_serverman.adapters.windows.steamcmd_app import WindowsSteamCmdAppInfo, build_app_info_argv
from dayz_serverman.domain.models import ManagerSettings


class AppInfoAdapterTests(unittest.TestCase):
    """Detailed design 14.4: the command, the preflight and the supervised launch; no SteamCMD runs."""

    def setUp(self) -> None:
        """Create a SteamCMD folder without any Workshop root."""
        self.temporary = tempfile.TemporaryDirectory()
        self.steam = Path(self.temporary.name) / "Steam CMD"
        self.steam.mkdir()
        self.executable = self.steam / "steamcmd.exe"
        self.executable.write_bytes(b"fixture")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def settings(self, **values) -> ManagerSettings:
        """Return account-mode settings with only the SteamCMD paths set."""
        fields = {"revision": 2, "dayz_root": None, "dayz_executable": None,
                  "steamcmd_root": str(self.steam), "steamcmd_executable": str(self.executable),
                  "workshop_content_root": None, "custom_backup_root": None,
                  "last_validated_paths": MappingProxyType({}), "steam_account_name": "operator",
                  "steam_authentication_mode": "ACCOUNT", **values}
        return ManagerSettings(**fields)

    def test_the_only_command_is_anonymous(self) -> None:
        """Exact argument vector; the builder takes no account."""
        self.assertEqual(build_app_info_argv(self.executable), (
            str(self.executable), "+login", "anonymous", "+app_info_update", "1",
            "+app_info_print", "223350", "+quit"))
        self.assertEqual(list(inspect.signature(build_app_info_argv).parameters), ["executable"])

    def test_the_adapter_source_names_no_install_command(self) -> None:
        """A source scan: no update, validation, install folder or Workshop download."""
        source = inspect.getsource(steamcmd_app).casefold()
        literals = re.findall(r'"([^"\n]*)"', source)
        for forbidden in ("app_update", "force_install_dir", "workshop_download_item", "account_name",
                          "steam_account"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        # "validate" is a SteamCMD argument; the preflight's "revalidate" is not
        self.assertFalse([value for value in literals if "validate" in value.split()], literals)
        self.assertNotIn("+validate", source)

    def test_preflight_checks_only_the_steamcmd_paths(self) -> None:
        """No Workshop root is needed; unset, nested or drifted paths fail."""
        preflight = SteamCmdPreflight()
        paths = preflight.inspect_executable(self.settings())
        self.assertEqual((paths.root, paths.executable), (self.steam.resolve(), self.executable.resolve()))
        preflight.revalidate_executable(paths)
        for values in ({"steamcmd_root": None}, {"steamcmd_executable": None},
                       {"steamcmd_executable": str(self.steam / "nested" / "steamcmd.exe")},
                       {"steamcmd_root": str(self.steam) + "\\..\\Steam CMD"}):
            with self.subTest(values=values):
                with self.assertRaises(SteamCmdPreflightError):
                    preflight.inspect_executable(self.settings(**values))
        # A different size changes the identity even when the write lands in the same clock tick
        self.executable.write_bytes(b"changed after the preflight")
        with self.assertRaises(SteamCmdPreflightError):
            preflight.revalidate_executable(paths)

    def test_launch_is_supervised_like_a_workshop_update(self) -> None:
        """Merged pipe, null stdin, SteamCMD folder as working folder, scrubbed environment."""
        preflight = SteamCmdPreflight()
        paths = preflight.inspect_executable(self.settings())
        process = Mock(pid=31, returncode=0, stdout=iter(("line\n",)))
        process.poll.return_value = 0
        tree = Mock()
        tree.wait_absent.return_value = True
        with patch.dict(os.environ, {"GITHUB_TOKEN": "never-forward", "SYSTEMROOT": "C:\\Windows"}), \
                patch.object(steamcmd_app.subprocess, "Popen", return_value=process) as popen, \
                patch("dayz_serverman.adapters.windows.steamcmd.OwnedProcessTree", return_value=tree):
            result = WindowsSteamCmdAppInfo(preflight).run_app_info(paths, lambda: False)
        arguments, options = popen.call_args
        self.assertEqual(arguments[0], list(build_app_info_argv(paths.executable)))
        self.assertEqual(options["cwd"], str(paths.root))
        self.assertIs(options["stdin"], steamcmd_app.subprocess.DEVNULL)
        self.assertIs(options["stderr"], steamcmd_app.subprocess.STDOUT)
        self.assertFalse(options["shell"])
        self.assertNotIn("GITHUB_TOKEN", options["env"])
        self.assertEqual((result.exit_code, result.lines, result.termination_confirmed), (0, ("line\n",), True))

    def test_drift_before_the_launch_starts_nothing(self) -> None:
        """The path identities are compared directly before the process starts."""
        preflight = SteamCmdPreflight()
        paths = preflight.inspect_executable(self.settings())
        # A different size changes the identity even when the write lands in the same clock tick
        self.executable.write_bytes(b"changed after the preflight")
        with patch.object(steamcmd_app.subprocess, "Popen") as popen:
            with self.assertRaises(SteamCmdPreflightError):
                WindowsSteamCmdAppInfo(preflight).run_app_info(paths, lambda: False)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
