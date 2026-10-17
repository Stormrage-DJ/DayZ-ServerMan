"""`map preview` and `map apply`: the Apply review and the reviewed Apply of the saved map plan (leaf 10.5).

Both are owner commands (AR-1): `map preview` changes nothing, and it refuses while the window runs. Both read
the saved plan first, so the review names each written encounter from it (AR-9).

`map apply` follows the order of D14. First the recovery gate, then the review and its state refusals, then the
checks of the choice options. Then the review goes to stdout with the question or `--yes`. Last comes one lane
operation. `--yes` never makes a choice (PO-2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...application.activity_wording import KIND_TEXTS
from ...application.mission_map_apply_choices import CHOICE_ENCOUNTERS_TEXT, CHOICE_FILES_TEXT
from .. import map_publish_wording as words
from .. import map_wording
from ..bridge_client import CliBridgeError
from ..exit_codes import NOT_CONFIRMED, REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..map_review_render import review_blocks
from ..output import CliFailure, CommandResult, Echo, Line, Value, sentence
from ..waiter import end_line
from ..wording import NOTHING_CHANGED, missing_mission
from .common import profile_id, profile_name, read_data
from .write_common import ask, check_recovery_block, write_result

# The lane kind of Apply
APPLY_KIND = "APPLY_MISSION_MAP"
# The error code of each read-only reason of the load (D13); the others are PROFILE_CONTEXT_MISMATCH
STATE_CODES = {"invalid_saved_data": "REVISION_CONFLICT"}


def preview(context: Any) -> CommandResult:
    """Show what Apply would change; the JSON value is the 8.6 answer unchanged, with its fingerprint."""
    loaded, answer = _review(context, changes=False)
    return CommandResult(answer, review_blocks(context.profile, loaded, answer))


def apply(context: Any) -> CommandResult:
    """Apply the saved plan with the choices of the command line, after the review and the question."""
    stop_if_interrupted(context.interrupts)
    # A recovery block that the snapshot shows exits 6 before any review (rule 5)
    check_recovery_block(context.call("get_application_snapshot"))
    loaded, answer = _review(context, changes=True)
    _check_state(answer)
    choices = choices_of(context.options, answer)
    ask(context, review_blocks(context.profile, loaded, answer), answer)
    try:
        record = run_operation(context, "apply_mission_map", APPLY_KIND, profile_id=profile_id(context.profile),
                               preview_fingerprint=answer.get("fingerprint"), choices=choices)
    except CliFailure as failure:
        raise late_change(failure) from None
    return write_result(record, answer, [end_line(record, APPLY_KIND)])


def _review(context: Any, *, changes: bool) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Read the saved plan, then the Apply review; a read-only plan exits 3 with the words of `map show`.

    `changes` is true for `map apply`, whose refusal then ends with "Nothing was changed." (evidence 10.5).
    """
    stop_if_interrupted(context.interrupts)
    loaded = read_data(lambda: context.call("load_mission_map", profile_id=profile_id(context.profile)),
                       lambda: missing_mission(profile_name(context.profile)))
    if loaded.get("read_only"):
        raise state_refusal(str(loaded.get("read_only_reason")), changes=changes)
    # Ctrl+C before the call exits 5, and nothing is sent (D15)
    stop_if_interrupted(context.interrupts)
    try:
        answer = context.call("preview_mission_map", profile_id=profile_id(context.profile))
    except CliBridgeError as error:
        details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
        if details.get("reason") in map_wording.READ_ONLY_TEXTS:
            raise state_refusal(str(details["reason"]), error.code, changes=changes) from error
        # The texts of the lane kind: the no-plan text and the 6.11 text show unchanged (D12)
        raise error.failure(APPLY_KIND) from error
    return loaded, answer


def state_refusal(reason: str, code: str | None = None, *, changes: bool) -> CliFailure:
    """Return the exit 3 refusal of a read-only plan: the window text, then the CLI line of `map show`."""
    text = map_wording.READ_ONLY_TEXTS.get(reason, map_wording.READ_ONLY_FALLBACK)
    way_out = map_wording.read_only_way_out(reason)
    message = sentence(f"{text} ", *way_out.parts) if way_out is not None else sentence(text)
    if changes:
        message = sentence(*message.parts, f" {NOTHING_CHANGED}")
    return CliFailure(code or STATE_CODES.get(reason, "PROFILE_CONTEXT_MISMATCH"), message, REFUSED,
                      details={"reason": reason})


