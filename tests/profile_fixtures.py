"""Reusable profile payloads and their on-disk launch paths."""
from __future__ import annotations

from pathlib import Path


def profile_payload(**overrides: object) -> dict[str, object]:
    """Return a profile payload with optional field overrides."""
    value: dict[str, object] = {
        "profile_id": "livonia-main",
        "display_name": "Livonia Közösségi",
        "server_executable": r"Bin\DayZ Server_x64.exe",
        "server_config": r"Config Files\serverDZ.cfg",
        "runtime_profile": r"Profiles\Máin Runtime",
        "mission_root": r"mpmissions\dayzOffline.enoch",
        "game_port": 2302,
        "mods": [
            {
                "directory": "@Community Framework",
                "launch_scope": "client",
                "source": {"kind": "workshop", "workshop_id": "1559212036"},
            },
            {
                "directory": "@Server Tools",
                "launch_scope": "server",
                "source": {"kind": "external"},
            },
        ],
        "extra_arguments": ["-doLogs", "-adminLog", "-name=Árvíztűrő Server"],
    }
    value.update(overrides)
    return value


def create_profile_paths(root: Path) -> None:
    """Create the runtime, mission, and mod directories for a profile payload."""
    for relative in (
        r"Profiles\Máin Runtime", r"mpmissions\dayzOffline.enoch",
        "@Community Framework", "@Server Tools",
    ):
        root.joinpath(*relative.split("\\")).mkdir(parents=True, exist_ok=True)
    # Seed the executable and config files the profile references
    for relative in (r"Bin\DayZ Server_x64.exe", r"Config Files\serverDZ.cfg"):
        path = root.joinpath(*relative.split("\\"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic fixture")
