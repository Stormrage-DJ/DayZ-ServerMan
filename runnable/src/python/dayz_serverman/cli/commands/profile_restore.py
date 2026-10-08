"""`profile restore --archive ZIP` (10.2, 8.1, 8.2; R-5 ruling): a profile rebuilt from a backup archive.

The pre-step inspected the archive in an observer session; its selection token ended with that
session, so the owner session inspects the archive again and uses only its own token. The
preview is the review. A preview that replaces an existing world needs `--overwrite`, as the
window needs its own checkbox, and then the question or `--yes`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import plain_sentence
from ...application import review_wording as texts
from ..bridge_client import CliBridgeError
from ..confirm import confirm
from ..exit_codes import NOT_CONFIRMED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..output import Block, CliFailure, CommandResult, Table, Value, sentence
from ..review import profile_restore_review
from ..wording import mission_occupied, overwrite_needed
from .write_common import check_recovery_block

# `--storage` choices and the storage policies of the bridge
STORAGE_POLICIES = {"preserve": "preserve_original", "new": "allocate_new", "replace": "replace_existing"}
REPLACE = "replace_existing"
# The host's reason when `--storage preserve` meets an original mission that another profile uses
# (`domain/profile_restore_destinations.py`); its "select isolation" names the window's choice
OCCUPIED_MISSION = "original mission is occupied"


def restore(context: Any) -> CommandResult:
    """Rebuild a profile from the archive: gate, inspect again, preview, `--overwrite`, question, operation."""
    options = context.options
    stop_if_interrupted(context.interrupts)
    check_recovery_block(context.call("get_application_snapshot"))
    inspected = _inspect(context)
    request = {"backup_id": inspected.get("backup_id"), "profile_id": options.profile_id,
               "display_name": options.name, "storage_policy": STORAGE_POLICIES.get(options.storage),
               "game_port": options.game_port, "steam_query_port": options.query_port}
    # The preview takes the installation mutex and needs a stopped server: a refusal comes before the question
    try:
        preview = context.call("preview_profile_restore", **request)
    except CliBridgeError as error:
        raise _preview_refusal(error) from error
    replacing = preview.get("storage_policy") == REPLACE
    blocks = profile_restore_review(preview, str(inspected.get("archive_name") or ""),
                                    _names(context) if replacing else {})
    if replacing and not options.overwrite:
        # The window's own checkbox: without --overwrite nothing is asked and nothing changes (8.1)
        context.output.show(blocks)
        raise CliFailure("CONFIRMATION_REQUIRED", overwrite_needed(), NOT_CONFIRMED, details={"review": preview})
    stop_if_interrupted(context.interrupts)
    confirm(context.output, context.interrupts, context.stdin, yes=options.yes, review=blocks, review_value=preview)
    confirmation = ({"preview_fingerprint": preview.get("preview_fingerprint"),
                     "affected_profile_ids": preview.get("affected_profile_ids")} if replacing else None)
    record = run_operation(context, "restore_profile_from_backup", "RESTORE_PROFILE_FROM_BACKUP", **request,
                           expected_manifest_digest=preview.get("manifest_digest"),
                           preview_fingerprint=preview.get("preview_fingerprint"),
                           overwrite_confirmation=confirmation)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    return CommandResult({"operations": [dict(record)], "review": preview, "result": dict(result)},
                         result_lines(result))


def _inspect(context: Any) -> Mapping[str, Any]:
    """Inspect the archive in the owner session (R-5); one that no longer inspects is an argument error (6.4.1)."""
    try:
        return context.call("inspect_backup_archive", path=context.resolved["archive"])
    except CliBridgeError as error:
        failure = error.failure()
        raise CliFailure(failure.code, failure.message, USAGE, failure.retryable, failure.details) from error


def _preview_refusal(error: CliBridgeError) -> CliFailure:
    """Return the failure of a refused preview; an occupied original world gets the CLI way out (QF-42)."""
    failure = error.failure()
    if OCCUPIED_MISSION in str(error.error.get("message", "")):
        return CliFailure(failure.code, mission_occupied(), failure.exit_code, failure.retryable, failure.details)
    return failure


def _names(context: Any) -> dict[str, str]:
    """Return the display name of every registered profile by its ID, for the profiles of a replaced world."""
    return {str(item.get("profile_id")): str(item.get("display_name") or item.get("profile_id"))
            for item in context.call("list_profiles") if isinstance(item, Mapping)}


def result_lines(result: Mapping[str, Any]) -> list[Block]:
    """Return the restored profile and the window's status sentence after a profile restore that succeeded."""
    profile = result.get("profile") if isinstance(result.get("profile"), Mapping) else {}
    blocks: list[Block] = [Table(("Profile", "ID"), ((str(profile.get("display_name", "")),
                                                      str(profile.get("profile_id", ""))),),
                                 input_columns=frozenset({1}), value_columns=frozenset({0}))]
    readiness = result.get("readiness") if isinstance(result.get("readiness"), Mapping) else {}
    reasons = [text for text in map(plain_sentence, readiness.get("reasons") or []) if text]
    if result.get("cleanup_pending"):
        blocks.append(sentence(texts.PROFILE_RESTORED_CLEANUP))
    elif reasons:
        blocks.append(sentence(" ".join((texts.PROFILE_RESTORED, *reasons))))
    else:
        blocks.append(sentence(texts.PROFILE_RESTORED_CHECK))
    if result.get("recovery_copy"):
        blocks.append(sentence(f"{texts.WORLD_RECOVERY_COPY}: ", Value(str(result["recovery_copy"]))))
    return blocks
