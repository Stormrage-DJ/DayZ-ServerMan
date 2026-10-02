"""Read-only archive catalog independent of registered profiles."""

from pathlib import Path
from typing import Any

from .backup_archives import read_archive_manifest, verify_archive, FutureBackupArchive
from .backup_publication import _summary
from .backup_sources import safe_directory
from .backup_verification import is_reparse


def list_catalog(root: Path) -> dict[str, Any]:
    """Keep corrupt neighbors visible without making them available for reconstruction."""
    destination = safe_directory(root, "Backup root", writable=False)
    backups, diagnostics = [], []
    for archive in sorted(destination.glob("*.zip"), key=lambda item: item.name.casefold()):
        try:
            if is_reparse(archive) or not archive.is_file():
                raise ValueError("Unsafe archive.")
            manifest = read_archive_manifest(archive)
            verify_archive(archive, manifest)
            complete = manifest.schema_version == 3
            summary = _summary(manifest)
            summary.update(display_name=manifest.reconstruction["profile"]["display_name"] if complete else manifest.profile_id,
                           can_restore_profile=complete,
                           reconstruction_reason="Complete profile metadata is available." if complete else "This older backup lacks a complete profile definition. Create a new full backup to restore a profile directly.")
            backups.append(summary)
        except FutureBackupArchive:
            diagnostics.append({"backup_id": archive.stem, "code": "FUTURE_SCHEMA", "message": "Unsupported backup version."})
        except (OSError, ValueError, RuntimeError):
            diagnostics.append({"backup_id": archive.stem, "code": "CORRUPT", "message": "This archive failed verification."})
    backups.sort(key=lambda item: (item["created_at"], item["backup_id"]), reverse=True)
    return {"backups": backups, "diagnostics": diagnostics}
