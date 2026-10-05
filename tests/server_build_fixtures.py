"""Shared fixtures of the server build check tests: VDF text, manifests and temporary installations.

No fixture reads or writes a live installation; every tree lives in a temporary directory.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows.steamcmd import SteamCmdRunResult  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd_paths import SteamCmdExecutable  # noqa: E402
from dayz_serverman.domain.server_build import BuildCheckRecord  # noqa: E402

# A SteamID64-shaped marker in `LastOwner`; it must never appear in any output of the check
OWNER_MARKER = "76561190000000042"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def vdf(value: dict[str, Any], indent: str = "") -> str:
    """Write a nested dictionary as Steam text VDF with escaped strings."""
    lines = []
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f'{indent}"{_escape(key)}"\n{indent}{{\n{vdf(item, indent + chr(9))}{indent}}}\n')
        else:
            lines.append(f'{indent}"{_escape(key)}"\t\t"{_escape(str(item))}"\n')
    return "".join(lines)


def _escape(text: str) -> str:
    """Escape backslashes and quotes like Steam does."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def manifest(**overrides: Any) -> str:
    """Return a manifest of the evidence 6.1 shape; an override of None removes the field."""
    fields: dict[str, Any] = {
        "appid": "223350", "universe": "1", "LauncherPath": "C:\\Steam\\steam.exe",
        "name": "DayZ Server", "StateFlags": "4", "installdir": "DayZServer",
        "lastupdated": "1787323747", "SizeOnDisk": "2201722434", "buildid": "24570360",
        "LastOwner": OWNER_MARKER, "TargetBuildID": "24570360",
        "InstalledDepots": {"223351": {"manifest": "1", "size": "2"}},
        "UserConfig": {"language": "english"}, "MountedConfig": {"language": "english"},
    }
    for key, value in overrides.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return vdf({"AppState": fields})


def library_install(root: Path, text: str | None = None, *, steam_exe: bool = True) -> Path:
    """Create a Steam library layout and return its DayZ root."""
    library = root / "Steam Library"
    dayz = library / "steamapps" / "common" / "DayZServer"
    dayz.mkdir(parents=True)
    if text is not None:
        (library / "steamapps" / "appmanifest_223350.acf").write_text(text, encoding="utf-8")
    if steam_exe:
        (library / "steam.exe").write_bytes(b"fixture")
    return dayz


def force_install(root: Path, text: str) -> Path:
    """Create a SteamCMD force-install layout and return its DayZ root."""
    dayz = root / "DayZ Force"
    (dayz / "steamapps").mkdir(parents=True)
    (dayz / "steamapps" / "appmanifest_223350.acf").write_text(text, encoding="utf-8")
    return dayz


def app_info_lines() -> tuple[str, ...]:
    """Return the sanitized console output of the evidence 6.1 run, line by line."""
    text = (FIXTURES / "steamcmd_app_info_223350.txt").read_text(encoding="utf-8")
    return tuple(text.splitlines(keepends=True))


def result(*, exit_code=0, lines=None, cancelled=False, confirmed=True) -> SteamCmdRunResult:
    """Return one supervised run result; the default is the recorded spike output."""
    return SteamCmdRunResult(exit_code, app_info_lines() if lines is None else lines, cancelled, confirmed, 9)


class FakeAppInfo:
    """App-information adapter stand-in; it never starts a process."""

    def __init__(self) -> None:
        """Answer with the recorded output until a test changes it."""
        self.answer = result()
        self.calls = 0
        self.during = None

    def run_app_info(self, _paths, cancellation_requested):
        """Count the run, call the scripted hook and return the scripted answer."""
        self.calls += 1
        if self.during is not None:
            self.during(cancellation_requested)
        return self.answer


class FakePreflight:
    """Executable preflight stand-in."""

    def __init__(self) -> None:
        """Accept the paths until a test sets an error."""
        self.error: Exception | None = None

    def inspect_executable(self, settings):
        """Return fixed paths or raise the scripted error."""
        if self.error is not None:
            raise self.error
        return SteamCmdExecutable(Path(settings.steamcmd_root), Path(settings.steamcmd_executable), ())


class FakeCache:
    """In-memory cache that records every save."""

    def __init__(self, record: BuildCheckRecord | None = None) -> None:
        """Start from the given record."""
        self.record, self.saved = record, []

    def load(self):
        """Return the starting record."""
        return self.record

    def save(self, record):
        """Record one save."""
        self.saved.append(record)
        return True


class FakeLogger:
    """Structured logger stand-in that keeps the events."""

    def __init__(self) -> None:
        """Start without events."""
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, event, level="INFO", fields=None):
        """Keep one event."""
        self.events.append((event, level, dict(fields or {})))
