"""Current mission inputs of the apply preview, read with containment checks and the shared-file adapter (8.6).

The preview reads the managed files, the `mapgroupcluster*.xml` files, the
events and globals files, the value-flag definitions and the item-type
documents that the economy loads. It writes nothing.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.profiles import ProfileValidationError, validate_relative_path
from ..repositories.configuration_common import UTF8_BOM, ConfigurationFileError
from ..repositories.mission_map_layout import MISSION_FILE_SET, TargetClass
from ..repositories.mission_map_owned import captured_only
from ..repositories.mission_xml import validate_xml
from .mission_configuration import _contained

# Unowned inputs of the candidates, relative to the mission root
EVENTS_FILE = "db/events.xml"
GLOBALS_FILE = "db/globals.xml"
LIMITS_FILE = "cfglimitsdefinition.xml"
ECONOMY_FILE = "cfgeconomycore.xml"
TYPES_FILE = "db/types.xml"
# Name pattern of the cluster placement files in the mission root, compared without case as Windows does
CLUSTER_PATTERN = "mapgroupcluster*.xml"


@dataclass(frozen=True)
class MissionInputs:
    """Bytes of the current mission files that the preview uses; None is an absent file."""

    managed: dict[str, bytes | None]
    clusters: dict[str, bytes]
    events: bytes | None
    globals_xml: bytes | None
    limits: bytes | None
    economy: bytes | None
    # Item-type documents by relative path; None when the registration cannot be read
    types: dict[str, bytes | None] | None

    def types_documents(self) -> list[bytes] | None:
        """Return the item-type documents for the loot report, or None (unknown) when one is missing."""
        if self.types is None or any(data is None for data in self.types.values()):
            return None
        return [data for data in self.types.values() if data is not None]

    def freshness(self, *, capture: bool) -> dict[str, str | None]:
        """Return the whole-file digests of the freshness set by relative path.

        `db/globals.xml` never joins it: no candidate byte depends on it, and the
        fingerprint binds its two shown values (Architect 2026-10-14 18:32:23).
        A captured-only file joins it only through the original capture of the
        first Apply (Architect 2026-10-14 12:16:06).
        """
        files: dict[str, bytes | None] = {path: data for path, data in self.managed.items()
                                          if capture or not captured_only(TargetClass.MISSION, path)}
        files.update(self.clusters)
        files.update({EVENTS_FILE: self.events, LIMITS_FILE: self.limits, ECONOMY_FILE: self.economy})
        if self.types is not None:
            files.update(self.types)
        return {path: None if data is None else hashlib.sha256(data).hexdigest()
                for path, data in sorted(files.items())}


def read_inputs(mission: Path) -> MissionInputs:
    """Read the inputs of one resolved mission folder; a missing file is None, an unsafe path raises."""
    managed = {path: read_optional(mission, path) for path in MISSION_FILE_SET}
    clusters = {}
    for child in sorted(mission.iterdir(), key=lambda item: item.name):
        # A folder with a cluster-like name is no cluster file, and nothing is read from it (QA-8.6-F4).
        # Every other match goes through the containment check, so a linked file still refuses.
        if fnmatch.fnmatchcase(child.name.lower(), CLUSTER_PATTERN) and not child.is_dir():
            data = read_optional(mission, child.name)
            if data is not None:
                clusters[child.name] = data
    economy = read_optional(mission, ECONOMY_FILE)
    return MissionInputs(managed, clusters, read_optional(mission, EVENTS_FILE), read_optional(mission, GLOBALS_FILE),
                         read_optional(mission, LIMITS_FILE), economy, _types(mission, economy))


def read_optional(mission: Path, relative: str) -> bytes | None:
    """Return the bytes of a contained regular file, or None when nothing exists at the path."""
    candidate = mission / Path(relative)
    if not os.path.lexists(candidate):
        return None
    return read_bytes_shared(_contained(mission, candidate, directory=False))


def _types(mission: Path, economy: bytes | None) -> dict[str, bytes | None] | None:
    """Return `db/types.xml` and every types file that `cfgeconomycore.xml` registers, or None when unreadable."""
    documents = {TYPES_FILE: read_optional(mission, TYPES_FILE)}
    if economy is None:
        return documents
    try:
        root = validate_xml(economy.removeprefix(UTF8_BOM).decode("utf-8"))
        for folder in root.findall("ce"):
            for item in folder.findall("file"):
                if item.get("type") == "types":
                    relative = validate_relative_path(f"{folder.get('folder', '')}/{item.get('name', '')}", "types")
                    documents[relative.replace("\\", "/")] = read_optional(mission, relative)
    except (UnicodeDecodeError, ConfigurationFileError, ProfileValidationError):
        return None
    return documents
