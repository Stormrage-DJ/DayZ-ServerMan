"""Bounded parsing of Steam A2S server-query datagrams: information, challenge, player list, split answers.

Every parser works on bytes only and opens no socket. A malformed datagram raises `A2sFormatError` with a
fixed message: no byte of the datagram, and so no player name, ever reaches an error text.
"""

from __future__ import annotations

import struct

from ..domain.online_players import OnlinePlayer, PlayerCount, usable_duration


# Steam connectionless prefix of a single-packet answer, and of one packet of a split answer
RESPONSE_PREFIX = b"\xff\xff\xff\xff"
SPLIT_PREFIX = b"\xff\xff\xff\xfe"
# Answer kinds: information (A2S_INFO), challenge, and player list (A2S_PLAYER)
INFO_RESPONSE = 0x49
CHALLENGE_RESPONSE = 0x41
PLAYER_RESPONSE = 0x44
# A2S_INFO request, and the A2S_PLAYER request that asks for a challenge with the token -1
INFO_QUERY = RESPONSE_PREFIX + b"TSource Engine Query\x00"
PLAYER_QUERY = RESPONSE_PREFIX + b"U"
NO_CHALLENGE = b"\xff\xff\xff\xff"
# Bounds of a split answer: packets per answer and joined bytes (255 players with long names fit easily)
MAX_SPLIT_PACKETS = 16
MAX_ANSWER_BYTES = 64 * 1024
# Header of one split packet: prefix, answer id, packet total, packet number, packet size (Source layout)
SPLIT_HEADER = struct.Struct("<4sLBBh")
# Extra data flag of an information answer: the game port follows (DayZ sets it; spike 8.4 shows 3302)
EXTRA_DATA_GAME_PORT = 0x80
# Answer ids with the top bit set mark a compressed answer, which this reader does not accept
COMPRESSED_FLAG = 0x8000_0000


class A2sFormatError(ValueError):
    """A datagram that does not follow the A2S protocol; the message never holds datagram content."""


def is_information(datagram: bytes) -> bool:
    """Return whether the datagram is an A2S information answer."""
    return len(datagram) >= 5 and datagram[:4] == RESPONSE_PREFIX and datagram[4] == INFO_RESPONSE


def challenge_token(datagram: bytes) -> bytes | None:
    """Return the four token bytes of a challenge answer, or None for any other datagram."""
    if len(datagram) >= 9 and datagram[:4] == RESPONSE_PREFIX and datagram[4] == CHALLENGE_RESPONSE:
        return datagram[5:9]
    return None


def is_player_list(datagram: bytes) -> bool:
    """Return whether the datagram is a complete single-packet A2S player answer."""
    return len(datagram) >= 6 and datagram[:4] == RESPONSE_PREFIX and datagram[4] == PLAYER_RESPONSE


def is_split_packet(datagram: bytes) -> bool:
    """Return whether the datagram is one packet of a split answer."""
    return datagram[:4] == SPLIT_PREFIX


def parse_information_count(datagram: bytes) -> PlayerCount | None:
    """Return players and max players of an information answer, or None when the answer ends early.

    Layout after the prefix and the kind byte: protocol (1 byte), four NUL-terminated strings (name, map,
    folder, game), app id (2 bytes), players (1 byte), max players (1 byte), bots (1 byte).
    """
    if not is_information(datagram):
        return None
    # Skip the protocol byte and the four strings without decoding them
    position = 6
    for _string in range(4):
        end = datagram.find(b"\x00", position)
        if end < 0:
            return None
        position = end + 1
    # The app id, players, max players and bots must all be present
    if len(datagram) < position + 5:
        return None
    return PlayerCount(datagram[position + 2], datagram[position + 3])


def parse_information_identity(datagram: bytes) -> tuple[str | None, int | None]:
    """Return the server name and the game port of an information answer, each None when not present.

    After the bots byte come server type, environment, visibility and VAC (1 byte each), the version string,
    and the extra data flag. Flag bit 0x80 means that the game port (2 bytes) follows first. The name is the
    server's hostname, not a player name; it is still never logged.
    """
    if not is_information(datagram):
        return None, None
    end = datagram.find(b"\x00", 6)
    if end < 0:
        return None, None
    name = datagram[6:end].decode("utf-8", errors="replace")
    # Skip map, folder and game, then app id, players, max players, bots and the four type bytes
    position = end + 1
    for _string in range(3):
        end = datagram.find(b"\x00", position)
        if end < 0:
            return name, None
        position = end + 1
    position += 2 + 3 + 4
    # Skip the version string; the extra data flag follows it
    end = datagram.find(b"\x00", position)
    if end < 0 or end + 1 >= len(datagram):
        return name, None
    flags = datagram[end + 1]
    if not flags & EXTRA_DATA_GAME_PORT or len(datagram) < end + 4:
        return name, None
    return name, struct.unpack_from("<H", datagram, end + 2)[0]


def parse_player_list(answer: bytes) -> tuple[OnlinePlayer, ...]:
    """Return the players of a complete A2S player answer.

    Each entry is: index (1 byte), name (NUL-terminated UTF-8), score (int32), duration (float32 seconds).
    Bytes after the last entry are ignored. A truncated entry raises `A2sFormatError`.
    """
    if not is_player_list(answer):
        raise A2sFormatError("not a player answer")
    count = answer[5]
    position = 6
    players: list[OnlinePlayer] = []
    for _entry in range(count):
        # Read the index and the name; a name without its NUL means that the answer was cut
        if position >= len(answer):
            raise A2sFormatError("player answer ends inside an entry")
        index = answer[position]
        end = answer.find(b"\x00", position + 1)
        if end < 0:
            raise A2sFormatError("player answer ends inside a name")
        name = answer[position + 1:end].decode("utf-8", errors="replace")
        position = end + 1
        # Read the score and the time connected
        if position + 8 > len(answer):
            raise A2sFormatError("player answer ends inside an entry")
        score, duration = struct.unpack_from("<lf", answer, position)
        position += 8
        players.append(OnlinePlayer(index, name, score, usable_duration(duration)))
    return tuple(players)


class SplitAnswer:
    """Join the packets of one split answer, bounded in packet count and total size."""

    def __init__(self) -> None:
        """Start without packets; the first packet fixes the answer id and the packet total."""
        self._answer_id: int | None = None
        self._total = 0
        self._parts: dict[int, bytes] = {}
        self._size = 0

    def add(self, datagram: bytes) -> bytes | None:
        """Add one packet; return the joined answer when every packet arrived, else None."""
        # Check the packet header against the bounds and the packets seen before
        if len(datagram) < SPLIT_HEADER.size or not is_split_packet(datagram):
            raise A2sFormatError("not a split packet")
        _prefix, answer_id, total, number, _size = SPLIT_HEADER.unpack_from(datagram)
        if answer_id & COMPRESSED_FLAG or not 1 <= total <= MAX_SPLIT_PACKETS or number >= total:
            raise A2sFormatError("split packet header is not supported")
        if self._answer_id is None:
            self._answer_id, self._total = answer_id, total
        elif (answer_id, total) != (self._answer_id, self._total):
            raise A2sFormatError("split packet belongs to another answer")
        # Keep the first copy of each packet and stop at the size bound
        if number not in self._parts:
            payload = datagram[SPLIT_HEADER.size:]
            self._size += len(payload)
            if self._size > MAX_ANSWER_BYTES:
                raise A2sFormatError("split answer is too large")
            self._parts[number] = payload
        if len(self._parts) < self._total:
            return None
        return b"".join(self._parts[number] for number in range(self._total))
