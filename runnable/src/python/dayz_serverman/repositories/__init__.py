"""Portable persistence adapters."""

from .json_store import VersionedJsonRepository
from .paths import PortablePaths, normalize_external_path, normalize_manager_relative

__all__ = [
    "PortablePaths",
    "VersionedJsonRepository",
    "normalize_external_path",
    "normalize_manager_relative",
]

