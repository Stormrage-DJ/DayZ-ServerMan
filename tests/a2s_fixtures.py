"""Shared A2S datagrams for the player-count and names tests (D18): the datagrams recorded in spike 8.4 and
protocol-correct synthetic player answers, plus a scripted UDP socket so that no test opens a real socket."""
from __future__ import annotations

import socket
import struct

# Recorded on 2026-10-05 from the customer's test server on 127.0.0.1:3305 (spike 8.4): the A2S_INFO answer
# (0 of 60 players, no challenge) and the A2S_PLAYER answer to the challenge request (empty list, no challenge)
RECORDED_INFO = bytes.fromhex(
    "ffffffff491174657374323032363130303100636865726e61727573706c7573006461797a00000000003c0064770001312e"
    "32392e31363337303900b1e60c05f4081406ca4001626174746c6579652c65787465726e616c2c70726976486976652c7368"
    "6172644142433132332c6c7173302c65746d312e3030303030302c656e746d312e3030303030302c6d6f642c30393a303700"
    "ac5f030000000000")
RECORDED_EMPTY_PLAYERS = bytes.fromhex("ffffffff4400")

# Synthetic names: UTF-8 in three scripts, an empty name (a player still connecting), and a long name
UTF8_NAMES = ["Dr. Kovač", "Сергей_Волков", "玩家一号", "Ash 🔥 Walker"]
LONG_NAME = "Chernarus-Survivor-" + "x" * 101
SEVERAL = [(0, "Ash_Walker", 0, 11520.5), (1, UTF8_NAMES[0], 3, 3840.0), (2, UTF8_NAMES[1], 0, 61.9),
           (3, UTF8_NAMES[2], 0, 59.0), (4, UTF8_NAMES[3], -2, 0.0), (5, "", 0, 4.0), (6, LONG_NAME, 0, 172800.0)]
# The real run of QF-083: DayZ answered 2 players with empty names, connected 45 and 43 minutes
NAMELESS_RUN = [(0, "", 0, 2712.4), (1, "", 0, 2591.0)]
# 25 players with distinct names and falling connection times
TWENTY_FIVE = [(index, f"Survivor_{index:02d}", 0, float(11520 - index * 450)) for index in range(25)]


def information_answer(name: str, players: int = 12, max_players: int = 60, game_port: int = 3302,
                       flags: int | None = 0xB1) -> bytes:
    """Return an A2S_INFO answer laid out like the recorded DayZ answer, with the given name, count and port.

    `flags` is the extra data flag: 0xB1 (port, Steam id, keywords, game id) as DayZ sends it; None leaves
    the extra data out; a flag without 0x80 carries no game port.
    """
    body = (b"\xff\xff\xff\xff\x49\x11" + name.encode("utf-8") + b"\x00" + b"chernarusplus\x00dayz\x00\x00"
            + struct.pack("<hBBB", 0, players, max_players, 0) + b"dw\x00\x01" + b"1.29.163709\x00")
    if flags is None:
        return body
    extra = bytes([flags])
    if flags & 0x80:
        extra += struct.pack("<H", game_port)
    if flags & 0x10:
        extra += struct.pack("<Q", 90_270_000_000_000_000)
    if flags & 0x20:
        extra += b"battleye,external\x00"
    if flags & 0x01:
        extra += struct.pack("<Q", 221_100)
    return body + extra


def player_entry(index: int, name: str, score: int, seconds: float) -> bytes:
    """Return one A2S_PLAYER entry: index, NUL-terminated UTF-8 name, int32 score, float32 duration."""
    return bytes([index]) + name.encode("utf-8") + b"\x00" + struct.pack("<lf", score, seconds)


def player_answer(entries: list[tuple[int, str, int, float]], trailing: bytes = b"") -> bytes:
    """Return a single-packet A2S_PLAYER answer with the given entries."""
    return b"\xff\xff\xff\xff\x44" + bytes([len(entries)]) + b"".join(
        player_entry(*entry) for entry in entries) + trailing


def split_packets(answer: bytes, chunk: int, answer_id: int = 0x0102_0304) -> list[bytes]:
    """Split an answer into Source-layout split packets of at most `chunk` payload bytes."""
    parts = [answer[start:start + chunk] for start in range(0, len(answer), chunk)]
    return [b"\xff\xff\xff\xfe" + struct.pack("<LBBh", answer_id, len(parts), number, 1248) + part
            for number, part in enumerate(parts)]


def challenge(token: bytes = b"\x0a\x0b\x0c\x0d") -> bytes:
    """Return an A2S challenge answer carrying the token."""
    return b"\xff\xff\xff\xff\x41" + token


class ScriptedSocket:
    """UDP socket stand-in: records what is sent and replays scripted datagrams, timeouts, or errors.

    A script item is bytes (from the queried server), a `(bytes, address)` pair (from another sender), or an
    exception instance to raise. An exhausted script times out like a silent server.
    """

    def __init__(self, port: int, script: list[object]) -> None:
        """Store the queried port and the replies in order."""
        self.port = port
        self.script = list(script)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeouts: list[float] = []
        self.closed = False

    def __enter__(self) -> "ScriptedSocket":
        """Enter the socket context like `socket.socket`."""
        return self

    def __exit__(self, *_details: object) -> None:
        """Close the socket at the end of the read."""
        self.closed = True

    def settimeout(self, value: float) -> None:
        """Record the receive timeout that the reader sets."""
        self.timeouts.append(value)

    def sendto(self, payload: bytes, address: tuple[str, int]) -> int:
        """Record one request."""
        self.sent.append((payload, address))
        return len(payload)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        """Return the next scripted datagram; fail like Windows when it is larger than the buffer."""
        if not self.script:
            raise socket.timeout("timed out")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        datagram, address = item if isinstance(item, tuple) else (item, ("127.0.0.1", self.port))
        if len(datagram) > size:
            raise OSError(10040, "message too long")
        return datagram, address


def frozen_clock() -> float:
    """Return one fixed clock reading, so that a wait computed as deadline minus now is exact.

    Python 3.12 on Windows reads `time.monotonic` from GetTickCount64, in 15.6 ms steps. Two readings in one
    step give `(t + wait) - t`, which exceeds `wait` by a rounding error at some uptimes. That happened on a
    freshly started CI runner (QF-067). A fixed reading of 0.0 keeps the arithmetic exact.
    """
    return 0.0
