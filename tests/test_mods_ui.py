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
                           for name in ("mods.js", "mods_operations.js", "mods_display.js",
                                        "mods_update_actions.js", "mods_progress.js"))
        publication = (FRONTEND / "mod-publication.js").read_text(encoding="utf-8")
        bar = "\n".join((FRONTEND / name).read_text(encoding="utf-8")
                        for name in ("operation_bar.js", "operation_bar_view.js"))
        # Named registrations and safe rendering markers are present
        self.assertIn("window.ServerManMods", html)
        self.assertIn("window.ServerManModPublication", html)
        self.assertIn("Configured mods", html)
        self.assertIn("mods-access-row", html)
        self.assertIn("mods-table", html)
        self.assertIn('modsNode("button", "button button-primary", "Update all")', source)
        self.assertIn('all.id = "update-workshop"', source)
        self.assertIn('start.id = "update-start"', source)
        self.assertIn('label: "Update & start"', source)
        self.assertIn('restart ? "Update & restart" : "Update & start"', source)
        self.assertIn("modsState.settings.steam_account_name,\n    start,\n  );", source)
        self.assertNotIn("Download / update", html)
        self.assertIn("list_mod_inventory", source)
        self.assertNotIn('id = "mods-profile"', source)
        self.assertIn("update_workshop_items", source)
        self.assertIn("PUBLICATION_REQUIRED", source)
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn('type = "password"', source)
        # Cancel, phase and percent live in the operation bar; the page has no Cancel button of its own
        self.assertNotIn("request_operation_cancellation", source)
        self.assertNotIn("cancel-workshop-operation", source)
        self.assertNotIn("progress_percent", source)
        self.assertIn("request_operation_cancellation", bar)
        self.assertIn("progress_percent", bar)
        self.assertIn("window.ServerManOperationBar.adopt(result.value.operation_id)", source)
        self.assertIn("AUTHENTICATE_STEAMCMD", source)
        self.assertIn("Credentials remain owned by SteamCMD", source)
        self.assertIn("renderModsItems", source)
        self.assertIn("SteamCMD exited without a verifiable update result", source)
        self.assertIn("steamcmd_exit_code", source)
        self.assertIn("steamcmd_summary", source)
        self.assertIn('["VERIFIED", "EMPTY"]', source)
        self.assertIn("entry?.item?.workshop_id", source)
        # Outcome and item error code are worded by the catalogue and never printed raw
        self.assertIn("window.ServerManDiagnosticLabels.modOutcome(entry)", source)
        self.assertNotIn("entry?.error_code", source)
        self.assertIn("entry?.error_code",
                      (FRONTEND / "diagnostic_labels.js").read_text(encoding="utf-8"))
        self.assertIn("preview_mod_publication", publication)
        self.assertIn("publish_mods_and_keys", publication)
        self.assertIn("After the mods are verified", publication)
        self.assertIn("Apply mods and keys", publication)
        self.assertIn("No server start was requested", publication)
        # The start outcome of an apply is worded by the catalogue, as in the operation bar
        self.assertIn("window.ServerManOperationBar.pageResult(operation)", publication)
        self.assertIn('result.start_state === "CANCELLED"',
                      (FRONTEND / "operation_messages.js").read_text(encoding="utf-8"))
        self.assertIn('setAttribute("aria-modal", "true")', publication)
        self.assertIn('event.key === "Escape"', publication)
        self.assertIn('event.key !== "Tab"', publication)
        self.assertIn("textContent", source + publication)
        self.assertNotIn("innerHTML", publication)
        self.assertIn("apply_mods_and_restart", publication)
        for method in ("start_server", "stop_server", "restart_server"):
            self.assertNotIn(method, publication + source)

    def test_update_states_header_and_check_now_are_composed(self) -> None:
        """Verify the update-state labels, reasons, check header, and asset order are present."""
        # Compose the shell and load the update-state sources
        html = compose_shell_html(FRONTEND)
        status = (FRONTEND / "update_status.js").read_text(encoding="utf-8")
        display = (FRONTEND / "mods_display.js").read_text(encoding="utf-8")
        mods = (FRONTEND / "mods.js").read_text(encoding="utf-8")
        styles = (FRONTEND / "mods.css").read_text(encoding="utf-8")
        # The shared state is published before the page that reads it
        self.assertIn("window.ServerManUpdateStatus = Object.freeze(", status)
        self.assertLess(html.index("window.ServerManUpdateStatus = "),
                        html.index("window.ServerManMods = "))
        # Named bridge calls: status per profile, non-forced and forced requests
        self.assertIn("get_update_status(profileId)", status)
        self.assertIn('request_update_check("mods", force)', status)
        self.assertIn("requestUpdateCheck(false)", status)
        self.assertIn("recheckUpdateStatus(true)", status)
        self.assertIn("automatic_update_checks", status)
        self.assertIn("UPDATE_STATUS_IDLE_INTERVAL = 5000", status)
        # Each label row and reason of the presentation table
        for label in (
            '"Checking…"', '"Could not check"', '"Update available"', '"Installed"', '"Steam"',
            '"Downloaded - not applied"', '"Current"', '"Not downloaded"', '"Local"',
            '"Unavailable"', "Last checked", "No successful check yet",
            "Folder exists but was never verified. Run Verify files or Update.",
        ):
            self.assertIn(label, display)
        for reason in (
            "Steam does not list this item", "automatic checks are off",
            "the last check is too old", "not checked yet", "NETWORK_UNREACHABLE", "TIMEOUT",
            "TLS_FAILURE", "HTTP_STATUS", "RESPONSE_TOO_LARGE", "RESPONSE_MALFORMED",
        ):
            self.assertIn(reason, status)
        # The header: summary, last check, busy mark, and the on-demand action
        for fragment in (
            '"Check now"', "Last checked", '"Not checked yet"', 'setAttribute("role", "status")',
            "update_count", "pending_apply_count", "downloaded - not applied",
            "mods are current", "Could not check: ", "dateStyle", "node.title = title",
        ):
            self.assertIn(fragment, status)
        # A refresh redraws the inventory panel only and never the whole workspace
        self.assertIn('"serverman:update-status"', status)
        self.assertIn('addEventListener("serverman:update-status"', mods)
        self.assertIn("body.replaceChildren(renderModRows())", display)
        self.assertEqual(mods.count('getElementById("content-region").replaceChildren'), 1)
        self.assertIn('shellState.section === "mods") await window.ServerManUpdateStatus.poll()',
                      (FRONTEND / "app.js").read_text(encoding="utf-8"))
        # Safe rendering, motion preference, and the size gate
        for source in (status, display):
            self.assertNotIn("innerHTML", source)
            self.assertNotIn("localStorage", source)
        self.assertIn("@media (prefers-reduced-motion: reduce) "
                      "{ .mods-update-busy::before { animation: none; } }", styles)
        for name in ("mods.js", "mods_operations.js", "mods_display.js", "update_status.js", "mods.css"):
            lines = (FRONTEND / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)

    def test_verify_files_control_is_composed(self) -> None:
        """Verify the "Verify files" button, its bridge call, wording, and asset order."""
        # Compose the shell and load the sources of the verification flow
        html = compose_shell_html(FRONTEND)
        verify = (FRONTEND / "mods_verify.js").read_text(encoding="utf-8")
        mods = "\n".join((FRONTEND / name).read_text(encoding="utf-8")
                         for name in ("mods.js", "mods_operations.js"))
        display = (FRONTEND / "mods_display.js").read_text(encoding="utf-8")
        status = (FRONTEND / "update_status.js").read_text(encoding="utf-8")
        # The flow is published after the page helpers it uses and before the shell
        self.assertIn("window.ServerManModsVerify = Object.freeze(", verify)
        self.assertLess(html.index("function renderModsItems("),
                        html.index("window.ServerManModsVerify = "))
        self.assertLess(html.index("window.ServerManModsVerify = "),
                        html.index("window.ServerManModPublication = "))
        # The button: secondary style, beside "Check now", with its disabled rules
        self.assertIn('modsNode("button", "button", "Verify files")', verify)
        self.assertIn('"mods-header-actions"', status)
        self.assertIn('header.querySelector(".mods-header-actions").append('
                      "window.ServerManBusy.mark(button), description)", verify)
        self.assertIn("modsState.busy || !selectedProfile()", verify)
        self.assertIn('row.source_kind === "workshop" && row.workshop_id', verify)
        self.assertIn('setAttribute("aria-describedby", description.id)', verify)
        self.assertIn("Reads all mod files", verify)
        self.assertIn("This can take minutes", verify)
        # The named bridge call and the shared pending-operation flow
        self.assertIn("window.pywebview.api.verify_mod_files(", verify)
        self.assertIn("profile.profile_id, profile.revision, modsState.settings.revision,",
                      verify)
        self.assertIn('window.ServerManOperationLabels.phase("VERIFY_WORKSHOP_FILES", verifyPhases[0]).text',
                      verify)
        self.assertIn("window.ServerManModsVerify.sync();", mods)
        self.assertIn("window.ServerManModsVerify.progress(operation)", mods)
        self.assertIn('operation.kind === "VERIFY_WORKSHOP_FILES") '
                      "return window.ServerManModsVerify.finished(operation)", mods)
        self.assertIn("window.ServerManModsVerify.rowLines(row)", display)
        self.assertIn("window.ServerManModsVerify.signature()", display)
        # Operator wording of progress, problems, summary, and cancellation
        catalogue = (FRONTEND / "operation_labels.js").read_text(encoding="utf-8")
        for text in ('"Verifying downloaded files"', '"Verifying server folder copies"'):
            self.assertIn(text, catalogue)
            self.assertNotIn(text, verify)
        for text in (
            '"Verifying…"',
            '"Download missing"', '"Download could not be read"',
            '"Download changed since it was recorded"', '"Not applied to the server folder"',
            '"Server copy differs from the download"', '"Server copy could not be read"',
            "mods verified; server copies match", "problems found",
        ):
            self.assertIn(text, verify)
        # The cancellation sentence comes from the wording catalogue, as in the operation bar
        self.assertIn('"Verification cancelled. Finished mods stay recorded."',
                      (FRONTEND / "operation_labels.js").read_text(encoding="utf-8"))
        self.assertIn("window.ServerManOperationBar.pageResult(operation)", verify)
        # Safe rendering, existing tokens only, and the size gate
        self.assertNotIn("innerHTML", verify)
        self.assertNotIn("localStorage", verify)
        self.assertTrue(verify.startswith("// "))
        self.assertIn('"use strict";', verify)
        self.assertLessEqual(len(verify.splitlines()), 300)
        tokens = (FRONTEND / "tokens.css").read_text(encoding="utf-8")
        for token in ("--primitive-space-2:", "--color-status-warning:"):
            self.assertIn(token, tokens)

    def test_content_security_policy_is_unchanged(self) -> None:
        """Keep the shell content security policy byte-identical."""
        document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        policy = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; "
            "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
            "form-action 'none'; base-uri 'none'"
        )
        self.assertIn(
            f'<meta http-equiv="Content-Security-Policy" content="{policy}">', document)

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
            "get_update_status", "request_update_check",
        }.issubset(public))
        for forbidden in ("dispatch", "run_command", "read_token", "submit_password"):
            self.assertNotIn(forbidden, public)

    def test_app_routes_mod_operations_by_operation_id(self) -> None:
        """Route mod operations under the mods section by operation id."""
        # Load the app router and the mods sources
        source = (FRONTEND / "app.js").read_text(encoding="utf-8")
        mods = "\n".join((FRONTEND / name).read_text(encoding="utf-8")
                         for name in ("mods.js", "mods_operations.js", "mods_display.js"))
        # Section routing and operation-id correlation are wired
        self.assertIn('shellState.section === "mods"', source)
        # The section registry hands operation events to the visible Mods section
        registry = (FRONTEND / "sections.js").read_text(encoding="utf-8")
        self.assertIn("ServerManMods.operationFinished", registry)
        self.assertIn("ServerManSections.operationFinished(shellState.section", source)
        self.assertIn("operation.operation_id !== modsState.pending.id", mods)
        self.assertIn("ServerManWorkspace.isActive", mods)


if __name__ == "__main__":
    unittest.main()
