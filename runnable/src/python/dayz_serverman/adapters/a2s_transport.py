"""Shared UDP rules of the localhost Steam A2S readers: who is asked, how large a datagram may be, and which
datagram counts as the server's answer. Used by the readiness probe (count) and by the names reader (D18)."""

from __future__ import annotations

import socket


# Only the server on this computer is asked
LOCALHOST = "127.0.0.1"
# Largest datagram one receive accepts; Windows fails the receive of a larger one, and so the read
MAX_DATAGRAM_BYTES = 4096


def udp_socket() -> socket.socket:
    """Return a new IPv4 UDP socket for one read."""
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def from_queried_server(address: object, port: int) -> bool:
    """Return whether a datagram came from 127.0.0.1 on the queried port; any other sender is ignored."""
    return isinstance(address, tuple) and tuple(address[:2]) == (LOCALHOST, port)
