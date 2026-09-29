"""Dispatch supported external configuration formats through one contract."""

from __future__ import annotations

from pathlib import Path

from ..domain.configuration import ConfigurationValidationError
from .configuration_common import (
    UTF8_BOM,
    ConfigurationFileError,
    ConfigurationSnapshot,
    digest_bytes,
)
from .gameplay_configuration import (
    load_gameplay_configuration,
    transform_gameplay_configuration,
)
from .server_configuration import load_server_configuration, transform_server_configuration


def load_configuration_file(path: Path, target: str) -> ConfigurationSnapshot:
    """Load one supported configuration file by target name."""
    # Dispatch to the parser that owns the requested target
    if target == "server":
        return load_server_configuration(path)
    if target == "gameplay":
        return load_gameplay_configuration(path)
    raise ConfigurationValidationError("target must be server or gameplay")


def transform_configuration(
    snapshot: ConfigurationSnapshot,
    updates: dict[str, str | bool | int | float],
) -> bytes:
    """Rebuild one configuration file with the requested updates applied."""
    # Dispatch to the transformer that owns the snapshot's target
    if snapshot.target == "server":
        return transform_server_configuration(snapshot, updates)
    if snapshot.target == "gameplay":
        return transform_gameplay_configuration(snapshot, updates)
    raise ConfigurationValidationError("target must be server or gameplay")


__all__ = [
    "UTF8_BOM",
    "ConfigurationFileError",
    "ConfigurationSnapshot",
    "digest_bytes",
    "load_configuration_file",
    "transform_configuration",
]
