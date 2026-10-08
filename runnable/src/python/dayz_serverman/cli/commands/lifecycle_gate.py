"""State gate of `server start/stop/restart` before the confirmation question (criterion 28, rule 5, QF-31).

The checks are the window's own reasons to turn Start, Stop and Restart off before its dialog
(`frontend/overview_server.js` `overviewControlStates`), plus D11 from the same status read. Each
refusal asks nothing, changes nothing and exits 3, or 6 for a recovery block. The operation's
guards stay in place; this gate only answers early what the status read already decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..exit_codes import REFUSED
from ..output import CliFailure, Line, TypeText, sentence
from ..wording import other_profile_running
from .common import profile_id, profile_name
from .write_common import check_recovery_block

# Why a control is off, per server state (copies of `overviewStateReasons`)
STATE_REASONS: dict[str, str] = {
    "STOPPED": "The server is not running.",
    "RUNNING_MANAGED": "The server is already running.",
    "STARTING": "The server is starting.",
    "STOPPING": "The server is stopping.",
    "RUNNING_EXTERNAL": "The server runs outside DayZ-ServerMan. Stop it there.",
}
# A state that is not known (`overviewBlockReasons.unconfirmed`)
UNCONFIRMED_REASON = "The server state could not be confirmed."
# The JSON code of each refused state: the code that the operation's own state check reports
STATE_CODES: dict[str, str] = {"RUNNING_EXTERNAL": "EXTERNAL_PROCESS", "UNKNOWN": "PROCESS_STATE_UNKNOWN",
                               "AMBIGUOUS": "PROCESS_STATE_UNKNOWN"}
# The state that each action needs
NEEDED_STATES = {"start": "STOPPED", "stop": "RUNNING_MANAGED", "restart": "RUNNING_MANAGED"}


def check_state(context: Any, action: str, snapshot: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    """Refuse before the question when the read state does not allow the action; else return."""
    check_recovery_block(snapshot)
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    if action == "start" and not (settings.get("dayz_root") and settings.get("dayz_executable")):
        raise CliFailure("SETUP_REQUIRED", setup_needed(), REFUSED)
    state = str(status.get("state"))
    if state != NEEDED_STATES[action]:
        raise CliFailure(STATE_CODES.get(state, "CONTROL_CONFLICT"),
                         sentence(STATE_REASONS.get(state, UNCONFIRMED_REASON)), REFUSED, details={"server": status})
    running = status.get("profile_id")
    if action != "start" and running is not None and running != profile_id(context.profile):
        # D11 from the same read (criterion 26 text)
        profiles = [item for item in context.call("list_profiles") if isinstance(item, Mapping)]
        named = next((item for item in profiles if item.get("profile_id") == running), {"profile_id": running})
        raise CliFailure("INVALID_REQUEST", other_profile_running(profile_name(named)), REFUSED,
                         details={"running_profile_id": running})


def setup_needed() -> Line:
    """Word a manager without DayZ server paths (`overviewBlockReasons.setup`, with the CLI way out)."""
    return sentence("Complete the setup with ", TypeText("settings set"), " first.")
