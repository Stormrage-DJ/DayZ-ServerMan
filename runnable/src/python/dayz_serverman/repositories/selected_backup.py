"""Session-bound archive selections without importing or modifying source ZIPs."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import uuid

from .backup_archives import read_archive_manifest, verify_archive, materialize_archive
from .backup_sources import is_reparse
from .backup_verification import sha256_file, verify_directory


class SelectedBackups:
    """Bind opaque references to verified native selections and their byte hashes."""
    def __init__(self):
        """Keep selections local to the current application session."""
        self.selections = {}

    def inspect(self, raw_path):
        """Validate one archive and retain its original path without copying it."""
        if not isinstance(raw_path, str) or not raw_path or not Path(raw_path).is_absolute():
            raise ValueError("Select an absolute backup ZIP path.")
        path = Path(raw_path)
        if path.suffix.lower() != ".zip" or not path.is_file() or any(is_reparse(p) for p in (path, *path.parents)):
            raise ValueError("Select a regular backup ZIP without links.")
        before = sha256_file(path)
        manifest = read_archive_manifest(path, check_archive_name=False)
        verify_archive(path, manifest)
        if before != sha256_file(path):
            raise ValueError("The selected archive changed during verification. Browse again.")
        if manifest.schema_version != 3:
            raise ValueError("This older backup lacks a complete profile definition. Select a new full backup.")
        if len(self.selections) >= 256:
            raise ValueError("Too many archive selections. Restart the manager before browsing again.")
        token = "selected-" + uuid.uuid4().hex
        self.selections[token] = (path, before)
        return {"backup_id": token, "archive_name": path.name, "display_name": manifest.reconstruction["profile"]["display_name"],
                "profile_id": manifest.profile_id, "created_at": manifest.created_at,
                "total_size": sum(entry.size for entry in manifest.entries), "entry_count": len(manifest.entries), "can_restore_profile": True}

    def path(self, token):
        """Reject expired selections or changed source bytes before preview or apply."""
        try:
            path, digest = self.selections[token]
        except KeyError as error:
            raise ValueError("Backup selection expired. Browse for the archive again.") from error
        if not path.is_file() or any(is_reparse(p) for p in (path, *path.parents)) or sha256_file(path) != digest:
            raise ValueError("The selected archive changed or became unavailable. Browse again.")
        return path

    @contextmanager
    def open_verified(self, token):
        """Extract verified content privately, leaving the selected folder untouched."""
        path = self.path(token)
        manifest = read_archive_manifest(path, check_archive_name=False)
        verify_archive(path, manifest)
        with tempfile.TemporaryDirectory(prefix="serverman-selected-backup-") as temporary:
            directory = Path(temporary)
            materialize_archive(path, directory, manifest)
            verify_directory(directory, manifest)
            self.path(token)
            yield directory, manifest
