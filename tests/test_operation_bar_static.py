"""Static contracts of the operation bar: document structure, tokens, styles, asset order, and removed takeover."""
from __future__ import annotations

import re
import unittest

try:
    from tests.ui_harness_support import FRONTEND, ROOT
except ModuleNotFoundError:
    from ui_harness_support import FRONTEND, ROOT

from dayz_serverman.host.assets import compose_shell_html


# Modules of the bar and the busy rule, in the order in which the composed script holds them
BAR_MODULES = (
    "operation_labels.js", "operation_messages.js", "operation_announcer.js",
    "operation_bar_view.js", "operation_bar.js", "busy_controls.js",
)
# Every helper that makes the page inert behind a dialog
MODAL_HELPERS = (
    "transition_guard.js", "overview_lifecycle_dialog.js", "tweaks_dialog.js", "mod-publication.js",
    "profile_delete.js", "backups.js", "restore.js", "migration.js",
)
POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; "
    "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
    "form-action 'none'; base-uri 'none'"
)


class OperationBarStaticTests(unittest.TestCase):
    """Specification sections 1, 4 and 6 as far as the source text can show them."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the shell document, the styles, and every frontend script once."""
        cls.document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        cls.scripts = {path.name: path.read_text(encoding="utf-8") for path in FRONTEND.glob("*.js")}
        cls.styles = {path.name: path.read_text(encoding="utf-8") for path in FRONTEND.glob("*.css")}
        cls.composed = compose_shell_html(FRONTEND)

    def test_bar_element_sits_above_the_page_inside_the_workspace(self) -> None:
        """The bar is the first child of the workspace column, outside the content region."""
        workspace = self.document.split('<div class="workspace">', 1)[1]
        bar = '<section class="operation-bar" id="operation-bar" aria-label="Current operation" hidden></section>'
        self.assertIn(bar, workspace)
        self.assertLess(workspace.index(bar), workspace.index('<main id="main-content"'))
        self.assertLess(self.document.index('href="#main-content"'), self.document.index(bar))
        self.assertEqual(self.document.count('id="operation-bar"'), 1)
        self.assertNotIn("aria-live", bar)

    def test_announcers_and_reason_are_direct_children_of_body(self) -> None:
        """Two announcer elements and the reason element follow the shell, outside every panel."""
        tail = self.document.split('<div class="drawer-scrim"', 1)[1]
        self.assertIn('<div class="sr-only" id="operation-announcer" role="status" data-announcer></div>', tail)
        self.assertIn('<div class="sr-only" id="operation-alert" role="alert" data-announcer></div>', tail)
        self.assertIn('<span id="busy-reason" hidden></span>', tail)
        self.assertIn('<span class="status-dot" id="menu-status-dot" aria-hidden="true" hidden></span>',
                      self.document)

    def test_content_security_policy_is_unchanged(self) -> None:
        """The policy string stays byte-identical."""
        self.assertIn(f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">', self.document)

    def test_component_tokens_and_bar_styles(self) -> None:
        """The three named tokens exist and the bar styles use tokens for colour and size."""
        tokens = self.styles["tokens.css"]
        for token in ("--operation-bar-min-height: 56px;", "--operation-bar-control-height: 32px;",
                      "--operation-bar-z: 10;"):
            self.assertIn(token, tokens)
        bar = self.styles["operation_bar.css"]
        for rule in (
            "position: sticky; top: 0; z-index: var(--operation-bar-z);",
            "min-height: var(--operation-bar-min-height);",
            "min-height: var(--operation-bar-control-height);",
            ".operation-progress.is-indeterminate .progress-bar { width: 40%; animation: operation-slide 1.5s linear infinite; }",
            "@media (prefers-reduced-motion: reduce)", "animation: none;", "repeating-linear-gradient(45deg",
            "@media (max-width: 859px)", ".operation-progress { max-width: none; }",
            "max-width: 320px", "font-variant-numeric: tabular-nums", "min-width: 4ch",
        ):
            self.assertIn(rule, bar)
        # No colour literal: every colour comes from a token
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", bar))
        controls = self.styles["controls.css"]
        for rule in (
            '.button[disabled], .button[aria-disabled="true"] {',
            ".operation-bar { border: 1px solid CanvasText; }",
            ".progress-track, .progress-bar { forced-color-adjust: none; }",
            ".progress-track .progress-bar { background: Highlight; }",
            '.button[aria-disabled="true"] { border-color: GrayText; color: GrayText; }',
        ):
            self.assertIn(rule, controls)
        self.assertNotIn(".operation-error", self.styles["styles.css"])

    def test_bar_modules_are_composed_after_the_shell_helpers_in_order(self) -> None:
        """Labels, messages, announcer, view, model, and busy helper load in dependency order."""
        marks = [
            "window.ServerManUi = Object.freeze(", "window.ServerManOperationLabels = Object.freeze(",
            "window.ServerManOperationMessages = Object.freeze(", "function announceOperationResult(",
            "function drawOperationBar(", "window.ServerManOperationBar = Object.freeze(",
            "window.ServerManBusy = Object.freeze(", "window.ServerManWorkspace = Object.freeze(",
        ]
        positions = [self.composed.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(".operation-bar {", self.composed)
        for name in BAR_MODULES:
            source = self.scripts[name]
            self.assertTrue(source.startswith("// "), name)
            self.assertIn('"use strict";', source, name)
            self.assertNotIn("innerHTML", source, name)
            self.assertNotIn("localStorage", source, name)

    def test_takeover_and_page_cancel_are_gone(self) -> None:
        """No script draws the operation panel, and only the bar asks for a cancellation."""
        for name, source in self.scripts.items():
            self.assertNotIn("renderOperation", source, name)
            self.assertNotIn("operation-panel", source, name)
            self.assertNotIn("data-cancel-operation", source, name)
            self.assertNotIn("cancel-workshop-operation", source, name)
            if name != "operation_bar.js":
                self.assertNotIn("request_operation_cancellation", source, name)
        self.assertIn("request_operation_cancellation(operationId)", self.scripts["operation_bar.js"])
        runtime = (ROOT / "runnable" / "src" / "python" / "dayz_serverman" / "host" / "runtime.py").read_text(
            encoding="utf-8")
        self.assertIn("document.querySelector('.overview-controls, .operation-bar:not([hidden])')", runtime)
        self.assertNotIn("operation-panel", runtime)

    def test_every_modal_helper_leaves_the_announcers_active(self) -> None:
        """Each helper that sets inert on the page skips the announcer elements."""
        for name in MODAL_HELPERS:
            self.assertEqual(self.scripts[name].count('hasAttribute("data-announcer")'), 1, name)
        others = [name for name, source in self.scripts.items()
                  if "inert = true" in source and name not in MODAL_HELPERS]
        self.assertEqual(others, [])

    def test_bar_publishes_one_busy_event_and_pages_only_mark(self) -> None:
        """The shell dispatches the operation-change event; pages use the busy helper, not the event."""
        self.assertEqual(self.scripts["operation_bar.js"].count(
            'new CustomEvent("serverman:operation-change"'), 1)
        self.assertIn('document.addEventListener("serverman:operation-change"', self.scripts["busy_controls.js"])
        self.assertIn('document.addEventListener("click", guardLockedActivation, true)',
                      self.scripts["busy_controls.js"])
        self.assertIn('document.addEventListener("submit", guardLockedSubmit, true)',
                      self.scripts["busy_controls.js"])
        marked = sorted(name for name, source in self.scripts.items() if "ServerManBusy" in source
                        and name != "busy_controls.js")
        self.assertEqual(marked, sorted((
            "overview_server.js", "overview_lifecycle_dialog.js", "profiles.js", "profile_create.js",
            "profile_delete.js", "configuration.js", "tweaks.js", "tweaks_dialog.js", "tweaks_render.js",
            "mods_signin.js", "mods_verify.js", "mods_update_actions.js", "mod-publication.js", "backups.js",
            "restore.js",
            "profile_restore.js", "settings.js", "migration.js",
        )))

    def test_global_names_are_unique_across_the_composed_script(self) -> None:
        """All scripts share one scope, so a second declaration of a name would replace the first."""
        seen: dict[str, str] = {}
        for name, source in sorted(self.scripts.items()):
            for declared in re.findall(r"^(?:async )?function ([A-Za-z0-9_]+)|^(?:const|let) ([A-Za-z0-9_]+)",
                                       source, re.MULTILINE):
                identifier = declared[0] or declared[1]
                self.assertNotIn(identifier, seen, f"{identifier}: {seen.get(identifier)} and {name}")
                seen[identifier] = name

    def test_frontend_files_stay_within_the_size_limit(self) -> None:
        """Every frontend source file has at most 300 lines."""
        for path in sorted(FRONTEND.iterdir()):
            if path.suffix in {".js", ".css", ".html"}:
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 300, path.name)


if __name__ == "__main__":
    unittest.main()
