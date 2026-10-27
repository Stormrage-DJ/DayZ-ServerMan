"""`map export`, `map import` and `map copy`: the saved map plan to and from a file or another profile (D11 to D17).

`map export` is an observer read that asks nothing (PO-4). The bridge method returns the text and takes no
path. The CLI checks the output path before the call and writes the file after it, through `map_files.py`.

`map import` is an owner command with a review (D14). The pre-step checks `--mode` and reads `--from-file`
through `map_input.py` before the instance lock. The import preview gives the D7 report.

Listed objects, unmapped items and a placement review need `--confirm-listed`; `--yes` never confirms them
(PO-3). The import sends the revision of its preview, or the `--expect-revision` pin.

`map copy` (leaf 10.7) takes the same steps. Its pre-step resolves `--from-profile` as `--profile` is resolved,
and the same profile exits 2. The service reads only the active association of the source profile, and the
source plan stays unchanged (R18).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...application.mission_map_transfer_coordinator import (
    CONFIRM_LISTED_COPY_TEXT, CONFIRM_LISTED_TEXT, INVALID_MODE_TEXT, MODES, REFUSAL_TEXTS,
)

from .. import map_wording as words
from ..bridge_client import CliBridgeError
from ..exit_codes import FAILED, NOT_CONFIRMED, REFUSED, USAGE, dispatch_exit
from ..flow import stop_if_interrupted
from ..map_files import check_output, write_export
from ..map_input import read_input_text
from ..map_publish_wording import filled
from ..map_transfer_render import transfer_blocks
from ..output import CliFailure, CommandResult, Line, Value, sentence
from ..profiles import resolve_profile
from ..registry import ProfileRule
from ..wording import missing_mission, revision_pinned
from .common import profile_id, profile_name, read_data, with_profile
from .write_common import ask

# Refusals of the transfer methods whose bridge text the CLI prints with its command words as text to type
TRANSFER_REASONS = frozenset(("no_valid_plan", "mission_path_changed", "plan_limit", "confirmation_required",
                              "invalid_mode", "source_is_destination"))


def export(context: Any) -> CommandResult:
    """Write the portable form of the saved plan to the file of `--output`, and nothing else."""
    overwrite = bool(context.options.overwrite_output)
    # The path refusals (exit 2) and an existing file (exit 4) come before the bridge call
    target = check_output(context.options.output_file, protected_roots(context.session), overwrite)
    exported = _export(context)
    # Ctrl+C during the call: nothing is written, and the command ends cancelled (exit 5)
    stop_if_interrupted(context.interrupts)
    written = write_export(target, str(exported.get("text")), overwrite)
    value = with_profile({"output": str(written.path), "size": written.size, "sha256": written.sha256,
                          "revision": exported.get("revision")}, context.profile)
    before, after = words.EXPORT_WRITTEN.split("{path}")
    return CommandResult(value, [sentence(before, Value(str(written.path)), after)])


def protected_roots(session: Any) -> tuple[Path, ...]:
    """Return the roots that an export never writes into: the manager root, the backup folder and the DayZ root.

    The manager root holds config, data, logs and the default backup folder (DC-3). The settings of this
    session name the backup folder and the DayZ root; a DayZ root that is not set protects nothing more.
    """
    settings = session.composition.settings
    current = settings.load()
    roots = [Path(session.paths.root), Path(settings.backup_root(current))]
    if current.dayz_root is not None:
        roots.append(Path(current.dayz_root))
    return tuple(roots)


def _export(context: Any) -> Mapping[str, Any]:
    """Read the export text; a plan that is not valid exits 3 with the bridge text, "map show" as text to type."""
    try:
        return read_data(lambda: context.call("export_mission_map_plan", profile_id=profile_id(context.profile)),
                         lambda: missing_mission(profile_name(context.profile)))
    except CliBridgeError as error:
        details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
        if details.get("reason") == "no_valid_plan":
            raise CliFailure(error.code, words.typed_show(str(error.error.get("message", ""))), REFUSED,
                             details=dict(details)) from error
        raise


def transfer_input(_call: Callable[..., Any], options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `map import`: the mode, then the text of `--from-file`, both before the instance lock (D17)."""
    if options.transfer_mode not in MODES:
        raise CliFailure("USAGE", words.typed(INVALID_MODE_TEXT), USAGE, details={"reason": "invalid_mode"})
    return {"text": read_input_text(options.plan_file), "mode": options.transfer_mode}


def copy_input(call: Callable[..., Any], options: argparse.Namespace, owner: str | None) -> dict[str, Any]:
    """Pre-step of `map copy`: the mode, then `--from-profile` as `--profile` resolves; the same profile exits 2."""
    if options.transfer_mode not in MODES:
        raise CliFailure("USAGE", words.typed(INVALID_MODE_TEXT), USAGE, details={"reason": "invalid_mode"})
    source_id = profile_id(resolve_profile(call, ProfileRule.WRITE_REQUIRED, options.source_profile))
    if source_id == owner:
        raise CliFailure("USAGE", words.typed(REFUSAL_TEXTS[("source_is_destination", "source")]), USAGE,
                         details={"reason": "source_is_destination"})
    return {"source_profile_id": source_id, "mode": options.transfer_mode}


