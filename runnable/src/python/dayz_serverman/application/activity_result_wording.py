"""Operator wording for the result of a finished operation in the "Manager activity" log view (8.14).

Python sibling of `frontend/operation_results.js` and `frontend/operation_failures.js`. The texts moved
here from `activity_wording.py` unchanged; that module re-exports them for existing importers.
"""

from __future__ import annotations

# Kinds whose outcome depends on a result that the log record does not hold: a neutral end sentence
NEUTRAL_SUCCESS = {
    "UPDATE_WORKSHOP_ITEMS": "Mod update finished. The result is on the Mods page.",
    "VERIFY_WORKSHOP_FILES": "Mod file verification finished. The result is on the Mods page.",
    "APPLY_MODS_AND_RESTART": "Applying mods and restarting finished. The result is on the Mods page.",
}
# Sentences of "apply mods and restart" for a failed or cancelled end, chosen by the last working phase
RESTART_APPLY_TEXTS: dict[str, str] = {
    "cancelledRunning": "Cancelled. The server keeps running; nothing was applied.",
    "cancelledStopped": "Cancelled. The server stays stopped; the mods were not applied.",
    "preflight": "The reviewed plan is out of date, or the server state changed. The server keeps running; nothing was applied. Update again.",
    "stop": "The server could not be stopped. Nothing was applied.",
    "stopNotRunning": "The server was already stopped, so it was not restarted. Nothing was applied. To apply the mods and start the server, use Update & start.",
    "backup": "Server stopped, but the backup failed. The mods were not applied and the server was not started again.",
    "check": "Mods applied, but the server folder changed before the start. The server stays stopped.",
    "apply": "Server stopped, but the mods could not be applied. The server folder is as it was. The server was not started again.",
    "applyBlocked": "Server stopped, but the mods could not be applied.",
    "applyRefused": "The server was stopped, but its state changed before the mods could be applied. Nothing was applied; the server folder is as it was.",
    "startCheck": "Mods applied, but the server folder changed before the start. The server was not started.",
    "start": "Mods applied, but the server start did not succeed or could not be confirmed. Check the server state on Overview.",
}
# Error codes of a write guard that refused the apply because the server was not stopped
GUARD_CODES = frozenset(("CONTROL_CONFLICT", "EXTERNAL_PROCESS", "PROCESS_STATE_UNKNOWN"))


def restart_apply_text(
    state: object, phase: object, code: object = None, message: object = None,
) -> str | None:
    """Return the sentence of a failed or cancelled "apply mods and restart", or None for the generic one.

    `code` and `message` are the host error. A server state that the message names
    tells a refused stop of a stopped server and a refused apply from a real failure.
    """
    # The server state names stay with the error texts that also use them. activity_wording imports this
    # module, so the import waits until the first call.
    from .activity_wording import _STATE_NAME

    name = phase if isinstance(phase, str) else ""
    if state == "CANCELLED":
        # Before the stop the server still runs; every later safe point leaves it stopped
        return RESTART_APPLY_TEXTS["cancelledRunning" if name in ("", "preflight") else "cancelledStopped"]
    if not name:
        return None
    states = _STATE_NAME.findall(str(message or ""))
    named = states[-1] if states else None
    if name == "STOP_SERVER":
        # A stop that was refused because the server was already stopped did not fail to stop it
        key = "stopNotRunning" if named == "STOPPED" else "stop"
    elif name in ("preflight", "VERIFY_BEFORE_START", "START_SERVER"):
        # A failure of the start handoff proves neither a changed folder nor a stopped server
        key = {"preflight": "preflight", "VERIFY_BEFORE_START": "check", "START_SERVER": "start"}[name]
    elif name.startswith("BACKUP_"):
        key = "backup"
    elif state == "RECOVERY_REQUIRED":
        key = "applyBlocked"
    else:
        # A write guard that found the server no longer stopped applied nothing
        key = "applyRefused" if named not in (None, "STOPPED") and code in GUARD_CODES else "apply"
    return RESTART_APPLY_TEXTS[key]
