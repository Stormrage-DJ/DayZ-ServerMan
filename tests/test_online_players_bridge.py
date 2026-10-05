"""D18 bridge and privacy tests: `get_online_players` answers the names while the managed server runs, and no
name from a player answer reaches the structured log, an operation record, a file of the data root, or an
error text. The names come from protocol-correct datagrams through a scripted socket; no real socket is used.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))
sys.path.insert(0, str(PROJECT_ROOT))

from dayz_serverman.adapters.steam_player_query import SteamPlayerQuery  # noqa: E402
from dayz_serverman.application.online_players import OnlinePlayersCoordinator  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.online_players import OnlinePlayer, PlayerList, PlayerListState  # noqa: E402
from dayz_serverman.host.api import HostApi  # noqa: E402
from tests.a2s_fixtures import (  # noqa: E402
    RECORDED_EMPTY_PLAYERS, SEVERAL, ScriptedSocket, challenge, player_answer, split_packets,
)

PORT = 3305
# Names whose appearance anywhere outside the answer would be a leak
NAMES = [name for _index, name, _score, _seconds in SEVERAL if name]


class _Lifecycle:
    """Running-port source with a scripted port."""

    def __init__(self, port: int | None) -> None:
        """Store the port that the coordinator will find."""
        self.port = port

    def running_query_port(self) -> int | None:
        """Return the scripted port."""
        return self.port


class _Reader:
    """Player-list reader with a scripted answer that records the asked ports."""

    def __init__(self, answer: tuple[OnlinePlayer, ...] | None) -> None:
        """Store the answer."""
        self.answer = answer
        self.ports: list[int] = []

    def read_players(self, port: int) -> tuple[OnlinePlayer, ...] | None:
        """Record the port and return the answer."""
        self.ports.append(port)
        return self.answer


class OnlinePlayersCoordinatorTests(unittest.TestCase):
    """States and value shape of the names read."""

    def test_not_running_asks_no_port(self) -> None:
        """Without a managed server the read answers NOT_RUNNING and sends nothing."""
        reader = _Reader(())
        value = OnlinePlayersCoordinator(_Lifecycle(None), reader).get_online_players({})
        self.assertEqual(value, {"state": "NOT_RUNNING", "players": []})
        self.assertEqual(reader.ports, [])

    def test_no_answer_and_answer(self) -> None:
        """A failed read is a normal NO_ANSWER value; an answer lists name and whole seconds."""
        self.assertEqual(OnlinePlayersCoordinator(_Lifecycle(PORT), _Reader(None)).get_online_players({}),
                         {"state": "NO_ANSWER", "players": []})
        players = (OnlinePlayer(0, "Ash_Walker", 0, 3840.9), OnlinePlayer(1, "", 0, None))
        reader = _Reader(players)
        value = OnlinePlayersCoordinator(_Lifecycle(PORT), reader).get_online_players({})
        self.assertEqual(value, {"state": "OK", "players": [
            {"name": "Ash_Walker", "duration_seconds": 3840}, {"name": "", "duration_seconds": None}]})
        self.assertEqual(reader.ports, [PORT])

    def test_parameters_are_refused(self) -> None:
        """The read takes no parameters."""
        with self.assertRaises(ApplicationCallError):
            OnlinePlayersCoordinator(_Lifecycle(PORT), _Reader(())).get_online_players({"profile_id": "a"})

    def test_values_keep_names_out_of_their_repr(self) -> None:
        """A log line or a traceback that shows a value shows no name."""
        player = OnlinePlayer(0, "Ash_Walker", 0, 1.0)
        for text in (repr(player), str(player), repr(PlayerList(PlayerListState.OK, (player,)))):
            self.assertNotIn("Ash_Walker", text)


class OnlinePlayersPrivacyTests(unittest.TestCase):
    """No name from a player answer is logged, stored, or put into an error text (D18)."""

    def setUp(self) -> None:
        """Build the real composition on a temporary data root with a scripted names reader."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_players_")
        self.root = Path(self.temporary.name) / "Manager"
        self.scripts: list[list[object]] = []

        def reader() -> SteamPlayerQuery:
            """Return the production reader over a socket that replays the next script."""
            return SteamPlayerQuery(socket_factory=lambda: ScriptedSocket(PORT, self.scripts.pop(0)))
        patches = [mock.patch("dayz_serverman.lifecycle_composition.SteamPlayerQuery", side_effect=reader),
                   mock.patch.object(ReadinessLifecycleService, "running_query_port", return_value=PORT),
                   mock.patch("socket.socket", side_effect=AssertionError("a test opened a real socket"))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.composition = build_composition(self.root)
        # Write every bridge line, including the debug request and success lines
        self.composition.logger._minimum_level = 10
        self.api = HostApi(self.composition.bridge)

    def tearDown(self) -> None:
        """Shut the operations down and remove the temporary data root."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def read(self, script: list[object]) -> dict:
        """Read the names once with one scripted exchange."""
        self.scripts.append(script)
        return self.api.get_online_players()

    def test_names_reach_the_page_and_nothing_else(self) -> None:
        """OK, challenge, split, failed and unexpected reads leave no name outside the answers."""
        answer = player_answer(SEVERAL)
        results = [
            self.read([answer]),
            self.read([challenge(), answer]),
            self.read([*split_packets(answer, 60)]),
            self.read([answer[:-5]]),
            self.read([RECORDED_EMPTY_PLAYERS]),
            # An unexpected failure whose text holds a name
            self.read([RuntimeError(f"socket broke while reading {NAMES[0]}")]),
            self.composition.bridge.dispatch({"contract_version": 1, "request_id": "r-1",
                                              "method": "get_online_players", "parameters": {"name": NAMES[1]}}),
        ]
        # The page gets the names of the three complete answers
        for result in results[:3]:
            self.assertEqual(result["value"]["state"], "OK")
            self.assertEqual([player["name"] for player in result["value"]["players"]],
                             [entry[1] for entry in SEVERAL])
        self.assertEqual([result["value"]["state"] for result in results[3:5]], ["NO_ANSWER", "OK"])
        self.assertEqual([result["success"] for result in results[5:]], [False, False])
        # Error texts and the other reads of the page hold no name
        texts = [json.dumps(result["error"], ensure_ascii=False) for result in results[5:]]
        texts.append(json.dumps(self.api.get_server_status(), ensure_ascii=False))
        texts.append(json.dumps(self.api.get_application_snapshot(), ensure_ascii=False))
        # The structured log exists, and no file under the data root holds a name in any encoding
        log = self.composition.logger.path
        self.assertIn("get_online_players", log.read_text(encoding="utf-8"))
        stored = [path.read_bytes() for path in self.root.rglob("*") if path.is_file()]
        self.assertTrue(stored)
        for name in NAMES:
            forms = {name.encode("utf-8"), json.dumps(name)[1:-1].encode("ascii"), name.encode("utf-16-le")}
            for text in texts:
                self.assertNotIn(name, text)
            for content in stored:
                for form in forms:
                    self.assertNotIn(form, content, name)


if __name__ == "__main__":
    unittest.main()
