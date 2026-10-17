"""`map adopt`: the reviewed adoption of a moved, copied or restored mission (leaf 10.36, D11 to D15).

The order is that of D14. First the recovery gate, then the adoption review and its refusals. Then the review goes
to stdout with the question or `--yes`. Last comes one lane operation of the kind `ADOPT_MISSION_MAP`. Adoption
writes only manager records, so it needs no stopped server (AR-3).

The end line is the kind success line, then the ADOPT-8 line of the case (Product Owner 2026-10-16 14:26:38 in
evidence 10.1). The stale and committed-failure texts are the kind rows of the catalogues.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.mission_map_adoption import ADOPT_LANE_KIND
from .. import map_wording
from ..bridge_client import CliBridgeError
from ..exit_codes import REFUSED
from ..flow import run_operation, stop_if_interrupted
from ..map_adopt_render import adopt_blocks
from ..output import CliFailure, CommandResult, Value, sentence
from ..waiter import end_line
from .common import profile_id
from .map_publish import state_refusal
from .write_common import ask, check_recovery_block, write_result


def adopt(context: Any) -> CommandResult:
    """Adopt the mission of the profile, after the review and the question."""
    stop_if_interrupted(context.interrupts)
    # A recovery block that the snapshot shows exits 6 before any review (rule 5)
    check_recovery_block(context.call("get_application_snapshot"))
    answer = _review(context)
    _check_refusals(answer)
    ask(context, adopt_blocks(context.profile, answer), answer)
    record = run_operation(context, "adopt_mission_map", ADOPT_LANE_KIND, profile_id=profile_id(context.profile),
                           preview_fingerprint=answer.get("fingerprint"))
    line = end_line(record, ADOPT_LANE_KIND)
    success = answer.get("success")
    if isinstance(success, str) and success:
        line = sentence(*line.parts, " ", Value(success))
    return write_result(record, answer, [line])


def _review(context: Any) -> Mapping[str, Any]:
    """Read the adoption review; a state of the records that names no adoption exits 3 with the words of `map show`."""
    # Ctrl+C before the call exits 5, and nothing is sent (D15)
    stop_if_interrupted(context.interrupts)
    try:
        return context.call("preview_mission_map_adoption", profile_id=profile_id(context.profile))
    except CliBridgeError as error:
        details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
        reason = details.get("reason")
        if reason in map_wording.READ_ONLY_TEXTS and reason not in map_wording.ADOPTION_REASONS:
            raise state_refusal(str(reason), error.code, changes=True) from error
        raise error.failure(ADOPT_LANE_KIND) from error


def _check_refusals(answer: Mapping[str, Any]) -> None:
    """Exit 3 before the question for a refused review: the first refusal text unchanged, with its reason (D13)."""
    refusals = [item for item in answer.get("refusals") or [] if isinstance(item, Mapping)]
    if answer.get("refused") is True and refusals:
        first = refusals[0]
        raise CliFailure("INVALID_REQUEST", sentence(Value(str(first.get("message")))), REFUSED,
                         details={"reason": first.get("reason"), "path": first.get("file")})
