"""Manager-root discovery and portable directory ownership."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def normalize_external_path(path: str | os.PathLike[str]) -> str:
    """Return a normalized absolute path; reject relative input."""
    # Expand the user marker before the absoluteness check
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("external paths must be absolute")
    # Normalize separators so the stored path is canonical
    return os.path.normpath(str(candidate.resolve(strict=False)))


def normalize_manager_relative(path: str | os.PathLike[str]) -> str:
    """Return a forward-slash manager-relative path; reject escapes."""
    # Accept both separators so callers can pass native paths
    raw = str(path).replace("\\", "/")
    candidate = PurePosixPath(raw)
    # Reject absolute or parent-traversing paths
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError("manager-owned paths must be non-empty relative paths")
    return candidate.as_posix()


@dataclass(frozen=True)
class PortablePaths:
    """Resolved manager-owned directory and file locations."""
    root: Path
    config: Path
    manager_config: Path
    data: Path
    profiles: Path
    ui_preferences: Path
    schedules: Path
    state_file: Path
    applied_mod_state: Path
    content_proofs: Path
    update_check_cache: Path
    server_build_cache: Path
    logs: Path
    operations: Path
    publication: Path
    publication_journals: Path
    migrations: Path
    tweak_baselines: Path
    webview2: Path
    backups: Path
    backup_recovery: Path
    backup_originals: Path
    docs: Path

    @classmethod
    def from_root(cls, root: Path) -> PortablePaths:
        """Build the layout from a manager root, resolving it once."""
        # Resolve the root so every derived path shares one canonical base
        canonical = root.expanduser().resolve(strict=False)
        return cls(
            root=canonical,
            config=canonical / "config",
            manager_config=canonical / "config" / "manager.json",
            data=canonical / "data",
            profiles=canonical / "data" / "profiles",
            ui_preferences=canonical / "data" / "ui-preferences.json",
            schedules=canonical / "data" / "schedules.json",
            state_file=canonical / "data" / "state.json",
            applied_mod_state=canonical / "data" / "applied-mod-state.json",
            content_proofs=canonical / "data" / "content-proofs.json",
            update_check_cache=canonical / "data" / "update-check.json",
            server_build_cache=canonical / "data" / "server-build-check.json",
            logs=canonical / "data" / "logs",
            operations=canonical / "data" / "operations",
            publication=canonical / "data" / "publication",
            publication_journals=canonical / "data" / "publication" / "journals",
            migrations=canonical / "data" / "migrations",
            tweak_baselines=canonical / "data" / "tweak-baselines",
            webview2=canonical / "data" / "webview2",
            backups=canonical / "backups",
            backup_recovery=canonical / "backups" / "recovery",
            backup_originals=canonical / "backups" / "originals",
            docs=canonical / "data" / "docs",
        )

    @classmethod
    def from_source(cls, source_file: Path) -> PortablePaths:
        """Build the layout from a source file path inside a src tree."""
        source = source_file.resolve(strict=False)
        # Walk parents upward to locate the src directory
        for parent in source.parents:
            if parent.name.casefold() == "src":
                # The layout root is the parent of the src directory
                return cls.from_root(parent.parent)
        raise ValueError("source layout must contain a src directory")

    def create_layout(self) -> None:
        """Create every manager-owned directory if missing."""
        directories = (
            self.config,
            self.profiles,
            self.logs,
            self.operations,
            self.publication_journals,
            self.migrations,
            self.tweak_baselines,
            self.webview2,
            self.backups,
            self.backup_recovery,
            self.backup_originals,
            self.docs,
        )
        # File paths are created by their owners; only directories are made here
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def relative(self, path: Path) -> str:
        """Return a path relative to the manager root as a slash path."""
        resolved = path.resolve(strict=False)
        # Refuse paths outside the manager root
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as error:
            raise ValueError("path is outside the manager root") from error
        return normalize_manager_relative(relative)
