"""Windows SteamCMD preflight and subprocess boundary."""

from __future__ import annotations

import ntpath
import os
import queue
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

from ...domain.models import ManagerSettings
from ...domain.workshop import AuthenticationMode, RequiredWorkshopItem
from ...repositories.steam_vdf import VdfError, field, parse_vdf
from .process_tree import ChildEvidence, OwnedProcessTree, safe_child_environment


# Steam application id of the DayZ dedicated server
APP_ID = "221100"
# Retained console output is capped at 1 MiB to bound memory use during runs
OUTPUT_LIMIT = 1_048_576
# One console line is truncated to 8 KiB before retention
LINE_LIMIT = 8192


class SteamCmdPreflightError(RuntimeError):
    """Raised when SteamCMD paths or library registration fail preflight."""
    pass


@dataclass(frozen=True)
class PathIdentity:
    """Stat fingerprint captured for one SteamCMD-related path."""
    canonical: str
    device: int
    inode: int
    modified_ns: int
    size: int


@dataclass(frozen=True)
class SteamCmdPaths:
    """Resolved SteamCMD paths plus the identities captured at preflight."""
    root: Path
    executable: Path
    workshop_root: Path
    manifest: Path | None = None
    library_manifest: Path | None = None
    identities: tuple[PathIdentity, ...] = ()


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


class SteamCmdPreflight:
    """Verify SteamCMD layout, identity, and library registration before use."""

    def inspect(self, settings: ManagerSettings) -> SteamCmdPaths:
        """Validate configured paths and capture their identities."""
        # All three SteamCMD-related paths must be configured
        raw_values = (settings.steamcmd_root, settings.steamcmd_executable,
                      settings.workshop_content_root)
        if any(value is None for value in raw_values):
            raise SteamCmdPreflightError("SteamCMD and Workshop paths must be configured")
        assert all(isinstance(value, str) for value in raw_values)
        # Reject non-canonical spellings before touching the filesystem
        for raw in raw_values:
            self._validate_lexical(raw)
        root, executable, workshop = (Path(value) for value in raw_values)
        # Walk each parent chain so a reparse point cannot hide a redirect
        for path in (root, executable, workshop):
            self._validate_chain(path)
        # Every required path must exist with the expected file type
        if not root.is_dir() or not executable.is_file() or not workshop.is_dir():
            raise SteamCmdPreflightError("A configured SteamCMD or Workshop path is missing")
        # Resolve once so every later comparison uses the real location
        resolved = tuple(path.resolve(strict=True) for path in (root, executable, workshop))
        root_path, executable_path, workshop_path = resolved
        # The executable must sit directly inside the configured SteamCMD root
        if executable_path.parent != root_path or executable_path.name.casefold() != "steamcmd.exe":
            raise SteamCmdPreflightError("SteamCMD executable must be <SteamCMD root>/steamcmd.exe")
        # The workshop root must end with the DayZ app id directory
        expected_tail = ("steamapps", "workshop", "content", APP_ID)
        if tuple(part.casefold() for part in workshop_path.parts[-4:]) != expected_tail:
            raise SteamCmdPreflightError("Workshop root must end with steamapps/workshop/content/221100")
        # The appworkshop manifest records the subscribed items
        manifest = workshop_path.parent.parent / f"appworkshop_{APP_ID}.acf"
        self._validate_chain(manifest)
        # A missing or symlinked manifest cannot be trusted for updates
        if (manifest.parent != workshop_path.parent.parent or not manifest.is_file()
                or manifest.is_symlink()):
            raise SteamCmdPreflightError("Steam Workshop manifest is missing or unsafe")
        # SteamCMD must be able to read and write the content root
        if not os.access(workshop_path, os.R_OK | os.W_OK):
            raise SteamCmdPreflightError("Workshop content root must be readable and writable")
        library_manifest = root_path / "steamapps" / "libraryfolders.vdf"
        self._require_registered_library(root_path, workshop_path, library_manifest)
        tracked_library = library_manifest.resolve(strict=True) if library_manifest.is_file() else None
        # The manifest identity is always tracked; the library file only when present
        identity_paths = (*resolved, manifest.resolve(strict=True))
        if tracked_library is not None:
            identity_paths = (*identity_paths, tracked_library)
        return SteamCmdPaths(
            *resolved, manifest.resolve(strict=True), tracked_library,
            tuple(self._identity(path) for path in identity_paths),
        )

    def revalidate(self, paths: SteamCmdPaths) -> None:
        """Fail when any tracked path changed since preflight."""
        # Without a manifest identity no run can be trusted
        if paths.manifest is None:
            raise SteamCmdPreflightError("Steam Workshop manifest identity is unavailable")
        # Compare the same path set that preflight captured
        identity_paths = (paths.root, paths.executable, paths.workshop_root, paths.manifest)
        if paths.library_manifest is not None:
            identity_paths = (*identity_paths, paths.library_manifest)
        current = tuple(self._identity(path) for path in identity_paths)
        # Any identity change invalidates the prepared run
        if not paths.identities or current != paths.identities:
            raise SteamCmdPreflightError("SteamCMD configured path identity changed")
        # Re-check chains too: a new reparse point can appear after preflight
        for path in (paths.root, paths.executable, paths.workshop_root, paths.manifest):
            self._validate_chain(path)

    @classmethod
    def _require_registered_library(
        cls, root: Path, workshop: Path, library_manifest: Path,
    ) -> None:
        """Fail unless the workshop root is registered to the configured SteamCMD."""
        # The owning library root sits four levels above the app id directory
        configured_library = workshop.parents[3]
        registered = {os.path.normcase(str(root.resolve(strict=True)))}
        if library_manifest.is_file():
            cls._validate_chain(library_manifest)
            try:
                # Read every registered library path from libraryfolders.vdf
                parsed = parse_vdf(library_manifest.read_text(encoding="utf-8-sig"))
                folders = field(parsed, "libraryfolders")
                if not isinstance(folders, dict):
                    raise VdfError("Steam library list is invalid")
                for entry in folders.values():
                    if isinstance(entry, dict):
                        value = field(entry, "path")
                        if isinstance(value, str):
                            registered.add(os.path.normcase(str(Path(value).resolve(strict=False))))
            except (OSError, UnicodeError, VdfError) as error:
                raise SteamCmdPreflightError("SteamCMD library registration is invalid") from error
        # Reject a workshop root whose library is not registered
        if os.path.normcase(str(configured_library.resolve(strict=True))) not in registered:
            raise SteamCmdPreflightError(
                "Workshop content root is not registered to the configured SteamCMD instance"
            )

    @staticmethod
    def _validate_lexical(raw: str) -> None:
        """Reject non-canonical or unsafe path spellings."""
        # Require an already-normalized path with no embedded nulls
        if not raw or "\x00" in raw or raw != ntpath.normpath(raw):
            raise SteamCmdPreflightError("SteamCMD paths must be canonical")
        windows = PureWindowsPath(raw)
        # Reject UNC and relative forms before any filesystem access
        if not windows.is_absolute() or windows.anchor.startswith("\\"):
            raise SteamCmdPreflightError("SteamCMD paths must be absolute local paths")
        # Reject device drives and traversal segments
        if windows.drive.startswith("\\") or any(part in (".", "..") for part in windows.parts):
            raise SteamCmdPreflightError("SteamCMD paths must not contain traversal")
        # Reject alternate data streams in later segments
        if any(":" in part for part in windows.parts[1:]):
            raise SteamCmdPreflightError("SteamCMD paths must not use device or alternate streams")

    @staticmethod
    def _validate_chain(path: Path) -> None:
        """Reject reparse points anywhere in the path's parent chain."""
        # FILE_ATTRIBUTE_REPARSE_POINT flags junctions and symlink targets
        marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        current = path.absolute()
        chain: list[Path] = []
        # Collect the chain from the path up to the drive root
        while True:
            chain.append(current)
            if current.parent == current:
                break
            current = current.parent
        # Check from the root down so intermediate links are seen first
        for candidate in reversed(chain):
            if not candidate.exists():
                continue
            attributes = getattr(os.lstat(candidate), "st_file_attributes", 0)
            if candidate.is_symlink() or attributes & marker:
                raise SteamCmdPreflightError("SteamCMD paths must not contain reparse points")

    @staticmethod
    def _identity(path: Path) -> PathIdentity:
        """Capture a stat identity for one path without following links."""
        value = os.stat(path, follow_symlinks=False)
        return PathIdentity(str(path.resolve(strict=True)), value.st_dev, value.st_ino,
                            value.st_mtime_ns, value.st_size)


