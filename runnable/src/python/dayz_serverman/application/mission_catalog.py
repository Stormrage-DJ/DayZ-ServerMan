"""Discover and validate installed DayZ mission directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..domain.profiles import ProfileValidationError, validate_relative_path
from ..repositories.backup_verification import path_has_reparse


class MissionCatalogError(RuntimeError):
    """Raised when the configured mission catalog cannot be inspected safely."""


@dataclass(frozen=True)
class MissionChoice:
    """One installed mission offered by the profile creation interface."""

    relative_path: str
    display_name: str
    source: str = "discovered"

    def to_dict(self) -> dict[str, str]:
        """Return the bridge-safe mission representation."""
        return {
            "relative_path": self.relative_path,
            "display_name": self.display_name,
            "source": self.source,
        }


class MissionCatalog:
    """Read installed missions without following paths outside the DayZ root."""

    def list(self, dayz_root: str) -> tuple[MissionChoice, ...]:
        """Return direct child directories beneath the installed mission root."""
        root = self._safe_root(dayz_root)
        missions = root / "mpmissions"
        if not missions.exists():
            return ()
        if not missions.is_dir() or path_has_reparse(missions):
            raise MissionCatalogError("The DayZ mission directory is not safe to inspect.")
        choices: list[MissionChoice] = []
        try:
            children = sorted(missions.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            raise MissionCatalogError("The DayZ mission directory could not be read.") from error
        for child in children:
            if not child.is_dir() or path_has_reparse(child):
                continue
            resolved = child.resolve(strict=True)
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            normalized = validate_relative_path(str(relative), "mission_root")
            assert normalized is not None
            choices.append(MissionChoice(normalized, child.name))
        return tuple(choices)

    def validate(self, dayz_root: str, relative_path: object) -> MissionChoice:
        """Validate an installed custom mission and return its canonical choice."""
        root = self._safe_root(dayz_root)
        normalized = validate_relative_path(relative_path, "mission_root")
        assert normalized is not None
        target = root.joinpath(*PureWindowsPath(normalized).parts)
        if path_has_reparse(target):
            raise ProfileValidationError("mission_root contains a link or reparse point")
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ProfileValidationError("mission_root resolves outside the DayZ root") from error
        if not resolved.is_dir():
            raise ProfileValidationError("mission_root is not an installed mission directory")
        return MissionChoice(normalized, resolved.name, "custom")

    @staticmethod
    def _safe_root(dayz_root: str) -> Path:
        """Return a configured local DayZ root safe for mission inspection."""
        root = Path(dayz_root)
        if not root.is_absolute() or path_has_reparse(root):
            raise MissionCatalogError("The configured DayZ root is not safe to inspect.")
        resolved = root.resolve(strict=False)
        if not resolved.is_dir():
            raise MissionCatalogError("The configured DayZ root is unavailable.")
        return resolved
