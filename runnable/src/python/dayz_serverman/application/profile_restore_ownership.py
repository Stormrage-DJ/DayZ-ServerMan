"""Prove ownership before deleting a whole restored isolated mission."""

import json

from ..adapters.windows.shared_files import read_text_shared
from ..domain.profile_restore_mapping import MISSION_OWNER_MARKER
from ..repositories.profile_restore_inventory import safe_exists
from .mission_configuration import resolve_profile_mission


def owns_complete_mission(root, mission_root, mission, profile_id, records):
    """Require a valid matching marker and no other registered mission reference."""
    marker = mission / MISSION_OWNER_MARKER
    try:
        if not safe_exists(marker):
            return False
        value = json.loads(read_text_shared(marker, encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"profile_id", "mission_root", "operation_id"}:
            return False
        if value["profile_id"] != profile_id or value["mission_root"].casefold() != mission_root.casefold() or not isinstance(value["operation_id"], str) or not value["operation_id"]:
            return False
        for other in records:
            if other.values.profile_id != profile_id:
                other_root, _ = resolve_profile_mission(root, other)
                if other_root.casefold() == mission_root.casefold():
                    return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False
