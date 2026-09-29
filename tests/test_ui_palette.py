"""Palette tests for approved primitive tokens and semantic deltas."""
from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKENS = (PROJECT_ROOT / "runnable" / "src" / "frontend" / "tokens.css").read_text(
    encoding="utf-8"
)
STYLES = (PROJECT_ROOT / "runnable" / "src" / "frontend" / "styles.css").read_text(
    encoding="utf-8"
)


class UiPaletteTests(unittest.TestCase):
    """Approved palette and semantic token consumption contracts."""
    def test_approved_primitive_palette_is_exact(self) -> None:
        """Every approved primitive token is declared with its exact value."""
        expected = {
            "slate-950": "#0d1117",
            "slate-900": "#111820",
            "slate-850": "#161e28",
            "slate-800": "#1c2632",
            "slate-750": "#223043",
            "slate-700": "#2c3948",
            "slate-500": "#8794a3",
            "slate-300": "#aeb9c6",
            "slate-100": "#f3f6fa",
            "blue-500": "#4f8cff",
            "blue-300": "#8cb8ff",
            "green-500": "#4cc38a",
            "amber-500": "#f2b84b",
            "red-500": "#f07070",
            "violet-500": "#b39dff",
            "sky-500": "#66b3ff",
        }
        # Confirm each approved primitive appears verbatim in the token layer
        for name, value in expected.items():
            self.assertIn(f"--primitive-{name}: {value};", TOKENS)

    def test_semantic_and_component_delta_is_applied(self) -> None:
        """The semantic and component token deltas are applied."""
        expected = (
            "--color-surface-hover: var(--primitive-slate-750);",
            "--color-action: var(--primitive-blue-500);",
            "--color-focus: var(--primitive-blue-300);",
            "--color-info: var(--primitive-sky-500);",
            "--color-scrim: rgb(0 0 0 / 55%);",
            "--brand-height: 68px;",
            "--nav-background-hover: var(--color-surface-hover);",
            "--nav-background-current: color-mix(in srgb, var(--color-action) 18%, transparent);",
        )
        # Confirm every delta declaration appears in the token layer
        for declaration in expected:
            self.assertIn(declaration, TOKENS)

    def test_shell_chrome_consumes_semantic_tokens(self) -> None:
        """Shell chrome consumes semantic tokens instead of literal colors."""
        self.assertIn("min-height: var(--brand-height)", STYLES)
        self.assertIn("background: var(--color-scrim)", STYLES)
        self.assertNotIn("background: rgb(0 0 0 / 55%)", STYLES)


if __name__ == "__main__":
    unittest.main()
