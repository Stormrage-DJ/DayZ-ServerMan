"""Same-directory atomic publication for external configuration files."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable

from .configuration_common import digest_bytes


class ContentChangedError(RuntimeError):
    """Raised when the configuration target changes unexpectedly."""
    pass


class AtomicFilePublisher:
    """Publish a file in place only while its expected digest still matches."""

    def __init__(self, replace: Callable[[Path, Path], None] = os.replace) -> None:
        """Store the replacement strategy used for the final swap."""
        self._replace = replace

    def publish(self, path: Path, content: bytes, expected_digest: str) -> str:
        """Publish content and return the digest of the written file."""
        # Refuse publication when the file changed after it was loaded
        current = self._read_digest(path)
        if current != expected_digest:
            raise ContentChangedError("configuration changed after it was loaded")
        # Stage the replacement beside the target so the swap stays on one volume
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            # Write and flush the staged copy before any swap
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Re-check the digest so a concurrent edit cannot be overwritten
            if self._read_digest(path) != expected_digest:
                raise ContentChangedError("configuration changed before publication")
            # Swap the staged file into place and verify the published bytes
            self._replace(temporary, path)
            published = self._read_digest(path)
            if published != digest_bytes(content):
                raise OSError("published configuration failed digest verification")
            return published
        except Exception:
            # Remove the staged file when any step fails
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise

    @staticmethod
    def _read_digest(path: Path) -> str:
        """Return the digest of the current file bytes."""
        # Treat a missing target as changed content so the caller reloads it
        try:
            return digest_bytes(path.read_bytes())
        except FileNotFoundError as error:
            raise ContentChangedError("configuration target is no longer present") from error
