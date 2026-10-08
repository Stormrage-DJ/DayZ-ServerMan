"""`updates check [--scope] [--force]` and `updates auto on/off` (10.3, criterion 11).

The check runs outside the lane on its own worker; the command polls the update status until
the check of its scope no longer runs (section 7, "Waits outside the lane"). The check cannot be
cancelled, so Ctrl+C only says so and the command keeps waiting for the result.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from ..exit_codes import FAILED
from ..flow import stop_if_interrupted
from ..mods_wording import CHECK_NOT_CANCELLABLE, CHECK_TIMEOUT, automatic_checks, check_not_started
from ..output import CliFailure, CommandResult, sentence
from .common import profile_id
from .updates import status_lines

# `--scope` choices and the scopes of the bridge (the window's "Check now" names the same three)
SCOPES = {"mods": "mods", "server-build": "server_build", "all": "all"}
# Poll interval and limit of the wait (fixed values, section 7)
POLL_SECONDS = 0.5
LIMIT_SECONDS = 300
# Seams of the wait; tests replace them
SLEEP = time.sleep
CLOCK = time.monotonic


def check(context: Any) -> CommandResult:
    """Request a check of the scope, wait for its result and print the update status."""
    scope = SCOPES[context.options.scope or "all"]
    force = bool(context.options.force)
    stop_if_interrupted(context.interrupts)
    answer = context.call("request_update_check", scope=scope, force=force)
    preferences = context.call("get_ui_preferences")
    profile = _selected(context, preferences.get("selected_profile_id"))
    automatic = preferences.get("automatic_update_checks", True) is not False
    if _started(answer, scope):
        status, note = _wait(context, scope, profile_id(profile)), None
    else:
        # Nothing runs (a recent check, the switch, or no SteamCMD for the build): the last result is the answer
        status, note = context.call("get_update_status", profile_id=profile_id(profile)), check_not_started(force)
    value = {"operations": [], "review": None, "result": {**status, "profile_id": profile_id(profile)},
             "request": answer}
    lines = status_lines(status, profile, automatic)
    return CommandResult(value, lines if note is None else [*lines, note])


def auto(context: Any) -> CommandResult:
    """Turn the automatic update checks on or off, as the switch in Settings saves it."""
    stop_if_interrupted(context.interrupts)
    enabled = context.options.state == "on"
    result = context.call("save_automatic_update_checks", enabled=enabled)
    saved = result.get("automatic_update_checks", enabled) if isinstance(result, Mapping) else enabled
    return CommandResult({"operations": [], "review": None, "result": result}, [automatic_checks(saved is True)])


def _selected(context: Any, selected: object) -> Mapping[str, Any] | None:
    """Return the profile that the window has selected, whose mod rows the status counts; None without one."""
    if not isinstance(selected, str):
        return None
    profiles = [item for item in context.call("list_profiles") if isinstance(item, Mapping)]
    return next((item for item in profiles if item.get("profile_id") == selected), None)


def _started(answer: Mapping[str, Any], scope: str) -> bool:
    """Report whether the request started a check of the scope or found one running."""
    build = answer.get("server_build") if isinstance(answer.get("server_build"), Mapping) else {}
    top = bool(answer.get("accepted") or answer.get("checking"))
    if scope == "all":
        return top or bool(build.get("accepted") or build.get("checking"))
    return top


def _checking(status: Mapping[str, Any], scope: str) -> bool:
    """Report whether a check of the scope still runs; `server_build.checking` is the build check's flag."""
    build = status.get("server_build") if isinstance(status.get("server_build"), Mapping) else {}
    mods = bool(status.get("checking")) and scope in ("mods", "all")
    return mods or (bool(build.get("checking")) and scope in ("server_build", "all"))


def _wait(context: Any, scope: str, selected: str | None) -> Mapping[str, Any]:
    """Poll the update status until the check of the scope ends; after 5 minutes exit 1."""
    deadline = CLOCK() + LIMIT_SECONDS
    noted = False
    while True:
        status = context.call("get_update_status", profile_id=selected)
        if not _checking(status, scope):
            return status
        if context.interrupts.take() and not noted:
            # The check has no safe point: say so once and keep waiting for its result
            context.output.note((sentence(CHECK_NOT_CANCELLABLE),))
            noted = True
        if CLOCK() >= deadline:
            raise CliFailure("CHECK_NOT_FINISHED", sentence(CHECK_TIMEOUT), FAILED, True, {"status": status})
        SLEEP(POLL_SECONDS)
