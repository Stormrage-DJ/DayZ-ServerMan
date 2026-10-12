"""Read-only coherent terrain snapshots and end-of-read digests through shared files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_terrain import (
    FIXED_HEADER, MISSION_FILE_UNREADABLE, VALUE_BITS_FIELD, TerrainCapabilities, TerrainDataError,
    ValueRows, build_header, parse_fixed_header, terrain_family, value_bits_position, value_flags,
)
from .configuration_common import UTF8_BOM, ConfigurationFileError
from .mission_xml import validate_xml


@dataclass(frozen=True)
class EvaluationSnapshot:
    """Header, complete value layer, names and file digests of one shared-read snapshot."""

    mission_root: Path
    capabilities: TerrainCapabilities
    values: ValueRows
    areaflags_sha256: str
    limits_sha256: str

    def is_fresh(self) -> bool:
        """Rehash both sources; callers also compare saved revision or draft fingerprint."""
        area = _read(self.mission_root / "areaflags.map")
        limits = _read(self.mission_root / "cfglimitsdefinition.xml")
        return (_digest(area) == self.areaflags_sha256 and _digest(limits) == self.limits_sha256)


def read_snapshot(mission_root: Path) -> EvaluationSnapshot:
    """Read both sources once, deriving header and flags from exactly the hashed bytes.

    The caller owns mission containment and any observer reader scope.
    """
    area = _read(mission_root / "areaflags.map")
    limits = _read(mission_root / "cfglimitsdefinition.xml")
    fields = parse_fixed_header(area[:FIXED_HEADER.size])
    position = value_bits_position(fields[0], fields[1], fields[4])
    header = build_header(fields, area[position:position + VALUE_BITS_FIELD.size], len(area))
    # Parse names from the same byte content whose digest guards the result.
    try:
        root = validate_xml(limits.removeprefix(UTF8_BOM).decode("utf-8"))
        section = root.find("valueflags")
        flags = value_flags([] if section is None else [entry.get("name") for entry in section.findall("value")])
    except (UnicodeError, ConfigurationFileError) as error:
        raise TerrainDataError("limits_invalid", "cfglimitsdefinition.xml is not valid UTF-8 XML") from error
    capabilities = TerrainCapabilities(terrain_family(mission_root.name), header, flags)
    return EvaluationSnapshot(mission_root, capabilities, ValueRows(header, 0, header.rows, area[header.value_offset:]),
                              _digest(area), _digest(limits))


def _read(path: Path) -> bytes:
    """Map a failed shared read to the accepted object-read failure reason."""
    try:
        return read_bytes_shared(path)
    except OSError as error:
        raise TerrainDataError(MISSION_FILE_UNREADABLE, f"{path.name} could not be read") from error


def _digest(content: bytes) -> str:
    """Return the SHA-256 of the exact source bytes."""
    return hashlib.sha256(content).hexdigest()
