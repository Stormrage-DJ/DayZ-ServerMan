"""Strict JSON-compatible request and result contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


# Wire-format version accepted and emitted by this build
CONTRACT_VERSION = 1
# Exact field set every request object must carry
REQUEST_FIELDS = frozenset(("contract_version", "request_id", "method", "parameters"))
# Request ids: 1-128 characters of letters, digits, and supported separators
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
# Method names: lowercase snake case, at most 64 characters
METHOD_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ErrorCode(str, Enum):
    """Machine-readable failure codes shared with native callers."""
    INVALID_REQUEST = "INVALID_REQUEST"
    CONTRACT_VERSION_UNSUPPORTED = "CONTRACT_VERSION_UNSUPPORTED"
    NOT_FOUND = "NOT_FOUND"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    MUTATION_CONFLICT = "MUTATION_CONFLICT"
    CONTROL_CONFLICT = "CONTROL_CONFLICT"
    EXTERNAL_PROCESS = "EXTERNAL_PROCESS"
    PROCESS_STATE_UNKNOWN = "PROCESS_STATE_UNKNOWN"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    STOP_METHOD_UNPROVEN = "STOP_METHOD_UNPROVEN"
    GAMEPLAY_NOT_ENABLED = "GAMEPLAY_NOT_ENABLED"
    RUNTIME_PROFILE_UNRESOLVED = "RUNTIME_PROFILE_UNRESOLVED"
    PENDING_RUNTIME_PROFILE_SUPPORT = "PENDING_RUNTIME_PROFILE_SUPPORT"
    PROFILE_CONTEXT_MISMATCH = "PROFILE_CONTEXT_MISMATCH"
    UNSUPPORTED_SNAPSHOT_CONTENT = "UNSUPPORTED_SNAPSHOT_CONTENT"
    MIGRATION_CONFLICT = "MIGRATION_CONFLICT"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    PATH_INVALID = "PATH_INVALID"
    PATH_OUTSIDE_ALLOWED_ROOT = "PATH_OUTSIDE_ALLOWED_ROOT"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    STEAMCMD_UNAVAILABLE = "STEAMCMD_UNAVAILABLE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    ENTITLEMENT_DENIED = "ENTITLEMENT_DENIED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    WORKSHOP_CONTENT_FAILED = "WORKSHOP_CONTENT_FAILED"
    CACHE_VERIFICATION_FAILED = "CACHE_VERIFICATION_FAILED"
    UPDATE_CANCELLED = "UPDATE_CANCELLED"
    UPDATE_RESULT_UNKNOWN = "UPDATE_RESULT_UNKNOWN"
    PUBLICATION_REQUIRED = "PUBLICATION_REQUIRED"
    PUBLICATION_PREVIEW_STALE = "PUBLICATION_PREVIEW_STALE"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    OPERATION_NOT_CANCELLABLE = "OPERATION_NOT_CANCELLABLE"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


@dataclass(frozen=True)
class BridgeContractError(ValueError):
    """Request validation failure carrying a safe public message."""
    code: ErrorCode
    safe_message: str

    def __str__(self) -> str:
        """Return the safe message for display."""
        return self.safe_message


def ensure_json_value(value: Any, path: str = "value") -> None:
    """Reject any value that cannot round-trip through strict JSON."""
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        # NaN and infinities cannot be represented in JSON
        if value != value or value in (float("inf"), float("-inf")):
            raise BridgeContractError(ErrorCode.INVALID_REQUEST, f"{path} is not finite")
        return
    if isinstance(value, list):
        # Validate list entries recursively with indexed paths for messages
        for index, item in enumerate(value):
            ensure_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        # Field names must be strings; values are validated recursively
        for key, item in value.items():
            if not isinstance(key, str):
                raise BridgeContractError(
                    ErrorCode.INVALID_REQUEST,
                    f"{path} contains a non-string field name",
                )
            ensure_json_value(item, f"{path}.{key}")
        return
    raise BridgeContractError(ErrorCode.INVALID_REQUEST, f"{path} is not JSON-compatible")


@dataclass(frozen=True)
class BridgeRequest:
    """One parsed request: identity, method, and JSON parameters."""
    contract_version: int
    request_id: str
    method: str
    parameters: Mapping[str, Any]

    @classmethod
    def parse(cls, raw: object) -> BridgeRequest:
        """Parse and validate an incoming raw request mapping."""
        # The wire form must be a JSON object
        if not isinstance(raw, dict):
            raise BridgeContractError(ErrorCode.INVALID_REQUEST, "request must be an object")
        unknown = set(raw).difference(REQUEST_FIELDS)
        missing = REQUEST_FIELDS.difference(raw)
        # Reject field drift in either direction before reading values
        if unknown:
            raise BridgeContractError(
                ErrorCode.INVALID_REQUEST,
                f"request contains unknown fields: {sorted(unknown)}",
            )
        if missing:
            raise BridgeContractError(
                ErrorCode.INVALID_REQUEST,
                f"request is missing fields: {sorted(missing)}",
            )
        version = raw["contract_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise BridgeContractError(
                ErrorCode.INVALID_REQUEST,
                "contract_version must be an integer",
            )
        # Only the supported contract version may proceed
        if version != CONTRACT_VERSION:
            raise BridgeContractError(
                ErrorCode.CONTRACT_VERSION_UNSUPPORTED,
                "bridge contract version is unsupported",
            )
        request_id = raw["request_id"]
        # Request ids are bounded and pattern-checked before use in logs
        if not isinstance(request_id, str) or IDENTIFIER_PATTERN.fullmatch(request_id) is None:
            raise BridgeContractError(ErrorCode.INVALID_REQUEST, "request_id is invalid")
        method = raw["method"]
        # Method names are pattern-checked before any handler lookup
        if not isinstance(method, str) or METHOD_PATTERN.fullmatch(method) is None:
            raise BridgeContractError(ErrorCode.INVALID_REQUEST, "method is invalid")
        parameters = raw["parameters"]
        if not isinstance(parameters, dict):
            raise BridgeContractError(ErrorCode.INVALID_REQUEST, "parameters must be an object")
        # Parameters must survive a strict JSON compatibility check
        ensure_json_value(parameters, "parameters")
        return cls(version, request_id, method, dict(parameters))


@dataclass(frozen=True)
class BridgeError:
    """Serializable error payload returned to native callers."""
    code: ErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible error payload."""
        value: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        # Details are included only when present and JSON-safe
        if self.details:
            ensure_json_value(dict(self.details), "error.details")
            value["details"] = dict(self.details)
        return value


@dataclass(frozen=True)
class BridgeResult:
    """Serializable outcome of one dispatched bridge request."""
    request_id: str
    success: bool
    value: Any = None
    error: BridgeError | None = None

    @classmethod
    def ok(cls, request_id: str, value: Any) -> BridgeResult:
        """Build a success result after validating the payload."""
        ensure_json_value(value)
        return cls(request_id, True, value=value)

    @classmethod
    def failed(
        cls,
        request_id: str,
        code: ErrorCode,
        message: str,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> BridgeResult:
        """Build a failure result from a code and safe message."""
        return cls(request_id, False, error=BridgeError(code, message, retryable, details))

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible result envelope."""
        result: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "success": self.success,
        }
        # Success carries a value; failure carries the error object
        if self.success:
            result["value"] = self.value
        else:
            assert self.error is not None
            result["error"] = self.error.to_dict()
        return result
