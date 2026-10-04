"""Static contracts of the sidebar, the registry-built navigation and the update badge."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import FRONTEND
except ModuleNotFoundError:
    from ui_harness_support import FRONTEND

from dayz_serverman.host.assets import compose_shell_html


class ShellSidebarStaticTests(unittest.TestCase):
    """Composition and style contracts of the sidebar, the navigation and the badge."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the shell stylesheet, the document and the composed page once."""
        cls.styles = (FRONTEND / "shell.css").read_text(encoding="utf-8")
        cls.document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        cls.composed = compose_shell_html(FRONTEND)

    def test_new_modules_are_composed_between_the_registry_and_the_shell_loop(self) -> None:
        """The state, heading and navigation modules follow the registry and precede the loop."""
        registry = self.composed.index("window.ServerManSections = Object.freeze(")
        loop = self.composed.index("const shellState = {")
        for marker in ("window.ServerManServerState = Object.freeze(",
                       "window.ServerManPageContext = Object.freeze(",
                       "window.ServerManNavigation = Object.freeze("):
            self.assertLess(registry, self.composed.index(marker), marker)
            self.assertLess(self.composed.index(marker), loop, marker)
        self.assertIn(self.styles, self.composed)
        for name in ("server_state.js", "page_context.js", "navigation.js", "shell.css"):
            source = (FRONTEND / name).read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 300, name)
            self.assertNotIn("innerHTML", source)

    def test_sidebar_order_and_selector_identity(self) -> None:
        """Brand, server block, navigation, application status; one selector with its state line."""
        order = [self.document.index(marker) for marker in (
            'class="brand"', 'class="sidebar-server"', 'id="global-profile"',
            'id="sidebar-server-state"', 'id="sidebar-running"', 'id="sidebar-create-profile"',
            'class="nav-list"', 'id="application-status"')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(self.document.count("<select"), 1)
        self.assertIn('<p class="page-server" id="page-server" hidden></p>', self.document)
        self.assertIn('id="page-update-badge" type="button" hidden', self.document)

    def test_shell_styles_follow_the_specification(self) -> None:
        """Group labels, item size, current-page bar, badge colours, motion and forced colours."""
        for rule in (
            ".nav-list { flex: 1 1 auto; min-height: 0; overflow-y: auto;",
            "letter-spacing: .06em; text-transform: uppercase; }",
            "  min-height: 38px;",
            ".nav-icon { flex: none; width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8;",
            "box-shadow: inset 3px 0 0 var(--color-action); color: var(--color-text); font-weight: 600; }",
            '.nav-item[aria-current="page"] .nav-icon { color: var(--color-action-strong); }',
            ".nav-badge.is-count { background: var(--color-status-warning); color: var(--primitive-slate-950); }",
            "border: 1px solid var(--color-status-error); color: var(--color-status-error); }",
            ".status-neutral { color: var(--color-text-muted); }",
            "@media (prefers-reduced-motion: reduce) { .nav-badge.is-checking { animation: none; } }",
            '.nav-item[aria-current="page"] { border: 1px solid Highlight; }',
            "forced-color-adjust: none; }",
            'body[data-section="overview"] .page-server-state { display: none; }',
            "width: 32px; height: 32px;",
        ):
            self.assertIn(rule, self.styles)
        # No colour is written as a literal: every colour is a token or a system colour
        self.assertNotRegex(self.styles, r"#[0-9a-fA-F]{3,8}\b|rgb\(")


if __name__ == "__main__":
    unittest.main()
