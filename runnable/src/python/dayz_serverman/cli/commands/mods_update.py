"""`mods update [--start / --restart [--backup-after-stop / --no-backup-after-stop]]` (10.3, 8.1; criteria 15, 19, 24, 26).

One process runs the Mods page's flow: the download, the apply review, the question, then the
plain apply (with the start that the update recorded) or the apply and restart. The state gate
comes before the download (rule 5, criterion 26). Every refusal after the download ends with
"The mods are downloaded; nothing was applied." and reports the update operation (criterion 24, QF-16).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import KIND_TEXTS, error_text
from ..bridge_client import CliBridgeError
from ..confirm import confirm
from ..exit_codes import CANCELLED, FAILED, REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..mods_wording import (
    DOWNLOAD_RESULTS, UPDATE_TEXTS, backup_needs_restart, choose_sign_in, current_running, everything_current,
    outcome_text, sign_in_again, success_line, unverified_detail,
)
from ..output import Block, CliFailure, CommandResult, Line, Table, sentence
from ..wording import MODS_DOWNLOADED, STEP_CANCELLED, other_profile_running
from .common import profile_id, profile_line, profile_name
from .mods_review import PUBLISH, review_blocks, variant
from .write_common import check_recovery_block, read_revisions

# The JSON code of a server state that the request does not allow (`_refusal_code`, installation_guard.py)
STATE_CODES = {"RUNNING_EXTERNAL": "EXTERNAL_PROCESS", "UNKNOWN": "PROCESS_STATE_UNKNOWN",
               "AMBIGUOUS": "PROCESS_STATE_UNKNOWN"}
UPDATE, APPLY = "UPDATE_WORKSHOP_ITEMS", {PUBLISH: ("publish_mods_and_keys", "PUBLISH_MODS_AND_KEYS"),
                                          "restart": ("apply_mods_and_restart", "APPLY_MODS_AND_RESTART")}


def flag_rule(_call: Any, options: Any, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step: a backup choice belongs to `--restart`, the only request that stops the server (exit 2)."""
    if options.backup_after_stop is not None and not options.restart:
        raise CliFailure("USAGE", backup_needs_restart(), USAGE)
    return {}


def update(context: Any) -> CommandResult:
    """Download, review, ask, then apply; or end with the download only."""
    start, restart = bool(context.options.start), bool(context.options.restart)
    state = read_revisions(context)
    check_recovery_block(state.snapshot)
    settings = state.snapshot.get("settings") if isinstance(state.snapshot.get("settings"), Mapping) else {}
    if settings.get("steam_authentication_mode") not in ("ACCOUNT", "ANONYMOUS"):
        raise CliFailure("AUTHENTICATION_REQUIRED", choose_sign_in(), REFUSED)
    _gate(context, start, restart, context.call("get_server_status"))
    record = run_operation(
        context, "update_workshop_items", UPDATE, profile_id=profile_id(context.profile),
        expected_profile_revision=state.profile_revision,
        expected_semantic_profile_digest=state.profile.get("semantic_digest"),
        expected_settings_revision=state.settings_revision,
        authentication_mode=settings.get("steam_authentication_mode"),
        account_name=settings.get("steam_account_name"), update_all_and_start=start or restart)
    return _after_download(context, record, start, restart)


def _gate(context: Any, start: bool, restart: bool, status: Mapping[str, Any]) -> None:
    """Step 2: refuse before any download when the server state does not allow the request (exit 3)."""
    server = str(status.get("state"))
    if (start and server != "STOPPED") or (restart and server != "RUNNING_MANAGED"):
        code = STATE_CODES.get(server, "CONTROL_CONFLICT")
        raise CliFailure(code, sentence(error_text(code, f"state is {server}")), REFUSED, True, {"state": server})
    running = status.get("profile_id")
    if restart and running is not None and running != profile_id(context.profile):
        # D11 from the same read (criterion 26): the restart would stop another profile's server
        raise CliFailure("INVALID_REQUEST", other_profile_running(_running_name(context, running)), REFUSED,
                         details={"running_profile_id": running})


