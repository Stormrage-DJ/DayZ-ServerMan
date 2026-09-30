"""Static contracts for authoritative frontend profile selection."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "runnable" / "src" / "frontend" / "profile_context.js").read_text(
    encoding="utf-8",
)


class ProfileContextUiTests(unittest.TestCase):
    """Keep profile catalog refresh and selection in one shared owner."""

    def test_refresh_reloads_catalog_selects_and_persists(self) -> None:
        """A newly created profile can become the remembered shared selection."""
        for marker in (
            "async function refreshAndSelectProfile",
            "profileContextState.profiles = result.value",
            "known.has(preferredProfileId)",
            "renderGlobalProfileSelector(); announceProfileSelection()",
            "save_selected_profile(profileContextState.selectedId)",
            "refreshAndSelect: refreshAndSelectProfile",
        ):
            self.assertIn(marker, SOURCE)

    def test_unknown_direct_selection_is_rejected(self) -> None:
        """Direct selection cannot introduce an ID absent from the catalog."""
        self.assertIn("The selected profile is unavailable.", SOURCE)


if __name__ == "__main__":
    unittest.main()
