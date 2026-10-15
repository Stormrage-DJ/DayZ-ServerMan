"""Refusals of a medical loot-zone toggle on a mission that the map editor manages (leaf 8.13).

The texts are plain sentences without identifiers, so the activity view and
the command line show them as they are under the existing medical kind.
"""

from __future__ import annotations

from .models import RevisionConflict

# Journal, applied-record and ledger kind of the toggle on an editor-managed mission (D4 "Medical Operation Kind")
MEDICAL_KIND = "medical"
# Texts of the Product Owner, 2026-10-15 06:27:10 in evidence 8.13: the reason, "No file was changed.", the way out
# R17: the applied record does not match the files, its realization is unknown, or the generated groups differ
MISMATCH_TEXT = ("Medical loot zones cannot change because the mission files do not match the map editor records of "
                 "this profile. No file was changed. Apply the map plan of this profile again in the map editor, then "
                 "change this setting.")
# The mission is editor-managed, but this profile has no applied record for it (`applied_record_missing` only)
NO_APPLIED_PLAN_TEXT = ("Medical loot zones cannot change because the map editor manages this mission, but this profile "
                        "has no applied map plan for it. No file was changed. If another profile applied its map plan "
                        "to this mission, change this setting in that profile. Otherwise apply a map plan of this "
                        "profile in the map editor, then change this setting.")
# The zone Apply came without the reviewed preview fingerprint, or with a malformed one
FINGERPRINT_MISSING_TEXT = ("The map editor manages this mission, so a change of medical loot zones must include the "
                            "fingerprint of its reviewed preview. No file was changed. Review the change, then apply "
                            "it with that fingerprint.")
# The context of the review changed before the Apply, also a fingerprint on a mission that is not editor-managed
FINGERPRINT_STALE_TEXT = ("The profile, the settings, the mission files or the map editor records changed after the "
                          "review of medical loot zones. No file was changed. Review the change again, then apply it.")


class ZoneToggleRefused(ValueError):
    """A refused zone toggle on an editor-managed mission; the bridge code is the existing INVALID_REQUEST."""

    def __init__(self, reason: str, message: str) -> None:
        """Keep the reason code next to the admin text."""
        super().__init__(message)
        self.reason = reason


class ZoneReviewStale(RevisionConflict):
    """The reviewed zone preview is stale; the bridge code is the retryable REVISION_CONFLICT."""
