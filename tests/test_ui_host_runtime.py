"""Desktop host runtime tests for inline EdgeChromium window behavior."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.host.api import HostApi  # noqa: E402
from dayz_serverman.host.assets import resolve_frontend_root  # noqa: E402
from dayz_serverman.host.runtime import launch_application  # noqa: E402


FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"


class FakeWebview:
    """pywebview stand-in that records the launch arguments it receives."""
    FOLDER_DIALOG = "folder"
    OPEN_DIALOG = "file"

    def __init__(self) -> None:
        """Initialize the captured settings, window options, and start options."""
        self.settings: dict[str, object] = {}
        self.window_arguments: dict[str, object] = {}
        self.start_arguments: dict[str, object] = {}
        self.window = FakeWindow()

    def create_window(self, _title: str, **arguments: object) -> object:
        """Capture the window options and return the fake window."""
        self.window_arguments = arguments
        return self.window

    def start(self, callback: object, **arguments: object) -> None:
        """Capture the start options including the GUI and server flags."""
        self.start_arguments = {"callback": callback, **arguments}


class FakeWindow:
    """Fake window that records dialog requests and returns the scripted selection."""
    def __init__(self) -> None:
        """Initialize the scripted selection and the dialog call log."""
        self.selection: str | None = None
        self.dialog_calls: list[tuple[object, dict[str, object]]] = []

    def create_file_dialog(self, dialog: object, **options: object) -> str | None:
        """Record one dialog request and return the scripted selection."""
        self.dialog_calls.append((dialog, options))
        return self.selection


class HostRuntimeTests(unittest.TestCase):
    """Host runtime contracts for the inline local-only window."""
    def setUp(self) -> None:
        """Build a composition rooted in a temporary manager directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_ui_")
        self.manager_root = Path(self.temporary.name) / "Review Manager"
        self.composition = build_composition(self.manager_root)

    def tearDown(self) -> None:
        """Shut the operations manager down and remove the temporary directory."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def test_runtime_forces_inline_edgechromium_without_http_server(self) -> None:
        """The runtime forces an inline EdgeChromium window without an HTTP server."""
        # Launch against the studio frontend with a fake webview module
        fake = FakeWebview()
        self.assertEqual(
            self.composition.host_bridge.allowed_methods,
            self.composition.bridge.allowed_methods,
        )
        with patch("dayz_serverman.host.runtime.find_webview2_version", return_value="1.0"):
            result = launch_application(self.composition, frontend_root=FRONTEND, webview_module=fake)
        # Confirm the window contract and the forced local-only settings
        self.assertEqual(result, 0)
        self.assertEqual(fake.window_arguments["min_size"], (800, 560))
        self.assertIn("<style>", fake.window_arguments["html"])
        self.assertIsInstance(fake.window_arguments["js_api"], HostApi)
        self.assertEqual(fake.start_arguments["gui"], "edgechromium")
        self.assertFalse(fake.start_arguments["http_server"])
        self.assertEqual(fake.start_arguments["storage_path"], str(self.composition.paths.webview2))
        self.assertFalse(fake.settings["ALLOW_DOWNLOADS"])
        self.assertFalse(fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])

    def test_packaged_frontend_is_preferred_without_cwd_lookup(self) -> None:
        """A packaged frontend beside the manager root wins over a lookup."""
        # Create the packaged frontend directory the resolver should prefer
        packaged = self.manager_root / "frontend"
        packaged.mkdir()
        self.assertEqual(resolve_frontend_root(self.manager_root), packaged.resolve())

    def test_backup_picker_uses_zip_filter_and_configured_initial_folder(self) -> None:
        """The native archive picker permits one ZIP and handles dismissal safely."""
        fake = FakeWebview()
        with patch("dayz_serverman.host.runtime.find_webview2_version", return_value="1.0"):
            launch_application(self.composition, frontend_root=FRONTEND, webview_module=fake)
        api = fake.window_arguments["js_api"]
        result = api.select_backup_archive()
        self.assertTrue(result["value"]["cancelled"])
        kind, options = fake.window.dialog_calls[0]
        self.assertEqual(kind, fake.OPEN_DIALOG)
        self.assertEqual(options["file_types"], ("Backup archives (*.zip)",))
        self.assertFalse(options["allow_multiple"])
        self.assertEqual(options["directory"], str(self.composition.settings.backup_root(self.composition.settings.load())))

    def test_runtime_selector_uses_fixed_file_and_folder_dialog_kinds(self) -> None:
        """The path selector uses the fixed folder dialog and rejects file kinds."""
        # Launch the runtime with a fake webview to reach the selector
        fake = FakeWebview()
        # Prepare a DayZ folder that holds the expected executable
        folder = self.manager_root / "DayZ Folder ő"
        folder.mkdir(parents=True)
        (folder / "DayZServer_x64.exe").write_bytes(b"fixture")
        with patch("dayz_serverman.host.runtime.find_webview2_version", return_value="1.0"):
            launch_application(self.composition, frontend_root=FRONTEND, webview_module=fake)
        api = fake.window_arguments["js_api"]
        # Accept the folder once and confirm the directory dialog kind
        fake.window.selection = str(folder)
        self.assertTrue(api.select_settings_path("dayz_root")["success"])
        self.assertEqual(fake.window.dialog_calls[0][0], fake.FOLDER_DIALOG)
        # Confirm the executable selector refuses the folder-only dialog
        self.assertFalse(api.select_settings_path("dayz_executable")["success"])
        self.assertEqual(len(fake.window.dialog_calls), 1)


if __name__ == "__main__":
    unittest.main()
