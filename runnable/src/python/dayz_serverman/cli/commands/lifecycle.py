"""`server start [--wait-ready]`, `server stop` and `server restart` (10.1, criteria 5, 6, 10, 12, 15, 25, 26).

Each command reads the revisions it sends, shows the review of the Overview dialog, asks
(or takes `--yes`), submits one lane operation through dispatch and waits for its end state.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from ...application.phase_wording import server_status_text
from ..confirm import confirm
from ..exit_codes import FAILED, REFUSED
from ..flow import run_operation, stop_if_interrupted
from ..waiter import Waiter, WaitState
from ..output import Block, CliFailure, CommandResult, sentence
from ..review import lifecycle_review
from ..wording import bridge_error_line, not_ready, other_profile_running, ready_wait_stopped, server_left_running
from .common import labelled, profile_id, profile_name
from .lifecycle_gate import check_state

# Readiness wait of `--wait-ready` (fixed values, section 7)
READY_POLL_SECONDS = 2.0
READY_DEFAULT_SECONDS = 300
# Seams of the readiness wait; tests replace them
SLEEP = time.sleep
CLOCK = time.monotonic


def start(context: Any) -> CommandResult:
    """Start the profile's server; with `--wait-ready` also wait until it answers as ready."""
    profile_revision, settings_revision = revisions(context, "start")
    blocks, review = lifecycle_review("start", context.profile)
    _ask(context, blocks, review)
    waiter = Waiter(context.call, context.output, context.interrupts)
    record = run_operation(context, "start_server", "START_SERVER", waiter=waiter,
                           profile_id=profile_id(context.profile), expected_profile_revision=profile_revision,
                           expected_settings_revision=settings_revision)
    status = record.get("result") or {}
    timeout = context.options.ready_timeout
    if not (context.options.wait_ready or timeout is not None):
        return _result(record, review, status)
    # Criterion 29: Ctrl+C during a start that still succeeded starts no readiness wait (exit 0)
    stopped = waiter.state is not WaitState.WAITING or context.interrupts.take()
    if not stopped:
        status, stopped = wait_ready(context, record, review, timeout or READY_DEFAULT_SECONDS)
    result = _result(record, review, status)
    if stopped:
        result.value["ready_wait_stopped"] = True
        result.blocks = [*result.blocks, ready_wait_stopped()]
    return result


def stop(context: Any) -> CommandResult:
    """Save and stop the server of the profile, with an optional backup after the stop."""
    return _control(context, "stop", "stop_server", "STOP_SERVER")


def restart(context: Any) -> CommandResult:
    """Save, stop and start the server of the profile, with an optional backup after the stop."""
    return _control(context, "restart", "restart_server", "RESTART_SERVER")


def _control(context: Any, action: str, method: str, kind: str) -> CommandResult:
    """Run a stop or restart: revisions, the backup choice, the review, the question, the operation."""
    profile_revision, settings_revision = revisions(context, action)
    backup = context.options.backup_after_stop
    if backup is None:
        # The default is the profile's saved choice, as the Overview's switch shows it
        saved = context.call("get_ui_preferences").get("backup_after_stop_profiles") or []
        backup = profile_id(context.profile) in saved
    blocks, review = lifecycle_review(action, context.profile, backup)
    _ask(context, blocks, review)
    try:
        record = run_operation(
            context, method, kind, restart_backup=backup, profile_id=profile_id(context.profile),
            expected_profile_revision=profile_revision, expected_settings_revision=settings_revision,
            backup_after_stop=backup,
        )
    except CliFailure as failure:
        raise _with_running_profile(context, failure) from failure
    return _result(record, review, record.get("result") or {})


def revisions(context: Any, action: str) -> tuple[int, int]:
    """Read the revisions that the write sends (`--expect-…` pins them), then gate on the state (criterion 28).

    Every refusal here comes before the question, so it exits 3 (or 6) with and without `--yes`.
    """
    stop_if_interrupted(context.interrupts)
    profile_revision = context.call("read_profile", profile_id=profile_id(context.profile)).get("revision")
    snapshot = context.call("get_application_snapshot")
    settings_revision = snapshot.get("settings", {}).get("revision")
    expected = ((context.options.expect_profile_revision, profile_revision),
                (context.options.expect_settings_revision, settings_revision))
    if any(pinned is not None and pinned != read for pinned, read in expected):
        raise CliFailure("REVISION_CONFLICT", bridge_error_line({"code": "REVISION_CONFLICT"}), REFUSED, True)
    check_state(context, action, snapshot, context.call("get_server_status"))
    return profile_revision, settings_revision


def wait_ready(context: Any, record: Mapping[str, Any], review: Any, limit: int) -> tuple[Mapping[str, Any], bool]:
    """Poll the server status every 2 s until it is ready; UNRESPONSIVE keeps waiting (section 7).

    Return the last status and whether the operator stopped the wait with Ctrl+C (criterion 29).
    """
    deadline = CLOCK() + limit
    while True:
        status = context.call("get_server_status")
        details = {"operation": dict(record), "review": review, "server": status}
        if status.get("state") != "RUNNING_MANAGED":
            raise CliFailure("NOT_READY", server_left_running(server_status_text(status)[0]), FAILED,
                             details=details)
        if status.get("readiness") == "READY":
            return status, False
        if context.interrupts.take():
            # The start succeeded; only the wait ends, and the server keeps running (exit 0)
            return status, True
        if CLOCK() >= deadline:
            raise CliFailure("NOT_READY", not_ready(limit), FAILED, details=details)
        SLEEP(READY_POLL_SECONDS)


def _ask(context: Any, blocks: list[Block], review: Any) -> None:
    """Ctrl+C during the reads exits 5; then the 8.1 question, `--yes` or a refusal with exit 4."""
    stop_if_interrupted(context.interrupts)
    confirm(context.output, context.interrupts, context.stdin, yes=context.options.yes, review=blocks,
            review_value=review)


def _with_running_profile(context: Any, failure: CliFailure) -> CliFailure:
    """Name the running profile in the D11 refusal (criteria 4 and 26); every other failure stays as it is."""
    # The D11 refusal is the one INVALID_REQUEST with exit 3 (6.4, the OTHER_PROFILE_RUNNING message)
    if failure.code != "INVALID_REQUEST" or failure.exit_code != REFUSED:
        return failure
    running = context.call("get_server_status").get("profile_id")
    profiles = [item for item in context.call("list_profiles") if isinstance(item, Mapping)]
    named = next((item for item in profiles if item.get("profile_id") == running), {"profile_id": running})
    return CliFailure(failure.code, other_profile_running(profile_name(named)), failure.exit_code, failure.retryable,
                      {**(failure.details or {}), "running_profile_id": running})


def _result(record: Mapping[str, Any], review: Any, status: Mapping[str, Any]) -> CommandResult:
    """Return the write value of 6.3 and the server state lines for stdout."""
    label, explanation = server_status_text(status)
    lines: list[Block] = [sentence(f"State: {label}"), sentence(explanation)]
    backup = status.get("backup") if isinstance(status.get("backup"), Mapping) else None
    if backup is not None and backup.get("backup_id"):
        lines.append(labelled("Backup", str(backup["backup_id"])))
    return CommandResult({"operations": [dict(record)], "review": review, "result": dict(status)}, lines)
