"""Named browser methods for daily lifecycle schedules."""

from __future__ import annotations

from typing import Any


class ScheduleHostMethods:
    """Native methods for daily lifecycle schedules."""
    def get_lifecycle_schedule(self, profile_id: object) -> dict[str, Any]:
        """Return the lifecycle schedule stored for a profile."""
        return self._invoke("get_lifecycle_schedule", {"profile_id": profile_id})

    def save_lifecycle_schedule(
        self, profile_id: object, hour: object, minute: object, action: object,
    ) -> dict[str, Any]:
        """Persist the daily lifecycle action for a profile."""
        return self._invoke("save_lifecycle_schedule", {
            "profile_id": profile_id,
            "hour": hour,
            "minute": minute,
            "action": action,
        })