def import_plan(context: Any) -> CommandResult:
    """Import the plan of the file with Replace or Merge, after the review and the question (D14)."""
    return _reviewed(context, ("preview_mission_map_import", "import_mission_map_plan"),
                     {"text": context.resolved["text"]}, CONFIRM_LISTED_TEXT, words.IMPORTED, words.IMPORT_LABELS)


def copy_plan(context: Any) -> CommandResult:
    """Copy the active plan of another profile with Replace or Merge, after the review and the question (D14)."""
    return _reviewed(context, ("preview_mission_map_copy", "copy_mission_map_plan"),
                     {"source_profile_id": context.resolved["source_profile_id"]}, CONFIRM_LISTED_COPY_TEXT,
                     words.COPIED, words.COPY_LABELS)


def _reviewed(context: Any, methods: tuple[str, str], what: Mapping[str, Any], confirm_text: str,
              done_text: str, labels: Mapping[str, str]) -> CommandResult:
    """Run the preview, the checks, the review and the question, then the one revision-guarded save."""
    preview_method, commit_method = methods
    common = {"profile_id": profile_id(context.profile), "mode": context.resolved["mode"], **what}
    stop_if_interrupted(context.interrupts)
    report = _transfer_call(lambda: context.call(preview_method, **common), context)
    revision = _checked(context, report)
    blocks = transfer_blocks(context.profile, report, labels)
    if report.get("confirmation_required") and not context.options.confirm_listed:
        # The review shows first, so the refusal can point at the list above (PO-3, also with --yes)
        context.output.show(blocks)
        raise CliFailure("CONFIRMATION_REQUIRED", words.typed(confirm_text), NOT_CONFIRMED,
                         details={"reason": "confirmation_required", "review": report})
    ask(context, blocks, report)
    stop_if_interrupted(context.interrupts)
    # D14: `confirmed` is true only for a report that needed --confirm-listed, which the check above required
    confirmed = bool(report.get("confirmation_required"))
    saved = _transfer_call(lambda: context.call(commit_method, **common, expected_revision=revision,
                                                confirmed=confirmed), context)
    result = {"revision": saved.get("revision"), "report": saved.get("report")}
    return CommandResult({"operations": [], "review": report, "result": result}, [_done(done_text, saved)])


def _checked(context: Any, report: Mapping[str, Any]) -> Any:
    """Return the revision to send; a plan-level limit or a pin that differs exits 3 before the question."""
    if report.get("refusal") is not None:
        message = REFUSAL_TEXTS[("plan_limit", "destination")].format(
            objects=report.get("result_object_count"), vertices=report.get("result_vertex_count"))
        raise CliFailure("INVALID_REQUEST", sentence(message), REFUSED, details={"reason": "plan_limit"})
    revision, pinned = report.get("destination_revision"), context.options.plan_revision
    # QF-45: a pin that differs exits 3; without a pin the revision of the preview is sent
    if pinned is not None and pinned != revision:
        raise CliFailure("REVISION_CONFLICT", revision_pinned(words.PLAN_SUBJECT, revision, pinned), REFUSED, True,
                         {"revision": words.PLAN_SUBJECT, "expected": pinned, "current": revision})
    return revision


def _done(text: str, saved: Mapping[str, Any]) -> Line:
    """Return the success line with the new revision; objects that were left out add their count."""
    line = filled(text, n=str(saved.get("revision")))
    report = saved.get("report") if isinstance(saved.get("report"), Mapping) else {}
    count = report.get("excluded_count") or 0
    if count:
        left_out = sentence(words.LEFT_OUT_ONE) if count == 1 else filled(words.LEFT_OUT, count=str(count))
        line = sentence(*line.parts, " ", *left_out.parts)
    return line


def _transfer_call(call: Callable[[], Any], context: Any) -> Any:
    """Run one transfer call; its refusals get the bridge texts with the command words as text to type."""
    try:
        return read_data(call, lambda: missing_mission(profile_name(context.profile)))
    except CliBridgeError as error:
        raise _refusal(error) from error


def _refusal(error: CliBridgeError) -> Exception:
    """Return the CLI failure of a transfer refusal; a refusal of another kind keeps the bridge wording."""
    details = error.error.get("details") if isinstance(error.error.get("details"), Mapping) else {}
    reason = details.get("reason")
    message = str(error.error.get("message", ""))
    if reason == "stale_revision":
        line = words.typed_show(words.STALE_PLAN)
    elif reason == "invalid_document":
        problems = [str(item.get("message")) for item in details.get("problems") or [] if isinstance(item, Mapping)]
        # Each problem in its own row, as the answer states it
        line = sentence(message, *(part for problem in problems for part in ("\n  ", Value(problem))))
    elif reason in TRANSFER_REASONS:
        line = words.typed(message)
    else:
        return error
    exit_code = dispatch_exit(error.error, names_confirmed=error.names_confirmed)
    return CliFailure(error.code, line, FAILED if exit_code is None else exit_code,
                      bool(error.error.get("retryable")), dict(details))
