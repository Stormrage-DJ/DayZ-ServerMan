"""Steam discovery-port configuration contracts."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.domain.configuration import (  # noqa: E402
    ConfigurationValidationError,
    validate_updates,
)


class SteamQueryPortTests(unittest.TestCase):
    """Expose one bounded UDP discovery port through guided configuration."""

    def test_port_is_available_in_the_configuration_catalog(self) -> None:
        """The guided interface names the setting and its network responsibility."""
        catalog = (
            PROJECT_ROOT / "runnable" / "src" / "frontend" / "configuration_catalog.js"
        ).read_text(encoding="utf-8")
        self.assertIn('configurationField("steamQueryPort", "Steam query port"', catalog)
        self.assertIn("host firewall and router", catalog)

    def test_port_accepts_only_the_udp_port_range(self) -> None:
        """Reject values outside the valid port range and boolean coercion."""
        self.assertEqual(
            validate_updates("server", {"steamQueryPort": 2305}),
            {"steamQueryPort": 2305},
        )
        for value in (0, 65_536, True):
            with self.subTest(value=value), self.assertRaises(ConfigurationValidationError):
                validate_updates("server", {"steamQueryPort": value})


if __name__ == "__main__":
    unittest.main()
