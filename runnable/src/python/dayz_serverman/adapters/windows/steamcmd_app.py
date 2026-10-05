"""Anonymous SteamCMD app-information run for the DayZ server build check.

The command only reads the public app information of the DayZ server. It
installs nothing, checks no installed files and never uses an account.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .process_tree import safe_child_environment
from .steamcmd import SteamCmdRunResult, supervise_owned
from .steamcmd_paths import SteamCmdExecutable, SteamCmdPreflight

# Steam application id of the DayZ dedicated server
SERVER_APP_ID = "223350"


def build_app_info_argv(executable: Path) -> tuple[str, ...]:
    """Return the only command of the build check; it has no account parameter."""
    return (
        str(executable), "+login", "anonymous",
        # Refresh the app information from Steam before it is printed
        "+app_info_update", "1", "+app_info_print", SERVER_APP_ID,
        # +quit guarantees SteamCMD exits instead of an interactive prompt
        "+quit",
    )


class WindowsSteamCmdAppInfo:
    """Launch one anonymous app-information run and supervise it as an owned process tree."""

    def __init__(self, preflight: SteamCmdPreflight | None = None) -> None:
        """Use the given preflight validator or a fresh default."""
        self._preflight = preflight or SteamCmdPreflight()

    def run_app_info(
        self, paths: SteamCmdExecutable, cancellation_requested: Callable[[], bool],
    ) -> SteamCmdRunResult:
        """Run the command and capture its bounded console output.

        The cancellation callback ends the run through the supervisor's escalation.
        """
        # Revalidate the two paths immediately before the process starts
        self._preflight.revalidate_executable(paths)
        # Its own process group enables a console-style close signal later
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # Merge both streams into one pipe, launched exactly like a Workshop update
        process = subprocess.Popen(
            list(build_app_info_argv(paths.executable)), cwd=str(paths.root),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            shell=False, text=True, encoding="utf-8", errors="replace", close_fds=True,
            creationflags=flags, env=safe_child_environment(),
        )
        return supervise_owned(process, cancellation_requested, lambda _evidence: None, [])
