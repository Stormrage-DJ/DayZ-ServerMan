"""Workshop domain tests for item discovery order and download gates."""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.domain.workshop import (
    ItemOutcome,
    ItemResult,
    derive_required_items,
    download_gate,
)


def profile() -> ProfileRecord:
    """Build a record with two workshop mods and one external mod."""
    values = ProfileInput.parse({
        "profile_id": "primary", "display_name": "Primary",
        "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
        "runtime_profile": None, "mission_root": None, "game_port": 2302,
        "mods": [
            {"directory": "@Client", "launch_scope": "client",
             "source": {"kind": "workshop", "workshop_id": "111"}},
            {"directory": "@Local", "launch_scope": "client", "source": {"kind": "external"}},
            {"directory": "@Server", "launch_scope": "server",
             "source": {"kind": "workshop", "workshop_id": "222"}},
        ],
        "extra_arguments": [],
    })
    return ProfileRecord(4, values)


class WorkshopDomainTests(unittest.TestCase):
    """Required item derivation and download gate classification contracts."""
    def test_discovery_preserves_workshop_order_and_excludes_local(self) -> None:
        """Discovery keeps workshop order and excludes external mods."""
        items = derive_required_items(profile())
        self.assertEqual([item.workshop_id for item in items], ["111", "222"])
        self.assertEqual([item.launch_scope for item in items], ["client", "server"])
        self.assertEqual([item.order_index for item in items], [0, 1])

    def test_download_gate_distinguishes_terminal_classes(self) -> None:
        """The download gate distinguishes every terminal item class."""
        items = derive_required_items(profile())
        # Confirm the empty case and each terminal outcome reach the gate
        self.assertEqual(download_gate(()), "EMPTY")
        success = tuple(ItemResult(item, ItemOutcome.UPDATED_VERIFIED) for item in items)
        self.assertEqual(download_gate(success), "VERIFIED")
        self.assertEqual(download_gate((ItemResult(items[0], ItemOutcome.CANCELLED),)), "CANCELLED")
        self.assertEqual(
            download_gate((ItemResult(items[0], ItemOutcome.UNKNOWN_FAILED),)),
            "UNKNOWN",
        )
        self.assertEqual(
            download_gate((ItemResult(items[0], ItemOutcome.ENTITLEMENT_FAILED),)),
            "FAILED",
        )


if __name__ == "__main__":
    unittest.main()
