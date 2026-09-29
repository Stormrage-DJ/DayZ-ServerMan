"""Bounded advisory metadata reader for local DayZ mod directories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .backup_verification import is_reparse


# Metadata files above this byte bound are ignored as unreliable
MAX_METADATA_BYTES = 128 * 1024
# Matches simple name and version assignments in mod metadata scripts
ASSIGNMENT = re.compile(
    r"(?im)^\s*(?P<key>name|version)\s*=\s*[\"'](?P<value>[^\"'\r\n]{1,160})[\"']\s*;"
)


@dataclass(frozen=True)
class ModMetadata:
    """Advisory name and version read from a mod directory's metadata files."""
    name: str | None
    version: str | None


def read_mod_metadata(mod_root: Path) -> ModMetadata:
    """Return the advisory name and version of a local mod directory."""
    # Only plain, real directories are read; links are refused outright
    if not mod_root.is_dir() or mod_root.is_symlink() or is_reparse(mod_root):
        return ModMetadata(None, None)
    values: dict[str, str] = {}
    # Read the two known metadata scripts, mod.cpp first
    for filename in ("mod.cpp", "meta.cpp"):
        path = mod_root / filename
        if not path.is_file() or path.is_symlink():
            continue
        try:
            # Oversized files are skipped because their content is unreliable
            if path.stat().st_size > MAX_METADATA_BYTES:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        parsed = {
            match.group("key").casefold(): " ".join(match.group("value").split())
            for match in ASSIGNMENT.finditer(text)
        }
        # The first file to declare a field wins
        for key in ("name", "version"):
            if key not in values and parsed.get(key):
                values[key] = parsed[key]
    return ModMetadata(values.get("name"), values.get("version"))
