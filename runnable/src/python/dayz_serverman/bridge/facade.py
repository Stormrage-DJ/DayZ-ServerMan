"""Allowlisted bridge dispatch without reflection or raw exception exposure."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..observability.structured_log import CorrelationScope, StructuredLogger

from .contracts import (
    BridgeContractError,
    BridgeRequest,
    BridgeResult,
    ErrorCode,
    IDENTIFIER_PATTERN,
)


# Handler signature every allowlisted bridge method must provide
BridgeHandler = Callable[[Mapping[str, Any]], Any]


class ApplicationCallError(RuntimeError):
    """Application failure that maps onto a bridge error result."""

    def __init__(
        self,
        code: ErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Store the error code, safe message, and optional details."""
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.details = details
        super().__init__(safe_message)


class BridgeFacade:
    """Dispatch allowlisted methods and translate every failure to a result."""

    def __init__(
        self,
        handlers: Mapping[str, BridgeHandler],
        logger: StructuredLogger | None = None,
    ) -> None:
        """Copy the handler allowlist and keep the optional logger."""
        # Copy so later mutation of the caller mapping cannot change the allowlist
        self._handlers = dict(handlers)
        self._logger = logger

    @property
    def allowed_methods(self) -> frozenset[str]:
        """Return the frozen set of dispatchable method names."""
        return frozenset(self._handlers)

    def dispatch(self, raw_request: object) -> dict[str, Any]:
        """Parse, dispatch, and serialize one request without raising."""
        # Extract a safe id up front so even malformed requests get an answer
        request_id = self._safe_request_id(raw_request)
        method: str | None = None
        try:
            request = BridgeRequest.parse(raw_request)
            method = request.method
            # Correlate every log line with the parsed request id
            with CorrelationScope(request.request_id):
                self._log("bridge.request", request.request_id, {"method": request.method}, "DEBUG")
                handler = self._handlers.get(request.method)
                # Unknown methods are rejected instead of reflected
                if handler is None:
                    raise BridgeContractError(
                        ErrorCode.INVALID_REQUEST,
                        "bridge method is not allowed",
                    )
                value = handler(request.parameters)
                self._log("bridge.success", request.request_id, {"method": request.method}, "DEBUG")
                return BridgeResult.ok(request.request_id, value).to_dict()
        except BridgeContractError as error:
            # Contract failures are safe to return verbatim
            self._log("bridge.failure", request_id, {
                "method": method, "error_code": error.code.value, "message": error.safe_message,
            }, "WARNING")
            return BridgeResult.failed(request_id, error.code, error.safe_message).to_dict()
        except ApplicationCallError as error:
            # Application failures map to their declared code and retryable flag
            owner = (error.details or {}).get("owner")
            self._log("bridge.failure", request_id, {
                "method": method, "error_code": error.code.value, "message": error.safe_message,
                # Additive: the owner of a refusing block, so Manager activity names the same way out as the page
                **({"owner": owner} if owner else {}),
            }, "WARNING")
            return BridgeResult.failed(
                request_id,
                error.code,
                error.safe_message,
                error.retryable,
                error.details,
            ).to_dict()
        except Exception:
            # Unexpected failures are hidden behind a generic internal error
            self._log("bridge.failure", request_id, {
                "method": method, "error_code": "INTERNAL_FAILURE",
                "message": "The request could not be completed.",
            }, "ERROR")
            return BridgeResult.failed(
                request_id,
                ErrorCode.INTERNAL_FAILURE,
                "The request could not be completed.",
            ).to_dict()

    def _log(
        self,
        event: str,
        correlation_id: str,
        fields: Mapping[str, Any],
        level: str = "INFO",
    ) -> None:
        """Emit a structured event, ignoring logger failures."""
        # Logging must never break dispatch; logger errors are swallowed
        if self._logger is not None:
            try:
                self._logger.emit(
                    event,
                    level=level,
                    correlation_id=correlation_id,
                    fields=fields,
                )
            except (OSError, TypeError, ValueError):
                return

    @staticmethod
    def _safe_request_id(raw_request: object) -> str:
        """Return the request id when valid, else a fixed placeholder."""
        if isinstance(raw_request, dict):
            request_id = raw_request.get("request_id")
            # Accept the id only when it can be safely logged and echoed
            if isinstance(request_id, str) and IDENTIFIER_PATTERN.fullmatch(request_id):
                return request_id
        return "invalid-request"
