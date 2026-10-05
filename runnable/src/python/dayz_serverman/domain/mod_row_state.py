"""Pure rule that derives one mod row state from local, remote and target evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .update_check import RemoteFact, RemoteItemResult
from .update_check_rules import CheckState


class TargetProof(str, Enum):
    """What is known about the server-folder copy of one Workshop item."""

    UNKNOWN = "UNKNOWN"
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"
    MISSING = "MISSING"


@dataclass(frozen=True)
class RowInputs:
    """Evidence for one row; check_state is None when no check source is wired."""

    source_kind: str
    cache_readable: bool
    installed_manifest_id: str | None
    latest_manifest_id: str | None
    local_time_updated: int | None
    fact: RemoteFact | None
    check_state: CheckState | None
    target: TargetProof
    # Whether the Workshop content folder of the item exists; a record without it is not downloaded (QF-054)
    content_present: bool = True


@dataclass(frozen=True)
class RowState:
    """Derived state of one row with its three additive fields."""

    state: str
    remote_time_updated: int | None = None
    remote_check: str | None = None
    pending_reason: str | None = None


def row_state(inputs: RowInputs) -> RowState:
    """Return the state of one row; the first matching rule wins."""
    # Rows without a usable local Workshop record have no remote comparison
    if inputs.source_kind != "workshop":
        return RowState("LOCAL", remote_check=_not_applicable(inputs))
    if not inputs.cache_readable:
        return RowState("UNAVAILABLE", remote_check=_not_applicable(inputs))
    # A manifest record whose content folder is gone has nothing to apply, as "Update all" already assumes
    if inputs.installed_manifest_id is None or not inputs.content_present:
        return RowState("NOT_DOWNLOADED", remote_check=_not_applicable(inputs))
    fact = inputs.fact
    fact_ok = fact is not None and fact.result is RemoteItemResult.OK
    remote_time = fact.time_updated if fact_ok else None
    remote_check = _remote_check(inputs, fact_ok)
    local_time = inputs.local_time_updated
    latest = inputs.latest_manifest_id

    def result(state: str, reason: str | None = None) -> RowState:
        """Return the row with the remote fields shared by all later rules."""
        return RowState(state, remote_time, remote_check, reason)

    # A newer remote time counts even when the check is stale or failed
    if (latest and latest != inputs.installed_manifest_id) or (
        remote_time is not None and local_time is not None and remote_time > local_time
    ):
        return result("UPDATE_AVAILABLE")
    # Downloaded content without a proven server-folder copy is not applied
    if inputs.target is TargetProof.UNPROVEN:
        return result("PENDING_APPLY", "TARGET_UNPROVEN")
    if inputs.target is TargetProof.MISSING:
        return result("PENDING_APPLY", "TARGET_MISSING")
    # Only a fresh, equal remote answer proves that the item is current
    if (inputs.check_state is CheckState.OK and remote_time is not None
            and remote_time == local_time):
        return result("CURRENT")
    # Without a check source the local manifest comparison decides, as before
    if inputs.check_state is None and latest and latest == inputs.installed_manifest_id:
        return result("CURRENT")
    # An unverified item is installed, never current
    return result("INSTALLED")


def _not_applicable(inputs: RowInputs) -> str | None:
    """Return the remote-check value of a row that has nothing to compare."""
    return None if inputs.check_state is None else "NOT_APPLICABLE"


def _remote_check(inputs: RowInputs, fact_ok: bool) -> str | None:
    """Classify how far the remote answer for an installed item can be trusted."""
    # No check source wired: the field stays absent
    if inputs.check_state is None:
        return None
    if inputs.fact is None or inputs.check_state is CheckState.NEVER:
        return "NEVER"
    if not fact_ok:
        return "UNKNOWN_ITEM"
    # An OK fact is as fresh as the last check
    if inputs.check_state in (CheckState.FAILED, CheckState.STALE):
        return inputs.check_state.value
    return "OK"
