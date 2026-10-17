"""The export output-path adapter of `map export` (leaf 10.6, D17); this module holds nothing else (DC-6).

`map export` is the only command of an observer session that writes a file (AR-2). It writes the one file
that `--output` names, after the bridge call, with no reader hold and no log line. The path check, the staged
write and the read-back check are the shared steps of `adapters/windows/user_files.py`. This module maps their
closed reasons to the CLI texts and exit codes:

- A missing parent folder exits 2, and so does a folder or reparse point as the final path. A protected root
  also exits 2: the manager root, the backup folder of the settings or the DayZ root.
- An existing file needs `--overwrite`, else exit 4, also with `--yes` (PO-4). A file that appears after the
  check is never replaced, and it exits 4 with the same text.
- A write or read-back failure exits 1, and no new file stays.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# The shared-file steps of the write; `write_file` takes them from this module, so a test can replace one here
from ..adapters.windows.shared_files import (  # noqa: F401
    read_bytes_shared, remove_file, rename_new_file, replace_file, stage_bytes,
)
from ..adapters.windows.user_files import UserFileError, WrittenFile, checked_target, write_file
from . import map_wording as words
from .exit_codes import FAILED, NOT_CONFIRMED, USAGE
from .output import CliFailure, Echo, Line, SentencePart, TypeText, sentence

# The option that the exists refusal names, as text to type
OVERWRITE_FLAG = "--overwrite"
# The exit 2 text of each path reason of the shared check
PATH_TEXTS = {"no_folder": words.EXPORT_NO_FOLDER, "folder_or_link": words.EXPORT_FOLDER_OR_LINK,
              "protected": words.EXPORT_PROTECTED}

__all__ = ["ExportTarget", "WrittenFile", "check_output", "exists_refusal", "write_export"]


@dataclass(frozen=True)
class ExportTarget:
    """The checked output: the path as the operator typed it, and the final path in the resolved folder."""

    typed: str
    path: Path


def check_output(typed: str, protected: Iterable[Path], overwrite: bool) -> ExportTarget:
    """Return the output target, or exit 2 for a path refusal and 4 for an existing file without `--overwrite`."""
    try:
        return ExportTarget(typed, checked_target(typed, protected, overwrite))
    except UserFileError as error:
        if error.reason == "exists":
            raise exists_refusal(typed) from error
        raise _failure(PATH_TEXTS[error.reason], typed, "USAGE", USAGE) from error


def write_export(target: ExportTarget, text: str, overwrite: bool) -> WrittenFile:
    """Write the UTF-8 bytes of `text` unchanged to the target and check them; a failed step leaves no new file."""
    try:
        return write_file(target.path, text.encode("utf-8"), overwrite, steps=sys.modules[__name__])
    except UserFileError as error:
        if error.reason == "exists":
            raise exists_refusal(target.typed) from error
        raise _failure(words.EXPORT_NOT_WRITTEN, target.typed, "STORAGE_FAILURE", FAILED) from error


def exists_refusal(typed: str) -> CliFailure:
    """Return the exit 4 refusal of an existing file without `--overwrite` (PO-4, the race of 14:35:24)."""
    return _failure(words.EXPORT_EXISTS, typed, "CONFIRMATION_REQUIRED", NOT_CONFIRMED)


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
