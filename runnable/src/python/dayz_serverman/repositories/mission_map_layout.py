"""Editor area layout below the manager data folder: target keys, folders, file sets and dated names (D2)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from functools import cache
from pathlib import Path

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_values import TARGET_KEY
from ..domain.profiles import validate_profile_id


class TargetClass(str, Enum):
    """Class of a physical target that has a protected original (A4)."""

    MISSION = "mission"
    RUNTIME = "runtime"


# Files of each target class that the editor manages; the original captures each, also when absent
MISSION_FILE_SET = (
    "areaflags.map", "mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml",
    "env/zombie_territories.xml", "env/wolf_territories.xml", "env/bear_territories.xml",
)
# The runtime profile folder also holds server logs, so only provider files belong to the set
RUNTIME_FILE_SET = ("AI_Bandits/DynamicAIB.json",)
FILE_SETS = {TargetClass.MISSION: MISSION_FILE_SET, TargetClass.RUNTIME: RUNTIME_FILE_SET}

# Record files of one association folder
PLAN_FILE = "plan.json"
BASELINE_FILE = "baseline.json"
APPLIED_FILE = "applied.json"
RETIRED_FILE = "retired.json"
# Glob of the plan-save staging files that VersionedJsonRepository leaves after an interrupted save
PLAN_STAGING_GLOB = f".{PLAN_FILE}.*.tmp"
# UTC time stamp form of dated evidence names
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
# Committed Windows ordinal upcase table of the target keys (Architect 16:35:31, evidence 2.8): version name,
# data file beside the domain modules, and the pinned SHA-256 of its LF-normalized bytes. A new table needs a new
# version name and a key-migration leaf; tools/generate_ordinal_upcase.py writes it on Windows.
ORDINAL_TABLE_VERSION = "windows-ordinal-1"
ORDINAL_TABLE_FILE = Path(__file__).resolve().parent.parent / "domain" / "ordinal_upcase.txt"
ORDINAL_TABLE_SHA256 = "448b633fd010586d16a6e3496f96620ae287692750b5dcb580b3c24dfd2c5285"
# UTF-16 surrogate code units, which keep their value so that supplementary-plane letters never fold
SURROGATE_UNITS = range(0xD800, 0xE000)


class OrdinalTableError(RuntimeError):
    """Raised when the committed ordinal upcase table does not match its pinned digest."""
    pass


def load_ordinal_table(path: Path) -> dict[int, int]:
    """Read the table, check its pinned digest over the LF-normalized bytes, and return code unit to upcase.

    The digest is taken after CRLF becomes LF, because a Windows checkout may write CRLF line ends.
    """
    data = read_bytes_shared(path).replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() != ORDINAL_TABLE_SHA256:
        raise OrdinalTableError(f"the ordinal table {path.name} does not match its pinned SHA-256")
    # The digest pins the whole text: the version header, then one "unit upcase" hex pair per line
    pairs = (line.split() for line in data.decode("ascii").splitlines() if not line.startswith("#"))
    return {int(unit, 16): int(upper, 16) for unit, upper in pairs}


@cache
def _ordinal_table() -> dict[int, int]:
    """Return the committed table, loaded and checked once per process."""
    return load_ordinal_table(ORDINAL_TABLE_FILE)


def ordinal_spelling(path_text: str) -> str:
    """Return the Windows ordinal comparison form of a path text, with "/" separators.

    The text becomes UTF-16 code units. Each unit that is not a surrogate maps through the committed
    RtlUpcaseUnicodeChar table, which CompareStringOrdinal with ignore-case uses. Surrogate units stay, so
    supplementary-plane letters never fold, as on Windows. There is no case folding and no Unicode normalization:
    NTFS keeps "Straße" and "strasse", "mission" and "mıssion", and composed and decomposed accents, apart.

    A lone surrogate raises ValueError. NTFS allows one in a name, but such a path has no well-formed text form
    here. The refusal is the safe behavior, because no key and no comparison form exist for it.
    """
    try:
        data = path_text.replace("\\", "/").encode("utf-16-le")
    except UnicodeEncodeError:
        raise ValueError("a path spelling must not hold a lone surrogate") from None
    table = _ordinal_table()
    units = (int.from_bytes(data[index:index + 2], "little") for index in range(0, len(data), 2))
    mapped = b"".join((unit if unit in SURROGATE_UNITS else table.get(unit, unit)).to_bytes(2, "little")
                      for unit in units)
    return mapped.decode("utf-16-le")


def target_key(target_class: TargetClass, resolved: Path) -> str:
    """Return the first 32 hex digits of SHA-256 over the class, the table version and the ordinal form of the path.

    The caller passes the strictly resolved final path that the operating system returns for the existing folder,
    after the containment and reparse-point checks.
    """
    if not resolved.is_absolute():
        raise ValueError("a target key needs a resolved absolute path")
    spelling = ordinal_spelling(str(resolved))
    # The table version is part of the digest input, so a new table can never merge or split keys silently
    digest_input = f"{target_class.value}|{ORDINAL_TABLE_VERSION}|{spelling}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:32]


def profile_folder(area: Path, profile_id: str) -> Path:
    """Return the folder that holds every association folder of one profile."""
    return area / "profiles" / validate_profile_id(profile_id)


def association_folder(area: Path, profile_id: str, mission_key: str) -> Path:
    """Return the folder of one profile and mission association."""
    return profile_folder(area, profile_id) / _key(mission_key)


def original_folder(area: Path, key: str) -> Path:
    """Return the folder of the protected original of one target."""
    return area / "originals" / _key(key)


def ledger_folder(area: Path, key: str) -> Path:
    """Return the output ledger folder of one target."""
    return area / "ledger" / _key(key)


def is_target_key(name: str) -> bool:
    """Return whether a folder name has the form of a target key."""
    return TARGET_KEY.fullmatch(name) is not None


def utc_stamp(moment: datetime) -> str:
    """Return the UTC stamp YYYYMMDDTHHMMSSZ of an aware time."""
    if moment.tzinfo is None:
        raise ValueError("dated names need an aware time")
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)


def set_aside_name(moment: datetime) -> str:
    """Return the dated name of a plan record that an explicit set aside moved away."""
    return f"plan.invalid-{utc_stamp(moment)}.json"


def interrupted_name(moment: datetime, number: int) -> str:
    """Return the dated evidence name of one leftover plan-save staging file."""
    return f"plan.interrupted-{utc_stamp(moment)}-{number}.json"


def _key(value: str) -> str:
    """Return a target key that is safe as one folder name, or raise."""
    if not isinstance(value, str) or not is_target_key(value):
        raise ValueError("a target key must be 32 lowercase hexadecimal digits")
    return value
