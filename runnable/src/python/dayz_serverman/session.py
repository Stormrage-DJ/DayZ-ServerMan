"""Owner sessions over one manager root: the instance lock (A5), the server-folder lock file (A13), the composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import composition as composition_module
from .adapters.windows.instance_holder import HolderInfo, read_holder, write_holder
from .adapters.windows.instance_lock import (
    InstanceActive,
    InstanceLock,
    InstanceLockUnsupported,
    root_mutex_name,
)
from .adapters.windows.server_folder_lock import NO_FOLDER_LOCK, FolderLockFile, FolderWriter
from .composition import build_composition
from .composition_model import ApplicationComposition, SessionMode
from .repositories.paths import PortablePaths


# Files of the two locks and of the holder details, all in the manager data folder
INSTANCE_LOCK_FILE = "instance.lock"
HOLDER_FILE = "instance-holder.json"
FOLDER_LOCK_FILE = "server-folders.lock"
# Drain wait of the window at close (unchanged); a command waits without a limit
GUI_DRAIN_SECONDS = 5.0
# Wait of the server build check worker at close
BUILD_CHECK_STOP_SECONDS = 30.0
# P6/OD2: the window refuses a second instance of the same manager folder (5.3); the way out names the holder
GUI_REFUSAL = (
    "DayZ-ServerMan is already active for this folder{holder}. Close it, or wait until the command "
    "has finished, then start DayZ-ServerMan again."
)
# QF-24: the second sentence by the kind of holder
GUI_REFUSAL_WINDOW = ("DayZ-ServerMan is already active for this folder in another window. "
                      "Close the other window, then start DayZ-ServerMan again.")
GUI_REFUSAL_COMMAND = ("DayZ-ServerMan is already active for this folder{command}. "
                       "Wait until the command has finished, then start DayZ-ServerMan again.")
# The window where neither lock works (5.2)
GUI_UNSUPPORTED = (
    "DayZ-ServerMan cannot protect this folder against a second copy. Move the DayZ-ServerMan folder "
    "to a local drive, then start it again."
)

# Builds the composition of a session; a test may pass a recording builder
CompositionBuilder = Callable[..., ApplicationComposition]


def resolve_paths(manager_root: Path | None) -> PortablePaths:
    """Resolve the manager root once, by the same rule as build_composition (3.1 "Root")."""
    if manager_root is not None:
        return PortablePaths.from_root(manager_root)
    return PortablePaths.from_source(Path(composition_module.__file__))


def gui_refusal_text(holder: HolderInfo | None) -> str:
    """Return the window's refusal sentence, naming the holder and its way out only when the holder is known."""
    if holder is None:
        return GUI_REFUSAL.format(holder="")
    if holder.holder == "window":
        return GUI_REFUSAL_WINDOW
    return GUI_REFUSAL_COMMAND.format(command=f" (command: {holder.command})" if holder.command else "")


@dataclass
class OwnerSession:
    """An owner composition with the instance lock and the server-folder writer side it holds."""

    paths: PortablePaths
    composition: ApplicationComposition
    lock: InstanceLock
    lock_file: FolderLockFile | None
    writer: object

    def close(self, drain_seconds: float | None) -> None:
        """Drain the lane, stop the build check, then close the writer handle and the instance lock."""
        try:
            # Today's window close: request the drain and wait for it; a command waits without a limit
            self.composition.shutdown.request_shutdown()
            self.composition.shutdown.wait_for_close(drain_seconds)
            self.composition.server_build.service.stop(BUILD_CHECK_STOP_SECONDS)
        finally:
            if self.lock_file is not None:
                self.lock_file.close()
            self.lock.close()


def open_owner_session(
    manager_root: Path | None, holder: str, command: str | None = None, *,
    require_byte_range_lock: bool, build: CompositionBuilder = build_composition,
    lock_factory: Callable[..., InstanceLock] = InstanceLock,
) -> OwnerSession:
    """Take the instance lock before the composition is built; raise InstanceActive or InstanceLockUnsupported.

    The window passes `require_byte_range_lock` false and runs on the root mutex
    alone where the byte-range lock is unsupported; a command passes true.
    """
    paths = resolve_paths(manager_root)
    # (1) The data folder holds the lock files; only an owner creates it
    paths.data.mkdir(parents=True, exist_ok=True)
    # (2) and (3): the byte-range lock and the root mutex
    lock = lock_factory(paths.data / INSTANCE_LOCK_FILE, root_mutex_name(paths.root))
    try:
        lock.acquire(require_byte_range_lock)
    except InstanceActive as refusal:
        # The holder file is read only after a refusal, and only a live holder is named
        refusal.holder = read_holder(paths.data / HOLDER_FILE)
        raise
    try:
        # (4) The holder file, best effort
        write_holder(paths.data / HOLDER_FILE, holder, command)
        # (5) The server-folder lock file; without the byte-range lock the session has no writer side
        lock_file, writer = _folder_writer(paths, lock.byte_range_supported, require_byte_range_lock)
        try:
            # (6) The composition with its recoveries, which already take the writer side
            built = build(paths.root, mode=SessionMode.OWNER, folder_writer=writer)
        except BaseException:
            if lock_file is not None:
                lock_file.close()
            raise
    except BaseException:
        lock.close()
        raise
    if not lock.byte_range_supported:
        # The window runs on the root mutex alone in this folder (A5 ruling of 11:30:55)
        try:
            built.logger.emit("instance_lock.unsupported_folder", level="WARNING")
        except (OSError, ValueError):
            pass
    return OwnerSession(paths, built, lock, lock_file, writer)


def _folder_writer(
    paths: PortablePaths, byte_range_supported: bool, require_byte_range_lock: bool,
) -> tuple[FolderLockFile | None, object]:
    """Create the server-folder lock file and return it with the writer side of this session."""
    try:
        lock_file = FolderLockFile.create(paths.data / FOLDER_LOCK_FILE)
    except OSError as error:
        if require_byte_range_lock:
            raise InstanceLockUnsupported() from error
        return None, NO_FOLDER_LOCK
    # An unsupported folder still gets the file, but no writer side (3.2 "Unsupported folder")
    return lock_file, FolderWriter(lock_file) if byte_range_supported else NO_FOLDER_LOCK
