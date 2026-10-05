"""Bounded localhost Steam A2S readiness probing for DayZ servers."""

from __future__ import annotations

import socket
import time
from typing import Callable

from ..domain.online_players import InformationAnswer
from .a2s_codec import (
    INFO_QUERY, challenge_token, is_information, parse_information_count, parse_information_identity,
)
from .a2s_transport import LOCALHOST, MAX_DATAGRAM_BYTES, from_queried_server, udp_socket


# Datagrams from other senders that one exchange ignores before it gives up
MAX_STRAY_DATAGRAMS = 4


class SteamQueryProbe:
    """Report whether a localhost Steam query endpoint answers A2S_INFO, and the player count it gives."""

    def __init__(
        self, timeout_seconds: float = 0.25, *,
        socket_factory: Callable[[], socket.socket] = udp_socket,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the bounded wait of each query exchange, the socket source (a fake in tests) and the clock."""
        if timeout_seconds <= 0:
            raise ValueError("Steam query timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._socket_factory = socket_factory
        self._clock = clock

    def is_ready(self, port: int) -> bool:
        """Return True for a valid direct or challenge-based information reply."""
        return self.information(port) is not None

    def information(self, port: int) -> InformationAnswer | None:
        """Return the valid information answer with its player count, or None when none arrived.

        The count is taken from the same answer that proves readiness, so it costs no extra query.
        """
        if not 1 <= port <= 65_535:
            return None
        try:
            # Keep one source endpoint for the complete challenge exchange
            with self._socket_factory() as client:
                response = self._exchange(client, port, INFO_QUERY)
                # Complete one challenge round trip when required by the server
                token = challenge_token(response)
                if token is not None:
                    response = self._exchange(client, port, INFO_QUERY + token)
        except OSError:
            # Timeout and local network failures mean not ready for this poll
            return None
        if not is_information(response):
            return None
        name, game_port = parse_information_identity(response)
        return InformationAnswer(parse_information_count(response), name, game_port)

    def _exchange(self, client: socket.socket, port: int, payload: bytes) -> bytes:
        """Send one request and return the queried server's datagram within the bounded wait.

        Datagrams from another address or port are ignored, as the names reader does; a datagram above the
        size cap fails the receive, and a passed wait raises `TimeoutError`.
        """
        client.sendto(payload, (LOCALHOST, port))
        deadline = self._clock() + self._timeout_seconds
        for _datagram in range(MAX_STRAY_DATAGRAMS + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            # Each receive waits only for what is left of this exchange's wait
            client.settimeout(remaining)
            response, address = client.recvfrom(MAX_DATAGRAM_BYTES)
            if from_queried_server(address, port):
                return response
        raise TimeoutError("no answer from the queried server")
