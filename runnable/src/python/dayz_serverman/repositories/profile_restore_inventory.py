"""Fail-closed directory inventories used by preview and transaction recovery."""

import os
import stat
from pathlib import Path
from typing import Any

from ..domain.profiles import ProfileValidationError
from .backup_verification import sha256_file


def safe_exists(path: Path) -> bool:
    """Reject unsafe ancestors and distinguish absence from access failure."""
    current = path.__class__(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ProfileValidationError("Restore path contains a link or reparse point.")
    return True


def inventory(path: Path) -> dict[str, Any] | None:
    """Hash complete file/directory inventories, recording absent and empty targets."""
    if not safe_exists(path):
        return None
    if path.is_file():
        return {"type": "file", "sha256": sha256_file(path), "size": path.stat().st_size}
    if not path.is_dir():
        raise ProfileValidationError("Restore target has an unsupported type.")
    result = {"type": "directory", "directories": [], "files": {}}
    seen = set()
    def failed(error):
        """Propagate unreadable directory traversal instead of omitting data."""
        raise error
    for parent, directories, files in os.walk(path, followlinks=False, onerror=failed):
        for name in (*directories, *files):
            child = path.__class__(parent) / name
            safe_exists(child)
            relative = child.relative_to(path).as_posix()
            if relative.casefold() in seen:
                raise ProfileValidationError("Restore inventory has Windows case collisions.")
            seen.add(relative.casefold())
            if child.is_dir():
                result["directories"].append(relative)
            elif child.is_file():
                result["files"][relative] = {"sha256": sha256_file(child), "size": child.stat().st_size}
            else:
                raise ProfileValidationError("Restore inventory has an unsupported file type.")
    result["directories"].sort(key=str.casefold)
    return result
