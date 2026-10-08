"""`steam show` (10.1); `steam set` and `steam login` are in `steam_write.py`."""

from __future__ import annotations

from typing import Any

from ..output import CommandResult, Line, sentence
from ..read_wording import STEAM_MODE_TEXTS
from .common import labelled


def steam_show(context: Any) -> CommandResult:
    """Show the Steam sign-in mode and the account name of the settings."""
    settings = context.call("get_application_snapshot").get("settings") or {}
    mode, account = settings.get("steam_authentication_mode"), settings.get("steam_account_name")
    value = {"steam_authentication_mode": mode, "steam_account_name": account}
    return CommandResult(value, steam_lines(mode, account))


def steam_lines(mode: object, account: object) -> list[Line]:
    """Return the sign-in mode, and the account name for account sign-in."""
    lines = [sentence(f"Steam sign-in: {STEAM_MODE_TEXTS.get(str(mode), 'Not chosen')}")]
    if mode == "ACCOUNT" and account:
        lines.append(labelled("Steam account name", str(account)))
    return lines
