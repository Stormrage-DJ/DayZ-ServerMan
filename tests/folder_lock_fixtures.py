"""Shared helpers of the A13 tests: reader child processes, an in-process held reader and tree hashes."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"
sys.path.insert(0, str(PYTHON_ROOT))

from dayz_serverman.adapters.windows.server_folder_lock import (  # noqa: E402
    BODY,
    GATE,
    FolderLockFile,
    FolderReader,
    FolderWriter,
)

# Child process with one reader per mode:
#   hold     - one shared hold until stdin closes
#   hold_for - one shared hold for argv[4] seconds
#   loop     - two threads with their own handles read in overlapping 100 ms holds until stdin closes
#   folders  - reads inside the folders argv[4:] under the reader side until stdin closes; prints failures
READER_CHILD = r"""
import os, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderReader
from dayz_serverman.adapters.windows.shared_files import read_bytes_shared
path, mode = Path(sys.argv[2]), sys.argv[3]
stop = threading.Event()
def watch():
    sys.stdin.read()
    stop.set()
if mode == "hold":
    reader = FolderReader(FolderLockFile.create(path))
    with reader.shared(30):
        print("ready", flush=True)
        watch()
elif mode == "hold_for":
    reader = FolderReader(FolderLockFile.create(path))
    with reader.shared(30):
        print("ready", flush=True)
        time.sleep(float(sys.argv[4]))
    print("released", flush=True)
    watch()
elif mode == "loop":
    started = threading.Barrier(3)
    def run(offset):
        reader = FolderReader(FolderLockFile.create(path))
        time.sleep(offset)
        started.wait()
        while not stop.is_set():
            with reader.shared(30):
                time.sleep(0.1)
    for offset in (0.0, 0.05):
        threading.Thread(target=run, args=(offset,), daemon=True).start()
    started.wait()
    print("ready", flush=True)
    watch()
elif mode == "folders":
    reader = FolderReader(FolderLockFile.create(path))
    folders = [Path(item) for item in sys.argv[4:]]
    counts = {"reads": 0, "failures": 0}
    def run():
        while not stop.is_set():
            try:
                with reader.shared(30):
                    for folder in folders:
                        with os.scandir(folder) as entries:
                            names = [entry.name for entry in entries]
                        for name in names:
                            if (folder / name).is_file():
                                read_bytes_shared(folder / name)
                counts["reads"] += 1
            except OSError:
                # A folder that is being swapped may be missing for a moment between renames
                counts["failures"] += 1
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print("ready", flush=True)
    watch()
    thread.join(10)
    print(counts["reads"], counts["failures"], flush=True)
"""


def spawn_reader(lock_path: Path, mode: str, *arguments: str) -> subprocess.Popen:
    """Start a reader child and wait until it reports that it holds or loops."""
    child = subprocess.Popen(
        [sys.executable, "-c", READER_CHILD, str(PYTHON_ROOT), str(lock_path), mode, *arguments],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    line = child.stdout.readline().strip()
    if line != "ready":
        child.kill()
        raise RuntimeError(f"reader child did not start: {line!r}")
    return child


def kill(child: subprocess.Popen) -> None:
    """Terminate the child, wait for it and close its pipes."""
    child.kill()
    child.wait(10)
    for stream in (child.stdin, child.stdout):
        if stream is not None and not stream.closed:
            stream.close()


def finish(child: subprocess.Popen) -> str:
    """Close the child's stdin, so it releases and ends, and return the rest of its output."""
    output, _ = child.communicate(timeout=30)
    return output


@contextmanager
def held_reader(lock_path: Path) -> Iterator[FolderReader]:
    """Hold the reader side on a handle of its own in this process, as an observer call does."""
    reader = FolderReader(FolderLockFile.create(lock_path))
    try:
        with reader.shared(30):
            yield reader
    finally:
        reader.close()


def writer_for(lock_path: Path) -> FolderWriter:
    """Return an owner writer side on a handle of its own."""
    return FolderWriter(FolderLockFile.create(lock_path))


def body_and_gate_free(lock_path: Path) -> bool:
    """Report whether another handle can take both bytes exclusively right now, and release them."""
    probe = FolderLockFile.create(lock_path)
    try:
        taken = [offset for offset in (GATE, BODY) if probe.try_lock(offset, True)]
        for offset in taken:
            probe.unlock(offset)
        return len(taken) == 2
    finally:
        probe.close()


def tree_hashes(*roots: Path) -> dict[str, str | None]:
    """Return every path below the roots with the SHA-256 of each file, or None for a folder."""
    result: dict[str, str | None] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result
