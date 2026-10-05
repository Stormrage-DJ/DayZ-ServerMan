"""Named bridge calls for the update status and the check request."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RepositoryError
from ..domain.profiles import ProfileValidationError, validate_profile_id
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .mod_inventory import ModInventoryService
from .server_build import ServerBuildService
from .server_build_status import internal_view
from .settings import SettingsValidationError
from .update_check import UpdateCheckService

# Check scopes: the mods, the server build, or both ("Check now" on Overview)
SCOPE_MODS = "mods"
SCOPE_SERVER_BUILD = "server_build"
SCOPE_ALL = "all"
SCOPES = frozenset((SCOPE_MODS, SCOPE_SERVER_BUILD, SCOPE_ALL))


class UpdateCheckCoordinator:
    """Serve the update status of one profile and accept check requests."""

    def __init__(
        self, service: UpdateCheckService, inventory: ModInventoryService,
        server_build: ServerBuildService | None = None,
    ) -> None:
        """Store the mod check, the inventory that owns the row rule, and the build check."""
        self._service = service
        self._inventory = inventory
        self._server_build = server_build

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for the update status calls."""
        return {
            "get_update_status": self.get_update_status,
            "request_update_check": self.request_update_check,
        }

    def get_update_status(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the check state and the row counts for the requested profile.

        A null profile identifier returns the server build part only.
        """
        # Require exactly the profile identifier
        if set(parameters) != {"profile_id"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "profile_id is required")
        if parameters.get("profile_id") is None:
            view = self._service.snapshot()
            return {"mods": None, "server_build": self._build_status(),
                    "checking": view.checking, "revision": view.revision}
        # Derive the rows and the check view from one consistent reading
        try:
            profile_id = validate_profile_id(parameters.get("profile_id"))
            rows, derived = self._inventory.report(profile_id)
        except ProfileValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        except ProfileNotFound as error:
            raise ApplicationCallError(ErrorCode.NOT_FOUND, str(error)) from error
        except (ProfileStorageError, OSError) as error:
            raise ApplicationCallError(
                ErrorCode.STORAGE_FAILURE, "Profile storage is unavailable.",
            ) from error
        except (RepositoryError, SettingsValidationError) as error:
            # A settings record that cannot be read now is a storage failure, not an internal one
            raise ApplicationCallError(
                ErrorCode.STORAGE_FAILURE, "Settings storage is unavailable.",
            ) from error
        view = derived if derived is not None else self._service.snapshot()
        # Count with the row rule so the numbers agree with the Mods table
        states = [row["state"] for row in rows]
        return {
            "mods": {
                "check_state": view.check_state.value,
                "checked_at": view.checked_at,
                "last_success_at": view.last_success_at,
                "error_code": view.error_code,
                "update_count": states.count("UPDATE_AVAILABLE"),
                "pending_apply_count": states.count("PENDING_APPLY"),
                # Additive: Workshop mods that need a download before they can run (QF-054)
                "not_downloaded_count": states.count("NOT_DOWNLOADED"),
            },
            "server_build": self._build_status(),
            "checking": view.checking,
            "revision": view.revision,
        }

    def request_update_check(self, parameters: Mapping[str, Any]) -> dict[str, bool]:
        """Start a check when the trigger rules allow it; never waits for the run."""
        # Require the exact field set with a known scope and a boolean flag
        if set(parameters) != {"scope", "force"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "scope and force are required")
        scope = parameters.get("scope")
        if not isinstance(scope, str) or scope not in SCOPES:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "scope is invalid")
        force = parameters.get("force")
        if not isinstance(force, bool):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "force is invalid")
        if scope == SCOPE_MODS:
            accepted, checking = self._service.request(force)
            return {"accepted": accepted, "checking": checking}
        build = (self._server_build.request(force) if self._server_build is not None
                 else {"accepted": False, "checking": False, "waiting": False})
        if scope == SCOPE_SERVER_BUILD:
            return {"accepted": build["accepted"], "checking": build["checking"], "server_build": build}
        # "all": the top-level fields keep their mod meaning
        accepted, checking = self._service.request(force)
        return {"accepted": accepted, "checking": checking, "server_build": build}

    def _build_status(self) -> dict[str, object] | None:
        """Return the server build part; it never fails the status call."""
        if self._server_build is None:
            return None
        try:
            return self._server_build.status()
        except Exception:
            return internal_view()
