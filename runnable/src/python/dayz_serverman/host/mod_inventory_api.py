"""Read-only mod inventory method exposed to WebView2."""

from __future__ import annotations

from typing import Any


class ModInventoryHostMethods:
    """Native method exposing the read-only mod inventory."""
    def list_mod_inventory(self, profile_id: object) -> dict[str, Any]:
        """Return the mod inventory for a profile."""
        return self._invoke("list_mod_inventory", {"profile_id": profile_id})
