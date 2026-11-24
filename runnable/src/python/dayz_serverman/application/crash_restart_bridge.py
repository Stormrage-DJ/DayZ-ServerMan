"""Bridge handlers of the crash restart: the owner cancel method and the operator cancel on start and restart."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError, BridgeHandler
from .crash_restart_flow import CrashRestartState
from .lifecycle_coordinator import _exact_fields


class HandlerSource(Protocol):
    """Anything that supplies bridge handlers in order."""

    def handlers(self) -> Mapping[str, BridgeHandler]: ...


class CancelCrashRestart(Protocol):
    """The part of the crash restart monitor that cancels a pending restart."""

    def cancel(self) -> bool: ...


class CrashRestartPort(CancelCrashRestart, Protocol):
    """The part of the crash restart monitor that cancels and reads."""

    def read(self) -> CrashRestartState | None: ...

    def clear(self) -> bool: ...


def auto_restart_value(state: CrashRestartState | None) -> dict[str, str | None] | None:
    """Return the wire form of the crash restart state, or None when there is none."""
    return None if state is None else {"kind": state.kind, "reason": state.reason, "at": state.at}


class CrashRestartHandlers:
    """Extend the lifecycle handler table with the cancel method of the crash restart."""

    def __init__(self, source: HandlerSource, monitor: CrashRestartPort) -> None:
        """Bind the handler source and the monitor."""
        self._source = source
        self._monitor = monitor

    def handlers(self) -> dict[str, BridgeHandler]:
        """Return the source handlers in order, with the cancel method after restart_server."""
        result: dict[str, BridgeHandler] = {}
        for name, handler in self._source.handlers().items():
            # An operator start or restart takes precedence over a pending crash restart
            result[name] = self._cancelling(handler) if name in ("start_server", "restart_server") else handler
            # The status reply carries the state of the last crash restart
            if name == "get_server_status":
                result[name] = self._reporting(handler)
            if name == "restart_server":
                result["cancel_crash_restart"] = self.cancel_crash_restart
        return result

    def _reporting(self, handler: BridgeHandler) -> BridgeHandler:
        """Return the handler with the additive auto_restart field; errors of the handler pass through."""
        def with_auto_restart(parameters: Mapping[str, Any]) -> Any:
            """Run the handler first, then add the crash restart state to its reply."""
            value = handler(parameters)
            return {**value, "auto_restart": auto_restart_value(self._monitor.read())}

        return with_auto_restart

    def _cancelling(self, handler: BridgeHandler) -> BridgeHandler:
        """Return the handler behind a clear of the crash restart; the clear happens even when the handler refuses."""
        def cancelled_first(parameters: Mapping[str, Any]) -> Any:
            """Clear the pending crash restart and its stored state, then run the handler."""
            self._monitor.clear()
            return handler(parameters)

        return cancelled_first

    def cancel_crash_restart(self, parameters: Mapping[str, Any]) -> dict[str, bool]:
        """Cancel a pending crash restart; the answer tells whether one was pending."""
        # This method takes no parameters
        _exact_fields(parameters, set())
        return {"cancelled": self._monitor.cancel()}
