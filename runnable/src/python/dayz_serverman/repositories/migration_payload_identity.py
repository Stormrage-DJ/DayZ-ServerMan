"""Strict role and payload identity checks for migration publication."""

from __future__ import annotations

import json
from typing import Any

from ..domain.profiles import ProfileInput, ProfileValidationError
from .legacy_backup_index import LegacyBackupIndexError, parse_legacy_backup_fields
from .paths import normalize_external_path


# Fields accepted in a version 1 settings payload
SETTINGS_FIELDS = frozenset((
    "dayz_root", "dayz_executable", "steamcmd_root", "steamcmd_executable",
    "workshop_content_root", "custom_backup_root", "last_validated_paths",
    "steam_account_name", "steam_authentication_mode",
))
# Legacy settings payloads predate the Steam account fields
LEGACY_SETTINGS_FIELDS = SETTINGS_FIELDS.difference({
    "steam_account_name", "steam_authentication_mode",
})
# Settings fields that must hold normalized filesystem paths
PATH_FIELDS = SETTINGS_FIELDS.difference({
    "last_validated_paths", "steam_account_name", "steam_authentication_mode",
})
# Fields accepted in a migration report payload
REPORT_FIELDS = frozenset((
    "migration_id", "source_identity", "source_files", "selected_items",
    "skipped_items", "published", "warnings", "backup_history", "result",
))


class MigrationPayloadIdentityError(ValueError):
    """Raised when a payload does not match its role schema."""
    pass


def validate_role_payload(
    role: str, label: str, migration_id: str, payload: bytes,
) -> str:
    """Validate a payload against its role schema and return its identity."""
    document = _document(payload)
    # Pop the envelope fields so role validators see only payload fields
    version = document.pop("schema_version")
    revision = document.pop("revision")
    # Dispatch on the destination role that owns this payload schema
    if role == "PROFILE":
        if version != 2:
            raise MigrationPayloadIdentityError("profile payload schema is invalid")
        # The identity must come from the parsed profile itself
        try:
            profile = ProfileInput.parse(document)
        except ProfileValidationError as error:
            raise MigrationPayloadIdentityError("profile payload is invalid") from error
        if profile.profile_id != label:
            raise MigrationPayloadIdentityError("profile payload identity conflicts")
        return profile.profile_id
    if role == "SETTINGS":
        _validate_settings(version, document)
        return "manager-settings"
    if role == "LEGACY_BACKUP_INDEX":
        if version != 1:
            raise MigrationPayloadIdentityError("legacy index payload schema is invalid")
        # Parse through the legacy index grammar so identity is derived there
        try:
            index = parse_legacy_backup_fields(revision, document)
        except LegacyBackupIndexError as error:
            raise MigrationPayloadIdentityError("legacy index payload is invalid") from error
        return index.source_root_identity
    if role == "REPORT":
        _validate_report(version, revision, document, migration_id)
        return migration_id
    raise MigrationPayloadIdentityError("migration payload role is invalid")


def _document(payload: bytes) -> dict[str, Any]:
    """Decode strict JSON and require an integer schema envelope."""
    # Decode UTF-8 JSON with duplicate keys rejected
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise MigrationPayloadIdentityError("migration payload is not strict JSON") from error
    if not isinstance(value, dict) or "schema_version" not in value or "revision" not in value:
        raise MigrationPayloadIdentityError("migration payload envelope is invalid")
    # Schema and revision must be plain integers
    if type(value["schema_version"]) is not int or type(value["revision"]) is not int:
        raise MigrationPayloadIdentityError("migration payload version is invalid")
    if value["revision"] < 0:
        raise MigrationPayloadIdentityError("migration payload revision is invalid")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys so payloads stay unambiguous."""
    result: dict[str, Any] = {}
    # Track seen keys and refuse the first duplicate
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _validate_settings(version: int, fields: dict[str, Any]) -> None:
    """Validate a settings payload and its normalized path evidence."""
    # Only schema version 1 with a known field set is accepted
    if version != 1 or set(fields) not in (SETTINGS_FIELDS, LEGACY_SETTINGS_FIELDS):
        raise MigrationPayloadIdentityError("settings payload fields are invalid")
    # Backfill missing Steam fields from legacy payloads
    fields.setdefault("steam_account_name", None)
    fields.setdefault("steam_authentication_mode", None)
    # Path values must be stored already normalized
    for field in PATH_FIELDS:
        if fields[field] is not None and not isinstance(fields[field], str):
            raise MigrationPayloadIdentityError("settings payload value is invalid")
        if isinstance(fields[field], str):
            try:
                if normalize_external_path(fields[field]) != fields[field]:
                    raise MigrationPayloadIdentityError("settings payload path is not normalized")
            except ValueError as error:
                raise MigrationPayloadIdentityError("settings payload path is invalid") from error
    # Steam authentication fields must agree with each other
    account = fields["steam_account_name"]
    mode = fields["steam_authentication_mode"]
    if account is not None and not isinstance(account, str):
        raise MigrationPayloadIdentityError("Steam account payload value is invalid")
    if mode not in (None, "ACCOUNT", "ANONYMOUS"):
        raise MigrationPayloadIdentityError("Steam authentication mode is invalid")
    if (mode == "ACCOUNT") != bool(account) or (mode == "ANONYMOUS" and account):
        raise MigrationPayloadIdentityError("Steam authentication settings conflict")
    # Validation evidence maps keys to normalized path strings
    evidence = fields["last_validated_paths"]
    if not isinstance(evidence, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in evidence.items()
    ):
        raise MigrationPayloadIdentityError("settings payload evidence is invalid")
    # Every evidence value must itself be a normalized path
    for value in evidence.values():
        try:
            if normalize_external_path(value) != value:
                raise MigrationPayloadIdentityError("settings evidence path is not normalized")
        except ValueError as error:
            raise MigrationPayloadIdentityError("settings evidence path is invalid") from error


def _validate_report(
    version: int, revision: int, fields: dict[str, Any], migration_id: str,
) -> None:
    """Validate the final migration report payload fields."""
    # Reports must be revision zero with exactly the accepted field set
    if (
        version != 1 or revision != 0 or set(fields) != REPORT_FIELDS
        or fields["migration_id"] != migration_id or fields["result"] != "SUCCEEDED"
        or not isinstance(fields["source_identity"], str)
        or not all(isinstance(fields[name], list) for name in (
            "source_files", "selected_items", "skipped_items", "published", "warnings",
        ))
        or not isinstance(fields["backup_history"], dict)
    ):
        raise MigrationPayloadIdentityError("migration report payload is invalid")
