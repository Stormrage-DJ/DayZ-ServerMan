"""Bounded localhost Steam A2S readiness probing for DayZ servers."""

from __future__ import annotations

import socket


# A2S_INFO request defined by the Steam server-query protocol
INFO_QUERY = b"\xff\xff\xff\xffTSource Engine Query\x00"
# Steam connectionless response prefix and supported response kinds
RESPONSE_PREFIX = b"\xff\xff\xff\xff"
INFO_RESPONSE = 0x49
CHALLENGE_RESPONSE = 0x41


class SteamQueryProbe:
    """Report whether a localhost Steam query endpoint answers A2S_INFO."""

    def __init__(self, timeout_seconds: float = 0.25) -> None:
        """Store the bounded receive timeout used by each query exchange."""
        if timeout_seconds <= 0:
            raise ValueError("Steam query timeout must be positive")
        self._timeout_seconds = timeout_seconds

    def is_ready(self, port: int) -> bool:
        """Return True for a valid direct or challenge-based information reply."""
        if not 1 <= port <= 65_535:
            return False
        try:
            # Keep one source endpoint for the complete challenge exchange
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(self._timeout_seconds)
                response = self._exchange(client, port, INFO_QUERY)
                if self._is_information(response):
                    return True
                # Complete one challenge round trip when required by the server
                if self._is_challenge(response):
                    challenged = self._exchange(client, port, INFO_QUERY + response[5:9])
                    return self._is_information(challenged)
        except OSError:
            # Timeout and local network failures mean not ready for this poll
            return False
        return False

    @staticmethod
    def _exchange(client: socket.socket, port: int, payload: bytes) -> bytes:
        """Send one bounded UDP request and return the received datagram."""
        client.sendto(payload, ("127.0.0.1", port))
        response, _address = client.recvfrom(65_535)
        return response

    @staticmethod
    def _is_information(response: bytes) -> bool:
        """Return whether the datagram is an A2S information response."""
        return len(response) >= 5 and response[:4] == RESPONSE_PREFIX and response[4] == INFO_RESPONSE

    @staticmethod
    def _is_challenge(response: bytes) -> bool:
        """Return whether the datagram carries a complete A2S challenge."""
        return (
            len(response) >= 9
            and response[:4] == RESPONSE_PREFIX
            and response[4] == CHALLENGE_RESPONSE
        )
