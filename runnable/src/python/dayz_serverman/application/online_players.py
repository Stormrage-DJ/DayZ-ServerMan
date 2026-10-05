"""Bridge method that reads the names of the players online while the managed server runs (D18).

The read happens only when the page asks for it (the open names panel), never on the status poll. The answer
goes back to the page and is not kept: no log field, no operation record, no file, no cache. The method is a
direct read, not an operation, so no operation record can hold a name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.online_players import OnlinePlayer, PlayerList, PlayerListState


class RunningQueryPort(Protocol):
    """Name the query port of the server that this manager runs."""

    def running_query_port(self) -> int | None:
        """Return the port, or None when no server runs under this manager."""
        ...


class PlayerListReader(Protocol):
    """Read the player list of one localhost query port."""

    def read_players(self, port: int) -> tuple[OnlinePlayer, ...] | None:
        """Return the players, or None when the server gave no valid answer in time."""
        ...


class OnlinePlayersCoordinator:
    """Serve `get_online_players`: a transient names answer for the managed server."""

    def __init__(self, lifecycle: RunningQueryPort, reader: PlayerListReader) -> None:
        """Bind the source of the running query port and the player-list reader."""
        self._lifecycle = lifecycle
        self._reader = reader

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table of the names read."""
        return {"get_online_players": self.get_online_players}

    def get_online_players(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the state of the read and the players with name and seconds connected.

        A failed read is a normal answer with the state NO_ANSWER, not a bridge error, so a server that does
        not answer leaves no failure line in the manager log every 10 seconds.
        """
        # This read takes no parameters
        if set(parameters):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "player list parameters are invalid")
        # Without a server that this manager runs there is no query port to ask
        port = self._lifecycle.running_query_port()
        if port is None:
            return PlayerList(PlayerListState.NOT_RUNNING).to_dict()
        players = self._reader.read_players(port)
        if players is None:
            return PlayerList(PlayerListState.NO_ANSWER).to_dict()
        return PlayerList(PlayerListState.OK, players).to_dict()
