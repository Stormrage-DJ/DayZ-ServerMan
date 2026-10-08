"""Windows SteamCMD subprocess boundary: argument vectors, launch and owned supervision."""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from ...domain.workshop import AuthenticationMode, RequiredWorkshopItem
from .process_tree import ChildEvidence, OwnedProcessTree, safe_child_environment
# Preflight and path types live in their own module; they are re-exported for existing importers
from .steamcmd_paths import (  # noqa: F401
    APP_ID,
    PathIdentity,
    SteamCmdPaths,
    SteamCmdPreflight,
    SteamCmdPreflightError,
)


# Retained console output is capped at 1 MiB to bound memory use during runs
OUTPUT_LIMIT = 1_048_576
# One console line is truncated to 8 KiB before retention
LINE_LIMIT = 8192
# After a successful escalation the output reader gets this long to see the end of the stream
READER_GRACE_SECONDS = 5.0


class SteamCmdConsole(str, Enum):
    """Where the interactive sign-in asks its questions (design 10.5)."""

    # The window: SteamCMD opens its own console window
    NEW_CONSOLE = "NEW_CONSOLE"
    # A CLI command: SteamCMD shares the caller's console, in its own process group
    SHARED_CONSOLE = "SHARED_CONSOLE"


@dataclass(frozen=True)
class SteamCmdRunResult:
    """Outcome of one SteamCMD run including owned-child evidence."""
    exit_code: int | None
    lines: tuple[str, ...]
    cancelled: bool
    termination_confirmed: bool
    process_id: int
    child_evidence: ChildEvidence | None = None


def build_update_argv(
    executable: Path, mode: AuthenticationMode, account_name: str | None,
    items: tuple[RequiredWorkshopItem, ...],
) -> tuple[str, ...]:
    """Build the SteamCMD argument vector for one workshop update run."""
    # Anonymous mode ignores any account name; account mode requires one
    login = account_name if mode == AuthenticationMode.ACCOUNT else "anonymous"
    if mode == AuthenticationMode.ACCOUNT and not account_name:
        raise ValueError("ACCOUNT mode requires an account name")
    arguments = [str(executable), "+login", login]
    # Request every required item in one invocation
    for item in items:
        arguments.extend(("+workshop_download_item", APP_ID, item.workshop_id))
    # +quit guarantees SteamCMD exits instead of an interactive prompt
    arguments.append("+quit")
    return tuple(arguments)


def supervise_owned(
    process: subprocess.Popen[str], cancellation_requested: Callable[[], bool],
    on_launched: Callable[[ChildEvidence], None], retained: list[str],
) -> SteamCmdRunResult:
    """Supervise one launched SteamCMD process until it exits or is cancelled.

    It stays in this module because callers patch `OwnedProcessTree` here.
    """
    # Adopt the child into an owned job for identity and tree control
    try:
        tree = OwnedProcessTree(process)
    except OSError:
        # Without ownership the child is terminated rather than trusted
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
        return SteamCmdRunResult(None, (), cancellation_requested(),
                                 process.poll() is not None, process.pid)
    # Publish the first evidence snapshot to the caller
    last_evidence = tree.evidence
    on_launched(last_evidence)
    messages: queue.Queue[str | None] = queue.Queue()
    if process.stdout is not None:
        def read_output() -> None:
            """Forward console lines into the queue until the stream ends."""
            assert process.stdout is not None
            for line in process.stdout:
                messages.put(line[:LINE_LIMIT])
            # A None sentinel marks end-of-stream for the supervisor loop
            messages.put(None)
        threading.Thread(target=read_output, daemon=True).start()
    else:
        # No pipe means output capture is already complete
        messages.put(None)
    cancelled, reader_done, output_size = False, False, 0
    # Set after an escalation: the moment by which the output stream must have ended
    reader_deadline: float | None = None
    # Poll output and process state until exit and stream end
    while process.poll() is None or not reader_done:
        try:
            message = messages.get(timeout=0.1)
            if message is None:
                reader_done = True
            elif output_size < OUTPUT_LIMIT:
                # Retain output only while under the byte bound
                retained.append(message)
                output_size += len(message.encode("utf-8", errors="replace"))
        except queue.Empty:
            pass
        try:
            current_evidence = tree.evidence
        except OSError:
            return SteamCmdRunResult(None, tuple(retained), cancelled, False,
                                     process.pid, last_evidence)
        # Evidence changes as job members appear; report each change
        if current_evidence != last_evidence:
            last_evidence = current_evidence
            on_launched(last_evidence)
        # Escalate also after the root exited: a tree member can still hold the output pipe
        if cancellation_requested() and not cancelled:
            cancelled = True
            if not _escalate(tree):
                return SteamCmdRunResult(None, tuple(retained), True, False,
                                         process.pid, last_evidence)
            reader_deadline = time.monotonic() + READER_GRACE_SECONDS
        # A pipe that stays open after the whole tree is gone has an unknown holder: not proven
        if reader_deadline is not None and not reader_done and time.monotonic() >= reader_deadline:
            return SteamCmdRunResult(None, tuple(retained), True, False,
                                     process.pid, last_evidence)
    cancelled = cancelled or cancellation_requested()
    # One last absence check settles the termination verdict
    termination_confirmed = tree.wait_absent(5.0)
    return SteamCmdRunResult(process.returncode, tuple(retained), cancelled,
                             termination_confirmed, process.pid, last_evidence)


