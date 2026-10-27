"""Wording-free steps for a map plan file that the admin names: the target check, the staged write, the read-back
check and the bounded read (D17). `map export`, `map import`, `map save` and the editor window share them.

Each refusal raises `UserFileError` with one closed reason. The CLI and the window host map the reason to their
own texts and exit codes. The rules:

- The parent folder must exist, and the final path must not be a folder or a reparse point.
- The resolved, case-folded parent folder must not be inside a protected root. A junction in the path cannot
  bypass the check, because the parent is resolved first (DC-3).
- Without `overwrite`, an existing file is refused, and the last step is a rename that fails when the target
  exists. So a file that appears after the check is never replaced. With `overwrite`, the last step is
  `replace_file`.
- The file is read again with the shared-read opener. A different size or SHA-256, or an OSError, removes it.
- A read takes at most the limit plus one byte, so a larger file is refused without a read of the whole file.

Every file step goes through the shared-file adapter (A12). `write_bytes_atomically` is not used, because it
creates missing parent folders.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import shared_files

# The closed reasons of a refused user file
REASONS = frozenset(("no_folder", "folder_or_link", "protected", "exists", "not_written", "unreadable",
                     "too_large", "not_utf8"))
# The reparse-point attribute of a junction or a symbolic link; older Python versions lack the constant
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UserFileError(Exception):
    """Raised when a user file cannot be written or read; `reason` is one of `REASONS`.

    It is not an OSError, so a write step that handles OSError never takes a refusal for a failed step.
    """

    def __init__(self, reason: str) -> None:
        """Keep the closed reason; the message is the reason, for logs and tests only."""
        if reason not in REASONS:
            raise ValueError(f"unknown user file reason {reason}")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class WrittenFile:
    """The file that was written and read again: its path, its size in bytes and its SHA-256."""

    path: Path
    size: int
    sha256: str


def checked_target(typed: str, protected: Iterable[Path], overwrite: bool) -> Path:
    """Return the final path in the resolved parent folder, or raise `UserFileError` for a refused target.

    The checks run in this order: a missing folder, a folder or link, a protected root, then an existing file
    without `overwrite`.
    """
    lexical = Path(os.path.abspath(typed))
    if not lexical.parent.is_dir():
        raise UserFileError("no_folder")
    if _folder_or_link(lexical):
        raise UserFileError("folder_or_link")
    # The resolved parent follows every junction and link, so the comparison sees the real folder
    folder = lexical.parent.resolve(strict=True)
    if any(_inside(folder, root) for root in protected):
        raise UserFileError("protected")
    target = folder / lexical.name
    if not overwrite and os.path.lexists(target):
        raise UserFileError("exists")
    return target


def write_file(path: Path, payload: bytes, overwrite: bool, steps: Any = shared_files) -> WrittenFile:
    """Write the bytes to a checked target and check them; a failed step leaves no new file.

    `steps` holds the shared-file steps `stage_bytes`, `rename_new_file`, `replace_file`, `read_bytes_shared` and
    `remove_file`. A caller can pass its own module, so a test can replace one step there. A target that appears
    before the rename raises `exists`; any other OSError raises `not_written`.
    """
    digest = hashlib.sha256(payload).hexdigest()
    temporary: Path | None = None
    installed = False
    try:
        # A staging file in the target folder; the folder is never created
        temporary = steps.stage_bytes(path.parent, path.name, payload)
        try:
            if overwrite:
                steps.replace_file(temporary, path)
            else:
                steps.rename_new_file(temporary, path)
        except FileExistsError as error:
            # The file appeared after the check: it stays as it is, and the staging file goes
            _remove_quietly(steps, temporary)
            raise UserFileError("exists") from error
        installed = True
        # Read the file again, so a short or changed write is never reported as written
        written = steps.read_bytes_shared(path)
        if len(written) != len(payload) or hashlib.sha256(written).hexdigest() != digest:
            raise OSError("the written file differs from the payload")
    except OSError as error:
        _remove_quietly(steps, temporary)
        if installed:
            _remove_quietly(steps, path)
        raise UserFileError("not_written") from error
    return WrittenFile(path, len(payload), digest)


def read_text(path: Path, limit: int, opener: Any = shared_files.open_shared) -> str:
    """Return the UTF-8 text of a file of at most `limit` bytes, read through the shared-read opener.

    An OSError raises `unreadable`, a larger file `too_large` and bytes that are not UTF-8 `not_utf8`.
    """
    try:
        with opener(path) as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise UserFileError("unreadable") from error
    if len(data) > limit:
        raise UserFileError("too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UserFileError("not_utf8") from error


def _folder_or_link(path: Path) -> bool:
    """Report whether the final path is a folder or a reparse point; a path that is not there is neither."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT))


def _comparable(path: Path) -> str:
    """Return the case-folded comparison form of a resolved path."""
    return os.path.normcase(os.fspath(path)).casefold()


def _inside(folder: Path, root: Path) -> bool:
    """Report whether a resolved folder is a protected root or below it; the root is resolved here."""
    child, parent = _comparable(folder), _comparable(Path(root).resolve(strict=False))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def _remove_quietly(steps: Any, path: Path | None) -> None:
    """Remove a staging file or a failed file; a second failure keeps the first one as the cause."""
    if path is None:
        return
    try:
        steps.remove_file(path)
    except OSError:
        pass
