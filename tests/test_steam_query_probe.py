"""Protocol tests for bounded Steam A2S readiness probing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.adapters.steam_query import INFO_QUERY, SteamQueryProbe  # noqa: E402


class SteamQueryProbeTests(unittest.TestCase):
    """Validate direct, challenged, malformed, and failed query exchanges."""

    def test_accepts_direct_information_response(self) -> None:
        """A correctly framed information datagram proves readiness."""
        probe = SteamQueryProbe()
        probe._exchange = Mock(return_value=b"\xff\xff\xff\xffI\x11server")  # type: ignore[method-assign]
        self.assertTrue(probe.is_ready(2405))
        self.assertEqual(probe._exchange.call_args.args[1:], (2405, INFO_QUERY))

    def test_completes_one_challenge_round_trip(self) -> None:
        """A challenge token is appended to exactly one repeated request."""
        challenge = b"\x01\x02\x03\x04"
        probe = SteamQueryProbe()
        probe._exchange = Mock(side_effect=[  # type: ignore[method-assign]
            b"\xff\xff\xff\xffA" + challenge,
            b"\xff\xff\xff\xffI\x11server",
        ])
        self.assertTrue(probe.is_ready(27016))
        self.assertEqual(
            probe._exchange.call_args_list[1].args[1:],
            (27016, INFO_QUERY + challenge),
        )

    def test_rejects_invalid_ports_malformed_replies_and_socket_failures(self) -> None:
        """Invalid or unavailable endpoints remain a contained not-ready result."""
        for port in (0, 65_536):
            with self.subTest(port=port):
                self.assertFalse(SteamQueryProbe().is_ready(port))
        for response in (b"", b"\xff\xff\xff\xffX", OSError("timeout")):
            probe = SteamQueryProbe()
            probe._exchange = Mock(  # type: ignore[method-assign]
                side_effect=response if isinstance(response, Exception) else None,
                return_value=response if isinstance(response, bytes) else None,
            )
            with self.subTest(response=repr(response)):
                self.assertFalse(probe.is_ready(2405))


if __name__ == "__main__":
    unittest.main()
