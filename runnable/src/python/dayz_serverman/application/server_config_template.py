"""Deterministic baseline configuration for newly provisioned servers."""

from __future__ import annotations

from pathlib import PureWindowsPath


def render_server_config(
    display_name: str,
    game_port: int,
    mission_root: str,
    instance_id: int,
) -> bytes:
    """Return a conservative UTF-8 DayZ server configuration."""
    hostname = display_name.replace("\\", "\\\\").replace('"', '\\"')
    mission_template = PureWindowsPath(mission_root).name
    if not mission_template:
        raise ValueError("mission_root must identify a mission directory")
    mission_template = mission_template.replace("\\", "\\\\").replace('"', '\\"')
    # Keep the Steam discovery endpoint near the game port without overflowing UDP range
    steam_query_port = game_port + 3 if game_port <= 65_532 else game_port - 3
    lines = (
        f'hostname = "{hostname}";',
        'description = "";',
        'password = "";',
        'passwordAdmin = "";',
        "enableWhitelist = 0;",
        "maxPlayers = 60;",
        f"steamQueryPort = {steam_query_port};",
        "verifySignatures = 2;",
        "BattlEye = 1;",
        "forceSameBuild = 1;",
        "disableVoN = 0;",
        "vonCodecQuality = 20;",
        "disable3rdPerson = 0;",
        "disableCrosshair = 0;",
        "disablePersonalLight = 0;",
        "lightingConfig = 0;",
        'serverTime = "SystemTime";',
        "serverTimeAcceleration = 1;",
        "serverNightTimeAcceleration = 1;",
        "serverTimePersistent = 0;",
        "guaranteedUpdates = 1;",
        "loginQueueConcurrentPlayers = 5;",
        "loginQueueMaxPlayers = 500;",
        f"instanceId = {instance_id};",
        "storageAutoFix = 1;",
        "enableCfgGameplayFile = 0;",
        "",
        "class Missions",
        "{",
        "    class DayZ",
        "    {",
        f'        template = "{mission_template}";',
        "    };",
        "};",
    )
    return ("\n".join(lines) + "\n").encode("utf-8")
