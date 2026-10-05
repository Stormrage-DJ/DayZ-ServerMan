"""D18 extension: the player count of a server that runs outside the manager.

One A2S_INFO goes to the selected profile's configured query port; the count and the names read are allowed only
when the answer carries that profile's server name and game port. Ownership, readiness, state and guards stay as
observed. No test opens a real socket.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))
sys.path.insert(0, str(PROJECT_ROOT))

from dayz_serverman.adapters.a2s_codec import INFO_QUERY, parse_information_identity  # noqa: E402
from dayz_serverman.adapters.steam_query import SteamQueryProbe  # noqa: E402
from dayz_serverman.application.external_players import ExternalServerMatch  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import for_running_profile  # noqa: E402
from dayz_serverman.application.online_players import OnlinePlayersCoordinator  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState  # noqa: E402
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.online_players import InformationAnswer, PlayerCount  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from tests.a2s_fixtures import RECORDED_INFO, ScriptedSocket, information_answer  # noqa: E402
from tests.profile_fixtures import profile_payload  # noqa: E402
from tests.test_server_readiness import FakeLifecycle, FakeMissionProbe, FakeProbe, _Profiles, _Settings  # noqa: E402

QUERY_PORT = 3305
GAME_PORT = 2302
HOSTNAME = "Livonia Közösségi #1"


class _ScriptedProbe:
    """Information probe with one scripted answer that records the asked ports."""

    def __init__(self, answer: InformationAnswer | None) -> None:
        """Store the answer."""
        self.answer = answer
        self.ports: list[int] = []

    def information(self, port: int) -> InformationAnswer | None:
        """Record the port and return the answer."""
        self.ports.append(port)
        return self.answer


def matching(**changes: object) -> InformationAnswer:
    """Return an answer that matches the profile, with optional changes."""
    return replace(InformationAnswer(PlayerCount(12, 60), HOSTNAME, GAME_PORT), **changes)


class IdentityParserTests(unittest.TestCase):
    """Server name and game port of an information answer."""

    def test_recorded_answer_names_the_server_and_its_game_port(self) -> None:
        """The spike answer: hostname test20261001 and game port 3302 in the extra data."""
        self.assertEqual(parse_information_identity(RECORDED_INFO), ("test20261001", 3302))

    def test_type_bytes_are_skipped_whatever_their_value(self) -> None:
        """A server without VAC (byte 0) still gives its port: the four type bytes are skipped by count."""
        answer = information_answer("x", game_port=2402).replace(b"dw\x00\x01", b"dw\x00\x00", 1)
        self.assertEqual(parse_information_identity(answer), ("x", 2402))

    def test_missing_or_cut_extra_data_gives_no_port(self) -> None:
        """No extra data, a flag without the port bit, or a cut port gives no game port."""
        self.assertEqual(parse_information_identity(information_answer("x", flags=None)), ("x", None))
        self.assertEqual(parse_information_identity(information_answer("x", flags=0x31)), ("x", None))
        cut = information_answer("x", flags=0x80)[:-1]
        self.assertEqual(parse_information_identity(cut), ("x", None))
        for size in range(0, len(RECORDED_INFO)):
            with self.subTest(size=size):
                name, port = parse_information_identity(RECORDED_INFO[:size])
                self.assertIn(port, (None, 3302))


class ExternalMatchTests(unittest.TestCase):
    """The match rule: the selected profile's port, name and game port."""

    def setUp(self) -> None:
        """Write a server config with a hostname and a query port inside a temporary DayZ root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_external_")
        self.root = Path(self.temporary.name)
        self.config = self.root / "Config Files" / "serverDZ.cfg"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(f'hostname = "{HOSTNAME}";\nsteamQueryPort = {QUERY_PORT};\n', encoding="utf-8")
        self.profiles = _Profiles(ProfileRecord(7, ProfileInput.parse(profile_payload())))
        self.settings = _Settings(ManagerSettings(4, str(self.root), str(self.root / "DayZServer_x64.exe"),
                                                  None, None, None, None, MappingProxyType({})))
        self.selected: str | None = "livonia-main"

    def tearDown(self) -> None:
        """Remove the temporary DayZ root."""
        self.temporary.cleanup()

    def match(self, probe) -> ExternalServerMatch:
        """Return a match over the fixture profile with the given probe."""
        return ExternalServerMatch(self.profiles, self.settings, probe, lambda: self.selected)

    def test_matching_answer_gives_port_and_answer(self) -> None:
        """Name and game port equal to the profile: the configured port and the answer."""
        probe = _ScriptedProbe(matching())
        self.assertEqual(self.match(probe).find(), (QUERY_PORT, matching()))
        self.assertEqual(probe.ports, [QUERY_PORT])

    def test_every_mismatch_gives_no_match(self) -> None:
        """Another name, another game port, no game port, no name, or no answer."""
        # A name that only starts with the hostname is another server (QA R4)
        for answer in (matching(server_name="Livonia Közösségi #2"), matching(server_name=HOSTNAME.lower()),
                       matching(server_name=HOSTNAME + " EU"),
                       matching(game_port=2402), matching(game_port=None), matching(server_name=None), None):
            with self.subTest(answer=answer):
                self.assertIsNone(self.match(_ScriptedProbe(answer)).find())

    def test_nothing_to_match_sends_no_query(self) -> None:
        """No selection, an unknown profile, no hostname, or an unreadable config: no A2S_INFO at all."""
        probe = _ScriptedProbe(matching())
        self.selected = None
        self.assertIsNone(self.match(probe).find())
        self.selected = "deleted-profile"
        self.assertIsNone(self.match(probe).find())
        self.selected = "livonia-main"
        self.config.write_text(f"steamQueryPort = {QUERY_PORT};\n", encoding="utf-8")
        self.assertIsNone(self.match(probe).find())
        self.config.unlink()
        self.assertIsNone(self.match(probe).find())
        self.assertEqual(probe.ports, [])

    def test_real_probe_rules_apply(self) -> None:
        """Through the production probe: a match, then a stray sender, an oversize datagram and silence."""
        answer = information_answer(HOSTNAME, 25, 60, GAME_PORT)
        stray = (answer, ("127.0.0.1", QUERY_PORT + 1))
        cases = {"match": [stray, answer], "stray": [stray], "oversize": [answer + b"\x00" * 4100],
                 "timeout": [], "wrong port": [information_answer(HOSTNAME, 25, 60, 2402)],
                 "no port": [information_answer(HOSTNAME, 25, 60, flags=None)]}
        with mock.patch("socket.socket", side_effect=AssertionError("a test opened a real socket")):
            for name, script in cases.items():
                with self.subTest(case=name):
                    fake = ScriptedSocket(QUERY_PORT, script)
                    found = self.match(SteamQueryProbe(0.15, socket_factory=lambda: fake)).find()
                    if name == "match":
                        self.assertEqual((found[0], found[1].count), (QUERY_PORT, PlayerCount(25, 60)))
                        self.assertEqual(fake.sent, [(INFO_QUERY, ("127.0.0.1", QUERY_PORT))])
                    else:
                        self.assertIsNone(found)
                    # Every wait is bounded by the short probe of the outside server
                    self.assertTrue(all(0 < value <= 0.15 for value in fake.timeouts))


class ComposedExternalProbeTests(unittest.TestCase):
    """The composed outside-manager probe waits at most 0.15 s per exchange (QA R10)."""

    def test_composed_probe_waits_at_most_015_seconds(self) -> None:
        """The application's match uses its own short probe; a silent port waits 0.15 s per exchange."""
        from dayz_serverman.composition import build_composition
        with tempfile.TemporaryDirectory(prefix="serverman_external_probe_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                probe = composition.lifecycle._external._probe
                self.assertIsInstance(probe, SteamQueryProbe)
                self.assertEqual(probe._timeout_seconds, 0.15)
                # A silent port: each receive waits for the rest of 0.15 s at most
                fake = ScriptedSocket(QUERY_PORT, [])
                probe._socket_factory = lambda: fake
                with mock.patch("socket.socket", side_effect=AssertionError("a test opened a real socket")):
                    self.assertIsNone(probe.information(QUERY_PORT))
                self.assertTrue(fake.timeouts and all(0 < value <= 0.15 for value in fake.timeouts))
            finally:
                composition.operations.shutdown(2)


class ExternalStatusTests(ExternalMatchTests):
    """The status of a server outside the manager gains a count and nothing else."""

    def service(self, answer: InformationAnswer | None) -> tuple[ReadinessLifecycleService, _ScriptedProbe]:
        """Return a readiness service whose outside-manager match uses the scripted answer."""
        external = _ScriptedProbe(answer)
        self.lifecycle = FakeLifecycle()
        self.managed_probe = FakeProbe()
        service = ReadinessLifecycleService(
            self.lifecycle, self.profiles, self.settings, self.managed_probe, FakeMissionProbe(),
            external=self.match(external))
        return service, external

    def test_match_adds_only_the_count(self) -> None:
        """State, readiness, query port, profile and process stay as observed; only the count is added."""
        service, external = self.service(matching())
        observed = LifecycleSnapshot(ServerState.RUNNING_EXTERNAL, 4711, "PROCESS_EXTERNAL")
        self.lifecycle.snapshot = observed
        status = service.status()
        self.assertEqual(status, replace(observed, players=12, max_players=60))
        self.assertEqual((status.state, status.readiness, status.query_port, status.profile_id, status.started_at),
                         (ServerState.RUNNING_EXTERNAL, None, None, None, None))
        # One extra query per status read, and none from the readiness probe of a managed launch
        self.assertEqual((external.ports, self.managed_probe.ports), ([QUERY_PORT], []))

    def test_no_match_leaves_the_status_unchanged(self) -> None:
        """Without a match the status is exactly the observed one."""
        service, _external = self.service(matching(game_port=2402))
        observed = LifecycleSnapshot(ServerState.RUNNING_EXTERNAL, 4711)
        self.lifecycle.snapshot = observed
        self.assertEqual(service.status(), observed)

    def test_no_query_in_any_other_state(self) -> None:
        """Only RUNNING_EXTERNAL asks the selected profile's port."""
        service, external = self.service(matching())
        for state in (ServerState.STOPPED, ServerState.STARTING, ServerState.STOPPING, ServerState.UNKNOWN,
                      ServerState.AMBIGUOUS, ServerState.RUNNING_MANAGED):
            self.lifecycle.snapshot = LifecycleSnapshot(state, 4711)
            status = service.status()
            with self.subTest(state=state):
                self.assertIsNone(status.players)
        self.assertEqual(external.ports, [])

    def test_guards_see_the_same_server(self) -> None:
        """The D11 check passes a stop request through to the same state rules as without a count."""
        service, _external = self.service(matching())
        self.lifecycle.snapshot = LifecycleSnapshot(ServerState.RUNNING_EXTERNAL, 4711)
        handler = mock.Mock(return_value="handled")
        checked = for_running_profile(service, handler)
        self.assertEqual(checked({"profile_id": "livonia-main"}), "handled")
        self.assertIsNone(service.status().profile_id)
        self.assertFalse(service.shutdown_safe())

    def test_names_read_follows_the_match(self) -> None:
        """The names read asks the matched port only; without a match it reads nothing."""
        reader = mock.Mock(return_value=())
        service, _external = self.service(matching())
        self.lifecycle.snapshot = LifecycleSnapshot(ServerState.RUNNING_EXTERNAL, 4711)
        coordinator = OnlinePlayersCoordinator(service, mock.Mock(read_players=reader))
        self.assertEqual(coordinator.get_online_players({}), {"state": "OK", "players": []})
        reader.assert_called_once_with(QUERY_PORT)
        for answer in (matching(server_name="Other"), matching(game_port=None), None):
            service, _external = self.service(answer)
            self.lifecycle.snapshot = LifecycleSnapshot(ServerState.RUNNING_EXTERNAL, 4711)
            reader.reset_mock()
            value = OnlinePlayersCoordinator(service, mock.Mock(read_players=reader)).get_online_players({})
            with self.subTest(answer=answer):
                self.assertEqual(value["state"], "NOT_RUNNING")
                reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
