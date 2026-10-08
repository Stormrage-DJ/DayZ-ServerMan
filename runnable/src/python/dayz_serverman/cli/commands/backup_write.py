"""`backup create`, `backup restore ID` and `backup recover` (10.2, criteria 5, 6, 8, 10, 12 and 30).

Each command gates on the state it reads before any question (rule 5), shows the window's
review, asks or takes `--yes` (8.1), then runs one lane operation through dispatch, or for
`backup recover` the synchronous recovery inspection of the Backups page.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import block_reason_text
from ...application.restores import RECOVERY_NOT_STOPPED
from ...application.review_wording import RESTORE_VERIFIED
from ..confirm import confirm
from ..exit_codes import RECOVERY, REFUSED
from ..flow import run_operation, stop_if_interrupted
from ..output import Block, CliFailure, CommandResult, Table, sentence
from ..read_wording import RESTORE_DEFERRED_TEXT, RESTORE_RECOVERY_TEXT
from ..review import backup_create_review, restore_review
from ..wording import no_runtime_profile, recovery_clear, way_out
from .backup import backup_cells
from .common import local_time, profile_id, profile_line
from .write_common import check_recovery_block, read_revisions


def create(context: Any) -> CommandResult:
    """Create a verified backup of the profile, after the window's "Create backup?" review (criterion 30)."""
    state = read_revisions(context)
    # State gate before the question: the window turns Create backup off for these (rule 5)
    check_recovery_block(state.snapshot)
    if not state.profile.get("runtime_profile"):
        raise CliFailure("RUNTIME_PROFILE_UNRESOLVED", no_runtime_profile(), REFUSED)
    request = {"profile_id": profile_id(context.profile), "expected_profile_revision": state.profile_revision,
               "expected_settings_revision": state.settings_revision}
    _ask(context, backup_create_review(context.profile), request)
    record = run_operation(context, "create_backup", "CREATE_BACKUP", **request)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    table = Table(("Created", "Files", "Size", "Restore", "ID"), (backup_cells(result),),
                  input_columns=frozenset({4}), value_columns=frozenset({0, 1}))
    return CommandResult({"operations": [dict(record)], "review": request, "result": dict(result)},
                         [profile_line(context.profile), table])


def restore(context: Any) -> CommandResult:
    """Restore one backup of the profile: the preview is the review; the server must be stopped (10.2)."""
    stop_if_interrupted(context.interrupts)
    check_recovery_block(context.call("get_application_snapshot"))
    identifier = context.resolved["backup_id"]
    # The preview takes the installation mutex and needs a proven stopped server: a running server
    # refuses here, before the question, with exit 3 (rule 5)
    preview = context.call("preview_restore", profile_id=profile_id(context.profile), backup_id=identifier)
    _ask(context, [profile_line(context.profile), *restore_review(preview, local_time)], preview)
    record = run_operation(
        context, "apply_restore", "RESTORE_BACKUP", profile_id=profile_id(context.profile), backup_id=identifier,
        expected_profile_revision=preview.get("profile_revision"),
        expected_settings_revision=preview.get("settings_revision"),
        expected_manifest_digest=preview.get("manifest_digest"), preview_fingerprint=preview.get("fingerprint"),
    )
    return CommandResult({"operations": [dict(record)], "review": preview, "result": record.get("result")},
                         [sentence(RESTORE_VERIFIED)])


def recover(context: Any) -> CommandResult:
    """Finish or undo an interrupted restore, as the Backups page does when it opens (10.2).

    Not blocked: exit 0. Blocked because the server is not proven stopped, or deferred: exit 3
    (nothing was changed). Any other block: exit 6, a person must act.
    """
    stop_if_interrupted(context.interrupts)
    value = context.call("inspect_restore_recovery")
    if not value.get("blocked"):
        return CommandResult({"operations": [], "review": None, "result": value}, [recovery_clear()])
    details = {"recovery": value}
    diagnostics = [item for item in value.get("diagnostics") or [] if isinstance(item, Mapping)]
    code = str(diagnostics[0].get("code")) if diagnostics and diagnostics[0].get("code") else None
    if value.get("deferred"):
        raise CliFailure("CONTROL_CONFLICT", sentence(RESTORE_DEFERRED_TEXT), REFUSED, True, details)
    reason = value.get("reason")
    if reason == RECOVERY_NOT_STOPPED:
        raise CliFailure(code or "CONTROL_CONFLICT", way_out(block_reason_text(reason)), REFUSED, True, details)
    text = block_reason_text(reason) if reason else RESTORE_RECOVERY_TEXT
    raise CliFailure("RECOVERY_REQUIRED", way_out(text), RECOVERY, False, details)


def _ask(context: Any, blocks: list[Block], review: Any) -> None:
    """Ctrl+C during the reads exits 5; then the 8.1 question, `--yes` or a refusal with exit 4."""
    stop_if_interrupted(context.interrupts)
    confirm(context.output, context.interrupts, context.stdin, yes=context.options.yes, review=blocks,
            review_value=review)
