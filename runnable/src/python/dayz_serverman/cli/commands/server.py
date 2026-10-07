"""`server status` and `server players [--names]` (10.1, P3 in 6.6)."""

from __future__ import annotations

from typing import Any

from ...application.phase_wording import server_status_text
from ..output import CommandResult, Line, Table, TypeText, sentence
from ..read_wording import PROCESS_DIAGNOSTIC_TEXTS
from .common import duration_text, labelled, local_time, names_allowed
from .status import players_line

# Sentences of a names read without a list (overview_players_panel.js wording where the window has one)
PLAYERS_TEXTS = {
    "NOT_RUNNING": "No server runs under DayZ-ServerMan, so no player names can be read.",
    "NO_ANSWER": "Player names could not be read: the server did not answer.",
    "EMPTY": "No players are online.",
    "NAMELESS": "This server does not share player names. Connection times are shown.",
}


def status(context: Any) -> CommandResult:
    """Show the state of the server with its explanation, process detail and player count."""
    server = context.call("get_server_status")
    label, explanation = server_status_text(server)
    lines: list[Line] = [sentence(f"State: {label}"), sentence(explanation)]
    if server.get("diagnostic_code"):
        lines.append(sentence(PROCESS_DIAGNOSTIC_TEXTS.get(str(server["diagnostic_code"]),
                                                           "The server state could not be confirmed.")))
    elif server.get("query_port"):
        lines.append(labelled("Steam query port", str(server["query_port"])))
    if server.get("started_at"):
        lines.append(labelled("Started", local_time(server["started_at"])))
    lines.append(players_line(server))
    return CommandResult(server, lines)


def players(context: Any) -> CommandResult:
    """Show the players online: count always, names only where P3 allows them; names are never stored."""
    answer = context.call("get_online_players")
    shown = names_allowed(context)
    entries = [player for player in answer.get("players", []) if isinstance(player, dict)]
    # JSON keeps the names only with --names; without them each player keeps its connection time
    value = answer if context.options.names else {
        **answer, "players": [{"duration_seconds": player.get("duration_seconds")} for player in entries]}
    state = answer.get("state")
    if state != "OK":
        return CommandResult(value, [sentence(PLAYERS_TEXTS.get(str(state), PLAYERS_TEXTS["NO_ANSWER"]))])
    if not entries:
        return CommandResult(value, [sentence(PLAYERS_TEXTS["EMPTY"])])
    named = sorted((player for player in entries if str(player.get("name") or "").strip()),
                   key=lambda player: str(player["name"]).strip().casefold())
    nameless = sorted((player for player in entries if not str(player.get("name") or "").strip()),
                      key=lambda player: -(player.get("duration_seconds") or -1))
    lines: list[Line | Table] = [sentence(f"Players online: {len(entries)}")]
    rows: list[tuple[str, ...]] = []
    if shown:
        rows += [(str(player["name"]).strip(), duration_text(player.get("duration_seconds"))) for player in named]
    else:
        nameless = sorted(entries, key=lambda player: -(player.get("duration_seconds") or -1))
    rows += [(f"Player {index}", duration_text(player.get("duration_seconds")))
             for index, player in enumerate(nameless, start=1)]
    if shown and not named:
        lines.append(sentence(PLAYERS_TEXTS["NAMELESS"]))
    if not shown:
        lines.append(sentence("Names are shown on a terminal, or with ", TypeText("--names"), "."))
    names_column = frozenset({0}) if shown and named else frozenset()
    lines.append(Table(("Player", "Time online"), tuple(rows), value_columns=names_column))
    return CommandResult(value, lines)
