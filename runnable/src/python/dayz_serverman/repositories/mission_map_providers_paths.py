"""Read provider files through existing containment, reparse-point and shared-file owners."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from xml.etree.ElementTree import Element

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.profiles import ProfileValidationError, validate_relative_path
from .backup_verification import is_reparse, sha256_file
from .configuration_common import ConfigurationFileError, UTF8_BOM
from .mission_xml import validate_xml


class ProviderReadError(ValueError):
    """Carry a safe capability diagnostic while keeping filesystem details private."""


class ProviderReader:
    """Bind provider reads to one contained root and the application's existing path resolver."""

    def __init__(self, root: Path, containment: Callable[..., Path]) -> None:
        """Keep the trusted resolved root and a resolver that refuses unsafe targets."""
        self.root = root
        self._containment = containment

    def contained(self, relative: str, *, directory: bool = False) -> Path:
        """Resolve a regular contained target and reject final-file reparse points before reading."""
        try:
            # Validate lexical input before invoking the application's mission containment owner
            normalized = validate_relative_path(relative, "provider path")
            candidate = self.root / Path(normalized)
            if is_reparse(candidate):
                raise ProviderReadError("provider_path_invalid")
            if not candidate.exists():
                raise ProviderReadError("provider_file_missing")
            return self._containment(self.root, candidate, directory=directory)
        except ProfileValidationError as error:
            raise ProviderReadError("provider_path_invalid") from error
        except RuntimeError as error:
            # The existing application resolver reports unsafe targets as a domain runtime error
            raise ProviderReadError("provider_path_invalid") from error
        except OSError as error:
            raise ProviderReadError("provider_file_unreadable") from error

    def folder(self, relative: str) -> ProviderReader:
        """Return a reader restricted to one safely resolved child folder."""
        return ProviderReader(self.contained(relative, directory=True), self._containment)

    def bytes(self, relative: str) -> bytes:
        """Read a contained regular provider file through the shared-file adapter."""
        try:
            return read_bytes_shared(self.contained(relative))
        except OSError as error:
            raise ProviderReadError("provider_file_unreadable") from error

    def xml(self, relative: str) -> Element:
        """Read strict UTF-8 mission XML; malformed input becomes an unavailable capability."""
        try:
            return validate_xml(self.bytes(relative).removeprefix(UTF8_BOM).decode("utf-8"))
        except (UnicodeDecodeError, ConfigurationFileError) as error:
            raise ProviderReadError("provider_configuration_invalid") from error

    def digest(self, relative: str) -> str:
        """Stream the current provider build digest without unpacking or copying the mod."""
        try:
            return sha256_file(self.contained(relative))
        except OSError as error:
            raise ProviderReadError("provider_file_unreadable") from error
