"""The bounded read of the input file of `map save --from-file`, and later of `map import --from-file` (D17).

A12: the file is read through the shared-read opener. At most 8 MiB plus one byte is read, so a larger file
is refused without a read of the whole file (DC-6). Every refusal is an argument error: exit 2 with the
Product Owner text of its case (2026-10-16 16:25:27 in evidence 10.15), and nothing is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import open_shared
from ..domain.mission_map_values import MAX_DOCUMENT_BYTES, refuse_json_constant, unique_json_keys
from . import map_wording as words
from .exit_codes import USAGE
from .output import CliFailure, Echo, sentence
from .wording import invalid_file


def read_input_text(path: str) -> str:
    """Return the UTF-8 text of an input file; a missing, unreadable, too large or non-UTF-8 file exits 2."""
    try:
        with open_shared(Path(path)) as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as error:
        raise _refused(words.FILE_UNREADABLE, path) from error
    if len(data) > MAX_DOCUMENT_BYTES:
        raise _refused(words.FILE_TOO_LARGE, path)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _refused(words.FILE_NOT_UTF8, path) from error


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
