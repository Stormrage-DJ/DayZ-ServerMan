"""Player count for a DayZ server that runs outside the manager (D18 extension).

A process that this manager did not launch (for example after a manager restart) reads RUNNING_EXTERNAL. Its
query port is not known from a launch, so the selected profile's configured port is asked once with A2S_INFO.
The answer counts only when it carries that profile's server name (serverDZ.cfg `hostname`) and game port.
The match adds a count and allows a names read; it changes no ownership, readiness, lifecycle state, guard or
control. The server name is not logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..domain.online_players import InformationAnswer
from ..repositories.server_configuration import load_server_configuration
from .profiles import ProfileService
from .settings import SettingsService


# DayZ uses this query port when the legacy server config omits steamQueryPort
DEFAULT_STEAM_QUERY_PORT = 27_016
# Failures of reading a profile or its server config; each one means "no match", never a status failure
ENDPOINT_ERRORS = (OSError, RuntimeError, ValueError, LookupError)


class InformationProbe(Protocol):
    """Ask one local Steam query port for its information answer."""

    def information(self, port: int) -> InformationAnswer | None:
        """Return the valid information answer, or None when none arrived."""
        ...


@dataclass(frozen=True)
class ProfileEndpoint:
    """What a profile's configuration says about its server: query port, server name and game port."""

    query_port: int
    # The configured server name; None when serverDZ.cfg does not set it
    hostname: str | None = field(repr=False)
    game_port: int


def read_profile_endpoint(
    profiles: ProfileService, settings: SettingsService, profile_id: str,
) -> ProfileEndpoint:
    """Read a profile's query port, server name and game port from its serverDZ.cfg inside the DayZ root.

    Raises one of `ENDPOINT_ERRORS` when the profile, the settings or the config cannot be read, or when the
    config lies outside the DayZ root.
    """
    profile = profiles.read(profile_id)
    root_text = settings.load().dayz_root
    if root_text is None:
        return ProfileEndpoint(DEFAULT_STEAM_QUERY_PORT, None, profile.values.game_port)
    # Resolve the profile config inside the configured DayZ installation
    root = Path(root_text).resolve(strict=False)
    config = (root / profile.values.server_config).resolve(strict=False)
    config.relative_to(root)
    values = load_server_configuration(config).values
    port, hostname = values.get("steamQueryPort"), values.get("hostname")
    return ProfileEndpoint(
        port if isinstance(port, int) else DEFAULT_STEAM_QUERY_PORT,
        hostname if isinstance(hostname, str) else None,
        profile.values.game_port,
    )


class ExternalServerMatch:
    """Find the query port of a server outside the manager when it answers as the selected profile."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService, probe: InformationProbe,
        selected_profile: Callable[[], str | None],
    ) -> None:
        """Bind the profile and settings readers, the bounded probe, and the source of the selected profile."""
        self._profiles = profiles
        self._settings = settings
        self._probe = probe
        self._selected_profile = selected_profile

    def find(self) -> tuple[int, InformationAnswer] | None:
        """Return the query port and the matching answer, or None when there is no match.

        One A2S_INFO goes to the selected profile's query port. The answer matches only when its server name
        equals the configured hostname and its game port equals the profile's game port.
        """
        # Without a selected profile and a configured server name there is nothing to match
        try:
            profile_id = self._selected_profile()
            if not isinstance(profile_id, str):
                return None
            endpoint = read_profile_endpoint(self._profiles, self._settings, profile_id)
        except ENDPOINT_ERRORS:
            return None
        if endpoint.hostname is None:
            return None
        # Ask the configured port once and accept only an answer that names the same server
        answer = self._probe.information(endpoint.query_port)
        if answer is None or answer.server_name != endpoint.hostname or answer.game_port != endpoint.game_port:
            return None
        return endpoint.query_port, answer
