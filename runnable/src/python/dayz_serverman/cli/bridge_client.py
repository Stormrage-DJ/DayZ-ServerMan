"""Bridge calls of a CLI command through `BridgeFacade.dispatch`, the GUI's call path (A2, design 6.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from itertools import count
from typing import Any

from ..adapters.windows.instance_lock import InstanceLockUnsupported
from ..adapters.windows.server_folder_lock import ServerFolderBusy
from ..bridge.contracts import CONTRACT_VERSION
from ..session_observer import BridgeCallFailed, ObserverSession
from .exit_codes import FAILED, REFUSED, dispatch_exit
from .output import CliFailure, sentence
from .wording import FOLDER_BUSY, INSTANCE_LOCK_UNSUPPORTED, NOT_SET_UP, bridge_error_line


class CliBridgeError(Exception):
    """A bridge call answered with an error; `error` is the error object of the envelope."""

    def __init__(self, method: str, error: Mapping[str, Any], *, names_confirmed: bool = True) -> None:
        """Keep the method and the error object; the message is the host's safe message.

        The CLI confirms every operator identifier (P4 resolution, pre-step listings, parser
        choices) before it sends one, so by default a NOT_FOUND is data behind valid names (exit 1).
        """
        self.method = method
        self.error = dict(error)
        self.names_confirmed = names_confirmed
        super().__init__(str(self.error.get("message", "")))

    @property
    def code(self) -> str:
        """Return the error code of the answer."""
        return str(self.error.get("code"))

    def failure(self, kind: object = None) -> CliFailure:
        """Return the CLI failure of this answer: its exit code by the 6.4 table and the operator text."""
        exit_code = dispatch_exit(self.error, names_confirmed=self.names_confirmed)
        details = self.error.get("details") if isinstance(self.error.get("details"), Mapping) else None
        return CliFailure(self.code, bridge_error_line(self.error, kind),
                          FAILED if exit_code is None else exit_code,
                          bool(self.error.get("retryable")), details)


def envelope(method: str, parameters: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Build one bridge request with the exact field set that `BridgeRequest.parse` requires."""
    return {"contract_version": CONTRACT_VERSION, "request_id": request_id, "method": method,
            "parameters": dict(parameters)}


class BridgeClient:
    """Calls of an owner session: one dispatch per call, no retry."""

    def __init__(self, dispatch: Callable[[object], Mapping[str, Any]], prefix: str = "cli") -> None:
        """Keep the facade's dispatch function and the request-id prefix."""
        self._dispatch = dispatch
        self._prefix = prefix
        self._sequence = count(1)
        # Every method this client called, in order, for the parity and order tests
        self.calls: list[str] = []

    def call(self, method: str, **parameters: Any) -> Any:
        """Dispatch one call and return its value; raise CliBridgeError on an error answer."""
        self.calls.append(method)
        result = self._dispatch(envelope(method, parameters, f"{self._prefix}-{next(self._sequence)}"))
        if result.get("success"):
            return result.get("value")
        raise CliBridgeError(method, result.get("error") or {})


class ObserverClient:
    """Calls of an observer session, with the reader side of the folder lock per call (4.4)."""

    def __init__(self, session: ObserverSession) -> None:
        """Keep the observer session."""
        self._session = session
        self.calls: list[str] = []

    def call(self, method: str, **parameters: Any) -> Any:
        """Dispatch one read; map the reader-side refusals and a root without a data folder (4.5)."""
        self.calls.append(method)
        try:
            return self._session.call(method, parameters)
        except ServerFolderBusy as error:
            raise CliFailure("CONTROL_CONFLICT", sentence(FOLDER_BUSY), REFUSED, True) from error
        except InstanceLockUnsupported as error:
            raise CliFailure("INSTANCE_LOCK_UNSUPPORTED", sentence(INSTANCE_LOCK_UNSUPPORTED), FAILED) from error
        except BridgeCallFailed as error:
            if not self._session.paths.data.is_dir():
                # A read that cannot answer on a root that no owner has set up (4.5)
                raise CliFailure(str(error.error.get("code")), sentence(NOT_SET_UP), FAILED) from error
            raise CliBridgeError(method, error.error) from error
