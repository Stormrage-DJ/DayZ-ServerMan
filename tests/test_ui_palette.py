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
SHELL_STYLES = (PROJECT_ROOT / "runnable" / "src" / "frontend" / "shell.css").read_text(
    encoding="utf-8"
)
CONTROLS = (PROJECT_ROOT / "runnable" / "src" / "frontend" / "controls.css").read_text(
    encoding="utf-8"
)
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"


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
        # The sidebar chrome moved to the shell stylesheet
        self.assertIn("min-height: var(--brand-height)", SHELL_STYLES)
        self.assertIn("background: var(--color-scrim)", STYLES)
        self.assertNotIn("background: rgb(0 0 0 / 55%)", STYLES)

    def test_form_controls_share_one_component_contract(self) -> None:
        """Inputs, selectors, text areas, and choices use shared component tokens."""
        for declaration in (
            "--control-background: var(--color-surface-raised);",
            "--control-border-hover: var(--color-text-subtle);",
            "--control-text: var(--color-text);",
            "--control-height: 38px;",
            "--control-padding-inline: var(--primitive-space-3);",
            "--control-choice-size: 17px;",
            "--control-disabled-opacity: .65;",
        ):
            self.assertIn(declaration, TOKENS)
        for contract in (
            'input:not([type="checkbox"]):not([type="radio"]), select, textarea',
            "background: var(--control-background);",
            "color: var(--control-text);",
            'input[type="checkbox"]',
            "background: var(--choice-background);",
            "border-color: var(--choice-border-checked);",
            'input[type="radio"]',
            "accent-color: var(--color-action);",
            "textarea:focus-visible",
        ):
            self.assertIn(contract, CONTROLS)

    def test_page_styles_do_not_redefine_control_surfaces(self) -> None:
        """Page-level CSS may size controls but must not fork their visual surface."""
        page_styles = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in ("mods.css", "overview.css", "profiles.css")
        )
        for forbidden in (
            "background: var(--control-background)",
            "border: 1px solid var(--control-border)",
            "color: var(--control-text)",
            "background: var(--color-canvas)",
        ):
            self.assertNotIn(forbidden, page_styles)


if __name__ == "__main__":
    unittest.main()