class WindowsSteamCmdAdapter:
    """Launch and supervise SteamCMD runs with owned process evidence."""

    def __init__(self, preflight: SteamCmdPreflight | None = None) -> None:
        """Use the given preflight validator or a fresh default."""
        self._preflight = preflight or SteamCmdPreflight()

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
        # A separate console lets the user answer Steam's prompts
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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

    @staticmethod
    def _wait_owned(
        process: subprocess.Popen[str], cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None], retained: list[str],
    ) -> SteamCmdRunResult:
        """Supervise one launched SteamCMD process until it exits or is cancelled."""
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
            if cancellation_requested() and process.poll() is None and not cancelled:
                cancelled = True
                # Escalate from a polite close to termination, then to kill
                tree.request_close()
                if not tree.wait_absent(15.0):
                    tree.terminate_tree()
                    if not tree.wait_absent(5.0):
                        tree.kill_tree()
                        # A bounded wait decides whether the tree is really gone
                        if not tree.wait_absent(5.0):
                            return SteamCmdRunResult(None, tuple(retained), True, False,
                                                     process.pid, last_evidence)
        cancelled = cancelled or cancellation_requested()
        # One last absence check settles the termination verdict
        termination_confirmed = tree.wait_absent(5.0)
        return SteamCmdRunResult(process.returncode, tuple(retained), cancelled,
                                 termination_confirmed, process.pid, last_evidence)
