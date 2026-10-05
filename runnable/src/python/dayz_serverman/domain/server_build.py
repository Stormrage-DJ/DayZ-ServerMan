"""DayZ server build check: values and pure rules for flags, ownership and the status."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from datetime import datetime

from .update_check import CheckAttempt
from .update_check_rules import CheckState, attempt_age_seconds

# Steam application id of the DayZ dedicated server
SERVER_APP_ID = "223350"
# Largest Steam build id; build ids are unsigned 32-bit numbers
MAX_BUILD_ID = 4_294_967_295
# Branch names that the check reads and stores
BRANCH_PATTERN = r"[A-Za-z0-9_.-]{1,64}"
# Most branches read from one answer and stored in the cache
MAX_BRANCHES = 64
# Timing of the check, in seconds
INTERVAL_SECONDS = 6 * 3600
START_DEBOUNCE_SECONDS = 10 * 60
FORCED_DEBOUNCE_SECONDS = 60
WAITING_EXPIRY_SECONDS = 15 * 60
WAITING_EVALUATION_SECONDS = 5.0
RUN_DEADLINE_SECONDS = 60.0
# Longest close, terminate and kill escalation plus the reader grace of the supervisor
ESCALATION_SECONDS = 30.0
# Longest time one check holds the SteamCMD guard
CHECK_HOLD_SECONDS = RUN_DEADLINE_SECONDS + ESCALATION_SECONDS

# State flags of the Steam manifest (SteamKit EAppState); only 2 and 4 were observed
FLAG_UPDATE_REQUIRED = 2
FLAG_FULLY_INSTALLED = 4
FLAG_FILES_MISSING = 32
FLAG_FILES_CORRUPT = 128
# Every flag of this value or more means update work (running, paused, staging and more)
FLAG_UPDATE_WORK_FROM = 256


class BuildState(str, Enum):
    """Status of the installed server build against the newest build on Steam."""

    CURRENT = "CURRENT"
    UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
    UPDATE_PENDING = "UPDATE_PENDING"
    COULD_NOT_CHECK = "COULD_NOT_CHECK"
    UNKNOWN_INSTALLATION = "UNKNOWN_INSTALLATION"


class Ownership(str, Enum):
    """Who installed the DayZ server folder; chooses guidance wording only (D2)."""

    STEAMCMD = "STEAMCMD"
    STEAM_CLIENT = "STEAM_CLIENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstalledBuild:
    """The allowlisted facts of the installed manifest; nothing else is kept."""

    build_id: int
    target_build_id: int | None
    state_flags: int
    branch: str
    branch_change: bool


@dataclass(frozen=True)
class OwnershipSignals:
    """Observations that decide the ownership class (detailed design 14.3)."""

    layout: str | None = None
    steamcmd_root: bool = False
    steam_exe: bool = False
    client_library: bool = False
    launcher_steam: bool = False
    launcher_steamcmd: bool = False


@dataclass(frozen=True)
class InstalledRead:
    """Result of one installed-build read: the build, or the reason why it is unknown."""

    build: InstalledBuild | None
    unknown_reason: str | None
    signals: OwnershipSignals = field(default_factory=OwnershipSignals)


@dataclass(frozen=True)
class BranchFact:
    """Build id and Steam branch time of one branch."""

    build_id: int
    time_updated: int | None

    def to_dict(self) -> dict[str, object]:
        """Return the fact as its persisted JSON object."""
        return {"build_id": self.build_id, "time_updated": self.time_updated}


@dataclass(frozen=True)
class BuildCheckRecord:
    """Persisted state of the newest-build check: attempt data and branch facts."""

    last_attempt: CheckAttempt | None = None
    last_success_at: str | None = None
    branches: Mapping[str, BranchFact] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildStatus:
    """Derived status of 14.7 with the values that its wording needs."""

    state: BuildState
    reason: str | None
    available_build: int | None
    available_time: int | None
    target_build: int | None


def pending_reason(build: InstalledBuild) -> str | None:
    """Return the first pending reason of the installed build, or None (14.2)."""
    flags = build.state_flags
    if build.target_build_id is not None and build.target_build_id != build.build_id:
        return "TARGET_BUILD"
    if flags >= FLAG_UPDATE_WORK_FROM:
        return "UPDATE_RUNNING"
    if flags & FLAG_UPDATE_REQUIRED:
        return "UPDATE_REQUIRED"
    if flags & (FLAG_FILES_MISSING | FLAG_FILES_CORRUPT):
        return "FILES_DAMAGED"
    return "BRANCH_CHANGE" if build.branch_change else None


def ownership_class(read: InstalledRead) -> Ownership:
    """Classify the installation; signals that disagree give UNKNOWN (14.3, rows 1 to 8)."""
    signals = read.signals
    if read.build is None:
        return Ownership.UNKNOWN
    client = signals.steam_exe or signals.client_library or signals.launcher_steam
    if signals.layout == "A":
        return Ownership.UNKNOWN if signals.launcher_steam else Ownership.STEAMCMD
    if signals.steamcmd_root:
        return Ownership.UNKNOWN if client else Ownership.STEAMCMD
    if client:
        return Ownership.UNKNOWN if signals.launcher_steamcmd else Ownership.STEAM_CLIENT
    return Ownership.UNKNOWN


def build_status(
    read: InstalledRead, check_state: CheckState, branches: Mapping[str, BranchFact],
    *, has_success: bool, steamcmd_configured: bool,
) -> BuildStatus:
    """Apply the status rule of 14.7, first match wins."""
    build = read.build
    if build is None:
        return BuildStatus(BuildState.UNKNOWN_INSTALLATION, read.unknown_reason, *_available(None, branches))
    fact = branches.get(build.branch)
    available = (fact.build_id, fact.time_updated) if fact is not None else (None, None)
    pending = pending_reason(build)
    if pending is not None:
        target = build.target_build_id if pending == "TARGET_BUILD" else None
        return BuildStatus(BuildState.UPDATE_PENDING, pending, *available, target)
    if fact is not None and fact.build_id != build.build_id:
        return BuildStatus(BuildState.UPDATE_AVAILABLE, None, *available, None)
    if fact is not None and check_state is CheckState.OK:
        return BuildStatus(BuildState.CURRENT, None, *available, None)
    if has_success and fact is None:
        return BuildStatus(BuildState.COULD_NOT_CHECK, "BRANCH_NOT_LISTED", None, None, None)
    if not steamcmd_configured:
        return BuildStatus(BuildState.COULD_NOT_CHECK, "STEAMCMD_NOT_CONFIGURED", *available, None)
    return BuildStatus(BuildState.COULD_NOT_CHECK, check_state.value, *available, None)


def _available(branch: str | None, branches: Mapping[str, BranchFact]) -> tuple[int | None, int | None, None]:
    """Return the public build for a status without an installed branch."""
    fact = branches.get(branch or "public")
    return (fact.build_id, fact.time_updated, None) if fact is not None else (None, None, None)


def due_trigger(
    attempt: CheckAttempt | None, start_done: bool, now: datetime, enabled: bool,
) -> tuple[str | None, bool]:
    """Return the due automatic trigger and the new start flag (14.6).

    The start trigger stays due until it ran once; a check shortly before this
    start already counts as the start check. An interval run is due 6 h after
    the last attempt, also after a failure.
    """
    if not enabled:
        return None, start_done
    age = attempt_age_seconds(attempt, now)
    if not start_done:
        if age is None or age >= START_DEBOUNCE_SECONDS:
            return "START", False
        start_done = True
    return ("INTERVAL" if age is None or age >= INTERVAL_SECONDS else None), start_done


def seconds_until_due(
    attempt: CheckAttempt | None, start_done: bool, waiting: bool, now: datetime,
) -> float:
    """Return the seconds until the scheduler should evaluate again (no run active)."""
    if waiting:
        return WAITING_EVALUATION_SECONDS
    age = attempt_age_seconds(attempt, now)
    if not start_done or age is None:
        return 0.0
    return max(0.0, INTERVAL_SECONDS - age)
