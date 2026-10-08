"""`steam set --mode account/anonymous [--account NAME]` and `steam login` (10.3, 10.5, 8.1; D3).

`steam set` saves the sign-in choice as one lane operation under the settings revision.
`steam login` runs SteamCMD's interactive sign-in in this console: it needs a real console and
text mode, and refuses with exit 4 otherwise, whatever `--yes` says (criterion 6, QF-30).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..confirm import require_interactive
from ..exit_codes import REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..mods_wording import account_needed, account_not_allowed, choose_account_sign_in
from ..output import CliFailure, CommandResult
from .steam import steam_lines
from .write_common import check_pins

# `--mode` choices and the authentication modes of the bridge
MODES = {"account": "ACCOUNT", "anonymous": "ANONYMOUS"}


def account_rule(_call: Any, options: Any, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `steam set`: account sign-in names an account, anonymous sign-in names none (exit 2)."""
    if options.mode == "account" and not options.account:
        raise CliFailure("USAGE", account_needed(), USAGE)
    if options.mode == "anonymous" and options.account is not None:
        raise CliFailure("USAGE", account_not_allowed(), USAGE)
    return {}


def steam_set(context: Any) -> CommandResult:
    """Save the Steam sign-in mode and account under the settings revision just read."""
    stop_if_interrupted(context.interrupts)
    revision = _settings_revision(context)
    record = run_operation(context, "save_steam_settings", "SAVE_STEAM_SETTINGS", expected_revision=revision,
                           authentication_mode=MODES[context.options.mode], account_name=context.options.account)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    lines = steam_lines(result.get("authentication_mode"), result.get("account_name"))
    return CommandResult({"operations": [dict(record)], "review": None, "result": dict(result)}, lines)


def steam_login(context: Any) -> CommandResult:
    """Sign in to Steam in this console: SteamCMD asks for the password and Steam Guard itself (D3)."""
    stop_if_interrupted(context.interrupts)
    snapshot = context.call("get_application_snapshot")
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    check_pins(context, None, settings.get("revision"))
    # Rule 5: the state that the snapshot shows refuses before the console check (exit 3, not 4)
    if settings.get("steam_authentication_mode") != "ACCOUNT" or not settings.get("steam_account_name"):
        raise CliFailure("AUTHENTICATION_REQUIRED", choose_account_sign_in(), REFUSED)
    require_interactive(context.output, context.stdin)
    record = run_operation(context, "authenticate_steamcmd", "AUTHENTICATE_STEAMCMD",
                           expected_settings_revision=settings.get("revision"))
    return CommandResult({"operations": [dict(record)], "review": None, "result": record.get("result")},
                         steam_lines("ACCOUNT", settings.get("steam_account_name")))


def _settings_revision(context: Any) -> Any:
    """Read the settings revision that the save sends; `--expect-settings-revision` pins it."""
    snapshot = context.call("get_application_snapshot")
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    check_pins(context, None, settings.get("revision"))
    return settings.get("revision")
