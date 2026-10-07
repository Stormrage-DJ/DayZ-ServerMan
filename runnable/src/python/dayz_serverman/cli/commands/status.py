"""`status`: the server, the player count, the update state and unfinished work at a glance (10.1)."""

from __future__ import annotations

from typing import Any

from ...application.activity_wording import KIND_TEXTS
from ...application.phase_wording import server_status_text
from ...session_observer import pending_recoveries
from ..output import CommandResult, Line, sentence
from ..read_wording import PLAYERS_NOT_KNOWN, build_summary, mods_summary
from ..wording import way_out
from .common import profile_id, profile_line

# The way out of unfinished work in the CLI: any owner session runs the startup recoveries (4.5)
RECOVERY_WAY_OUT = "The next command that changes something, or DayZ-ServerMan when it opens, finishes it."


def run(context: Any) -> CommandResult:
    """Read the snapshot, the server state and the update state; add the pending recoveries (4.5)."""
    snapshot = context.call("get_application_snapshot")
    server = context.call("get_server_status")
    updates = context.call("get_update_status", profile_id=profile_id(context.profile))
    automatic = context.call("get_ui_preferences").get("automatic_update_checks", True) is not False
    pending = list(pending_recoveries(context.session.paths))
    value = {"snapshot": snapshot, "server": server, "updates": updates, "pending_recoveries": pending,
             "profile_id": profile_id(context.profile)}
    return CommandResult(value, status_lines(context.profile, server, updates, automatic, pending))


def status_lines(profile: Any, server: dict[str, Any], updates: dict[str, Any], automatic: bool,
                 pending: list[str]) -> list[Line]:
    """Return the text of `status`: profile, server, players, mods, server build, unfinished work."""
    label, explanation = server_status_text(server)
    lines = [profile_line(profile), sentence(f"Server: {label}. {explanation}"), players_line(server)]
    lines.append(way_out(f"Mods: {mods_summary(updates, automatic)}"))
    lines.append(way_out(f"DayZ server: {build_summary(updates.get('server_build'), automatic)}"))
    if pending:
        names = ", ".join(KIND_TEXTS.get(kind, ("Unfinished work",))[0] for kind in pending)
        lines.append(sentence(f"Recovery pending: {names}. {RECOVERY_WAY_OUT}"))
    return lines


def players_line(server: dict[str, Any]) -> Line:
    """Return the player count of a running server; never a name (P3)."""
    if server.get("state") not in ("RUNNING_MANAGED", "RUNNING_EXTERNAL"):
        return sentence("Players: none, the server is not running.")
    players, slots = server.get("players"), server.get("max_players")
    if not isinstance(players, int) or not isinstance(slots, int):
        return sentence(f"Players: {PLAYERS_NOT_KNOWN.lower()}.")
    return sentence(f"Players: {players} of {slots}.")
