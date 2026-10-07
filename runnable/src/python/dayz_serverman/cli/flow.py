"""Steps of a writing command: Ctrl+C between steps, one lane operation with its waiter, its exit code.

A flow stops before its next submit when Ctrl+C was pressed (design 7, "Ctrl+C at other
points"); an operation that ends other than succeeded becomes the command's failure, with the
record in the JSON details (6.3) and its exit code by 6.4 and 6.5.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .bridge_client import CliBridgeError
from .exit_codes import CANCELLED, SUCCESS, record_exit
from .interrupts import Interrupts
from .output import CliFailure, sentence
from .waiter import Waiter, end_line
from .wording import READ_CANCELLED

# The JSON code of a record that ended without an error object of its own
STATE_CODES = {"CANCELLED": "CANCELLED", "RECOVERY_REQUIRED": "RECOVERY_REQUIRED"}


def stop_if_interrupted(interrupts: Interrupts, text: str = READ_CANCELLED) -> None:
    """Exit 5 before the next step when Ctrl+C was pressed; the step before it has finished."""
    if interrupts.take():
        raise CliFailure("CANCELLED", sentence(text), CANCELLED)


def run_operation(context: Any, method: str, kind: str, *, restart_backup: bool = False,
                  step_text: str = READ_CANCELLED, waiter: Waiter | None = None,
                  **parameters: Any) -> Mapping[str, Any]:
    """Submit one lane operation, wait for its end state and return the record when it succeeded.

    Ctrl+C before the submit exits 5 with nothing submitted. A refusal at dispatch exits by
    6.4; an end other than succeeded raises the failure of `operation_failure`. `restart_backup`
    picks the first-change marker of a restart with a backup (6.5); `parameters` are the bridge's.
    """
    stop_if_interrupted(context.interrupts, step_text)
    try:
        handle = context.call(method, **parameters)
    except CliBridgeError as error:
        raise error.failure(kind) from error
    waiting = waiter or Waiter(context.call, context.output, context.interrupts)
    record = waiting.wait(str(handle["operation_id"]), kind)
    exit_code = record_exit(record, backup_after_stop=restart_backup)
    if exit_code != SUCCESS:
        raise operation_failure(record, kind, exit_code)
    return record


def operation_failure(record: Mapping[str, Any], kind: str, exit_code: int) -> CliFailure:
    """Return the failure of a record that ended failed, cancelled or recovery-required (6.3, 6.4)."""
    state = str(record.get("state"))
    error = record.get("terminal_error") if isinstance(record.get("terminal_error"), Mapping) else {}
    code = STATE_CODES.get(state) if state == "CANCELLED" else (error.get("code") or STATE_CODES.get(state))
    return CliFailure(str(code or "INTERNAL_FAILURE"), end_line(record, kind), exit_code,
                      bool(error.get("retryable")), {"operation": dict(record)})
