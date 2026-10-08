"""SteamCMD and Steam Web API fakes behind real CLI compositions (phase 5): no process, no network.

The patched factories give every composition of a test the same fakes, so each CLI run is its
own session over real services, as two manager processes would be.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import build_cache  # noqa: E402
from lifecycle_cli_fixtures import LifecycleRoot  # noqa: E402
from server_build_fixtures import FakeAppInfo, FakePreflight  # noqa: E402
from update_check_fixtures import FakeCatalog  # noqa: E402
from dayz_serverman import update_check_composition, workshop_composition  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd import SteamCmdConsole, SteamCmdPaths, SteamCmdRunResult  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402

# The Workshop mod of the fixture profile (`profile_fixtures.profile_payload`)
WORKSHOP_ID = "1559212036"


class FakeSteamCmd:
    """The SteamCMD adapter of every composition: records the console choice, the sign-ins and the updates."""

    def __init__(self) -> None:
        """Start without runs; a sign-in and an update end with exit code 0."""
        self.consoles: list[SteamCmdConsole] = []
        self.sign_ins: list[str] = []
        self.updates: list[tuple[str, ...]] = []
        self.exit_code = 0

    def adapter(self, _preflight=None, *, console: SteamCmdConsole = SteamCmdConsole.NEW_CONSOLE) -> "FakeSteamCmd":
        """Stand in for `WindowsSteamCmdAdapter(preflight, console=...)`."""
        self.consoles.append(console)
        return self

    def authenticate_interactive(self, paths, account_name, cancellation_requested, on_launched, before_launch):
        """Record one sign-in after the launch guard; no process starts."""
        del paths, cancellation_requested, on_launched
        before_launch()
        self.sign_ins.append(account_name)
        return SteamCmdRunResult(self.exit_code, (), False, True, 4242)

    def run_update(self, paths, argv, cancellation_requested, on_launched, before_launch):
        """Record one update run; no process starts and nothing is downloaded."""
        del paths, cancellation_requested, on_launched
        before_launch()
        self.updates.append(tuple(argv))
        return SteamCmdRunResult(self.exit_code, (), False, True, 4343)


class FakeWorkshopPreflight:
    """SteamCMD and Workshop path preflight of the update service: the configured paths, never checked."""

    def inspect(self, settings) -> SteamCmdPaths:
        """Return the configured paths."""
        return SteamCmdPaths(Path(settings.steamcmd_root), Path(settings.steamcmd_executable),
                             Path(settings.workshop_content_root or settings.steamcmd_root))

    def revalidate(self, paths) -> None:
        """Accept the paths."""
        del paths


def patched_steam(catalog: FakeCatalog, app_info: FakeAppInfo, steamcmd: FakeSteamCmd) -> ExitStack:
    """Return the patches that give every composition the Steam fakes."""
    stack = ExitStack()
    for module, name, factory in (
        (update_check_composition, "SteamWebApiCatalog", lambda **_options: catalog),
        (update_check_composition, "SteamCmdPreflight", FakePreflight),
        (update_check_composition, "WindowsSteamCmdAppInfo", lambda _preflight: app_info),
        (workshop_composition, "WindowsSteamCmdAdapter", steamcmd.adapter),
        (workshop_composition, "SteamCmdPreflight", FakeWorkshopPreflight),
    ):
        stack.enter_context(patch.object(module, name, factory))
    return stack


class SteamRoot(LifecycleRoot):
    """The lifecycle root with the Steam fakes and a SteamCMD folder in the settings."""

    def setUp(self) -> None:
        """Patch the Steam fakes and set up SteamCMD with account sign-in."""
        super().setUp()
        self.catalog, self.app_info, self.steamcmd = FakeCatalog(), FakeAppInfo(), FakeSteamCmd()
        stack = patched_steam(self.catalog, self.app_info, self.steamcmd)
        stack.__enter__()
        self.addCleanup(stack.close)
        self.steam_settings("ACCOUNT", "operator")

    def steam_settings(self, mode: str | None, account: str | None) -> None:
        """Save the SteamCMD folder and the sign-in choice through an owner composition."""
        steamcmd = self.manager.parent / "SteamCMD"
        steamcmd.mkdir(exist_ok=True)
        (steamcmd / "steamcmd.exe").write_bytes(b"fixture")
        composition = build_composition(self.manager)
        try:
            current = composition.settings.load()
            composition.settings.save(SettingsInput(
                dayz_root=current.dayz_root, dayz_executable=current.dayz_executable,
                steamcmd_root=str(steamcmd), steamcmd_executable=str(steamcmd / "steamcmd.exe"),
                workshop_content_root=str(steamcmd / "steamapps" / "workshop" / "content" / "221100"),
                steam_account_name=account, steam_authentication_mode=mode), current.revision)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def workshop_cache(self, *, matching: bool = True) -> Path:
        """Download the fixture's Workshop mod into the SteamCMD cache; `matching` makes the server copy equal."""
        item = build_cache(self.manager.parent / "SteamCMD", {WORKSHOP_ID: b"cf"}) / WORKSHOP_ID
        target = self.dayz / "@Community Framework"
        (item / "mod.cpp").write_bytes((target / "mod.cpp").read_bytes())
        (target / "Addons").mkdir(exist_ok=True)
        (target / "Addons" / "mod.pbo").write_bytes(b"cf" if matching else b"changed")
        return item
