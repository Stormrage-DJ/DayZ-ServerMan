"""Waits for one lane operation: progress on stderr, Ctrl+C as a cancellation request, the end state (design 7).

Ctrl+C only sets a flag (6.7). The waiter turns it into a cancellation request and keeps
polling, so the process never ends while a guarded write runs; the end state wins.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from ..adapters.windows.console import is_terminal
from ..application.activity_wording import KIND_FALLBACK, KIND_TEXTS, error_text, restart_apply_text
from ..application.phase_wording import cancelling_text, phase_text
from .bridge_client import CliBridgeError
from .interrupts import Interrupts
from .output import Line, Output, sentence
from .wording import ALREADY_CANCELLING, CANCEL_REQUESTED, NOT_CANCELLABLE, SIGN_IN_PROMPT, way_out

# Poll interval of get_operation (fixed values)
POLL_SECONDS = 0.5
# The "cannot be cancelled" note is shown at most this often
NOT_CANCELLABLE_NOTE_SECONDS = 2.0
TERMINAL_STATES = frozenset(("SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"))
# The sign-in shares the console with SteamCMD: no progress line may cut into its questions
SIGN_IN_KIND = "AUTHENTICATE_STEAMCMD"
# The kind whose failed and cancelled ends have their own sentences by the last working phase
RESTART_APPLY_KIND = "APPLY_MODS_AND_RESTART"


class WaitState(str, Enum):
    """Where the waiter stands with Ctrl+C (design 7)."""

    WAITING = "WAITING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    NOT_CANCELLABLE = "NOT_CANCELLABLE"


class Waiter:
    """Polls one operation through dispatch until its terminal state and renders its progress."""

    def __init__(
        self, call: Callable[..., Any], output: Output, interrupts: Interrupts, *,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
        terminal: bool | None = None, width: Callable[[], int] | None = None,
        now_text: Callable[[], str] | None = None,
    ) -> None:
        """Bind the bridge call, the output, the Ctrl+C flag and the seams of time and terminal."""
        self._call = call
        self._output = output
        self._interrupts = interrupts
        self._sleep = sleep
        self._clock = clock
        # A terminal rewrites one line; otherwise one line per change of state or phase
        self._terminal = terminal if terminal is not None else is_terminal(output.stderr)
        self._width = width or (lambda: shutil.get_terminal_size((80, 24)).columns)
        self._now_text = now_text or (lambda: datetime.now().strftime("%H:%M:%S"))
        self.state = WaitState.WAITING

    def wait(self, operation_id: str, kind: str) -> Mapping[str, Any]:
        """Return the operation record at its terminal state; Ctrl+C requests cancellation, never abandons."""
        self.state = WaitState.WAITING
        last_note = float("-inf")
        shown: tuple[object, object] | None = None
        if kind == SIGN_IN_KIND:
            self._output.note((sentence(SIGN_IN_PROMPT),))
        while True:
            record = self._call("get_operation", operation_id=operation_id)
            if record.get("state") in TERMINAL_STATES:
                self._output.clear_progress()
                # A success ends with its sentence here; any other end is the command's failure text (flow.py)
                if record.get("state") == "SUCCEEDED":
                    self._output.note((end_line(record, kind),))
                return record
            if kind != SIGN_IN_KIND:
                shown = self._render(record, kind, shown)
            if self._interrupts.take():
                last_note = self._cancel(operation_id, last_note)
            self._sleep(POLL_SECONDS)

    def _render(self, record: Mapping[str, Any], kind: str, shown: tuple[object, object] | None
                ) -> tuple[object, object]:
        """Draw the progress line; off a terminal only when the state or the phase changed."""
        key = (record.get("state"), record.get("progress_phase"))
        text = progress_text(record, kind)
        if self._terminal:
            self._output.progress(sentence(text), rewrite=True, width=self._width())
        elif key != shown:
            self._output.progress(sentence(f"{self._now_text()} {_kind_name(kind)}: {_phase_part(record, kind)}"),
                                  rewrite=False)
        return key

    def _cancel(self, operation_id: str, last_note: float) -> float:
        """Turn one Ctrl+C into a cancellation request and say what follows; return the time of the last note."""
        if self.state is WaitState.CANCEL_REQUESTED:
            self._output.note((sentence(ALREADY_CANCELLING),))
            return last_note
        try:
            self._call("request_operation_cancellation", operation_id=operation_id)
        except CliBridgeError as error:
            if error.code == "OPERATION_NOT_CANCELLABLE":
                # A later Ctrl+C asks again: a later step may have a safe point
                self.state = WaitState.NOT_CANCELLABLE
                if self._clock() - last_note >= NOT_CANCELLABLE_NOTE_SECONDS:
                    self._output.note((sentence(NOT_CANCELLABLE),))
                    return self._clock()
                return last_note
            # Any other answer (the operation ended meanwhile) changes nothing: keep waiting for the end state
            return last_note
        self.state = WaitState.CANCEL_REQUESTED
        self._output.note((sentence(CANCEL_REQUESTED),))
        return last_note


def progress_text(record: Mapping[str, Any], kind: str) -> str:
    """Return the terminal line: "<kind name>: <phase text> <percent>%", the percent only when determinate."""
    return f"{_kind_name(kind)}: {_phase_part(record, kind)}"


def _phase_part(record: Mapping[str, Any], kind: str) -> str:
    """Return the phase text of a record, with its percent when the phase is determinate."""
    if record.get("state") == "CANCELLING":
        return cancelling_text(kind)
    text, determinate = phase_text(kind, record.get("progress_phase"))
    percent = record.get("progress_percent")
    return f"{text} {percent}%" if determinate and isinstance(percent, int) else text


def _kind_name(kind: str) -> str:
    """Return the operator name of an operation kind."""
    return KIND_TEXTS.get(kind, KIND_FALLBACK)[0]


def end_line(record: Mapping[str, Any], kind: str) -> Line:
    """Return the kind's success, failure or cancel sentence; a failure adds its error text (design 7)."""
    texts = KIND_TEXTS.get(kind, KIND_FALLBACK)
    state = record.get("state")
    error = record.get("terminal_error") if isinstance(record.get("terminal_error"), Mapping) else {}
    code, message = error.get("code"), error.get("message")
    special = (restart_apply_text(state, record.get("last_working_phase"), code, message)
               if kind == RESTART_APPLY_KIND and state != "SUCCEEDED" else None)
    if state == "SUCCEEDED":
        text = texts[1]
    elif special is not None:
        text = special
    elif state == "CANCELLED":
        text = texts[3]
    else:
        text = f"{texts[2]} {error_text(code, message, kind=kind)}"
    return way_out(text)
