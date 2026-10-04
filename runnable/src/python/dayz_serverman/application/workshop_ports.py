"""Ports and the request type of SteamCMD authentication and Workshop updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..adapters.windows.process_tree import ChildEvidence
from ..adapters.windows.steamcmd import SteamCmdPaths, SteamCmdRunResult
from ..domain.models import ManagerSettings
from ..domain.workshop import AuthenticationMode


class SteamCmdPreflightPort(Protocol):
    """Port for SteamCMD path inspection and revalidation."""

    # Inspect the SteamCMD layout and return validated tool paths.
    def inspect(self, settings: ManagerSettings) -> SteamCmdPaths: ...
    # Re-check that prepared paths still identify the same tools before use.
    def revalidate(self, paths: SteamCmdPaths) -> None: ...


class SteamCmdPort(Protocol):
    """Port for interactive authentication and Workshop update runs."""

    # Run interactive sign-in and report the SteamCMD session result.
    def authenticate_interactive(
        self, paths: SteamCmdPaths, account_name: str,
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult: ...

    # Run one Workshop update command and report the SteamCMD session result.
    def run_update(
        self,
        paths: SteamCmdPaths,
        argv: tuple[str, ...],
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult: ...


@dataclass(frozen=True)
class UpdateRequest:
    """Immutable request describing one Workshop update submission."""

    profile_id: str
    expected_profile_revision: int
    expected_semantic_profile_digest: str
    expected_settings_revision: int
    authentication_mode: AuthenticationMode
    account_name: str | None
    update_all_and_start: bool = False
