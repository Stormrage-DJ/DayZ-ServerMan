"""Strict parsing of durable Workshop update evidence for publication."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ..domain.workshop import AuthenticationMode, CacheProof, derive_required_items
from .operations.models import OperationState
from .publication_identity import authentication_identity_digest


# Lowercase SHA-256 hex digest used across the recorded proof fields
SHA256 = re.compile(r"[0-9a-f]{64}")


class PublicationGateError(RuntimeError):
    """The Workshop update evidence is stale or malformed."""


def parse_publication_gate(gate, request, profile, settings) -> tuple[dict[str, CacheProof], bool]:
    """Convert a succeeded Workshop update gate into per-item cache proofs."""
    # Require a succeeded Workshop update gate of the expected kind
    if gate.kind != "UPDATE_WORKSHOP_ITEMS" or gate.state != OperationState.SUCCEEDED:
        raise PublicationGateError("Workshop update gate is incomplete.")
    # Read the recorded evidence without trusting its shape
    result = gate.result
    fields = {
        "profile_id", "profile_revision", "semantic_profile_digest", "settings_revision",
        "authentication_identity_digest", "download_state", "items", "publication_state",
        "start_requested", "start_authorized", "start_error", "process_id",
        "steamcmd_exit_code", "steamcmd_summary",
    }
    if not isinstance(result, dict) or set(result) != fields:
        raise PublicationGateError("Workshop update evidence is incomplete.")
    # Require integer revisions and digest-shaped identity fields
    if (any(not isinstance(result[field], int) or isinstance(result[field], bool)
            for field in ("profile_revision", "settings_revision"))
            or any(not isinstance(result[field], str) or SHA256.fullmatch(result[field]) is None
                   for field in ("semantic_profile_digest", "authentication_identity_digest"))):
        raise PublicationGateError("Workshop update identity is invalid.")
    # Validate the optional process identity before comparing evidence
    process_id = result["process_id"]
    if process_id is not None and (
        not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0
    ):
        raise PublicationGateError("Workshop process identity is invalid.")
    # Require a consistent SteamCMD result pair
    exit_code = result["steamcmd_exit_code"]
    summary = result["steamcmd_summary"]
    if (exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool))
            or summary is not None and not isinstance(summary, str)):
        raise PublicationGateError("SteamCMD result evidence is invalid.")
    start_requested = result["start_requested"]
    # Require the pre-publication gate state recorded by the update step
    if (not isinstance(start_requested, bool)
            or result["publication_state"] != "PENDING_PHASE_6_2"
            or result["start_authorized"] is not False
            or result["start_error"] != ("PUBLICATION_REQUIRED" if start_requested else None)):
        raise PublicationGateError("Workshop gate state is contradictory.")
    # Recompute the expected authentication identity for comparison
    mode = AuthenticationMode(settings.steam_authentication_mode)
    expected_auth = authentication_identity_digest(mode, settings.steam_account_name)
    actual = tuple(result[field] for field in (
        "profile_id", "profile_revision", "semantic_profile_digest",
        "settings_revision", "authentication_identity_digest",
    ))
    expected = (request.profile_id, request.profile_revision, request.semantic_profile_digest,
                request.settings_revision, expected_auth)
    required = derive_required_items(profile)
    state = "VERIFIED" if required else "EMPTY"
    # Compare the recorded context with the reviewed request
    if actual != expected:
        raise PublicationGateError("Workshop update context changed.")
    # Require the download state that matches the required item set
    if result["download_state"] != state:
        raise PublicationGateError("Workshop update did not produce a verified cache set.")
    if required and (process_id is None or exit_code != 0 or summary is not None):
        raise PublicationGateError("SteamCMD completion evidence is contradictory.")
    if not required and (process_id is not None or exit_code is not None or summary is not None):
        raise PublicationGateError("Empty Workshop evidence is contradictory.")
    items = result["items"]
    if not isinstance(items, list) or len(items) != len(required):
        raise PublicationGateError("Workshop proof set is incomplete.")
    proofs: dict[str, CacheProof] = {}
    for expected_item, raw in zip(required, items):
        # Require unchanged proof entries in the same order as the gate
        if not isinstance(raw, dict) or set(raw) != {
            "item", "outcome", "cache_proof", "error_code",
        } or raw.get("outcome") not in (
            "VERIFIED_CURRENT", "DOWNLOADED_VERIFIED", "UPDATED_VERIFIED",
        ) or raw.get("error_code") is not None or raw.get("item") != expected_item.to_dict():
            raise PublicationGateError("Workshop proof order changed.")
        proofs[expected_item.workshop_id] = _parse_proof(
            raw.get("cache_proof"), expected_item.workshop_id,
        )
    return proofs, start_requested


def _parse_proof(raw: object, workshop_id: str) -> CacheProof:
    """Validate one recorded cache proof for a Workshop item."""
    # Require the exact proof field set for the expected Workshop item
    fields = {"workshop_id", "manifest_record_digest", "content_inventory_digest",
              "regular_file_count", "total_regular_bytes", "verified_at",
              "installed_manifest_id", "metadata_inventory_digest", "verification_kind",
              "target_metadata_digest"}
    if not isinstance(raw, dict) or set(raw) != fields or raw.get("workshop_id") != workshop_id:
        raise PublicationGateError("Workshop proof is invalid.")
    try:
        proof = CacheProof(**raw)
    except TypeError as error:
        raise PublicationGateError("Workshop proof is invalid.") from error
    # Require digest-shaped proofs and positive file counters
    if any(not isinstance(value, str) or SHA256.fullmatch(value) is None for value in (
        proof.manifest_record_digest, proof.content_inventory_digest,
        proof.metadata_inventory_digest,
    )) or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (
        proof.regular_file_count, proof.total_regular_bytes,
    )) or not _utc_timestamp(proof.verified_at):
        raise PublicationGateError("Workshop proof is invalid.")
    # Require a decimal manifest identifier
    if (not isinstance(proof.installed_manifest_id, str)
            or not proof.installed_manifest_id.isdecimal()):
        raise PublicationGateError("Workshop manifest identifier is invalid.")
    # Accept only full-content and applied-state verification kinds
    if proof.verification_kind not in ("FULL_CONTENT", "APPLIED_STATE"):
        raise PublicationGateError("Workshop proof kind is invalid.")
    if (proof.verification_kind == "APPLIED_STATE"
            and (not isinstance(proof.target_metadata_digest, str)
                 or SHA256.fullmatch(proof.target_metadata_digest) is None)):
        raise PublicationGateError("Applied target metadata proof is invalid.")
    if proof.target_metadata_digest is not None and (
        not isinstance(proof.target_metadata_digest, str)
        or SHA256.fullmatch(proof.target_metadata_digest) is None
    ):
        raise PublicationGateError("Applied target metadata proof is invalid.")
    return proof


def _utc_timestamp(value: object) -> bool:
    """Return True when the value is an ISO-8601 UTC timestamp."""
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    # Require the explicit UTC offset on the parsed timestamp
    return parsed.tzinfo == UTC
