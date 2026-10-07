"""Non-mutating migration target and orphan-stage classification."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Mapping

from ..adapters.windows.shared_files import read_bytes_shared, read_text_shared
from .migration_destination_plan import (
    MigrationDestinationPlanError, validate_destination_plan,
)
from .migration_payload_identity import (
    MigrationPayloadIdentityError, validate_role_payload,
)


# Digests are 64-character lowercase hex values
SHA256 = re.compile(r"[0-9a-f]{64}")
# Every state a target can be classified into
TARGET_STATES = frozenset(("PRIOR", "STAGED_OUTPUT", "THIRD_STATE"))


def classify_destinations(
    manager_root: Path, destinations: list[dict[str, object]],
) -> tuple[str, ...]:
    """Classify every target as prior, staged output, or third state."""
    return tuple(
        classify_target(_target(manager_root, item), item) for item in destinations
    )


def classify_target(target: Path, evidence: Mapping[str, object]) -> str:
    """Classify one target from its bytes against the recorded digests."""
    # A missing target is prior only when it never existed
    if not target.exists():
        return "PRIOR" if evidence["prior_exists"] is False else "THIRD_STATE"
    # Directories and unreadable files cannot be proven safe
    if not target.is_file():
        return "THIRD_STATE"
    try:
        digest = _digest(read_bytes_shared(target))
    except OSError:
        return "THIRD_STATE"
    # Matching prior bytes prove the prior state
    if evidence["prior_exists"] is True and digest == evidence["prior_sha256"]:
        return "PRIOR"
    # Matching staged bytes prove the staged output arrived
    if digest == evidence["staged_sha256"]:
        return "STAGED_OUTPUT"
    # Anything else is ambiguous and blocks automatic recovery
    return "THIRD_STATE"


def orphan_stage_is_proven_prepublication(
    manager_root: Path, stage: Path, migration_id: str,
) -> bool:
    """Prove that an orphaned stage never began publication."""
    # An empty stage holds nothing that publication could have changed
    try:
        entries = {item.name for item in stage.iterdir()}
    except OSError:
        return False
    if not entries:
        return True
    # Recovery copies or journals prove publication left evidence behind
    if "recovery" in entries or any("journal" in name.casefold() for name in entries):
        return False
    # The marker file is the only accepted proof of the staging plan
    marker_path = stage / "plan.json"
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(read_text_shared(marker_path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(marker, dict):
        return False
    # The marker must belong to this migration and deny publication
    if marker.get("migration_id") != migration_id or marker.get("publication_started") is not False:
        return False
    # Dispatch on the recorded staging kind
    kind = marker.get("kind")
    if kind == "SOURCE_COPY_NOT_PUBLISHED":
        return _source_marker_safe(marker, entries)
    if kind != "PUBLICATION_OUTPUT_NOT_PUBLISHED":
        return False
    return _publication_marker_safe(manager_root, stage, marker, entries)


def _source_marker_safe(marker: dict[str, object], entries: set[str]) -> bool:
    """Check a source-copy marker with only its source directory staged."""
    # The marker must be exactly the accepted schema version 1 shape
    if set(marker) != {
        "schema_version", "kind", "migration_id", "source_digest",
        "publication_started",
    } or type(marker.get("schema_version")) is not int or marker.get("schema_version") != 1:
        return False
    # Only the plan and a copied source tree may sit beside the marker
    return entries.issubset({"plan.json", "source"}) and _sha(marker.get("source_digest"))


def _publication_marker_safe(
    manager_root: Path, stage: Path, marker: dict[str, object], entries: set[str],
) -> bool:
    """Prove a publication-output marker across staged and live evidence."""
    # The marker must be exactly the accepted schema version 1 shape
    if set(marker) != {
        "schema_version", "kind", "migration_id", "publication_started", "destinations",
    } or type(marker.get("schema_version")) is not int or marker.get("schema_version") != 1 or not entries.issubset({"plan.json", "output"}):
        return False
    destinations = marker.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        return False
    # Track target and staged identities to reject repeats
    identities: set[str] = set()
    staged_identities: set[str] = set()
    checked: list[dict[str, object]] = []
    # Each destination record must carry the exact expected field set
    for raw in destinations:
        if not isinstance(raw, dict) or set(raw) != {
            "role", "label", "payload_identity", "prior_payload_identity",
            "target_relative", "staged_relative", "prior_exists",
            "prior_sha256", "staged_sha256",
        }:
            return False
        # Relative paths must be portable
        if not _relative(raw["target_relative"]) or not _relative(raw["staged_relative"]):
            return False
        identity = str(raw["target_relative"]).casefold()
        staged_identity = str(raw["staged_relative"]).casefold()
        if (
            identity in identities or staged_identity in staged_identities
            or type(raw["prior_exists"]) is not bool
            or raw["prior_exists"] != (raw["prior_payload_identity"] is not None)
        ):
            return False
        identities.add(identity)
        staged_identities.add(staged_identity)
        # Prior proofs must agree with the prior flag
        prior = raw["prior_sha256"]
        if raw["prior_exists"] != (prior is not None) or (prior is not None and not _sha(prior)):
            return False
        # The staged digest must always be present
        if not _sha(raw["staged_sha256"]):
            return False
        # The staged output must stay inside the stage directory
        staged = stage / Path(str(raw["staged_relative"]))
        try:
            staged.resolve(strict=True).relative_to(stage.resolve(strict=True))
        except (OSError, ValueError):
            return False
        if staged.is_symlink() or not staged.is_file():
            return False
        # Staged payloads must match their digest and role identity
        payload = read_bytes_shared(staged)
        if _digest(payload) != raw["staged_sha256"]:
            return False
        try:
            identity = validate_role_payload(
                str(raw["role"]), str(raw["label"]), str(marker["migration_id"]), payload,
            )
        except MigrationPayloadIdentityError:
            return False
        if identity != raw["payload_identity"]:
            return False
        checked.append(raw)
    # The destination plan must satisfy the shared plan grammar
    try:
        validate_destination_plan(str(marker["migration_id"]), checked)
    except MigrationDestinationPlanError:
        return False
    # Live targets must still match their recorded prior evidence
    for raw in checked:
        if raw["prior_exists"] is False:
            continue
        try:
            target_payload = read_bytes_shared(_target(manager_root, raw))
            target_identity = validate_role_payload(
                str(raw["role"]), str(raw["label"]), str(marker["migration_id"]),
                target_payload,
            )
        except (OSError, ValueError, MigrationPayloadIdentityError):
            return False
        if (
            _digest(target_payload) != raw["prior_sha256"]
            or target_identity != raw["prior_payload_identity"]
        ):
            return False
    # The staged output tree must match the recorded staged paths
    try:
        actual = {
            item.relative_to(stage).as_posix().casefold()
            for item in (stage / "output").rglob("*") if item.is_file()
        }
    except OSError:
        return False
    if actual != staged_identities:
        return False
    # Every live target must remain in the prior state
    try:
        return all(state == "PRIOR" for state in classify_destinations(manager_root, checked))
    except (OSError, ValueError):
        return False


def _target(manager_root: Path, evidence: Mapping[str, object]) -> Path:
    """Resolve a recorded target relative to the manager root."""
    target = (manager_root / Path(str(evidence["target_relative"]))).resolve(strict=False)
    target.relative_to(manager_root)
    return target


def _relative(value: object) -> bool:
    """Accept only portable non-empty relative POSIX paths."""
    # Backslashes and drive colons would break portability
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    # Dot segments and absolute roots could escape the stage or root
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def _sha(value: object) -> bool:
    """Return True when the value is a lowercase hex SHA-256 digest."""
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _digest(payload: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of the payload."""
    return hashlib.sha256(payload).hexdigest()
