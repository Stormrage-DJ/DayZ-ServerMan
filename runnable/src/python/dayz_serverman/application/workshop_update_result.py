"""Assembly of the result that one Workshop update run reports to the bridge."""

from __future__ import annotations

from ..domain.workshop import ItemResult
from .publication_identity import authentication_identity_digest
from .workshop_ports import UpdateRequest


def update_result(
    request: UpdateRequest,
    results: tuple[ItemResult, ...],
    state: str,
    *,
    process_id: int | None = None,
    steamcmd_exit_code: int | None = None,
    steamcmd_summary: str | None = None,
) -> dict[str, object]:
    """Assemble the bridge payload reported for one update run."""
    # Publication is deferred, so start is recorded but never authorized
    return {
        "profile_id": request.profile_id,
        "profile_revision": request.expected_profile_revision,
        "semantic_profile_digest": request.expected_semantic_profile_digest,
        "settings_revision": request.expected_settings_revision,
        "authentication_identity_digest": authentication_identity_digest(
            request.authentication_mode, request.account_name,
        ),
        "download_state": state,
        "publication_state": "PENDING_PHASE_6_2",
        "start_requested": request.update_all_and_start,
        "start_authorized": False,
        "start_error": "PUBLICATION_REQUIRED" if request.update_all_and_start else None,
        "items": [item.to_dict() for item in results],
        "process_id": process_id,
        "steamcmd_exit_code": steamcmd_exit_code,
        "steamcmd_summary": steamcmd_summary,
    }
