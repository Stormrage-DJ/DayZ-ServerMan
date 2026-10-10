"""Content files of editor records, named by SHA-256 and read with digest verification (D2)."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Iterable

from ..adapters.windows.shared_files import read_bytes_shared, replace_file
from ..domain.models import RepositoryError


# Name of a content file, and of a temporary content file that the unused-content sweep removes
CONTENT_NAME = re.compile(r"[0-9a-f]{64}")
TEMPORARY_NAME = re.compile(r"\.[0-9a-f]{64}\.[0-9a-f]+\.tmp")


class ContentCorrupt(RepositoryError):
    """Raised when a content file is missing or its bytes do not match its digest; it is never repaired."""

    def __init__(self, path: Path, detail: str) -> None:
        """Store the content path and what is wrong with it."""
        self.path = path
        super().__init__(f"record content {path.name} {detail}; the owning record needs recovery")


class ContentStore:
    """The files/ folder of one owning record folder: an original, an association or a ledger target."""

    def __init__(self, owner_folder: Path) -> None:
        """Bind the store to the files folder of the owning record."""
        self.folder = owner_folder / "files"

    def write(self, data: bytes) -> str:
        """Store bytes once under their digest and return it; existing content must already match."""
        digest = hashlib.sha256(data).hexdigest()
        target = self.folder / digest
        # Content that exists is verified, never replaced; a mismatch is corruption, not a reason to rewrite
        if target.exists():
            self.read(digest)
            return digest
        # Write through a temporary file beside the target, then publish it atomically
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / f".{digest}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, target)
        # Prove the published bytes before a record names them
        self.read(digest)
        return digest

    def read(self, digest: str) -> bytes:
        """Return the bytes of one content file after checking them against their digest."""
        if CONTENT_NAME.fullmatch(digest) is None:
            raise ValueError("a content digest must be 64 lowercase hexadecimal digits")
        path = self.folder / digest
        try:
            data = read_bytes_shared(path)
        except FileNotFoundError as error:
            raise ContentCorrupt(path, "is missing") from error
        if hashlib.sha256(data).hexdigest() != digest:
            raise ContentCorrupt(path, "does not match its digest")
        return data

    def sweep(self, used: Iterable[str]) -> list[Path]:
        """Remove content that no record uses and leftover temporary content; call only after a commit or rollback."""
        if not self.folder.is_dir():
            return []
        keep = set(used)
        removed = []
        # Only content and temporary-content names are candidates; other files stay
        for path in sorted(self.folder.iterdir()):
            unused = CONTENT_NAME.fullmatch(path.name) is not None and path.name not in keep
            if path.is_file() and (unused or TEMPORARY_NAME.fullmatch(path.name)):
                path.unlink()
                removed.append(path)
        return removed
