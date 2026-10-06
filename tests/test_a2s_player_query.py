"""D18 protocol tests: the player count in the A2S_INFO answer and the bounded A2S_PLAYER names read.

Every exchange runs against recorded or synthetic datagrams through a scripted socket; `socket.socket` is
patched to fail, so no test opens a real socket.
"""
from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))
sys.path.insert(0, str(PROJECT_ROOT))

from dayz_serverman.adapters.a2s_codec import (  # noqa: E402
    MAX_ANSWER_BYTES, NO_CHALLENGE, PLAYER_QUERY, A2sFormatError, SplitAnswer, parse_information_count,
    parse_player_list,
)
from dayz_serverman.adapters.steam_player_query import SteamPlayerQuery  # noqa: E402
from dayz_serverman.adapters.a2s_codec import INFO_QUERY  # noqa: E402
from dayz_serverman.adapters.steam_query import SteamQueryProbe  # noqa: E402
from dayz_serverman.domain.online_players import InformationAnswer, PlayerCount  # noqa: E402
from tests.a2s_fixtures import (  # noqa: E402
    LONG_NAME, NAMELESS_RUN, RECORDED_EMPTY_PLAYERS, RECORDED_INFO, SEVERAL, TWENTY_FIVE, ScriptedSocket, challenge,
    player_answer, split_packets,
)

PORT = 3305


