"""The export output-path adapter of `map export` (leaf 10.6, D17); this module holds nothing else (DC-6).

`map export` is the only command of an observer session that writes a file (AR-2). It writes the one file
that `--output` names, after the bridge call, with no reader hold and no log line. The rules:

- The parent folder must exist, and the final path must not be a folder or a reparse point (exit 2).
- The resolved, case-folded parent folder must not be inside a protected root: the manager root, the backup
  folder of the settings or the DayZ root. A junction in the path cannot bypass the check (DC-3, exit 2).
- An existing file needs `--overwrite`, else exit 4, also with `--yes` (PO-4). Without `--overwrite` the
  last step is a rename that fails when the target exists, so a file that appears after the check is never
  replaced. With `--overwrite` the last step is `replace_file`.
- The file is read again with the shared-read opener. A different size or SHA-256, or an OSError, exits 1.

Every file step goes through the shared-file adapter (A12). `write_bytes_atomically` is not used, because
it creates missing parent folders.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..adapters.windows.shared_files import (
    read_bytes_shared, remove_file, rename_new_file, replace_file, stage_bytes,
)
from . import map_wording as words
from .exit_codes import FAILED, NOT_CONFIRMED, USAGE
from .output import CliFailure, Echo, Line, SentencePart, TypeText, sentence

# The reparse-point attribute of a junction or a symbolic link; older Python versions lack the constant
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# The option that the exists refusal names, as text to type
OVERWRITE_FLAG = "--overwrite"


@dataclass(frozen=True)
class ExportTarget:
    """The checked output: the path as the operator typed it, and the final path in the resolved folder."""

    typed: str
    path: Path


@dataclass(frozen=True)
class WrittenFile:
    """The file that the export wrote and read again: its path, its size in bytes and its SHA-256."""

    path: Path
    size: int
    sha256: str


def check_output(typed: str, protected: Iterable[Path], overwrite: bool) -> ExportTarget:
    """Return the output target, or exit 2 for a path refusal and 4 for an existing file without `--overwrite`."""
    lexical = Path(os.path.abspath(typed))
    if not lexical.parent.is_dir():
        raise _failure(words.EXPORT_NO_FOLDER, typed, "USAGE", USAGE)
    if _folder_or_link(lexical):
        raise _failure(words.EXPORT_FOLDER_OR_LINK, typed, "USAGE", USAGE)
    # The resolved parent follows every junction and link, so the comparison sees the real folder
    folder = lexical.parent.resolve(strict=True)
    if any(_inside(folder, root) for root in protected):
        raise _failure(words.EXPORT_PROTECTED, typed, "USAGE", USAGE)
    target = folder / lexical.name
    if not overwrite and os.path.lexists(target):
        raise exists_refusal(typed)
    return ExportTarget(typed, target)


def write_export(target: ExportTarget, text: str, overwrite: bool) -> WrittenFile:
    """Write the UTF-8 bytes of `text` unchanged to the target and check them; a failed step leaves no new file."""
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    temporary: Path | None = None
    installed = False
    try:
        # A staging file in the target folder; the folder is never created
        temporary = stage_bytes(target.path.parent, target.path.name, payload)
        try:
            if overwrite:
                replace_file(temporary, target.path)
            else:
                rename_new_file(temporary, target.path)
        except FileExistsError as error:
            # The file appeared after the check: it stays as it is, and the staging file goes
            _remove_quietly(temporary)
            raise exists_refusal(target.typed) from error
        installed = True
        written = read_bytes_shared(target.path)
        if len(written) != len(payload) or hashlib.sha256(written).hexdigest() != digest:
            raise OSError("the export file differs from the exported text")
    except OSError as error:
        _remove_quietly(temporary)
        if installed:
            _remove_quietly(target.path)
        raise _failure(words.EXPORT_NOT_WRITTEN, target.typed, "STORAGE_FAILURE", FAILED) from error
    return WrittenFile(target.path, len(payload), digest)


def exists_refusal(typed: str) -> CliFailure:
    """Return the exit 4 refusal of an existing file without `--overwrite` (PO-4, the race of 14:35:24)."""
    return _failure(words.EXPORT_EXISTS, typed, "CONFIRMATION_REQUIRED", NOT_CONFIRMED)


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


def _remove_quietly(path: Path | None) -> None:
    """Remove a staging file or a failed export file; a second failure keeps the first one as the cause."""
    if path is None:
        return
    try:
        remove_file(path)
    except OSError:
        pass


def _failure(text: str, typed: str, code: str, exit_code: int) -> CliFailure:
    """Return a refusal with the path as the operator typed it and `--overwrite` as text to type."""
    return CliFailure(code, _line(text, typed), exit_code)


def _line(text: str, typed: str) -> Line:
    """Build one line of an export text: `{path}` as typed, `--overwrite` as text to type."""
    parts: list[SentencePart] = []
    for index, piece in enumerate(text.split("{path}")):
        if index:
            parts.append(Echo(typed))
        flag_pieces = piece.split(OVERWRITE_FLAG)
        for flag_index, flag_piece in enumerate(flag_pieces):
            if flag_index:
                parts.append(TypeText(OVERWRITE_FLAG))
            parts.append(flag_piece)
    return sentence(*parts)