def _check_state(answer: Mapping[str, Any]) -> None:
    """Exit 3 before the question for an unverified family (6.11) or a refused review, as the lane would refuse."""
    capabilities = answer.get("capabilities") if isinstance(answer.get("capabilities"), Mapping) else {}
    family = capabilities.get("family") if isinstance(capabilities.get("family"), Mapping) else {}
    if family.get("verified") is False:
        # The 6.11 gate text unchanged, with the details of the lane refusal (Architect 2026-10-14 17:28:16)
        raise CliFailure("UNSUPPORTED_SNAPSHOT_CONTENT", sentence(Value(str(family.get("text")))), REFUSED,
                         details={"reason": family.get("reason"), "operation": "apply",
                                  "terrain_id": family.get("terrain_id")})
    refusals = [item for item in answer.get("refusals") or [] if isinstance(item, Mapping)]
    if answer.get("refused") is True and refusals:
        first = refusals[0]
        # The adoption line of `capture.adoption` follows the refusal (Product Owner 14:35:24 in evidence 10.1)
        capture = answer.get("capture") if isinstance(answer.get("capture"), Mapping) else {}
        adoption = map_wording.adoption_line(capture.get("adoption"), capture=True)
        extra = (" ", *adoption.parts) if adoption is not None else ()
        raise CliFailure("INVALID_REQUEST", sentence(Value(str(first.get("message"))), *extra), REFUSED,
                         details={"reason": "preview_refused", "path": first.get("file")})


def choices_of(options: Any, answer: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Return the Apply choices of the three options, checked against the review (D14).

    A path or ID that the review lacks, or a path in both file options, exits 2. Keep the change on a file
    where the review refuses it exits 3 with that text. A missing choice exits 4, also with `--yes`.
    """
    keep, replace = list(options.keep_change or []), list(options.replace_file or [])
    exclude = list(options.exclude_encounter or [])
    wanted = answer.get("choices") if isinstance(answer.get("choices"), Mapping) else {}
    files = [str(path) for path in wanted.get("external_changes") or []]
    encounters = [str(item) for item in wanted.get("unavailable_encounters") or []]
    for value, listed in [*((value, files) for value in keep + replace), *((value, encounters) for value in exclude)]:
        if value not in listed:
            raise CliFailure("USAGE", words.filled(words.NOT_IN_REVIEW, value=Echo(value)), USAGE)
    both = [path for path in keep if path in replace]
    if both:
        raise CliFailure("USAGE", words.filled(words.IN_BOTH, path=Echo(both[0])), USAGE)
    changes = {str(item.get("path")): item for item in answer.get("external_changes") or []
               if isinstance(item, Mapping)}
    for path in keep:
        incorporate = changes.get(path, {}).get("incorporate")
        message = incorporate.get("message") if isinstance(incorporate, Mapping) else None
        if message:
            raise CliFailure("INVALID_REQUEST", sentence(Value(str(message))), REFUSED,
                             details={"reason": "incorporation_refused", "path": path})
    _require_every_choice(files, keep + replace, encounters, exclude, answer)
    return {"external_changes": {**dict.fromkeys(keep, "incorporate"), **dict.fromkeys(replace, "replace")},
            "unavailable_encounters": dict.fromkeys(exclude, "exclude")}


def _require_every_choice(files: Sequence[str], chosen: Sequence[str], encounters: Sequence[str],
                          excluded: Sequence[str], answer: Mapping[str, Any]) -> None:
    """Exit 4 with the 8.7 text and the CLI line while a file or an encounter has no choice (PO-2)."""
    if set(files) - set(chosen):
        text = CHOICE_FILES_TEXT
    elif set(encounters) - set(excluded):
        text = CHOICE_ENCOUNTERS_TEXT
    else:
        return
    line = words.filled(words.MISSING_CHOICE)
    raise CliFailure("CONFIRMATION_REQUIRED", sentence(f"{text} ", *line.parts), NOT_CONFIRMED,
                     details={"reason": "choice_required", "review": answer})


def late_change(failure: CliFailure) -> CliFailure:
    """Return the failure of an Apply that a late file change stopped (D13).

    The text is the kind failure line, then the host text 7c with the file name. Every other failure stays as it
    is, and the exit code never changes.
    """
    operation = (failure.details or {}).get("operation")
    error = operation.get("terminal_error") if isinstance(operation, Mapping) else None
    message = error.get("message") if isinstance(error, Mapping) else None
    if failure.code != "REVISION_CONFLICT" or not str(message or "").startswith(words.LATE_CHANGE_START):
        return failure
    line: Line = sentence(f"{KIND_TEXTS[APPLY_KIND][2]} ", Value(str(message)))
    return CliFailure(failure.code, line, failure.exit_code, failure.retryable, failure.details, failure.notes)
