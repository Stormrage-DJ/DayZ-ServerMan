"""SteamCMD adapter tests for preflight, vectors, and process ownership."""
from __future__ import annotations

import tempfile
import unittest
import sys
import os
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.adapters.windows.steamcmd import (
    SteamCmdPreflight,
    SteamCmdPreflightError,
    WindowsSteamCmdAdapter,
    build_update_argv,
)
from dayz_serverman.adapters.windows.process_tree import ChildEvidence, ProcessIdentity
from dayz_serverman.application.steamcmd_results import classify_output, terminal_outcomes
from dayz_serverman.domain.models import ManagerSettings
from dayz_serverman.domain.workshop import (
    AuthenticationMode,
    ItemOutcome,
    RequiredWorkshopItem,
)


class SteamCmdContractTests(unittest.TestCase):
    """SteamCMD preflight, argument, taxonomy, and ownership contracts."""
    def setUp(self) -> None:
        """Create a fixture SteamCMD root, executable, and workshop tree."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.steam = self.root / "Steam CMD ü"
        self.steam.mkdir()
        self.executable = self.steam / "steamcmd.exe"
        self.executable.write_bytes(b"fixture")
        self.workshop = self.steam / "steamapps" / "workshop" / "content" / "221100"
        self.workshop.mkdir(parents=True)
        self.manifest = self.workshop.parent.parent / "appworkshop_221100.acf"
        self.manifest.write_bytes(b"fixture")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def settings(self) -> ManagerSettings:
        """Return manager settings pinned to the fixture paths."""
        return ManagerSettings(
            revision=2, dayz_root=None, dayz_executable=None,
            steamcmd_root=str(self.steam), steamcmd_executable=str(self.executable),
            workshop_content_root=str(self.workshop), custom_backup_root=None,
            last_validated_paths=MappingProxyType({}),
            steam_account_name="operator", steam_authentication_mode="ACCOUNT",
        )

    def test_preflight_uses_only_configured_paths(self) -> None:
        """Preflight resolves only the configured paths, never the working directory."""
        prior = Path.cwd()
        unrelated = self.root / "unrelated cwd"
        unrelated.mkdir()
        # Run preflight from an unrelated working directory
        os.chdir(unrelated)
        try:
            paths = SteamCmdPreflight().inspect(self.settings())
        finally:
            os.chdir(prior)
        self.assertEqual(paths.executable, self.executable.resolve())
        # Removing the executable must fail the next preflight
        self.executable.unlink()
        with self.assertRaises(SteamCmdPreflightError):
            SteamCmdPreflight().inspect(self.settings())

    def test_one_argument_vector_preserves_order_and_never_has_secret_fields(self) -> None:
        """The argument vector preserves item order and carries no secret fields."""
        items = (
            RequiredWorkshopItem("111", 0, "client"),
            RequiredWorkshopItem("222", 1, "server"),
        )
        argv = build_update_argv(
            self.executable, AuthenticationMode.ACCOUNT, "operator", items,
        )
        self.assertEqual(argv, (
            str(self.executable), "+login", "operator",
            "+workshop_download_item", "221100", "111",
            "+workshop_download_item", "221100", "222", "+quit",
        ))
        joined = " ".join(argv).casefold()
        # No secret material may appear anywhere in the vector
        for forbidden in ("password", "guard", "token", "cookie"):
            self.assertNotIn(forbidden, joined)
        anonymous = build_update_argv(
            self.executable, AuthenticationMode.ANONYMOUS, None, items[:1],
        )
        # Anonymous authentication uses the anonymous login name
        self.assertEqual(anonymous[2], "anonymous")

    def test_preflight_rejects_noncanonical_roles_and_detects_identity_drift(self) -> None:
        """Preflight rejects noncanonical roles and detects identity drift."""
        preflight = SteamCmdPreflight()
        paths = preflight.inspect(self.settings())
        # Drift in the executable invalidates a previous inspection
        self.executable.write_bytes(b"changed")
        with self.assertRaises(SteamCmdPreflightError):
            preflight.revalidate(paths)
        values = self.settings().__dict__.copy()
        values["steamcmd_executable"] = str(self.steam / "nested" / "steamcmd.exe")
        with self.assertRaises(SteamCmdPreflightError):
            preflight.inspect(ManagerSettings(**values))

    def test_preflight_rejects_unregistered_workshop_library(self) -> None:
        """Preflight rejects a workshop library that is not registered."""
        external = self.root / "External Library" / "steamapps/workshop/content/221100"
        external.mkdir(parents=True)
        (external.parent.parent / "appworkshop_221100.acf").write_bytes(b"fixture")
        values = self.settings().__dict__.copy()
        values["workshop_content_root"] = str(external)
        with self.assertRaisesRegex(SteamCmdPreflightError, "not registered"):
            SteamCmdPreflight().inspect(ManagerSettings(**values))

        libraries = self.steam / "steamapps/libraryfolders.vdf"
        # Register the external library, which the preflight then accepts
        libraries.write_text(
            '"libraryfolders" { "0" { "path" "' +
            str(self.steam).replace("\\", "\\\\") + '" } "1" { "path" "' +
            str(self.root / "External Library").replace("\\", "\\\\") + '" } }',
            encoding="utf-8",
        )
        self.assertEqual(SteamCmdPreflight().inspect(ManagerSettings(**values)).workshop_root,
                         external.resolve())

    def test_preflight_requires_safe_manifest_and_final_drift_blocks_child(self) -> None:
        """A missing manifest fails and a final drift blocks the child launch."""
        preflight = SteamCmdPreflight()
        paths = preflight.inspect(self.settings())
        # A missing manifest fails the preflight
        self.manifest.unlink()
        with self.assertRaises(SteamCmdPreflightError):
            preflight.inspect(self.settings())
        self.manifest.write_bytes(b"fixture")
        paths = preflight.inspect(self.settings())
        adapter = WindowsSteamCmdAdapter(preflight)
        # A manifest changed after the hook blocks the child launch
        with patch("dayz_serverman.adapters.windows.steamcmd.subprocess.Popen") as popen:
            with self.assertRaises(SteamCmdPreflightError):
                adapter.authenticate_interactive(
                    paths, "operator", lambda: False, lambda _evidence: None,
                    lambda: self.manifest.write_bytes(b"changed-after-hook"),
                )
        popen.assert_not_called()

    def test_preflight_rejects_reparse_manifest_when_supported(self) -> None:
        """A reparse-point manifest is rejected when links are available."""
        outside = self.root / "outside.acf"
        outside.write_bytes(b"fixture")
        self.manifest.unlink()
        try:
            self.manifest.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        with self.assertRaises(SteamCmdPreflightError):
            SteamCmdPreflight().inspect(self.settings())

    def test_failure_taxonomy_and_terminal_mapping(self) -> None:
        """Recognized failure lines map to their terminal outcomes."""
        # Each recognized error line maps to its item outcome
        matrix = {
            "ERROR! Not logged on": ItemOutcome.AUTHENTICATION_FAILED,
            "ERROR! Access Denied": ItemOutcome.ENTITLEMENT_FAILED,
            "ERROR! No Connection": ItemOutcome.CONNECTION_FAILED,
            "ERROR! Download item failed": ItemOutcome.CONTENT_FAILED,
        }
        item = RequiredWorkshopItem("111", 0, "client")
        for line, expected in matrix.items():
            with self.subTest(line=line):
                evidence = classify_output((line,), (item,))
                self.assertEqual(terminal_outcomes((item,), evidence, cancelled=False)["111"], expected)
        success = classify_output(("Success. Downloaded item 111",), (item,))
        self.assertEqual(
            terminal_outcomes((item,), success, cancelled=False)["111"],
            ItemOutcome.VERIFIED_CURRENT,
        )
        unknown = classify_output(("unreviewed result text",), (item,))
        self.assertEqual(
            terminal_outcomes((item,), unknown, cancelled=False)["111"],
            ItemOutcome.UNKNOWN_FAILED,
        )
        # A success line mixed with an error line stays unknown
        contradictory = classify_output((
            "Success. Downloaded item 111", "ERROR! Access Denied",
        ), (item,))
        self.assertEqual(
            terminal_outcomes((item,), contradictory, cancelled=False)["111"],
            ItemOutcome.UNKNOWN_FAILED,
        )

    def test_ordered_classifier_rejects_reversed_duplicate_extra_and_missing(self) -> None:
        """Malformed progressions must not verify any item."""
        items = (RequiredWorkshopItem("111", 0, "client"),
                 RequiredWorkshopItem("222", 1, "server"))
        # Reversed, duplicate, and extra lines must all stay unknown
        matrices = (
            ("Success. Downloaded item 222",),
            ("Success. Downloaded item 111", "Success. Downloaded item 111"),
            ("Success. Downloaded item 111", "Success. Downloaded item 222",
             "Success. Downloaded item 333"),
        )
        for lines in matrices:
            outcomes = terminal_outcomes(items, classify_output(lines, items), cancelled=False)
            self.assertTrue(all(value == ItemOutcome.UNKNOWN_FAILED for value in outcomes.values()))
        missing = terminal_outcomes(
            items, classify_output(("Success. Downloaded item 111",), items), cancelled=False)
        self.assertEqual(missing["111"], ItemOutcome.VERIFIED_CURRENT)
        self.assertEqual(missing["222"], ItemOutcome.UNKNOWN_FAILED)

    def test_cancellation_uses_bounded_owned_process_escalation(self) -> None:
        """Cancellation escalates through close, terminate, and kill."""
        process = Mock()
        process.pid = 42
        process.stdout = iter(())
        process.poll.return_value = None
        process.returncode = None
        adapter = WindowsSteamCmdAdapter()
        paths = SteamCmdPreflight().inspect(self.settings())
        tree = Mock()
        tree.evidence.process_id = 42
        tree.wait_absent.side_effect = (False, False, False)
        # Cancellation escalates while waiting for the process tree to exit
        with patch("dayz_serverman.adapters.windows.steamcmd.subprocess.Popen", return_value=process), \
                patch("dayz_serverman.adapters.windows.steamcmd.OwnedProcessTree", return_value=tree):
            result = adapter.run_update(paths, (str(self.executable), "+quit"), lambda: True,
                                        lambda _evidence: None, lambda: None)
        tree.request_close.assert_called_once_with()
        tree.terminate_tree.assert_called_once_with()
        tree.kill_tree.assert_called_once_with()
        self.assertTrue(result.cancelled)
        self.assertFalse(result.termination_confirmed)

    def test_interactive_auth_uses_visible_console_safe_env_and_owned_identity(self) -> None:
        """Interactive authentication uses a scrubbed console-safe environment."""
        process = Mock(pid=73, stdout=None, returncode=0)
        process.poll.return_value = 0
        tree = Mock()
        tree.evidence.process_id = 73
        tree.evidence.creation_identity = "fake:73"
        adapter = WindowsSteamCmdAdapter()
        paths = SteamCmdPreflight().inspect(self.settings())
        launched = []
        # Launch authentication with a scrubbed environment
        with patch.dict(os.environ, {
                "SYSTEMROOT": r"C:\Windows", "COMSPEC": r"C:\Windows\System32\cmd.exe",
                "PROGRAMDATA": r"C:\ProgramData", "PATH": r"C:\Windows\System32",
                "PATHEXT": ".EXE", "GITHUB_TOKEN": "never-forward",
             }, clear=True), \
                patch("dayz_serverman.adapters.windows.steamcmd.subprocess.Popen", return_value=process) as popen, \
                patch("dayz_serverman.adapters.windows.steamcmd.OwnedProcessTree", return_value=tree):
            result = adapter.authenticate_interactive(
                paths, "operator", lambda: False, launched.append, lambda: None)
        arguments, options = popen.call_args
        self.assertEqual(arguments[0], [str(paths.executable), "+login", "operator", "+quit"])
        self.assertFalse(options["shell"])
        self.assertNotIn("stdin", options)
        self.assertNotIn("stdout", options)
        for required in ("SYSTEMROOT", "COMSPEC", "PROGRAMDATA", "PATH", "PATHEXT"):
            self.assertIn(required, options["env"])
        for forbidden in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY"):
            self.assertNotIn(forbidden, options["env"])
        self.assertEqual(launched[0].process_id, 73)
        self.assertTrue(result.termination_confirmed)

    def test_adapter_persists_expanded_job_membership_before_terminal_result(self) -> None:
        """Expanded job membership is persisted before the terminal result."""
        process = Mock(pid=73, stdout=None, returncode=0)
        process.poll.side_effect = (None, 0, 0)
        # Job membership expands before the terminal result is recorded
        root = ChildEvidence(73, "root", (ProcessIdentity(73, "root"),), "job")
        expanded = ChildEvidence(73, "root", (
            ProcessIdentity(73, "root"), ProcessIdentity(74, "child")), "job")
        tree = Mock()
        type(tree).evidence = PropertyMock(side_effect=(root, expanded, expanded))
        tree.wait_absent.return_value = True
        recorded = []
        with patch("dayz_serverman.adapters.windows.steamcmd.OwnedProcessTree", return_value=tree):
            result = WindowsSteamCmdAdapter._wait_owned(
                process, lambda: False, recorded.append, [])
        self.assertEqual(recorded, [root, expanded])
        self.assertEqual(result.child_evidence, expanded)
        self.assertTrue(result.termination_confirmed)


if __name__ == "__main__":
    unittest.main()
