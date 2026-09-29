"""Read-only legacy backup reference queries and explicit revalidation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..repositories.backup_verification import is_reparse, path_has_reparse, sha256_file
from ..repositories.external_root import validate_external_root
from ..repositories.legacy_backup_index import (
    LegacyBackupEntry, LegacyBackupIndexRepository,
)
from ..repositories.legacy_backup_proof import utc_timestamp


class LegacyBackupService:
    """List legacy backup references and re-check them against the filesystem."""

    def __init__(self, repository: LegacyBackupIndexRepository) -> None:
        """Store the legacy backup index repository."""
        self._repository = repository

    def list(self) -> dict[str, object]:
        """Return the public index or an empty external-only view."""
        index = self._repository.load_optional()
        # Report an absent index as external-only and not restorable
        if index is None:
            return {
                "revision": None, "entries": [], "external_reference_only": True,
                "restorable": False,
            }
        return index.public()

    def revalidate(self, expected_revision: object) -> dict[str, object]:
        """Re-check every stored entry against the external root and save the result."""
        # Require an integer revision guard before touching the index
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("expected_revision must be an integer")
        current = self._repository.load_optional()
        # Refuse revalidation when the stored revision no longer matches
        if current is None or current.revision != expected_revision:
            from ..domain.models import RevisionConflict
            raise RevisionConflict("legacy backup index revision changed")
        # Resolve the canonical external root before touching any entry
        canonical_root = validate_external_root(
            current.source_root, require_existing=False, require_canonical=True,
        )
        root = Path(canonical_root)
        # Stamp one verification time for the whole batch
        verified_at = utc_timestamp()
        # Re-check every entry against the current filesystem
        entries = tuple(
            replace(entry, status=_status(root, entry), last_verified_at=verified_at)
            for entry in current.entries
        )
        # Persist the refreshed index and return its public form
        updated = replace(current, last_verified_at=verified_at, entries=entries)
        return self._repository.save(updated, expected_revision).public()


def _status(root: Path, entry: LegacyBackupEntry) -> str:
    """Classify one legacy entry against the external root."""
    try:
        # A missing or linked root makes every entry missing
        if not root.is_dir() or path_has_reparse(root):
            return "MISSING"
        # Require the entry to resolve inside the root
        target = root / Path(entry.relative_path)
        resolved = target.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        # Require a regular readable file that is not behind a link
        if not target.is_file() or is_reparse(target) or path_has_reparse(target.parent):
            return "UNREADABLE"
        # A size or hash mismatch means the backup content changed
        if target.stat().st_size != entry.size or sha256_file(target) != entry.sha256:
            return "CHANGED"
        return "AVAILABLE"
    except FileNotFoundError:
        return "MISSING"
    except (OSError, ValueError):
        return "UNREADABLE"