def _after_download(context: Any, record: Mapping[str, Any], start: bool, restart: bool) -> CommandResult:
    """Steps 4 to 8: the result of the download decides whether a review, a question and an apply follow."""
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    items = _items_table(result)
    # The profile first, as the read commands name it; the results and the review follow
    context.output.show([profile_line(context.profile)])
    if result.get("download_state") not in ("VERIFIED", "EMPTY"):
        raise _unverified(record, result, start, restart, items)
    downloaded = Downloaded(record)
    downloaded.guard(lambda: stop_if_interrupted(context.interrupts, STEP_CANCELLED))
    now = downloaded.guard(lambda: context.call("get_server_status"))
    restart_now = (start or restart) and now.get("state") == "RUNNING_MANAGED"
    empty = result.get("download_state") == "EMPTY"
    if empty and not (start or restart):
        return downloaded.done([sentence(UPDATE_TEXTS["empty"])])
    if empty and restart_now:
        return downloaded.done([sentence(UPDATE_TEXTS["emptyRunning"])])
    preview = downloaded.guard(lambda: context.call(
        "preview_mod_publication", **_publication(context, record, result)))
    downloaded.review = preview
    current = everything_current(result) and preview.get("missing_key_count") == 0
    if current and not (start or restart):
        return downloaded.done([sentence(UPDATE_TEXTS["current"])])
    if current and restart_now:
        return downloaded.done([current_running()])
    # The saved choice is a read after the download: its failure keeps criterion 24 too (QF-53)
    backup = downloaded.guard(lambda: _backup_choice(context)) if restart else False
    chosen = variant(start, restart, now, profile_id(context.profile), preview, backup)
    shown = [] if current or empty else [items]
    blocks = review_blocks(chosen, preview, current or empty)
    if chosen.submit is None:
        # The "changed" and "refused" rows apply nothing: their reason, then the download sentence (exit 3)
        context.output.show([*shown, *blocks])
        ending = chosen.sentence if chosen.key == "changed" else (*chosen.sentence, f" {MODS_DOWNLOADED}")
        raise downloaded.failure("CONTROL_CONFLICT", sentence(*ending), REFUSED)
    context.output.show(shown)
    downloaded.guard(lambda: stop_if_interrupted(context.interrupts, STEP_CANCELLED))
    downloaded.guard(lambda: confirm(context.output, context.interrupts, context.stdin, yes=context.options.yes,
                                     review=blocks, review_value=preview, nothing_changed=MODS_DOWNLOADED,
                                     details={"operation": dict(record)}))
    return _apply(context, downloaded, chosen.submit, backup, _publication(context, record, result))


def _apply(context: Any, downloaded: "Downloaded", submit: str, backup: bool,
           request: dict[str, Any]) -> CommandResult:
    """Step 8: submit the reviewed apply and wait; a refusal before its first change keeps criterion 24."""
    method, kind = APPLY[submit]
    parameters = {**request, "publication_fingerprint": downloaded.review.get("publication_fingerprint")}
    if submit != PUBLISH:
        parameters["backup_after_stop"] = backup
    try:
        applied = run_operation(context, method, kind, **parameters)
    except CliFailure as failure:
        raise downloaded.apply_failure(context, failure) from failure
    result = applied.get("result") if isinstance(applied.get("result"), Mapping) else {}
    line = success_line(applied, kind, KIND_TEXTS[kind][1]) or sentence(KIND_TEXTS[kind][1])
    value = {"operations": [dict(downloaded.record), dict(applied)], "review": downloaded.review,
             "result": dict(result)}
    if result.get("start_state") == "FAILED":
        # The mods are applied but the requested start failed: the work did not succeed (exit 1)
        raise CliFailure(str(result.get("start_error") or "LAUNCH_FAILED"), line, FAILED, False,
                         {"operation": dict(applied), "update_operation": dict(downloaded.record)})
    return CommandResult(value, [line])


class Downloaded:
    """The state after a finished download: every failure from here reports it and ends with its sentence."""

    def __init__(self, record: Mapping[str, Any]) -> None:
        """Keep the update operation; the review comes with the preview."""
        self.record = record
        self.review: Any = None

    def guard(self, step: Any) -> Any:
        """Run one step; a failure that it raises ends with the download sentence (criterion 24)."""
        try:
            return step()
        except CliBridgeError as error:
            raise self.ended(error.failure()) from error
        except CliFailure as failure:
            raise self.ended(failure) from failure

    def ended(self, failure: CliFailure) -> CliFailure:
        """Return the failure with the download sentence last and the update operation in the details."""
        parts = failure.message.parts
        if not parts or not str(parts[-1].text).endswith(MODS_DOWNLOADED):
            parts = (*parts, *sentence(f" {MODS_DOWNLOADED}").parts)
        details = {**(failure.details or {}), "review": self.review, "operation": dict(self.record)}
        return CliFailure(failure.code, Line(tuple(parts)), failure.exit_code, failure.retryable, details,
                          failure.notes)

    def failure(self, code: str, message: Line, exit_code: int) -> CliFailure:
        """Return a refusal of this command after the download (exit 3), with the review and the update."""
        return CliFailure(code, message, exit_code, True, {"review": self.review, "operation": dict(self.record)})

    def apply_failure(self, context: Any, failure: CliFailure) -> CliFailure:
        """Word a refused or failed apply: before its first change it keeps criterion 24; else it is its own end."""
        applied = (failure.details or {}).get("operation")
        if applied is None:
            # Refused at dispatch: nothing was submitted (D11 names the running profile, QF-16)
            if failure.code == "INVALID_REQUEST" and failure.exit_code == REFUSED:
                failure = _d11_after_download(context, failure)
            return self.ended(failure)
        details = {**(failure.details or {}), "review": self.review, "update_operation": dict(self.record)}
        if failure.exit_code in (USAGE, REFUSED):
            # The record shows that the apply ended before its first change (6.5): nothing was applied
            return CliFailure(failure.code, self.ended(failure).message, failure.exit_code, failure.retryable,
                              details)
        return CliFailure(failure.code, failure.message, failure.exit_code, failure.retryable, details)

    def done(self, lines: list[Block]) -> CommandResult:
        """End without an apply: nothing to review or apply (exit 0)."""
        result = self.record.get("result")
        return CommandResult({"operations": [dict(self.record)], "review": self.review, "result": result}, lines)


