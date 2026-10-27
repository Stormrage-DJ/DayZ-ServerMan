"""Read-only classification of a stored `medical_loot_zones` baseline against the current `mapgroupproto.xml`.

The classification runs the 6.3 element mapping in memory: the state of the file and the bytes of the next
toggle. It writes nothing and guesses no state. A baseline that the toggle can use is mappable; otherwise the
6.3 refusal is the reason. A difference in line endings is information only, because the off write keeps the
line endings of the current file.
"""

from __future__ import annotations

from dataclasses import dataclass

from .atomic_file import ContentChangedError
from .configuration_common import ConfigurationFileError
from ..domain.medical_zone_rule import is_reserved
from .medical_features import whole_file_off
from .medical_zone_fragments import pairs_enabled, zone_edits, zone_pairs
from .medical_zone_scan import scan_groups

# Locations of a whole-file baseline: the manager store, or the legacy folder inside the mission
MANAGER = "manager"
LEGACY = "legacy"


@dataclass(frozen=True)
class ZoneBaselineClass:
    """Classification of one baseline that 6.5 shows.

    `enabled` is the state of the file when the baseline is mappable, and None otherwise. `reason` is the
    6.3 refusal when it is not mappable, and None otherwise. The two line-ending forms are "CRLF", "LF",
    "CR", a "+" join of several forms, or "none".
    """

    location: str
    mappable: bool
    enabled: bool | None
    reason: str | None
    baseline_line_endings: str
    current_line_endings: str

    @property
    def line_endings_differ(self) -> bool:
        """Return whether the baseline and the current file use different line endings."""
        return self.baseline_line_endings != self.current_line_endings


@dataclass(frozen=True)
class ZoneReading:
    """One scan of the file and its baseline: the state, the refusals, and whether an editor group is there.

    `error` is the refusal of the state reading, which a load raises as it is; `enabled` is None then.
    `refusal` is the reason why the next toggle refuses, or None. `reserved` is None when the file
    cannot be scanned, because the toggle then refuses with that scan error first.
    """

    enabled: bool | None
    error: ContentChangedError | ConfigurationFileError | None
    refusal: str | None
    reserved: bool | None


def read_zones(current: bytes, baseline: bytes) -> ZoneReading:
    """Read the state as the toggle does and test its next write, scanning each file only once (6.5).

    The results equal zones_enabled and project_zones of the opposite state; nothing is written.
    """
    try:
        groups = scan_groups(current)
    except ConfigurationFileError as error:
        return ZoneReading(None, error, str(error), None)
    reserved = any(is_reserved(node.attributes.get("name", "")) for node in groups)
    try:
        pairs = zone_pairs(current, baseline, groups)
        enabled = pairs_enabled(pairs)
    except (ContentChangedError, ConfigurationFileError) as error:
        return ZoneReading(None, error, str(error), reserved)
    try:
        # Test the opposite state with the steps of project_zones, without building the new file.
        # An off write of the earlier whole-file form puts back the baseline bytes; other writes patch.
        if pairs and not (enabled and whole_file_off(current, baseline)):
            zone_edits(current, baseline, pairs, not enabled)
    except (ContentChangedError, ConfigurationFileError) as error:
        return ZoneReading(enabled, None, str(error), reserved)
    return ZoneReading(enabled, None, None, reserved)


def classify_zone_baseline(current: bytes, baseline: bytes, location: str) -> ZoneBaselineClass:
    """Classify one baseline through the 6.3 state reading and the projection of the next toggle.

    Both inputs are bytes in memory, so no file is read or written here.
    """
    return zone_class(read_zones(current, baseline), current, baseline, location)


def zone_class(reading: ZoneReading, current: bytes, baseline: bytes, location: str) -> ZoneBaselineClass:
    """Return the classification of a reading that read_zones gave for these bytes."""
    forms = (line_endings(baseline), line_endings(current))
    if reading.refusal is not None:
        return ZoneBaselineClass(location, False, None, reading.refusal, *forms)
    return ZoneBaselineClass(location, True, reading.enabled, None, *forms)


def line_endings(data: bytes) -> str:
    """Return the line-ending forms that the bytes hold, in a fixed order."""
    crlf = data.count(b"\r\n")
    counts = (("CRLF", crlf), ("LF", data.count(b"\n") - crlf), ("CR", data.count(b"\r") - crlf))
    return "+".join(name for name, count in counts if count) or "none"

