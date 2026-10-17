"""`map restore original|previous` and `map recover`: the reviewed restore and the editor recovery (leaf 10.32).

`map restore` follows the order of D14. First the recovery gate, then the review and its state refusals. Then the
review goes to stdout with the question or `--yes`. Last comes one lane operation of the kind of the mode. The
8.8 refusal, stale-review and late-change texts print unchanged, with the existing exit codes.

`map recover` is the Check again of the window: one synchronous call, no question. It exits as `backup recover`
does, and it prints the window texts 6a and 6b (PO-7).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any

from ...application.activity_wording import KIND_TEXTS, block_reason_text
from ...application.mission_map_recovery import EDITOR_KIND, RECOVERY_NOT_STOPPED
from ...domain.mission_map_restore_texts import INVALID_MODE, INVALID_MODE_TEXT, LANE_KINDS, ORIGINAL, RECORD_KINDS
from .. import map_restore_wording as words
from .. import map_wording
from ..bridge_client import CliBridgeError
from ..exit_codes import RECOVERY, REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..map_restore_render import restore_blocks
from ..output import CliFailure, CommandResult, Line, TypeText, Value, sentence
from ..waiter import end_line
from ..wording import way_out
from .common import profile_id
from .map_publish import state_refusal
from .write_common import ask, check_recovery_block, write_result


def mode(_call: Callable[..., Any], options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `map restore`: the mode is original or previous, else exit 2 with the 8.8 text and the CLI line."""
    if options.restore_mode not in LANE_KINDS:
        original, previous = words.INVALID_MODE_COMMANDS
        head, rest = words.INVALID_MODE_LINE.split("{original}")
        middle, tail = rest.split("{previous}")
        line = sentence(Value(INVALID_MODE_TEXT), f" {head}", TypeText(original), middle, TypeText(previous), tail)
        raise CliFailure("USAGE", line, USAGE, details={"reason": INVALID_MODE})
    return {"restore_mode": options.restore_mode}


def restore(context: Any) -> CommandResult:
    """Restore the original or the previous state of the mission files, after the review and the question."""
    chosen = str(context.resolved["restore_mode"])
    kind = LANE_KINDS[chosen]
    stop_if_interrupted(context.interrupts)
    # A recovery block that the snapshot shows exits 6 before any review (rule 5)
    check_recovery_block(context.call("get_application_snapshot"))
    answer = _review(context, chosen, kind)
    _check_state(answer)
    ask(context, restore_blocks(context.profile, chosen, answer), answer)
    try:
        record = run_operation(context, "restore_mission_map", kind, profile_id=profile_id(context.profile),
                               mode=chosen, preview_fingerprint=answer.get("fingerprint"))
    except CliFailure as failure:
        raise late_change(failure, kind) from None
    return write_result(record, answer, [end_line(record, kind)])


def recover(context: Any) -> CommandResult:
    """Finish or undo an interrupted mission map change, as Check again of the window does (8.5, PO-7).

    No block left: exit 0. Deferred, or the server is not proven stopped: exit 3 (nothing was changed). Any other
    block: exit 6 with the block reason of the editor kind; a person must act.
    """
    stop_if_interrupted(context.interrupts)
    value = context.call("inspect_mission_map_recovery")
    if not value.get("blocked"):
        return CommandResult({"operations": [], "review": None, "result": value}, [sentence(words.CLEARED)])
    details = {"recovery": value}
    if value.get("deferred"):
        raise CliFailure("CONTROL_CONFLICT", sentence(words.DEFERRED), REFUSED, True, details)
    reason = value.get("reason")
    text = way_out(block_reason_text(reason, EDITOR_KIND))
    if reason == RECOVERY_NOT_STOPPED:
        raise CliFailure("CONTROL_CONFLICT", text, REFUSED, True, details)
    raise CliFailure("RECOVERY_REQUIRED", text, RECOVERY, False, details)


def _review(context: Any, chosen: str, kind: str) -> Mapping[str, Any]:
    """Read the restore review of the mode; a read-only state exits 3 with the words of `map show`."""
    # Ctrl+C before the call exits 5, and nothing is sent (D15)
    stop_if_interrupted(context.interrupts)
    try:
        return context.call("preview_mission_map_restore", profile_id=profile_id(context.profile), mode=chosen)
    except CliBridgeError as error:
        details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
        if details.get("reason") in map_wording.READ_ONLY_TEXTS:
            raise state_refusal(str(details["reason"]), error.code, changes=True) from error
        # The texts of the lane kind: the 8.8 refusal texts show unchanged (D12)
        raise error.failure(kind) from error


def _check_state(answer: Mapping[str, Any]) -> None:
    """Exit 3 before the question for an unverified family (6.11) or a refused review, as the lane would refuse."""
    family = answer.get("family") if isinstance(answer.get("family"), Mapping) else {}
    if family.get("verified") is False:
        # The 6.11 gate text unchanged, with the details of the lane refusal (Architect 2026-10-14 17:28:16)
        raise CliFailure("UNSUPPORTED_SNAPSHOT_CONTENT", sentence(Value(str(family.get("text")))), REFUSED,
                         details={"reason": family.get("reason"), "operation": RECORD_KINDS[ORIGINAL],
                                  "terrain_id": family.get("terrain_id")})
    refusals = [item for item in answer.get("refusals") or [] if isinstance(item, Mapping)]
    if answer.get("refused") is True and refusals:
        # The first 8.8 refusal names the reason: no original, no previous state or an unprojectable file
        first = refusals[0]
        raise CliFailure("INVALID_REQUEST", sentence(Value(str(first.get("message")))), REFUSED,
                         details={"reason": first.get("reason"), "path": first.get("file")})


def late_change(failure: CliFailure, kind: str) -> CliFailure:
    """Return the failure of a restore that a late file change stopped (D13).

    The text is the kind failure line, then the host text that names the file (Product Owner 2026-10-15
    07:10:16, section 2). Every other failure stays as it is, and the exit code never changes.
    """
    operation = (failure.details or {}).get("operation")
    error = operation.get("terminal_error") if isinstance(operation, Mapping) else None
    message = error.get("message") if isinstance(error, Mapping) else None
    if failure.code != "REVISION_CONFLICT" or not str(message or "").startswith(words.LATE_CHANGE_START):
        return failure
    line: Line = sentence(f"{KIND_TEXTS[kind][2]} ", Value(str(message)))
    return CliFailure(failure.code, line, failure.exit_code, failure.retryable, failure.details, failure.notes)
