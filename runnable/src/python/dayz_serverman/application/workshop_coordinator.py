"""Strict bridge handlers for Steam authentication and Workshop updates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RevisionConflict, SettingsInput
from ..domain.profiles import validate_profile_id
from ..domain.workshop import AuthenticationMode
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import (
    SETTINGS_FIELDS,
    SettingsService,
    SettingsValidationError,
    validate_steam_account_name,
)
from .workshop_updates import UpdateRequest, WorkshopUpdateService


# Lowercase SHA-256 digests expected in bridge requests
DIGEST = re.compile(r"[0-9a-f]{64}")


class WorkshopCoordinator:
    """Bridge-facing coordinator for Steam settings, auth, and updates."""

    def __init__(
        self,
        service: WorkshopUpdateService,
        settings: SettingsService,
        operations: OperationManager,
    ) -> None:
        """Store the update service, settings service, and operation manager."""
        self._service = service
        self._settings = settings
        self._operations = operations

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for Workshop calls."""
        return {
            "save_steam_settings": self.save_steam_settings,
            "authenticate_steamcmd": self.authenticate_steamcmd,
            "update_workshop_items": self.update_workshop_items,
        }

    def save_steam_settings(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Persist Steam authentication fields as a serialized operation."""
        # Validate the request envelope and authentication pair first
        self._exact(parameters, {"expected_revision", "authentication_mode", "account_name"})
        expected = self._revision(parameters.get("expected_revision"), "expected_revision")
        mode, account = self._authentication(parameters)

        def work(_context: object) -> dict[str, Any]:
            """Merge the Steam fields and save under the expected revision."""
            current = self._settings.load()
            values = {
                field: getattr(current, field)
                for field in SETTINGS_FIELDS
            }
            values.update(steam_authentication_mode=mode.value, steam_account_name=account)
            # Save under the expected revision so concurrent edits surface as conflicts
            try:
                saved = self._settings.save(SettingsInput(**values), expected)
            except RevisionConflict as error:
                raise OperationFailure("REVISION_CONFLICT", str(error)) from error
            except SettingsValidationError as error:
                raise OperationFailure("INVALID_REQUEST", str(error)) from error
            return {
                "revision": saved.revision,
                "authentication_mode": saved.steam_authentication_mode,
                "account_name": saved.steam_account_name,
            }

        return self._submit("SAVE_STEAM_SETTINGS", work)

    def authenticate_steamcmd(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Submit an interactive SteamCMD authentication operation."""
        self._exact(parameters, {"expected_settings_revision"})
        expected = self._revision(
            parameters.get("expected_settings_revision"),
            "expected_settings_revision",
        )
        # Submit interactive sign-in under the operation lane
        return self._submit(
            "AUTHENTICATE_STEAMCMD",
            lambda context: self._service.authenticate(expected, context),
            safe_points=frozenset(("preflight",)),
        )

    def update_workshop_items(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate an update request and submit it as an operation."""
        allowed = {
            "profile_id", "expected_profile_revision", "expected_semantic_profile_digest",
            "expected_settings_revision", "authentication_mode", "account_name",
            "update_all_and_start",
        }
        # Enforce the exact request envelope before reading fields
        self._exact(parameters, allowed)
        # Validate the authentication pair and the profile identifier
        mode, account = self._authentication(parameters)
        try:
            profile_id = validate_profile_id(parameters.get("profile_id"))
        except ValueError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        # Require a lowercase SHA-256 digest of the semantic profile
        digest = parameters.get("expected_semantic_profile_digest")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ApplicationCallError(
                ErrorCode.INVALID_REQUEST,
                "expected_semantic_profile_digest must be lowercase SHA-256",
            )
        # Start is opt-in and stays false unless the caller says otherwise
        start = parameters.get("update_all_and_start", False)
        if not isinstance(start, bool):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "update_all_and_start must be boolean")
        # Assemble the update request with all expected revisions
        request = UpdateRequest(
            profile_id,
            self._revision(parameters.get("expected_profile_revision"), "expected_profile_revision"),
            digest,
            self._revision(parameters.get("expected_settings_revision"), "expected_settings_revision"),
            mode,
            account,
            start,
        )
        return self._submit(
            "UPDATE_WORKSHOP_ITEMS",
            lambda context: self._service.update(request, context),
            safe_points=frozenset((
                "preflight", "resolve_items", "check_remote", "download", "verify_items",
                "verify_set",
            )),
            log_fields={"profile_id": profile_id},
            target_profile_id=profile_id,
        )

    def _submit(self, kind: str, work: Any, **options: Any) -> dict[str, Any]:
        """Submit work through the operation manager and return its identity."""
        try:
            record = self._operations.submit(kind, work, **options)
        except QueueUnavailable as error:
            # A saturated queue surfaces as a retryable mutation conflict
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT,
                str(error),
                retryable=True, details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}

    @staticmethod
    def _exact(parameters: Mapping[str, Any], allowed: set[str]) -> None:
        """Reject requests with unknown or missing fields."""
        unknown = set(parameters).difference(allowed)
        missing = allowed.difference(parameters)
        if unknown or missing:
            raise ApplicationCallError(
                ErrorCode.INVALID_REQUEST,
                "Workshop request fields are missing or unknown.",
            )

    @staticmethod
    def _revision(value: object, field: str) -> int:
        """Validate a non-negative revision integer."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, f"{field} is invalid")
        return value

    @staticmethod
    def _authentication(parameters: Mapping[str, Any]) -> tuple[AuthenticationMode, str | None]:
        """Validate the authentication mode and account pair from a request."""
        # Reject unknown modes before checking the account
        try:
            mode = AuthenticationMode(parameters.get("authentication_mode"))
        except (TypeError, ValueError) as error:
            raise ApplicationCallError(
                ErrorCode.INVALID_REQUEST,
                "authentication_mode must be ACCOUNT or ANONYMOUS",
            ) from error
        account = parameters.get("account_name")
        if account is not None and not isinstance(account, str):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "account_name must be text or null")
        try:
            account = validate_steam_account_name(account)
        except SettingsValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        # Enforce the account requirements implied by the selected mode
        if mode == AuthenticationMode.ACCOUNT and account is None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "ACCOUNT mode requires account_name")
        if mode == AuthenticationMode.ANONYMOUS and account is not None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "ANONYMOUS mode forbids account_name")
        return mode, account
