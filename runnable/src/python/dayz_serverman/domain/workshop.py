"""Workshop selection, outcomes, and update-gate domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .profiles import ProfileRecord, WORKSHOP_ID


class WorkshopValidationError(ValueError):
    """Raised when a Workshop selection violates the update contract."""
    pass


class AuthenticationMode(str, Enum):
    """Steam authentication mode used for Workshop downloads."""

    ACCOUNT = "ACCOUNT"
    ANONYMOUS = "ANONYMOUS"


class ItemOutcome(str, Enum):
    """Terminal outcome of one required Workshop item."""

    VERIFIED_CURRENT = "VERIFIED_CURRENT"
    DOWNLOADED_VERIFIED = "DOWNLOADED_VERIFIED"
    UPDATED_VERIFIED = "UPDATED_VERIFIED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    ENTITLEMENT_FAILED = "ENTITLEMENT_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    CONTENT_FAILED = "CONTENT_FAILED"
    CANCELLED = "CANCELLED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    UNKNOWN_FAILED = "UNKNOWN_FAILED"


# Outcomes that prove the local cache matches the required manifest
SUCCESS_OUTCOMES = frozenset((
    ItemOutcome.VERIFIED_CURRENT,
    ItemOutcome.DOWNLOADED_VERIFIED,
    ItemOutcome.UPDATED_VERIFIED,
))


@dataclass(frozen=True)
class RequiredWorkshopItem:
    """One Workshop item a profile requires, with its launch order."""

    workshop_id: str
    order_index: int
    launch_scope: str

    def to_dict(self) -> dict[str, object]:
        """Return the item as its persisted JSON object."""
        return {
            "workshop_id": self.workshop_id,
            "order_index": self.order_index,
            "launch_scope": self.launch_scope,
        }


@dataclass(frozen=True)
class CacheProof:
    """Evidence that a Workshop item cache matches its verified manifest."""

    workshop_id: str
    manifest_record_digest: str
    content_inventory_digest: str
    regular_file_count: int
    total_regular_bytes: int
    verified_at: str
    installed_manifest_id: str | None = None
    metadata_inventory_digest: str | None = None
    verification_kind: str = "FULL_CONTENT"
    target_metadata_digest: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the proof as its persisted JSON object."""
        return self.__dict__.copy()


@dataclass(frozen=True)
class WorkshopObservation:
    """Installed and latest manifest identities observed for one item."""

    workshop_id: str
    installed_manifest_id: str | None
    latest_manifest_id: str | None
    installed_time_updated: int | None
    latest_time_updated: int | None

    @property
    def installed(self) -> bool:
        """Return whether an installed manifest identity was observed."""
        return self.installed_manifest_id is not None


@dataclass(frozen=True)
class ItemResult:
    """Outcome of one required item, with cache proof or error code."""

    item: RequiredWorkshopItem
    outcome: ItemOutcome
    proof: CacheProof | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the result as its persisted JSON object."""
        return {
            "item": self.item.to_dict(),
            "outcome": self.outcome.value,
            "cache_proof": self.proof.to_dict() if self.proof else None,
            "error_code": self.error_code,
        }


def derive_required_items(profile: ProfileRecord) -> tuple[RequiredWorkshopItem, ...]:
    """Return the Workshop items a profile requires, in launch order."""
    items: list[RequiredWorkshopItem] = []
    seen: set[str] = set()
    # Keep only mods sourced from the Workshop, in profile order
    for mod in profile.values.mods:
        if mod.source.kind != "workshop":
            continue
        workshop_id = mod.source.workshop_id
        # Reject invalid or repeated identifiers instead of guessing
        if workshop_id is None or WORKSHOP_ID.fullmatch(workshop_id) is None:
            raise WorkshopValidationError("Workshop source has an invalid identifier")
        if workshop_id in seen:
            raise WorkshopValidationError("Workshop identifiers must be unique")
        seen.add(workshop_id)
        # Record accepted items with their zero-based launch order
        items.append(RequiredWorkshopItem(workshop_id, len(items), mod.launch_scope))
    return tuple(items)


def download_gate(results: tuple[ItemResult, ...]) -> str:
    """Return the aggregate download gate for a set of item results."""
    # An empty plan cannot be verified or failed
    if not results:
        return "EMPTY"
    # All successes prove the local cache matches the manifest
    if all(item.outcome in SUCCESS_OUTCOMES for item in results):
        return "VERIFIED"
    # Cancellation outranks unknown and ordinary failures
    if any(item.outcome == ItemOutcome.CANCELLED for item in results):
        return "CANCELLED"
    if any(item.outcome == ItemOutcome.UNKNOWN_FAILED for item in results):
        return "UNKNOWN"
    return "FAILED"
