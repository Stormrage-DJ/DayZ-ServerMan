"""Exit code per error code and operation end state, with the pre-change evidence rule (design 6.4, 6.5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..application.lifecycle_coordinator import OTHER_PROFILE_RUNNING

# The product rule of exit codes (criterion 8, R1)
SUCCESS, FAILED, USAGE, REFUSED, NOT_CONFIRMED, CANCELLED, RECOVERY = 0, 1, 2, 3, 4, 5, 6

# CLI codes that are not bridge error codes: the closed list of A11 (QF-62), design 6.4
CLI_CODES: dict[str, int] = {
    "USAGE": USAGE, "INSTANCE_ACTIVE": REFUSED, "SETUP_REQUIRED": REFUSED, "NOTHING_TO_CONVERT": REFUSED,
    "CONFIRMATION_REQUIRED": NOT_CONFIRMED, "NOT_INTERACTIVE": NOT_CONFIRMED, "CANCELLED": CANCELLED,
    "INSTANCE_LOCK_UNSUPPORTED": FAILED, "CHECK_NOT_FINISHED": FAILED, "NOT_READY": FAILED,
}

# Refusal codes: their class when dispatch returns them; inside a FAILED record only when pre-change (6.5)
REFUSAL_CODES: dict[str, int] = {
    "INVALID_REQUEST": USAGE, "NOT_FOUND": USAGE, "PATH_INVALID": USAGE, "PATH_OUTSIDE_ALLOWED_ROOT": USAGE,
    "REVISION_CONFLICT": REFUSED, "CONTROL_CONFLICT": REFUSED, "EXTERNAL_PROCESS": REFUSED,
    "PROCESS_STATE_UNKNOWN": REFUSED, "GAMEPLAY_NOT_ENABLED": REFUSED, "RUNTIME_PROFILE_UNRESOLVED": REFUSED,
    "PENDING_RUNTIME_PROFILE_SUPPORT": REFUSED, "PROFILE_CONTEXT_MISMATCH": REFUSED,
    "UNSUPPORTED_SNAPSHOT_CONTENT": REFUSED, "MIGRATION_CONFLICT": REFUSED, "SOURCE_CHANGED": REFUSED,
    "STEAMCMD_UNAVAILABLE": REFUSED, "AUTHENTICATION_REQUIRED": REFUSED, "PUBLICATION_REQUIRED": REFUSED,
    "PUBLICATION_PREVIEW_STALE": REFUSED,
    # Operation codes that are not ErrorCode members
    "STEAMCMD_BUSY": REFUSED, "STEAMCMD_PATH_CHANGED": REFUSED,
}
# Codes with one exit code wherever they appear
FIXED_CODES: dict[str, int] = {
    "CONTRACT_VERSION_UNSUPPORTED": FAILED, "LAUNCH_FAILED": FAILED, "STOP_METHOD_UNPROVEN": FAILED,
    "RECOVERY_REQUIRED": RECOVERY, "STORAGE_FAILURE": FAILED, "AUTHENTICATION_FAILED": FAILED,
    "ENTITLEMENT_DENIED": FAILED, "CONNECTION_FAILED": FAILED, "WORKSHOP_CONTENT_FAILED": FAILED,
    "CACHE_VERIFICATION_FAILED": FAILED, "UPDATE_RESULT_UNKNOWN": FAILED, "UPDATE_CANCELLED": CANCELLED,
    "PUBLICATION_FAILED": FAILED, "EVENT_CURSOR_EXPIRED": FAILED, "INTERNAL_FAILURE": FAILED,
    # Every ProfileDeletionError is raised before the first change (profile_deletion.py)
    "DELETION_BLOCKED": REFUSED,
}
# MUTATION_CONFLICT at dispatch, by the cause in its details
CONFLICT_REASONS: dict[str | None, int] = {
    "QUEUE_FULL": REFUSED, None: REFUSED, "RECOVERY_BLOCK": RECOVERY, "SHUTTING_DOWN": FAILED,
}
# Answer of a cancellation request that changes no exit: the waiter keeps waiting
NO_EXIT_CODES = frozenset(("OPERATION_NOT_CANCELLABLE",))

# Pre-change evidence (6.5): kinds judged by the percent below a first-change marker
MARKER_PERCENT: dict[str, int] = {
    "START_SERVER": 11, "STOP_SERVER": 21, "APPLY_MODS_AND_RESTART": 9,
}
# RESTART_SERVER: the marker without and with a backup after the stop
RESTART_MARKERS = {False: 12, True: 16}
# Kinds judged by the last working phase; None means no checkpoint ran
PRE_CHANGE_PHASES: dict[str, frozenset[str | None]] = {
    "PUBLISH_MODS_AND_KEYS": frozenset((None, "PUBLICATION_PREFLIGHT", "DISCOVER_ITEM", "CACHE_PROOF_RECHECK",
                                        "STAGE_TARGET", "CHECK_TARGET", "COPY_FILE", "COPY_KEY")),
    "RESTORE_BACKUP": frozenset((None, "VERIFY_SOURCE", "PREPARE_RECOVERY", "STAGE_TARGETS")),
    "RESTORE_PROFILE_FROM_BACKUP": frozenset((None,)),
    "APPLY_CONFIGURATION": frozenset((None, "loaded")),
    "APPLY_MISSION_CONFIGURATION": frozenset((None, "loaded")),
    "CONVERT_STARTER_LOADOUT": frozenset((None, "loaded")),
    "APPLY_MEDICAL_FEATURE": frozenset((None,)),
    "CREATE_BACKUP": frozenset((None,)),
    "VERIFY_WORKSHOP_FILES": frozenset((None,)),
    "AUTHENTICATE_STEAMCMD": frozenset((None, "preflight", "wait_steamcmd")),
    "UPDATE_WORKSHOP_ITEMS": frozenset((None, "preflight", "wait_steamcmd", "resolve_items")),
}
# Kinds whose records never show the side of the first change: every refusal there exits 1
NEVER_PRE_CHANGE = frozenset(("PROVISION_PROFILE", "SAVE_PROFILE", "SAVE_SETTINGS", "SAVE_STEAM_SETTINGS",
                              "IMPORT_LEGACY"))


def dispatch_exit(error: Mapping[str, Any], *, names_confirmed: bool = False) -> int | None:
    """Return the exit code of a bridge error that dispatch returned (nothing was submitted).

    None means that the code is no exit (OPERATION_NOT_CANCELLABLE). `names_confirmed` says
    that the CLI already confirmed every identifier of the command line against its listing:
    a NOT_FOUND is then data behind valid names, exit 1 (criterion 27, QF-21 ruling).
    """
    code = str(error.get("code"))
    if code in NO_EXIT_CODES:
        return None
    if code == "NOT_FOUND" and names_confirmed:
        return FAILED
    if code == "INVALID_REQUEST" and error.get("message") == OTHER_PROFILE_RUNNING:
        # D11: another profile than the running one is a refusal of the current state (criterion 26)
        return REFUSED
    if code == "MUTATION_CONFLICT":
        details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
        return CONFLICT_REASONS.get(details.get("reason"), REFUSED)
    if code in CLI_CODES:
        return CLI_CODES[code]
    if code in REFUSAL_CODES:
        return REFUSAL_CODES[code]
    return FIXED_CODES.get(code, FAILED)


def pre_change(record: Mapping[str, Any], *, backup_after_stop: bool = False) -> bool:
    """Report whether a FAILED record shows that its operation ended before its first change (6.5)."""
    kind = record.get("kind")
    percent = record.get("progress_percent")
    if kind == "DELETE_PROFILE":
        return percent == 0
    if kind == "RESTART_SERVER":
        return isinstance(percent, int) and percent < RESTART_MARKERS[bool(backup_after_stop)]
    if kind in MARKER_PERCENT:
        return isinstance(percent, int) and percent < MARKER_PERCENT[str(kind)]
    if kind in PRE_CHANGE_PHASES:
        return record.get("last_working_phase") in PRE_CHANGE_PHASES[str(kind)]
    # NEVER_PRE_CHANGE and any kind this table does not know
    return False


def record_exit(record: Mapping[str, Any], *, backup_after_stop: bool = False) -> int:
    """Return the exit code of an operation at its terminal state (6.4 right column, 6.5)."""
    state = record.get("state")
    if state == "SUCCEEDED":
        return SUCCESS
    if state == "CANCELLED":
        return CANCELLED
    if state == "RECOVERY_REQUIRED":
        return RECOVERY
    error = record.get("terminal_error") if isinstance(record.get("terminal_error"), Mapping) else {}
    code = str(error.get("code"))
    if code in FIXED_CODES:
        return FIXED_CODES[code]
    if code in REFUSAL_CODES:
        # A refusal keeps its class only with evidence that nothing was changed yet
        return REFUSAL_CODES[code] if pre_change(record, backup_after_stop=backup_after_stop) else FAILED
    return FAILED