def _escalate(tree: OwnedProcessTree) -> bool:
    """Close, then terminate, then kill the owned tree; return whether it is gone.

    A step is skipped once the tree is absent, so an exited tree costs no waiting.
    """
    tree.request_close()
    if tree.wait_absent(15.0):
        return True
    tree.terminate_tree()
    if tree.wait_absent(5.0):
        return True
    tree.kill_tree()
    # A bounded wait decides whether the tree is really gone
    return tree.wait_absent(5.0)


class WindowsSteamCmdAdapter:
    """Launch and supervise SteamCMD runs with owned process evidence."""

    def __init__(self, preflight: SteamCmdPreflight | None = None, *,
                 console: SteamCmdConsole = SteamCmdConsole.NEW_CONSOLE) -> None:
        """Use the given preflight validator or a fresh default, and the console of the sign-in."""
        self._preflight = preflight or SteamCmdPreflight()
        self._console = console

    # The run methods and existing tests reach the shared supervisor through this name
    _wait_owned = staticmethod(supervise_owned)

    def authenticate_interactive(
        self, paths: SteamCmdPaths, account_name: str,
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult:
        """Run the interactive login so the user can complete Steam Guard."""
        if not account_name:
            raise ValueError("account name is required")
        before_launch()
        # Revalidate paths immediately before the process starts
        self._preflight.revalidate(paths)
        # A separate console lets the user answer Steam's prompts; a CLI command shares its own
        # console instead, and the new process group keeps the console's Ctrl+C away from SteamCMD
        flags = sign_in_flags(self._console)
        process = subprocess.Popen(
            [str(paths.executable), "+login", account_name, "+quit"],
            cwd=str(paths.root), shell=False, close_fds=True,
            creationflags=flags, env=safe_child_environment(),
        )
        return self._wait_owned(process, cancellation_requested, on_launched, [])

    def run_update(
        self, paths: SteamCmdPaths, argv: tuple[str, ...],
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult:
        """Run the workshop update and capture bounded console output."""
        before_launch()
        # Revalidate paths immediately before the process starts
        self._preflight.revalidate(paths)
        # Its own process group enables a console-style close signal later
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # Merge both streams into one pipe for ordered log capture
        process = subprocess.Popen(
            list(argv), cwd=str(paths.root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
            text=True, encoding="utf-8", errors="replace", close_fds=True,
            creationflags=flags, env=safe_child_environment(),
        )
        return self._wait_owned(process, cancellation_requested, on_launched, [])


def sign_in_flags(console: SteamCmdConsole) -> int:
    """Return the creation flags of the interactive sign-in for its console (design 10.5)."""
    group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if console is SteamCmdConsole.SHARED_CONSOLE:
        return group
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | group
