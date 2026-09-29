"""Profile use cases and launch-command preview generation."""

from __future__ import annotations

from .arguments import LaunchCommand, build_launch_command
from .settings import SettingsService
from ..domain.profiles import ProfileInput, ProfileRecord, ProfileValidationError
from ..repositories.profiles import ProfileRepository


class ProfileService:
    """Application service for profile CRUD and launch previews."""

    def __init__(self, repository: ProfileRepository, settings: SettingsService) -> None:
        """Store the profile repository and settings service."""
        self._repository = repository
        self._settings = settings

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every stored profile."""
        return self._repository.list()

    def read(self, profile_id: object) -> ProfileRecord:
        """Load one profile by identifier."""
        return self._repository.load(profile_id)

    def save(self, values: ProfileInput, expected_revision: int | None) -> ProfileRecord:
        """Persist profile values against the expected revision."""
        return self._repository.save(values, expected_revision)

    def delete(self, profile_id: object, expected_revision: object) -> str:
        """Delete one profile and return the deleted identifier."""
        return self._repository.delete(profile_id, expected_revision)

    def preview_launch(self, profile_id: object) -> LaunchCommand:
        """Build the launch command for one profile using current settings."""
        # Load the selected profile and current settings
        profile = self.read(profile_id)
        settings = self._settings.load()
        # Refuse previews until the DayZ root is configured
        if settings.dayz_root is None:
            raise ProfileValidationError("DayZ root is not configured")
        return build_launch_command(profile, settings.dayz_root)
