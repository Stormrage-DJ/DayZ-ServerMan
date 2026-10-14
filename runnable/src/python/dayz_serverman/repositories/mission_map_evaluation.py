"""Read-only terrain snapshots: whole-file reads with end digests, and snapshots from an Inspect read pass."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_terrain import (
    AREAFLAGS_MISSING, FIXED_HEADER, LIMITS_MISSING, MISSION_FILE_UNREADABLE, VALUE_BITS_FIELD,
    TerrainCapabilities, TerrainDataError, ValueRows, build_header, parse_fixed_header, terrain_family,
    value_bits_position, value_flags,
)
from .configuration_common import UTF8_BOM, ConfigurationFileError
from .mission_map_read_pass import PassContent
from .mission_map_terrain import parse_value_flags
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


@dataclass(frozen=True)
class PassSnapshot:
    """Capabilities, value layer and full digests built from one completed capture pass (A181)."""

    capabilities: TerrainCapabilities
    # None without a valid header or without valid value flags
    values: ValueRows | None
    digests: tuple[str | None, ...]
    header_error: TerrainDataError | None
    limits_error: TerrainDataError | None

    def require_tiers(self) -> ValueRows:
        """Return the value layer of an object read, or raise the first header or limits failure.

        A missing file keeps the 4.1 object-read reason mission_file_unreadable.
        """
        for error in (self.header_error, self.limits_error):
            if error is None:
                continue
            if error.reason in (AREAFLAGS_MISSING, LIMITS_MISSING):
                name = "areaflags.map" if error.reason == AREAFLAGS_MISSING else "cfglimitsdefinition.xml"
                raise TerrainDataError(MISSION_FILE_UNREADABLE, f"{name} could not be read") from error
            raise error
        if self.values is None:
            raise RuntimeError("a snapshot without errors has a value layer")
        return self.values


def snapshot_from_pass(mission_root: Path, content: PassContent, digests: tuple[str | None, ...]) -> PassSnapshot:
    """Run the 3.5 header and size checks, then the limits checks, on the retained bytes of a pass.

    The checks run outside the reader side, with the order, reasons and messages of the capability read.
    """
    header, header_error = None, None
    if content.area_size is None:
        header_error = TerrainDataError(AREAFLAGS_MISSING, "the mission has no areaflags.map")
    else:
        try:
            header = build_header(parse_fixed_header(content.head), content.value_bits_field, content.area_size)
        except TerrainDataError as error:
            header_error = error
    flags: tuple[str, ...] = ()
    limits_error = None
    if content.limits is None:
        limits_error = TerrainDataError(LIMITS_MISSING, "the mission has no cfglimitsdefinition.xml")
    else:
        try:
            flags = parse_value_flags(content.limits)
        except TerrainDataError as error:
            limits_error = error
    capabilities = TerrainCapabilities(terrain_family(mission_root.name), header, flags,
                                       None if header_error is None else header_error.reason,
                                       None if limits_error is None else limits_error.reason)
    # Original tier names need both the layer and the value flags
    values = None
    if header is not None and limits_error is None:
        values = ValueRows(header, 0, header.rows, content.value_layer)
    return PassSnapshot(capabilities, values, digests, header_error, limits_error)
