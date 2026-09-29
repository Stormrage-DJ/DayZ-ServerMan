"""Authoritative profile-derived snapshot inventory."""

from __future__ import annotations

from pathlib import PureWindowsPath

from .backups import entry_path_key
from .profiles import ProfileInput


# Mission configuration files captured with every profile-backed snapshot
MISSION_INVENTORY = (
    "cfgGameplay.json",
    "cfgspawnabletypes.xml",
    "cfgweather.xml",
    "db/events.xml",
    "db/globals.xml",
    "init.c",
)


def accepted_payload_sources(profile: ProfileInput) -> tuple[str, ...]:
    """Return the profile files a snapshot captures, in manifest order."""
    # The server configuration is always part of the payload
    paths = [profile.server_config]
    # Add mission files when the profile defines a mission root
    if profile.mission_root is not None:
        paths.extend(
            str(PureWindowsPath(profile.mission_root) / PureWindowsPath(item))
            for item in MISSION_INVENTORY
        )
    # Sort with the Windows-safe ordering key used inside the archive
    return tuple(sorted(paths, key=lambda item: entry_path_key(_archive_path(item))))


def accepted_payload_entries(profile: ProfileInput) -> tuple[str, ...]:
    """Return the archive payload paths for the accepted sources."""
    return tuple(_archive_path(path) for path in accepted_payload_sources(profile))


def _archive_path(relative: str) -> str:
    """Return the archive payload path for a Windows relative path."""
    return "payload/" + "/".join(PureWindowsPath(relative).parts)
