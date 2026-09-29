"""Static contract tests of the mods workspace shell and host API surface."""
from __future__ import annotations

import inspect
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.host.api import HostApi
from dayz_serverman.host.assets import compose_shell_html


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "runnable" / "src" / "frontend"


class ModsUiTests(unittest.TestCase):
    """Contract: the mods workspace exposes only named, safe composition surfaces."""
    def test_shell_composes_named_mods_workspace_without_remote_surface(self) -> None:
        """Verify the composed shell keeps named mods workspaces and safe rendering."""
        # Compose the shell and load the mods scripts
        html = compose_shell_html(FRONTEND)
        source = "\n".join((FRONTEND / name).read_text(encoding="utf-8")
                           for name in ("mods.js", "mods_display.js"))
        publication = (FRONTEND / "mod-publication.js").read_text(encoding="utf-8")
        # Named registrations and safe rendering markers are present
        self.assertIn("window.ServerManMods", html)
        self.assertIn("window.ServerManModPublication", html)
        self.assertIn("Configured mods", html)
        self.assertIn("mods-access-row", html)
        self.assertIn("mods-table", html)
        self.assertIn("Download / update mods", html)
        # Hidden or unsafe surfaces must stay absent
        self.assertNotIn("Download / update & start server", html)
        self.assertNotIn('"update-start"', source)
        self.assertIn('id === "update-workshop" ? "button button-primary"', source)
        self.assertIn("list_mod_inventory", source)
        self.assertNotIn('id = "mods-profile"', source)
        self.assertIn("update_workshop_items", source)
        self.assertIn("PUBLICATION_REQUIRED", source)
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn('type = "password"', source)
        self.assertIn("request_operation_cancellation", source)
        self.assertIn("operation.progress_phase", source)
        self.assertIn("operation.progress_percent", source)
        self.assertIn("AUTHENTICATE_STEAMCMD", source)
        self.assertIn("Credentials remain owned by SteamCMD", source)
        self.assertIn("renderModsItems", source)
        self.assertIn("SteamCMD exited without a verifiable update result", source)
        self.assertIn("steamcmd_exit_code", source)
        self.assertIn("steamcmd_summary", source)
        self.assertIn('["VERIFIED", "EMPTY"]', source)
        self.assertIn("entry?.item?.workshop_id", source)
        self.assertIn("entry?.error_code", source)
        self.assertIn("preview_mod_publication", publication)
        self.assertIn("publish_mods_and_keys", publication)
        self.assertIn("After the mods are verified", publication)
        self.assertIn("Apply mods and keys", publication)
        self.assertIn("No server start was requested", publication)
        self.assertIn('start_state === "CANCELLED"', publication)
        self.assertIn('setAttribute("aria-modal", "true")', publication)
        self.assertIn('event.key === "Escape"', publication)
        self.assertIn('event.key !== "Tab"', publication)
        self.assertIn("textContent", source + publication)
        self.assertNotIn("innerHTML", publication)
        self.assertNotIn("start_server", publication)
        self.assertNotIn("start_server", source)

    def test_host_exposes_only_named_phase_61_methods(self) -> None:
        """Expose only the named phase-61 host methods on the webview API."""
        # Collect the public host methods
        public = {
            name for name, member in inspect.getmembers(HostApi, inspect.isfunction)
            if not name.startswith("_")
        }
        # The named phase-61 subset exists and generic dispatchers do not
        self.assertTrue({
            "save_steam_settings", "authenticate_steamcmd", "update_workshop_items",
            "list_mod_inventory", "preview_mod_publication", "publish_mods_and_keys",
        }.issubset(public))
        for forbidden in ("dispatch", "run_command", "read_token", "submit_password"):
            self.assertNotIn(forbidden, public)

    def test_app_routes_mod_operations_by_operation_id(self) -> None:
        """Route mod operations under the mods section by operation id."""
        # Load the app router and the mods sources
        source = (FRONTEND / "app.js").read_text(encoding="utf-8")
        mods = "\n".join((FRONTEND / name).read_text(encoding="utf-8")
                         for name in ("mods.js", "mods_display.js"))
        # Section routing and operation-id correlation are wired
        self.assertIn('shellState.section === "mods"', source)
        self.assertIn("ServerManMods.operationFinished", source)
        self.assertIn("operation.operation_id !== modsState.pending.id", mods)
        self.assertIn("ServerManWorkspace.isActive", mods)


if __name__ == "__main__":
    unittest.main()
