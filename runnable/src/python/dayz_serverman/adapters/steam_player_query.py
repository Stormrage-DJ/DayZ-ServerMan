"""Bounded localhost A2S_PLAYER read of the players online, on request only (D18).

One read sends at most two requests (the first, and one after a challenge) and stops at a fixed deadline.
It keeps the names in memory for the one answer it returns. It writes no log line, no file and no cache,
and every failure becomes a plain None, so no name can reach an error text.
"""

from __future__ import annotations

import socket
import time
from typing import Callable

from ..domain.online_players import OnlinePlayer
from .a2s_codec import (
    MAX_SPLIT_PACKETS,
    NO_CHALLENGE,
    PLAYER_QUERY,
    A2sFormatError,
    SplitAnswer,
    challenge_token,
    is_player_list,
    is_split_packet,
    parse_player_list,
)
from .a2s_transport import LOCALHOST, MAX_DATAGRAM_BYTES, from_queried_server, udp_socket


# Datagrams one read accepts in total: a challenge, every packet of a split answer, and a few strays
MAX_DATAGRAMS = MAX_SPLIT_PACKETS + 4
# Whole-read deadline in seconds; the customer's server answered in about 60 ms (spike 8.4)
DEFAULT_TIMEOUT_SECONDS = 1.0


class SteamPlayerQuery:
    """Read the player list of a localhost Steam query port within one deadline."""

    def __init__(
        self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, *,
        socket_factory: Callable[[], socket.socket] = udp_socket,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the deadline, the socket source (a fake in tests) and the monotonic clock."""
        if timeout_seconds <= 0:
            raise ValueError("Steam player query timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._socket_factory = socket_factory
        self._clock = clock

    def read_players(self, port: int) -> tuple[OnlinePlayer, ...] | None:
        """Return the players that the server reports, or None when no valid answer arrived in time."""
        if not 1 <= port <= 65_535:
            return None
        deadline = self._clock() + self._timeout_seconds
        try:
            # Keep one source endpoint for the request, the challenge round and every split packet
            with self._socket_factory() as client:
                answer = self._answer(client, port, deadline)
            return parse_player_list(answer)
        except (OSError, A2sFormatError):
            # Timeout, a local network failure or a malformed answer: the names are not known this time
            return None

    def _answer(self, client: socket.socket, port: int, deadline: float) -> bytes:
        """Ask for the player list and return the complete answer, after a challenge round when asked."""
        client.sendto(PLAYER_QUERY + NO_CHALLENGE, (LOCALHOST, port))
        challenged = False
        split: SplitAnswer | None = None
        for _datagram in range(MAX_DATAGRAMS):
            datagram = self._receive(client, port, deadline)
            if datagram is None:
                continue
            # Repeat the request once with the token when the server sends a challenge first
            token = challenge_token(datagram)
            if token is not None and not challenged and split is None:
                challenged = True
                client.sendto(PLAYER_QUERY + token, (LOCALHOST, port))
                continue
            # A single-packet answer is complete at once; a split answer when its last packet arrived
            if is_player_list(datagram) and split is None:
                return datagram
            if is_split_packet(datagram):
                split = split or SplitAnswer()
                joined = split.add(datagram)
                if joined is not None:
                    return joined
                continue
            raise A2sFormatError("unexpected answer to the player query")
        raise A2sFormatError("too many datagrams for one player query")

    def _receive(self, client: socket.socket, port: int, deadline: float) -> bytes | None:
        """Receive one datagram before the deadline; return None for a datagram from another sender."""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError("player query deadline passed")
        client.settimeout(remaining)
        datagram, address = client.recvfrom(MAX_DATAGRAM_BYTES)
        # Ignore a stray datagram that did not come from the queried server
        if not from_queried_server(address, port):
            return None
        return datagram
