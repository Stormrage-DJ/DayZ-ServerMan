"""Read-only terrain capability reads of one mission folder; every read goes through the shared-file adapter."""

from __future__ import annotations

import os
from pathlib import Path

from ..adapters.windows.shared_files import open_shared, read_bytes_shared
from ..domain.mission_map_terrain import (
    AREAFLAGS_MISSING, FIXED_HEADER, LIMITS_INVALID, LIMITS_MISSING, MISSION_FILE_UNREADABLE, TIER_HEADER_INVALID,
    VALUE_BITS_FIELD, AreaFlagsHeader, TerrainCapabilities, TerrainDataError, ValueRows, build_header,
    parse_fixed_header, row_byte_span, terrain_family, value_bits_position, value_flags,
)
from .configuration_common import UTF8_BOM, ConfigurationFileError
from .mission_xml import validate_xml


# Mission files that the capability read uses, relative to the mission root
AREAFLAGS_FILE = "areaflags.map"
LIMITS_FILE = "cfglimitsdefinition.xml"


def read_header(path: Path) -> AreaFlagsHeader:
    """Read the areaflags.map header and the value-bit count, and check the A7 size formula.

    Only the header, the value-bit count and the file size are read, never the layers.
    """
    try:
        with open_shared(path) as stream:
            fields = parse_fixed_header(stream.read(FIXED_HEADER.size))
            stream.seek(value_bits_position(fields[0], fields[1], fields[4]))
            value_bits_field = stream.read(VALUE_BITS_FIELD.size)
            size = stream.seek(0, os.SEEK_END)
    except FileNotFoundError as error:
        raise TerrainDataError(AREAFLAGS_MISSING, "the mission has no areaflags.map") from error
    except OSError as error:
        raise TerrainDataError(MISSION_FILE_UNREADABLE, "areaflags.map could not be read") from error
    return build_header(fields, value_bits_field, size)


def read_value_rows(path: Path, header: AreaFlagsHeader, first_row: int, row_count: int) -> ValueRows:
    """Read the value-layer rows first_row to first_row + row_count − 1 in one shared read.

    The 4.1 object read builds its slices on this read. The caller compares file digests for freshness.
    """
    offset, length = row_byte_span(header, first_row, row_count)
    try:
        with open_shared(path) as stream:
            stream.seek(offset)
            data = stream.read(length)
    except OSError as error:
        raise TerrainDataError(MISSION_FILE_UNREADABLE, "areaflags.map could not be read") from error
    # A shorter read means the file changed after its header was read
    if len(data) != length:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map ended before the requested rows")
    return ValueRows(header, first_row, row_count, data)


def read_value_flags(path: Path) -> tuple[str, ...]:
    """Read the value-flag names of cfglimitsdefinition.xml in file order."""
    try:
        content = read_bytes_shared(path)
    except FileNotFoundError as error:
        raise TerrainDataError(LIMITS_MISSING, "the mission has no cfglimitsdefinition.xml") from error
    except OSError as error:
        raise TerrainDataError(MISSION_FILE_UNREADABLE, "cfglimitsdefinition.xml could not be read") from error
    try:
        text = content.removeprefix(UTF8_BOM).decode("utf-8")
        root = validate_xml(text)
    except (UnicodeDecodeError, ConfigurationFileError) as error:
        raise TerrainDataError(LIMITS_INVALID, "cfglimitsdefinition.xml is not valid UTF-8 XML") from error
    section = root.find("valueflags")
    if section is None:
        raise TerrainDataError(LIMITS_INVALID, "cfglimitsdefinition.xml has no valueflags section")
    return value_flags([element.get("name") for element in section.findall("value")])


def read_capabilities(mission_root: Path) -> TerrainCapabilities:
    """Return the capability snapshot of one resolved mission folder; a failed part gives its reason code.

    The caller resolves the mission root with its containment checks and holds the reader side when needed.
    """
    header, header_reason = None, None
    try:
        header = read_header(mission_root / AREAFLAGS_FILE)
    except TerrainDataError as error:
        header_reason = error.reason
    flags, limits_reason = (), None
    try:
        flags = read_value_flags(mission_root / LIMITS_FILE)
    except TerrainDataError as error:
        limits_reason = error.reason
    return TerrainCapabilities(terrain_family(mission_root.name), header, flags, header_reason, limits_reason)
