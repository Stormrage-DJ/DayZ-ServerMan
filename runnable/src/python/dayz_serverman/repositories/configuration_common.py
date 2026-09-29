"""Shared types and byte handling for external configuration files."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# UTF-8 byte-order mark preserved across read-modify-write cycles
UTF8_BOM = b"\xef\xbb\xbf"


class ConfigurationFileError(RuntimeError):
    """Raised when a configuration file is missing, unreadable, or malformed."""
    pass


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """One loaded configuration file with its bytes, values, and transform state."""

    target: str
    path: Path
    digest: str
    content: bytes
    values: dict[str, Any]
    state: object


def read_utf8(path: Path) -> tuple[bytes, str, bool]:
    """Return the raw bytes, decoded text, and BOM flag of a UTF-8 file."""
    # Read raw bytes and report missing or unreadable targets as domain errors
    try:
        content = path.read_bytes()
    except FileNotFoundError as error:
        raise ConfigurationFileError("configuration target was not found") from error
    except OSError as error:
        raise ConfigurationFileError("configuration target could not be read") from error
    # Detect a BOM so the original encoding can be preserved on rewrite
    bom = content.startswith(UTF8_BOM)
    try:
        text = content[len(UTF8_BOM):].decode("utf-8") if bom else content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationFileError("configuration must use valid UTF-8 encoding") from error
    return content, text, bom


def encode_utf8(text: str, bom: bool) -> bytes:
    """Encode text as UTF-8, re-adding the BOM when the source had one."""
    # Encode once, then prepend the BOM when the source had one
    content = text.encode("utf-8")
    return UTF8_BOM + content if bom else content


def newline_for(text: str) -> str:
    """Return the first newline convention found in the text, defaulting to LF."""
    # Prefer the source's own newline style so rewrites stay consistent
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match else "\n"


def digest_bytes(content: bytes) -> str:
    """Return the SHA-256 hex digest of the given bytes."""
    return hashlib.sha256(content).hexdigest()
