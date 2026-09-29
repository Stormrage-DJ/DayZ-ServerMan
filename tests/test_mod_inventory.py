"""Cover mod inventory ordering and bounded local workshop metadata."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable/src/python"))

from dayz_serverman.application.mod_inventory import ModInventoryService
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord


class _Profiles:
    """Provide a minimal profile reader for inventory tests."""

    def __init__(self, profile):
        """Store the profile record returned by read."""
        self.profile = profile

    def read(self, _profile_id):
        """Return the fixed profile record regardless of identifier."""
        return self.profile


class _Settings:
    """Provide a minimal settings reader pointing at the workshop cache."""

    def __init__(self, root: Path):
        """Store the workshop content root exposed through settings."""
        self.root = root

    def load(self):
        """Return a settings value carrying the workshop content root."""
        return SimpleNamespace(workshop_content_root=str(self.root))


class ModInventoryTests(unittest.TestCase):
    """Verify inventory rows are ordered and enriched from local metadata."""

    def test_inventory_is_ordered_and_uses_bounded_local_metadata(self) -> None:
        """Verify workshop and external rows keep order and report derived states."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "steamapps/workshop/content/221100"
            # Create one cached item with its own mod.cpp identity
            mod = root / "111"
            mod.mkdir(parents=True)
            (mod / "mod.cpp").write_text(
                'name = "Friendly Name";\nversion = "1.2.3";\n', encoding="utf-8")
            # Record a newer published manifest so the row reports an update
            (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
                '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { '
                '"111" { "manifest" "8" "size" "1" "timeupdated" "5" } } '
                '"WorkshopItemDetails" { "111" { "latest_manifest" "9" '
                '"latest_timeupdated" "6" } } }', encoding="utf-8")
            profile = ProfileRecord(1, ProfileInput.parse({
                "profile_id": "main", "display_name": "Main",
                "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
                "runtime_profile": None, "mission_root": None, "game_port": 2302,
                "mods": [
                    {"directory": "@Friendly", "launch_scope": "client",
                     "source": {"kind": "workshop", "workshop_id": "111"}},
                    {"directory": "@Local", "launch_scope": "server",
                     "source": {"kind": "external"}},
                ], "extra_arguments": [],
            }))
            # Inventory the profile's workshop and external mods
            rows = ModInventoryService(_Profiles(profile), _Settings(root)).list("main")
            self.assertEqual([row["order"] for row in rows], [1, 2])
            self.assertEqual(rows[0]["name"], "Friendly Name")
            self.assertEqual(rows[0]["version"], "1.2.3")
            self.assertEqual(rows[0]["state"], "UPDATE_AVAILABLE")
            self.assertEqual(rows[1]["state"], "LOCAL")
            self.assertIsNone(rows[1]["version"])


if __name__ == "__main__":
    unittest.main()