def _unverified(record: Mapping[str, Any], result: Mapping[str, Any], start: bool, restart: bool,
                items: Table) -> CliFailure:
    """Word an update whose downloads were not all verified: no review follows (`reportUnverifiedUpdate`)."""
    state = str(result.get("download_state"))
    suffix = UPDATE_TEXTS["keepsRunning"] if restart else UPDATE_TEXTS["notStarted"] if start else ""
    parts: list[Any] = [f"{DOWNLOAD_RESULTS.get(state, DOWNLOAD_RESULTS['FAILED'])}{unverified_detail(result)}{suffix}"]
    entries = [entry for entry in result.get("items") or [] if isinstance(entry, Mapping)]
    if state == "UNKNOWN" or any(entry.get("outcome") == "AUTHENTICATION_FAILED" for entry in entries):
        parts.extend(sign_in_again())
    if state == "CANCELLED":
        code, exit_code = "CANCELLED", CANCELLED
    else:
        failed = [entry.get("error_code") for entry in entries if entry.get("error_code")]
        code = "UPDATE_RESULT_UNKNOWN" if state == "UNKNOWN" else str(failed[0] if failed else "WORKSHOP_CONTENT_FAILED")
        exit_code = FAILED
    return CliFailure(code, sentence(*parts), exit_code, False, {"operation": dict(record)}, (items,))


def _items_table(result: Mapping[str, Any]) -> Table:
    """Return one row per Workshop item of the update with its outcome (`renderModsItems`)."""
    entries = [entry for entry in result.get("items") or [] if isinstance(entry, Mapping)]
    rows = tuple((str((entry.get("item") or {}).get("workshop_id") or "Unknown item"), outcome_text(entry))
                 for entry in entries)
    return Table(("Workshop item", "Result"), rows, value_columns=frozenset({0}))


def _publication(context: Any, record: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the request of the preview and the apply: the revisions that the update ran with."""
    return {"profile_id": profile_id(context.profile), "expected_profile_revision": result.get("profile_revision"),
            "expected_semantic_profile_digest": result.get("semantic_profile_digest"),
            "expected_settings_revision": result.get("settings_revision"),
            "update_operation_id": record.get("operation_id")}


def _backup_choice(context: Any) -> bool:
    """Return `--backup-after-stop` / `--no-backup-after-stop`, else the profile's saved choice."""
    if context.options.backup_after_stop is not None:
        return bool(context.options.backup_after_stop)
    saved = context.call("get_ui_preferences").get("backup_after_stop_profiles") or []
    return profile_id(context.profile) in saved


def _d11_after_download(context: Any, failure: CliFailure) -> CliFailure:
    """Word a D11 refusal at the apply with the running profile's name (QF-16).

    Both reads may fail after the download (QF-53): a failed status read keeps the original
    refusal, a failed name read names the running ID. The caller ends either with the download sentence.
    """
    try:
        running = context.call("get_server_status").get("profile_id")
    except (CliBridgeError, CliFailure):
        return failure
    try:
        name = _running_name(context, running)
    except (CliBridgeError, CliFailure):
        name = profile_name({"profile_id": running})
    return CliFailure(failure.code, other_profile_running(name), REFUSED, failure.retryable,
                      {"running_profile_id": running})


def _running_name(context: Any, running: object) -> str:
    """Return the display name of the profile that the server runs with."""
    profiles = [item for item in context.call("list_profiles") if isinstance(item, Mapping)]
    return profile_name(next((item for item in profiles if item.get("profile_id") == running),
                             {"profile_id": running}))
