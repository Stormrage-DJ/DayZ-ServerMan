"""Named bridge query for the read-only mod inventory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from .mod_inventory import ModInventoryService
from ..domain.profiles import validate_profile_id


class ModInventoryCoordinator:
    """Serve the read-only mod inventory query for one profile."""

    def __init__(self, service: ModInventoryService) -> None:
        """Store the inventory service used for the report."""
        self._service = service

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for the inventory query."""
        return {"list_mod_inventory": self.list_mod_inventory}

    def list_mod_inventory(self, parameters: Mapping[str, Any]) -> list[dict[str, object]]:
        """Return the inventory rows for the requested profile."""
        # Require exactly the profile identifier
        if set(parameters) != {"profile_id"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "profile_id is required")
        # Validate the identifier and delegate to the inventory service
        try:
            profile_id = validate_profile_id(parameters.get("profile_id"))
            return self._service.list(profile_id)
        except ValueError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
