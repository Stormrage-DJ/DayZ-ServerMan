"""The Inspect driver of `map inspect` (leaf 10.4, design D15): start, then continue until the answer is done.

Inspect is not a lane operation, so the waiter does not apply. Each continue call is one read slice,
so the driver does not sleep between calls. Progress goes to stderr; only the final answer is returned.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from typing import Any

from ..adapters.windows.console import is_terminal
from .bridge_client import CliBridgeError
from .exit_codes import CANCELLED, FAILED, REFUSED
from .map_inspect_wording import (
    CHANGED_WHILE_READING, READ_STOPPED, READING, READING_PERCENT, STALE_RESTART,
)
from .output import CliFailure, Output, TypeText, sentence
from .wording import READ_CANCELLED

# A stale answer starts the read again at most this often; the next one is the refusal
STALE_RESTARTS = 2
# A lost job starts the read again once; the next one is the failure of the window (T7)
UNKNOWN_RESTARTS = 1
# The command that the closing lines of a failed read tell the operator to run again
COMMAND = "map inspect"


def run_inspection(context: Any, profile_id: str | None, target: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drive one read to its end and return `done.result`; refusals and Ctrl+C end as the command's failure."""
    output: Output = context.output
    terminal = is_terminal(output.stderr)
    stale = lost = 0
    job_id: str | None = None
    shown: object = None
    while True:
        try:
            if job_id is None:
                answer = context.call("inspect_mission_map", profile_id=profile_id, plan=None, target=dict(target))
            else:
                answer = context.call("continue_mission_map_inspection", profile_id=profile_id, job_id=job_id)
        except CliBridgeError as error:
            if (error.error.get("details") or {}).get("reason") != "inspection_unknown":
                raise
            lost += 1
            if lost > UNKNOWN_RESTARTS:
                raise _stopped() from error
            job_id, shown = None, None
            continue
        status = answer.get("status")
        if status == "done":
            output.clear_progress()
            return answer["result"]
        if status == "stale":
            # The backend retired the job: no cancel is needed
            stale += 1
            job_id, shown = None, None
            if stale > STALE_RESTARTS:
                raise _changed()
            _stop_if_interrupted(context)
            output.note((sentence(STALE_RESTART),))
            continue
        job = answer["job"]
        job_id = str(job["id"])
        shown = _show_progress(output, terminal, job, shown)
        if context.interrupts.take():
            _cancel(context, profile_id, job_id)


def _show_progress(output: Output, terminal: bool, job: Mapping[str, Any], shown: object) -> object:
    """Write the progress of a running job; off a terminal only a change of the phase writes a line."""
    phase, done, total = job.get("phase"), job.get("rows_done"), job.get("rows_total")
    percent = None
    if phase == "sampling" and isinstance(done, int) and isinstance(total, int) and total > 0:
        # The window shows at most 99 until the result arrives
        percent = min(99, done * 100 // total)
    text = READING if percent is None else READING_PERCENT.format(percent=percent)
    if terminal:
        output.progress(sentence(text), rewrite=True, width=shutil.get_terminal_size((80, 24)).columns)
    elif phase != shown:
        output.progress(sentence(text), rewrite=False)
    return phase


def _stop_if_interrupted(context: Any) -> None:
    """Exit 5 when Ctrl+C was pressed while no job is live."""
    if context.interrupts.take():
        context.output.clear_progress()
        raise CliFailure("CANCELLED", sentence(READ_CANCELLED), CANCELLED)


def _cancel(context: Any, profile_id: str | None, job_id: str) -> None:
    """Retire the live job after Ctrl+C and end the command cancelled; a refused cancel changes nothing."""
    try:
        context.call("cancel_mission_map_inspection", profile_id=profile_id, job_id=job_id)
    except CliBridgeError:
        pass
    context.output.clear_progress()
    raise CliFailure("CANCELLED", sentence(READ_CANCELLED), CANCELLED)


def _changed() -> CliFailure:
    """Return the refusal after the third stale answer: the mission files kept changing (exit 3)."""
    before, after = CHANGED_WHILE_READING.split("{command}")
    return CliFailure("SOURCE_CHANGED", sentence(before, TypeText(COMMAND), after), REFUSED, True,
                      {"reason": "changed_while_reading"})


def _stopped() -> CliFailure:
    """Return the failure after the second lost job: the read stopped (exit 1, T7 of the window)."""
    return CliFailure("INVALID_REQUEST", sentence(f"{READ_STOPPED} Run ", TypeText(COMMAND), " again."), FAILED, True,
                      {"reason": "inspection_unknown"})
