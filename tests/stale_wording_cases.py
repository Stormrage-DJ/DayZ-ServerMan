"""Cases of the 8.14 stale texts, shared by the Python and the window catalogue tests.

Each case is (label, kind, code, host message, shown text). `kind` None is a bridge refusal that the
window words without a kind. Sources: Product Owner 2026-10-15 06:27:10 and 14:59:22 (evidence 8.13) and
07:10:16 (evidence 8.8).
"""

from __future__ import annotations

from dayz_serverman.application.activity_wording import ERROR_TEXTS
from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_STALE_TEXT
from dayz_serverman.domain.mission_map_restore_texts import LANE_KINDS, RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT

MEDICAL_KIND = "APPLY_MEDICAL_FEATURE"
GENERAL_TEXT = ERROR_TEXTS["REVISION_CONFLICT"]
# The 6.3 messages of an ordinary mission: a changed loot-zone file and a changed settings revision
CHANGED_TEXT = "medical feature target changed after it was loaded"
SETTINGS_TEXT = "expected settings revision does not match"
LATE_CHANGE_TEXT = TARGET_CHANGED_TEXT.format(path="mapgroupdirt.xml")

# A stale zone review gives the same envelope before and under the guard; it shows under every kind
STALE_REVIEW_CASES = tuple(
    (f"stale review {timing}, kind {kind}", kind, "REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, FINGERPRINT_STALE_TEXT)
    for timing in ("before the guard", "under the guard")
    for kind in (MEDICAL_KIND, None, "APPLY_MISSION_MAP"))
# An ordinary mission keeps the general text
ORDINARY_CASES = tuple(
    (f"ordinary mission, kind {kind}", kind, "REVISION_CONFLICT", message, GENERAL_TEXT)
    for message in (CHANGED_TEXT, SETTINGS_TEXT) for kind in (MEDICAL_KIND, None))
# A stale restore review and a late file change show the stale-restore text under both restore kinds
RESTORE_CASES = tuple(
    (f"restore {label}, kind {kind}", kind, "REVISION_CONFLICT", message, RESTORE_STALE_TEXT)
    for label, message in (("stale review", RESTORE_STALE_TEXT), ("late file change", LATE_CHANGE_TEXT))
    for kind in LANE_KINDS.values())
# Every case of the window and the Python catalogue
ALL_CASES = STALE_REVIEW_CASES + ORDINARY_CASES + RESTORE_CASES
