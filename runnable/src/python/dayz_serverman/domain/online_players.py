"""Online players of a running DayZ server, as its Steam server query reports them (D18).

The player count comes from the A2S_INFO answer of the readiness probe. The names come from an A2S_PLAYER
answer read only on request. Player names are personal data of other people: they live in memory for one
answer only and are never logged, stored, cached, or put into an error text. The value types therefore keep
names out of their `repr`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class PlayerCount:
    """Players online and player slots, from bytes of the A2S_INFO answer."""

    players: int
    max_players: int


@dataclass(frozen=True)
class InformationAnswer:
    """A valid A2S_INFO answer; the count is None when the answer ends before the player fields.

    The server name (the hostname of serverDZ.cfg) and the game port from the extra data identify the
    server for a match with a profile; each is None when the answer does not carry it.
    """

    count: PlayerCount | None
    # Kept out of repr so that a log line that shows the answer does not show the server name
    server_name: str | None = field(default=None, repr=False)
    game_port: int | None = None


@dataclass(frozen=True)
class OnlinePlayer:
    """One entry of an A2S_PLAYER answer.

    `index` and `score` are parsed for completeness; DayZ reports no score. `duration_seconds` is the time
    connected, or None when the server reports no usable value.
    """

    index: int
    # Excluded from repr so that a log line or a traceback that shows the object does not show the name
    name: str = field(repr=False)
    score: int
    duration_seconds: float | None


class PlayerListState(str, Enum):
    """Outcome of one names read."""

    # The server answered; the list may be empty
    OK = "OK"
    # A managed server runs, but its query port gave no valid player answer in time
    NO_ANSWER = "NO_ANSWER"
    # No server runs under this manager, so there is no query port to ask
    NOT_RUNNING = "NOT_RUNNING"


@dataclass(frozen=True)
class PlayerList:
    """One transient names answer for the bridge; built per request and not kept."""

    state: PlayerListState
    players: tuple[OnlinePlayer, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, object]:
        """Return the bridge value: the state and, per player, the name and whole seconds connected."""
        return {
            "state": self.state.value,
            "players": [
                {"name": player.name, "duration_seconds": _whole_seconds(player.duration_seconds)}
                for player in self.players
            ],
        }


def usable_duration(value: float) -> float | None:
    """Return a reported connection time in seconds, or None when it is not finite or negative."""
    return value if math.isfinite(value) and value >= 0 else None


def _whole_seconds(value: float | None) -> int | None:
    """Return the connection time as whole seconds for the page, or None when it is not known."""
    return None if value is None else int(value)
