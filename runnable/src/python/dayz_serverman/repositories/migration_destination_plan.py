"""Authoritative destination grammar for legacy-import publication."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..domain.profiles import ProfileValidationError, validate_profile_id


# Migration identifiers are 32-character lowercase hex values
MIGRATION_ID = re.compile(r"[0-9a-f]{32}")
# Digests are 64-character lowercase hex values
SHA256 = re.compile(r"[0-9a-f]{64}")


class MigrationDestinationPlanError(ValueError):
    """Raised when a destination plan violates the migration destination grammar."""
    pass


def validate_destination_plan(
    migration_id: str, destinations: Sequence[Mapping[str, object]],
) -> None:
    """Validate the ordered destination plan for one migration identifier."""
    # Reject incomplete identifiers or plans before checking destinations
    if MIGRATION_ID.fullmatch(migration_id) is None or len(destinations) < 2:
        raise MigrationDestinationPlanError("migration destination plan is incomplete")
    # Track seen targets, profile identities, and role occurrences
    targets: set[str] = set()
    profiles: set[str] = set()
    profile_order: list[str] = []
    report_count = 0
    settings_count = 0
    index_count = 0
    # Validate every destination against the grammar of its declared role
    for index, item in enumerate(destinations):
        # Read the identity fields carried by every destination record
        role, label = item.get("role"), item.get("label")
        target, staged = item.get("target_relative"), item.get("staged_relative")
        payload_identity = item.get("payload_identity")
        # Require non-empty string identity data on every destination
        if not all(isinstance(value, str) and value for value in (
            role, label, target, staged, payload_identity,
        )):
            raise MigrationDestinationPlanError("migration destination identity is invalid")
        target_text, staged_text = str(target), str(staged)
        # Staged outputs must be named by plan position
        if staged_text != f"output/{index:04d}.bin":
            raise MigrationDestinationPlanError("migration destination order is invalid")
        # Compare folded targets so case variants cannot collide on Windows
        folded = target_text.casefold()
        if folded in targets:
            raise MigrationDestinationPlanError("migration destination target repeats")
        targets.add(folded)
        # Settings must occupy the first slot with the canonical target
        if role == "SETTINGS":
            settings_count += 1
            if (
                index != 0 or label != "Manager settings"
                or target_text != "config/manager.json"
                or payload_identity != "manager-settings"
            ):
                raise MigrationDestinationPlanError("migration settings destination is invalid")
        elif role == "PROFILE":
            # Validate the label through the shared profile identifier grammar
            try:
                profile_id = validate_profile_id(label)
            except ProfileValidationError as error:
                raise MigrationDestinationPlanError("migration profile identity is invalid") from error
            if profile_id in profiles or target_text != f"data/profiles/{profile_id}.json":
                raise MigrationDestinationPlanError("migration profile destination is invalid")
            profiles.add(profile_id)
            profile_order.append(profile_id)
            if payload_identity != profile_id:
                raise MigrationDestinationPlanError("migration profile payload identity is invalid")
        # The legacy index must sit directly before the final report slot
        elif role == "LEGACY_BACKUP_INDEX":
            index_count += 1
            if (
                label != "Legacy backup index"
                or target_text != "data/migrations/legacy-backup-index.json"
                or index != len(destinations) - 2
                or SHA256.fullmatch(str(payload_identity)) is None
            ):
                raise MigrationDestinationPlanError("migration legacy index destination is invalid")
        # The report must be the final destination and carry the migration id
        elif role == "REPORT":
            report_count += 1
            if (
                index != len(destinations) - 1 or label != migration_id
                or target_text != f"data/migrations/reports/{migration_id}.json"
                or payload_identity != migration_id
            ):
                raise MigrationDestinationPlanError("migration report destination is invalid")
        # Any other role falls outside the supported destination grammar
        else:
            raise MigrationDestinationPlanError("migration destination role is invalid")
    # Require exactly one report and at most one settings or index slot
    if settings_count > 1 or index_count > 1 or report_count != 1:
        raise MigrationDestinationPlanError("migration destination roles are incomplete")
    # Profiles must stay in deterministic case-insensitive order
    if profile_order != sorted(profile_order, key=lambda value: (value.casefold(), value)):
        raise MigrationDestinationPlanError("migration profile destinations are out of order")
