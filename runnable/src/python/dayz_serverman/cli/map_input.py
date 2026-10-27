"""The bounded read of the input file of `map save --from-file` and `map import --from-file` (D17).

A12: the shared read of `adapters/windows/user_files.py` reads the file through the shared-read opener. It reads
at most 8 MiB plus one byte, so a larger file is refused without a read of the whole file (DC-6). This module
maps its closed reasons to the CLI texts. Every refusal is an argument error: exit 2 with the Product Owner text
of its case (2026-10-16 16:25:27 in evidence 10.15), and nothing is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..adapters.windows.user_files import UserFileError, read_text
from ..domain.mission_map_values import MAX_DOCUMENT_BYTES, refuse_json_constant, unique_json_keys
from . import map_wording as words
from .exit_codes import USAGE
from .output import CliFailure, Echo, sentence
from .wording import invalid_file

# The exit 2 text of each read reason of the shared read
READ_TEXTS = {"unreadable": words.FILE_UNREADABLE, "too_large": words.FILE_TOO_LARGE,
              "not_utf8": words.FILE_NOT_UTF8}


def read_input_text(path: str) -> str:
    """Return the UTF-8 text of an input file; a missing, unreadable, too large or non-UTF-8 file exits 2."""
    try:
        # The limit is read here at each call, so a test can make it small
        return read_text(Path(path), MAX_DOCUMENT_BYTES)
    except UserFileError as error:
        raise _refused(READ_TEXTS[error.reason], path) from error


def read_plan_object(path: str) -> dict[str, Any]:
    """Return the JSON object of an input file, parsed as strict as D1 parses a plan; other content exits 2."""
    text = read_input_text(path)
    try:
        # D1: no NaN or infinity and no repeated key
        document = json.loads(text, parse_constant=refuse_json_constant, object_pairs_hook=unique_json_keys)
    except (ValueError, RecursionError) as error:
        raise _refused(words.FILE_NOT_JSON, path) from error
    if not isinstance(document, dict):
        raise CliFailure("USAGE", invalid_file(path), USAGE)
    return document


def _refused(text: str, path: str) -> CliFailure:
    """Return the exit 2 refusal of one file case, with the path as the operator typed it."""
    before, after = text.split("{path}")
    return CliFailure("USAGE", sentence(before, Echo(path), after), USAGE)
