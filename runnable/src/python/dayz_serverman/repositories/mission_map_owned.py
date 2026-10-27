"""Owned-value projections and D8 checks of the managed files, by target class and relative path (D3, D8, 7.9).

Each writable managed file has one adapter that defines its owned-value
projection. A managed file without one has a null projection: it is
captured-only (Architect 2026-10-14 12:16:06). The protected original records
its bytes, and no adapter, preview, Apply or restore writes it. Today these
are the wolf and bear territory files of the mission set and the provider
file of the runtime profile set.

The commit step (8.4) and the capture check take `owned_digest` and
`check_reserved` as their adapter callables. This module reads and writes no
file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from . import mission_map_infected as infected
from . import mission_map_mapgroup_projection as mapgroup_projection
from .mission_map_infected import TerritoryRefused
from .mission_map_layout import FILE_SETS, TargetClass
from .mission_map_mapgroup_read import DIRT, POS, PROTO, MapgroupRefused, check_reserved as mapgroup_reserved
from .mission_map_terrain import AREAFLAGS_FILE

# Relative path of the infected territory file in the mission folder
TERRITORY_FILE = "env/zombie_territories.xml"
# Writable mission files in the order of the managed file set; every other managed file is captured-only
WRITABLE_MISSION_FILES = (AREAFLAGS_FILE, PROTO, POS, DIRT, TERRITORY_FILE)
# Errors of a damaged owned block or of a file that the reserved-name check cannot read
OWNED_ERRORS = (MapgroupRefused, TerritoryRefused)


def _whole_file(data: bytes) -> str:
    """Return the D3 owned digest of areaflags.map: SHA-256 of the whole exact file."""
    return hashlib.sha256(data).hexdigest()


# Owned digest of present bytes for each writable mission file
_DIGESTS: dict[str, Callable[[bytes], str | None]] = {
    AREAFLAGS_FILE: _whole_file,
    PROTO: lambda data: mapgroup_projection.owned_digest(PROTO, data),
    POS: lambda data: mapgroup_projection.owned_digest(POS, data),
    DIRT: lambda data: mapgroup_projection.owned_digest(DIRT, data),
    TERRITORY_FILE: infected.owned_digest,
}
# D8 reserved-name check of present bytes for each writable mission file; areaflags.map holds no names
_RESERVED: dict[str, Callable[[bytes], None]] = {
    AREAFLAGS_FILE: lambda data: None,
    PROTO: lambda data: mapgroup_reserved(PROTO, data),
    POS: lambda data: mapgroup_reserved(POS, data),
    DIRT: lambda data: mapgroup_reserved(DIRT, data),
    TERRITORY_FILE: infected.check_reserved,
}


def writable(target_class: TargetClass, path: str) -> bool:
    """Return whether an adapter defines the owned-value projection of the managed file."""
    return target_class is TargetClass.MISSION and path in _DIGESTS


def captured_only(target_class: TargetClass, path: str) -> bool:
    """Return whether the file is managed but has a null projection: no adapter ever writes it."""
    return path in FILE_SETS[target_class] and not writable(target_class, path)


def owned_digest(target_class: TargetClass, path: str, data: bytes | None) -> str | None:
    """Return the D3 owned-value digest, or None for an absent file or a captured-only file (QA75-F1).

    A damaged owned block raises the refusal of its adapter.
    """
    if data is None or not writable(target_class, path):
        return None
    return _DIGESTS[path](data)


def check_reserved(target_class: TargetClass, path: str, data: bytes) -> None:
    """Refuse bytes that already hold a reserved editor name or marker (D8); a captured-only file has no check."""
    if writable(target_class, path):
        _RESERVED[path](data)
