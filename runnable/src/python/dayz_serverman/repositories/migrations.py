"""Manager-owned migration staging and durable sanitized reports."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..adapters.windows.shared_files import replace_file
from .json_store import VersionedJsonRepository


class MigrationStorageError(RuntimeError):
    """Raised when migration staging or reporting fails."""

    def __init__(self, message: str, *, recovery_required: bool = False) -> None:
        """Record whether the failure leaves mutations that need recovery."""
        self.recovery_required = recovery_required
        super().__init__(message)


class MigrationStorage:
    """Manage migration staging directories and sanitized reports."""

    def __init__(self, root: Path) -> None:
        """Store the resolved storage root and its reports directory."""
        self.root = root.resolve(strict=False)
        self.reports = self.root / "reports"

    def create_stage(self, operation_id: str) -> Path:
        """Create an exclusive hidden staging directory for one operation."""
        stage = self.root / f".{operation_id}.stage"
        # Exclusive creation so two operations cannot share a stage
        try:
            stage.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise MigrationStorageError("Migration staging could not be created.") from error
        return stage

    def copy_source(self, source_root: Path, relative_paths: tuple[str, ...], stage: Path) -> None:
        """Copy selected source files into the stage without following links."""
        copied = stage / "source"
        # Mirror only the selected paths into the stage source tree
        for relative in relative_paths:
            source = source_root / Path(relative)
            target = copied / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)

    def write_plan(self, stage: Path, plan: Mapping[str, Any]) -> None:
        """Persist the durable no-publication plan inside the stage."""
        self._atomic_json(stage / "plan.json", plan)

    def write_report(self, migration_id: str, report: Mapping[str, Any]) -> Path:
        """Write the sanitized migration report and return its path."""
        path = self.report_path(migration_id)
        # Surface repository failures as migration storage errors
        try:
            VersionedJsonRepository(path).save(report, None)
        except Exception as error:
            raise MigrationStorageError("Migration report publication failed.") from error
        return path

    def report_path(self, migration_id: str) -> Path:
        """Return the report path for a migration identifier."""
        return self.reports / f"{migration_id}.json"

    def cleanup(self, stage: Path) -> None:
        """Remove a stage directory after proving it belongs to this root."""
        # Refuse to remove anything outside the storage root
        try:
            stage.relative_to(self.root)
        except ValueError as error:
            raise MigrationStorageError("Migration staging escaped its owner root.") from error
        try:
            shutil.rmtree(stage)
        except OSError as error:
            raise MigrationStorageError("Migration staging cleanup failed.") from error

    @staticmethod
    def rollback(evidence: list[tuple[Path, bytes | None]]) -> bool:
        """Restore targets from recorded evidence, newest first; False on failure."""
        try:
            # Restore in reverse order so overlapping writes unwind safely
            for path, prior in reversed(evidence):
                # A missing prior means the mutation created the file
                if prior is None:
                    path.unlink(missing_ok=True)
                    continue
                # Write through a temp file so a failure cannot corrupt the target
                temporary = path.with_name(f".{path.name}.migration-recovery.tmp")
                temporary.write_bytes(prior)
                replace_file(temporary, path)
            return True
        except OSError:
            return False

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        """Write JSON through a temporary file and a final atomic swap."""
        # Ensure the parent directory exists before staging the write
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        # Serialize with stable ordering and a trailing newline
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                # Fsync before the swap so a crash cannot leave a partial file
                os.fsync(stream.fileno())
            replace_file(temporary, path)
        except OSError:
            # Never leave a partial temporary file behind
            temporary.unlink(missing_ok=True)
            raise
