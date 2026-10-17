"""`map save` and `map set-aside`: the owner commands of the saved map plan (leaf 10.15, design D11 to D14).

`map save` asks nothing, as the autosave of the window (PO-5). `map set-aside` shows the state of the invalid
plan and the 5.3 message of the window, then asks (D14).

Both read the plan first. When the state refuses the save or the set aside, the command exits 3 before it sends
anything. It prints the window text and the CLI line of `map show` for that state. A closing line says that
nothing was changed. The same refusal from the bridge gets the same words (Product Owner 2026-10-16 16:25:27
in evidence 10.15).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any

from .. import map_wording as words
from ..bridge_client import CliBridgeError
from ..exit_codes import REFUSED
from ..flow import stop_if_interrupted
from ..map_input import read_plan_object
from ..output import Block, CliFailure, CommandResult, Line, TypeText, Value, sentence
from ..wording import NOTHING_CHANGED, missing_mission, revision_pinned
from .common import labelled, profile_id, profile_line, profile_name, read_data
from .write_common import ask

# The error code of each state refusal (D13); the other read-only reasons are PROFILE_CONTEXT_MISMATCH
STATE_CODES = {"invalid_saved_data": "REVISION_CONFLICT", "plan_not_invalid": "REVISION_CONFLICT",
               "stale_revision": "REVISION_CONFLICT"}
# Reasons of the bridge that this module words itself; the other read-only reasons are in READ_ONLY_TEXTS
OWN_REASONS = frozenset(("plan_not_invalid", "stale_revision"))


def plan_document(_call: Callable[..., Any], options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `map save`: the plan document of `--from-file`, read before the instance lock (D17)."""
    return {"plan": read_plan_object(options.plan_file)}


def save(context: Any) -> CommandResult:
    """Save the plan of the file over the saved plan, at the revision that the command read or pinned."""
    loaded = _load(context)
    if loaded.get("read_only"):
        reason = str(loaded.get("read_only_reason"))
        raise _state_refusal(reason, {"reason": reason})
    revision = loaded.get("revision")
    pinned = context.options.plan_revision
    # QF-45: a pin that differs exits 3 before the save; without a pin, the revision of the read is sent
    if pinned is not None and pinned != revision:
        # A missing plan has no revision, so no pin can match it
        message = (words.typed_show(words.PIN_WITHOUT_PLAN.format(pinned=pinned)) if revision is None
                   else revision_pinned(words.PLAN_SUBJECT, revision, pinned))
        raise CliFailure("REVISION_CONFLICT", message, REFUSED, True,
                         {"revision": words.PLAN_SUBJECT, "expected": pinned, "current": revision})
    # Ctrl+C before the call exits 5, and nothing is sent (D15)
    stop_if_interrupted(context.interrupts)
    # The background path stays null (F5): only the image dialog of the window names a path
    saved = _submit(lambda: context.call("save_mission_map_plan", profile_id=profile_id(context.profile),
                                         plan=context.resolved["plan"], expected_revision=revision,
                                         background_selection_id=None))
    text = words.SAVED.split("{n}")
    return CommandResult({"operations": [], "review": None, "result": saved},
                         [sentence(text[0], Value(str(saved.get("revision"))), text[1])])


def set_aside(context: Any) -> CommandResult:
    """Keep the invalid saved plan under its dated name after the review and the question; a new plan starts."""
    loaded = _load(context)
    reason = loaded.get("read_only_reason")
    # The window offers set aside only for invalid saved data without a path change (5.3); another read-only
    # state refuses with its own text (Product Owner 2026-10-16 16:25:27, point 1)
    if loaded.get("read_only") and reason != "invalid_saved_data":
        raise _state_refusal(str(reason), {"reason": reason})
    if reason != "invalid_saved_data" or loaded.get("path_change"):
        raise _state_refusal("plan_not_invalid", {"reason": "plan_not_invalid"})
    ask(context, review_blocks(context.profile, loaded), loaded)
    answer = _submit(lambda: context.call("set_aside_mission_map_plan", profile_id=profile_id(context.profile),
                                          mission_key=loaded["association"]["mission_key"]))
    done = words.SET_ASIDE_DONE.split("{name}")
    line = sentence(done[0], Value(str(answer.get("set_aside"))), f"{done[1]} {words.SET_ASIDE_OPEN}")
    return CommandResult({"operations": [], "review": loaded, "result": answer}, [line])


def review_blocks(profile: Mapping[str, Any] | None, loaded: Mapping[str, Any]) -> list[Block]:
    """Return the review of set aside: profile, mission, state detail, the load problems, then the window message."""
    state = str(loaded.get("state"))
    # The window detail: its own sentence for the other-association state, else the backend detail
    detail = words.PLAN_STATES["OTHER_ASSOCIATION"] if state == "OTHER_ASSOCIATION" else loaded.get("detail")
    blocks: list[Block] = [profile_line(profile), labelled(words.MISSION_LABEL, str(loaded.get("mission_name", "")))]
    blocks += [sentence(Value(str(detail)))] if detail else []
    messages = [str(problem.get("message")) for problem in loaded.get("problems") or []
                if isinstance(problem, Mapping) and problem.get("message")]
    # The detail is the first problem of an invalid plan; it shows once
    blocks.extend(sentence("  ", Value(message)) for message in messages if message != detail)
    return [*blocks, sentence(words.SET_ASIDE_MESSAGE)]


def _load(context: Any) -> Mapping[str, Any]:
    """Read the saved plan and its state as `map show` does; a profile or mission that is gone exits 1."""
    return read_data(lambda: context.call("load_mission_map", profile_id=profile_id(context.profile)),
                     lambda: missing_mission(profile_name(context.profile)))


def _submit(call: Callable[[], Any]) -> Any:
    """Send the save or the set aside; a state refusal of the bridge gets the words of the same state."""
    try:
        return call()
    except CliBridgeError as error:
        details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
        reason = details.get("reason")
        if reason in OWN_REASONS or reason in words.READ_ONLY_TEXTS:
            raise _state_refusal(str(reason), details, error.code) from error
        raise


def _state_refusal(reason: str, details: Mapping[str, Any], code: str | None = None) -> CliFailure:
    """Return the exit 3 refusal of a plan state: the window text, the CLI line that names a command, the close."""
    if reason == "plan_not_invalid":
        message = sentence(f"{words.SET_ASIDE_NOT_INVALID} Run ", TypeText("map show"), ".")
    elif reason == "stale_revision":
        message = words.typed_show(words.STALE_PLAN)
    else:
        # A read-only state: the window text, its CLI line, then "Nothing was changed."
        text = words.READ_ONLY_TEXTS.get(reason, words.READ_ONLY_FALLBACK)
        way_out = words.read_only_way_out(reason) or Line(())
        message = sentence(f"{text} ", *way_out.parts, f" {NOTHING_CHANGED}") if way_out.parts else sentence(
            f"{text} {NOTHING_CHANGED}")
    return CliFailure(code or STATE_CODES.get(reason, "PROFILE_CONTEXT_MISMATCH"), message, REFUSED,
                      details=dict(details))
