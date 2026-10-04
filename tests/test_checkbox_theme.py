"""Shared checkbox theme and consumer inventory tests."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
TOKENS = (FRONTEND / "tokens.css").read_text(encoding="utf-8")
CONTROLS = (FRONTEND / "controls.css").read_text(encoding="utf-8")


class CheckboxThemeTests(unittest.TestCase):
    """Protect the shared checkbox component and its complete consumer list."""

    def test_every_checkbox_consumer_uses_the_shared_component(self) -> None:
        """Every script that creates a checkbox is part of the audited inventory."""
        consumers = {
            path.name
            for path in FRONTEND.glob("*.js")
            if 'type = "checkbox"' in path.read_text(encoding="utf-8")
        }
        self.assertEqual(consumers, {
            "migration.js",
            "overview_backup.js",
            "overview_schedule.js",
            "profile_restore.js",
            "profiles.js",
            "tweaks_render.js",
        })

    def test_component_tokens_cover_checkbox_visual_states(self) -> None:
        """Checkbox colors and geometry come from component tokens."""
        for declaration in (
            "--choice-background: var(--color-surface-raised);",
            "--choice-border: var(--color-text-subtle);",
            "--choice-border-hover: var(--color-action-strong);",
            "--choice-background-checked: var(--color-action);",
            "--choice-border-checked: var(--color-action);",
            "--choice-mark: var(--primitive-slate-950);",
            "--choice-radius: 4px;",
        ):
            self.assertIn(declaration, TOKENS)

    def test_shared_css_covers_interaction_and_accessibility_states(self) -> None:
        """The component has checked, hover, disabled, focus, and forced-color behavior."""
        for contract in (
            'input[type="checkbox"] {',
            'input[type="checkbox"]:hover:not(:disabled)',
            'input[type="checkbox"]:checked {',
            'input[type="checkbox"]:checked::before',
            'input[type="checkbox"]:disabled {',
            "input:focus-visible",
            "@media (forced-colors: active)",
            "appearance: auto; accent-color: auto;",
        ):
            self.assertIn(contract, CONTROLS)

    def test_page_styles_do_not_fork_checkbox_appearance(self) -> None:
        """Only the shared controls stylesheet may style checkbox appearance."""
        page_css = "\n".join(
            path.read_text(encoding="utf-8")
            for path in FRONTEND.glob("*.css")
            if path.name not in {"controls.css", "tokens.css"}
        )
        self.assertNotIn('input[type="checkbox"]', page_css)


if __name__ == "__main__":
    unittest.main()