class _NoRealSocket(unittest.TestCase):
    """Fail any test that would create a real socket."""

    def setUp(self) -> None:
        """Patch the socket constructor for the whole test."""
        patcher = mock.patch("socket.socket", side_effect=AssertionError("a test opened a real socket"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def reader(self, script: list[object], clock=None) -> tuple[SteamPlayerQuery, ScriptedSocket]:
        """Return a names reader whose only socket replays the script."""
        fake = ScriptedSocket(PORT, script)
        extra = {"clock": clock} if clock else {}
        return SteamPlayerQuery(socket_factory=lambda: fake, **extra), fake


class PlayerCountTests(unittest.TestCase):
    """The count comes from the A2S_INFO answer that proves readiness."""

    def setUp(self) -> None:
        """Give the probe an inert socket; its exchanges are replaced by recorded datagrams."""
        patcher = mock.patch("socket.socket", new=mock.MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recorded_information_answer_gives_zero_of_sixty(self) -> None:
        """The spike datagram: players 0, max players 60, from the one readiness exchange."""
        self.assertEqual(parse_information_count(RECORDED_INFO), PlayerCount(0, 60))
        probe = SteamQueryProbe()
        probe._exchange = mock.Mock(return_value=RECORDED_INFO)  # type: ignore[method-assign]
        self.assertEqual(probe.information(PORT), InformationAnswer(PlayerCount(0, 60), "test20261001", 3302))
        self.assertTrue(probe.is_ready(PORT))
        self.assertEqual(probe._exchange.call_count, 2)

    def test_count_fields_at_their_byte_positions(self) -> None:
        """Players and max players are the two bytes after the app id; bots do not count."""
        changed = bytearray(RECORDED_INFO)
        position = RECORDED_INFO.index(b"dayz\x00") + 5 + 1 + 2
        changed[position:position + 3] = bytes([127, 128, 5])
        self.assertEqual(parse_information_count(bytes(changed)), PlayerCount(127, 128))

    def test_challenged_information_answer_keeps_the_count(self) -> None:
        """A server that asks for a challenge still gives the count in the second answer."""
        probe = SteamQueryProbe()
        probe._exchange = mock.Mock(side_effect=[challenge(), RECORDED_INFO])  # type: ignore[method-assign]
        self.assertEqual(probe.information(PORT).count, PlayerCount(0, 60))

    def test_truncated_information_answer_is_ready_without_a_count(self) -> None:
        """Every cut of the recorded answer parses without error; a cut before the count gives None."""
        count_end = RECORDED_INFO.index(b"dayz\x00") + 5 + 1 + 5
        for cut in range(0, len(RECORDED_INFO)):
            with self.subTest(cut=cut):
                datagram = RECORDED_INFO[:cut]
                expected = PlayerCount(0, 60) if cut >= count_end else None
                self.assertEqual(parse_information_count(datagram), expected)
                probe = SteamQueryProbe()
                probe._exchange = mock.Mock(return_value=datagram)  # type: ignore[method-assign]
                answer = probe.information(PORT)
                self.assertEqual(answer is not None, cut >= 5)
                if answer is not None:
                    self.assertEqual(answer.count, expected)

    def test_count_comes_only_from_the_queried_server(self) -> None:
        """QF-066: an answer from another local port is ignored; an oversize datagram is no answer."""
        forged = bytearray(RECORDED_INFO)
        position = RECORDED_INFO.index(b"dayz\x00") + 5 + 1 + 2
        forged[position] = 59
        stray = (bytes(forged), ("127.0.0.1", 50123))
        other_host = (bytes(forged), ("10.0.0.5", PORT))
        fake = ScriptedSocket(PORT, [stray, other_host, RECORDED_INFO])
        probe = SteamQueryProbe(socket_factory=lambda: fake)
        self.assertEqual(probe.information(PORT), InformationAnswer(PlayerCount(0, 60), "test20261001", 3302))
        self.assertEqual(fake.sent, [(INFO_QUERY, ("127.0.0.1", PORT))])
        # Only strays, then silence: not ready and no count
        fake = ScriptedSocket(PORT, [stray, stray])
        self.assertIsNone(SteamQueryProbe(socket_factory=lambda: fake).information(PORT))
        # A datagram above 4096 bytes fails the receive
        fake = ScriptedSocket(PORT, [RECORDED_INFO + b"\x00" * 4000])
        self.assertIsNone(SteamQueryProbe(socket_factory=lambda: fake).information(PORT))
        self.assertTrue(all(0 < value <= 0.25 for value in fake.timeouts))

    def test_strays_keep_the_wait_of_one_exchange(self) -> None:
        """Strays do not lengthen the 0.25 s wait of an information exchange."""
        now = [0.0]

        def clock() -> float:
            """Advance 0.1 s on each reading of the clock."""
            now[0] += 0.1
            return now[0]
        stray = (RECORDED_INFO, ("127.0.0.1", PORT + 1))
        fake = ScriptedSocket(PORT, [stray] * 6)
        self.assertIsNone(SteamQueryProbe(socket_factory=lambda: fake, clock=clock).information(PORT))
        self.assertEqual([round(value, 6) for value in fake.timeouts], [0.15, 0.05])

    def test_no_answer_gives_no_count(self) -> None:
        """A timeout, a wrong kind, and an invalid port are not ready and carry no count."""
        for reply in (socket.timeout("timed out"), RECORDED_EMPTY_PLAYERS, b""):
            probe = SteamQueryProbe()
            effect = {"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply}
            probe._exchange = mock.Mock(**effect)  # type: ignore[method-assign]
            with self.subTest(reply=repr(reply)):
                self.assertIsNone(probe.information(PORT))
        self.assertIsNone(SteamQueryProbe().information(0))


class PlayerListParserTests(unittest.TestCase):
    """Parsing of complete and malformed A2S_PLAYER answers."""

    def test_recorded_empty_answer_is_an_empty_list(self) -> None:
        """The spike answer with 0 players, and the shape of the QF-083 run: names empty, times present."""
        self.assertEqual(parse_player_list(RECORDED_EMPTY_PLAYERS), ())
        players = parse_player_list(player_answer(NAMELESS_RUN))
        self.assertEqual([(player.name, int(player.duration_seconds)) for player in players], [("", 2712), ("", 2591)])

    def test_several_names_with_utf8_empty_and_long_names(self) -> None:
        """Index, name, score and duration of every entry; trailing bytes after the last entry are ignored."""
        players = parse_player_list(player_answer(SEVERAL, trailing=b"\x00\x01"))
        self.assertEqual([(p.index, p.name, p.score) for p in players], [entry[:3] for entry in SEVERAL])
        self.assertEqual([round(p.duration_seconds, 1) for p in players], [entry[3] for entry in SEVERAL])
        self.assertEqual(players[5].name, "")
        self.assertEqual(players[6].name, LONG_NAME)

    def test_twenty_five_players(self) -> None:
        """A full panel's worth of players in one datagram."""
        players = parse_player_list(player_answer(TWENTY_FIVE))
        self.assertEqual(len(players), 25)
        self.assertEqual(players[24].name, "Survivor_24")

    def test_invalid_bytes_and_durations_are_contained(self) -> None:
        """Bytes that are not UTF-8 are replaced; a negative or non-finite time is not known."""
        answer = player_answer([(0, "x", 0, -1.0), (1, "y", 0, float("nan")), (2, "z", 0, float("inf"))])
        answer = answer.replace(b"x\x00", b"\xff\xfe\x00", 1)
        players = parse_player_list(answer)
        self.assertEqual(players[0].name, "��")
        self.assertEqual([p.duration_seconds for p in players], [None, None, None])

    def test_every_truncation_raises_a_format_error_without_content(self) -> None:
        """Each cut inside the entries fails with a fixed message that holds no name."""
        answer = player_answer(SEVERAL)
        for cut in range(0, len(answer)):
            with self.subTest(cut=cut), self.assertRaises(A2sFormatError) as caught:
                parse_player_list(answer[:cut])
            text = str(caught.exception) + repr(caught.exception)
            for _index, name, _score, _seconds in SEVERAL:
                if len(name) > 2:
                    self.assertNotIn(name[:3], text)

    def test_wrong_prefix_or_kind_is_refused(self) -> None:
        """Only a 0x44 answer with the single-packet prefix is a player list."""
        answer = player_answer(SEVERAL)
        for broken in (b"\xff\xff\xff\xfe" + answer[4:], answer[:4] + b"\x49" + answer[5:], challenge()):
            with self.subTest(broken=broken[:6]), self.assertRaises(A2sFormatError):
                parse_player_list(broken)

    def test_split_answer_bounds(self) -> None:
        """Compressed, oversized, inconsistent and out-of-range split packets are refused."""
        packets = split_packets(player_answer(TWENTY_FIVE), 200)
        compressed = split_packets(player_answer(TWENTY_FIVE), 200, answer_id=0x8000_0001)
        self.assertRaises(A2sFormatError, SplitAnswer().add, compressed[0])
        self.assertRaises(A2sFormatError, SplitAnswer().add, packets[0][:11])
        other = SplitAnswer(); other.add(packets[0])
        self.assertRaises(A2sFormatError, other.add, split_packets(player_answer(SEVERAL), 50, 7)[1])
        too_many = split_packets(b"\x00" * 1700, 100)
        self.assertRaises(A2sFormatError, SplitAnswer().add, too_many[0])
        large = SplitAnswer()
        big = split_packets(b"\x00" * (MAX_ANSWER_BYTES + 10), MAX_ANSWER_BYTES // 2 + 5)
        large.add(big[0])
        self.assertRaises(A2sFormatError, large.add, big[1])


class PlayerQueryExchangeTests(_NoRealSocket):
    """The names read: requests, challenge round, split answers, bounds, and failures."""

    def test_recorded_answer_without_challenge_sends_one_request(self) -> None:
        """The customer's server answers the challenge request directly."""
        reader, fake = self.reader([RECORDED_EMPTY_PLAYERS])
        self.assertEqual(reader.read_players(PORT), ())
        self.assertEqual(fake.sent, [(PLAYER_QUERY + NO_CHALLENGE, ("127.0.0.1", PORT))])
        self.assertTrue(fake.closed)

    def test_challenge_round_repeats_the_request_with_the_token(self) -> None:
        """A 0x41 answer is followed by exactly one request that carries its token."""
        reader, fake = self.reader([challenge(b"\x01\x02\x03\x04"), player_answer(SEVERAL)])
        players = reader.read_players(PORT)
        self.assertEqual([player.name for player in players], [entry[1] for entry in SEVERAL])
        self.assertEqual([payload for payload, _address in fake.sent],
                         [PLAYER_QUERY + NO_CHALLENGE, PLAYER_QUERY + b"\x01\x02\x03\x04"])

    def test_a_second_challenge_fails_the_read(self) -> None:
        """The read makes one challenge round only."""
        reader, fake = self.reader([challenge(), challenge(), player_answer(SEVERAL)])
        self.assertIsNone(reader.read_players(PORT))
        self.assertEqual(len(fake.sent), 2)

    def test_split_answer_is_joined_in_any_order(self) -> None:
        """25 players in five packets, out of order, with one packet repeated, after a challenge."""
        packets = split_packets(player_answer(TWENTY_FIVE), 120)
        script = [challenge(), packets[2], packets[0], packets[2], *packets[3:], packets[1]]
        reader, _fake = self.reader(script)
        self.assertEqual(len(reader.read_players(PORT)), 25)

    def test_stray_datagrams_from_other_senders_are_ignored(self) -> None:
        """Only datagrams from 127.0.0.1 on the queried port count."""
        stray = (player_answer([(0, "Intruder", 0, 1.0)]), ("127.0.0.1", PORT + 1))
        reader, _fake = self.reader([stray, (b"x", ("10.0.0.5", PORT)), player_answer(TWENTY_FIVE)])
        self.assertEqual(len(reader.read_players(PORT)), 25)

    def test_malformed_truncated_and_unexpected_answers_give_none(self) -> None:
        """A cut answer, an information answer, an oversize datagram and garbage are no names."""
        answer = player_answer(SEVERAL)
        for script in ([answer[:len(answer) - 3]], [RECORDED_INFO], [b"\x00" * 5000], [b"garbage!"],
                       [challenge(), answer[:20]], [split_packets(answer, 40)[0]]):
            with self.subTest(script=[len(item) for item in script]):
                reader, _fake = self.reader(script)
                self.assertIsNone(reader.read_players(PORT))

    def test_timeout_and_network_failure_give_none(self) -> None:
        """A silent server and a refused port end the read with None."""
        for script in ([], [ConnectionResetError(10054, "reset")], [challenge()]):
            with self.subTest(script=script):
                reader, _fake = self.reader(script)
                self.assertIsNone(reader.read_players(PORT))

    def test_deadline_bounds_the_whole_read(self) -> None:
        """Each receive waits only for the rest of the deadline; a passed deadline stops the read."""
        now = [100.0]
        packets = split_packets(player_answer(TWENTY_FIVE), 120)

        def clock() -> float:
            """Advance 0.3 s on each reading of the clock."""
            now[0] += 0.3
            return now[0]
        reader, fake = self.reader(packets, clock=clock)
        self.assertIsNone(reader.read_players(PORT))
        # The deadline is 100.3 + 1.0; the receives start at 100.6, 100.9 and 101.2, and the fourth is too late
        self.assertEqual([round(value, 6) for value in fake.timeouts], [0.7, 0.4, 0.1])
        self.assertLess(len(fake.timeouts), len(packets))

    def test_strays_do_not_extend_the_deadline(self) -> None:
        """QF-064: stray datagrams before a silent server end the read at 1 s, not 1 s per receive."""
        now = [0.0]
        stray = (b"x", ("127.0.0.1", PORT + 1))

        def clock() -> float:
            """Advance 0.3 s on each reading of the clock, as if each stray arrived 0.3 s apart."""
            now[0] += 0.3
            return now[0]
        reader, fake = self.reader([stray, stray, stray], clock=clock)
        self.assertIsNone(reader.read_players(PORT))
        # The deadline is 0.3 + 1.0; each wait ends there (receives at 0.6, 0.9 and 1.2), never a full second later
        self.assertEqual([round(value, 6) for value in fake.timeouts], [0.7, 0.4, 0.1])
        self.assertEqual(len(fake.script), 0)

    def test_invalid_port_opens_nothing(self) -> None:
        """Ports outside 1-65535 are refused before a socket is made."""
        reader = SteamPlayerQuery(socket_factory=mock.Mock(side_effect=AssertionError("socket made")))
        for port in (0, 65_536, -1):
            self.assertIsNone(reader.read_players(port))
        with self.assertRaises(ValueError):
            SteamPlayerQuery(0)


if __name__ == "__main__":
    unittest.main()
