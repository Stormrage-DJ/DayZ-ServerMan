"""Reviewed list of verified mission families and the Apply and Restore original gate (leaf 6.11).

A family is the terrain family of a mission folder: the template suffix after the last dot. Only the families
in VERIFIED_FAMILIES passed the native CE probe (6.10 or 6.12).

On every other family, and without a known family, Apply and Restore original are refused. Planning, saving,
import, export and Inspect stay available (Product Owner Q5, evidence 2.1).

The gate is one more precondition: the per-mission checks of a verified family still apply. 6.12 adds a family
only with its own probe pass and a unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mission_map_terrain import TERRAIN_FAMILY_UNKNOWN


# Raw probe evidence of 6.10: the set name and its aggregate SHA-256 (Operations 2026-10-14 17:00:24).
# Vigil moved the set (2026-10-14 17:02:45), so the repository names the set, not a machine path
RAW_EVIDENCE_SET = "probe-runs-2026-10-14"
RAW_EVIDENCE_SHA256 = "cdc31c3d43e8bbf2be681713a9fd4b25620e0a4ba477bb4556edd6d811176e16"
_RAW_EVIDENCE = (f"Raw evidence: the set {RAW_EVIDENCE_SET} (aggregate SHA-256 {RAW_EVIDENCE_SHA256[:16]}, "
                 "Operations 2026-10-14 17:00:24; location in evidence 6.10, Vigil 2026-10-14 17:02:45).")

# Raw probe evidence of 6.12: the set name and its aggregate SHA-256 (QA 2026-10-16 16:57:11 checked the aggregate;
# Vigil 2026-10-17 00:52:42 records the location)
RAW_EVIDENCE_612_SET = "probe-runs-2026-10-16-612"
RAW_EVIDENCE_612_SHA256 = "40a2bb72e1ebdf37e43dbb173973dab56626aa4b23752a63694a7b21d3a7e5be"
_RAW_EVIDENCE_612 = (f"Raw evidence: the set {RAW_EVIDENCE_612_SET} "
                     f"(aggregate SHA-256 {RAW_EVIDENCE_612_SHA256[:16]}, QA 2026-10-16 16:57:11; location in "
                     "evidence 6.12, Vigil 2026-10-17 00:52:42).")

# Operations that the gate refuses on an unverified family; Restore previous state is not one of them
APPLY = "apply"
RESTORE_ORIGINAL = "restore_original"
GATED_OPERATIONS = (APPLY, RESTORE_ORIGINAL)

# Closed reason code of a family that is not in the list; a mission without a family keeps the 3.5 code
FAMILY_NOT_VERIFIED = "family_not_verified"

# Product Owner texts of 2026-10-14 17:28:16 (evidence 6.11), word for word. The window, the preview, the
# refusals and the CLI show the same text. {family} is the folder suffix as written, with its case unchanged
_RULE = ("Apply and Restore original are not available for this mission. This version of DayZ-ServerMan allows "
         "them only for terrain families that passed a test with a DayZ server. ")
_STILL = "You can still change, save, import and export the plan."
NOT_VERIFIED_TEXT = _RULE + "The terrain family {family} is not one of them. " + _STILL
FAMILY_UNKNOWN_TEXT = (_RULE + "The mission folder name has no terrain family after a dot, as in "
                       "dayzOffline.chernarusplus. " + _STILL)


@dataclass(frozen=True)
class VerifiedFamily:
    """One entry of the reviewed list: the family, its probe evidence, its probe scope and its limits."""

    family: str
    evidence: str
    raw_evidence: str
    scope: str
    limits: tuple[str, ...]

    def text(self) -> str:
        """Return the entry text as the Architect worded it (evidence 6.10, 2026-10-14 17:00:50)."""
        limits = " ".join(f"({number}) {limit}" for number, limit in enumerate(self.limits, start=1))
        return f"{self.evidence} {self.raw_evidence} {self.scope} Limits: {limits}"


# Limits that both 6.10 families share; the Architect text gives them in this order
_LIVE_QUANTITIES = "The probe proves that CE loads the candidate files, not live loot quantities."
_ALIAS_PAIR = ("CE binding of case-only alias spellings was proved for one pair of truck-wreck groups; the other "
               "alias groups are covered by the 6.8 rule, not by their own census.")
_FIXED_DISPATCH = ("Fixed dispatch items are not governed by lootmax, so a group with ceiling 0 still shows its "
                   "dispatch parts.")
_WRITER_PIN = "The candidates came from the 16 repository writer files pinned at commit f343e48."

# The reviewed list (6.11); it starts with the families that passed 6.10 (QA 2026-10-14 16:53:03).
# Then come the optional families that passed 6.12 (QA 2026-10-16 16:57:11; Architect 2026-10-16 17:13:21)
VERIFIED_FAMILIES = (
    VerifiedFamily(
        "chernarusplus",
        "Verified by the 6.10 probe (evidence 6.10; QA 2026-10-14 16:53:03; Architect 2026-10-14 15:45:25).",
        _RAW_EVIDENCE,
        "Probe: runs R0 to R4 with global abundance +50 and four zones, on DayZ server 1.29.163709; medical off, "
        "on and the toggle after Apply were CE-loaded.",
        (_LIVE_QUANTITIES, _ALIAS_PAIR, _FIXED_DISPATCH, _WRITER_PIN,
         "DayZ server 1.30 changed the chernarusplus files mapgroupproto.xml, mapgrouppos.xml and cfgeconomycore.xml "
         "after the probe. The writer forms were CE-loaded on 1.30 on enoch; the changed chernarusplus content stays "
         "under the per-mission checks.")),
    VerifiedFamily(
        "Pripyat",
        "Verified by the 6.10 probe (evidence 6.10; QA 2026-10-14 16:53:03; Architect 2026-10-14 15:11:54, 15:45:25 "
        "and 17:00:50).",
        _RAW_EVIDENCE,
        "Probe: runs R0, R1 and R4 with global abundance 0 and four zones, on DayZ server 1.29.163709, loaded "
        "with @PripyatDelta;@GSCAssets2026.",
        ("Pripyat was probed in its installed state, where medical off equals on, because its stored original is "
         "unmappable (evidence 6.4). The off form with generated groups was not CE-loaded on Pripyat; medical off, "
         "on and the toggle after Apply were CE-loaded on chernarusplus only.",
         "Pripyat was probed with global abundance 0 only, because its duplicate groups "
         "Land_Jupiter_Building_Plant_1_8 and Land_Military_building2 refuse any other global value "
         "(source_ambiguous). The global write on name-bound groups was CE-loaded on chernarusplus; on Pripyat "
         "it becomes possible only after the duplicates are removed outside the editor.",
         "Only the terrain mods were loaded; the other eight mods of the real profile are not part of the proof.",
         _LIVE_QUANTITIES, _ALIAS_PAIR, _FIXED_DISPATCH, _WRITER_PIN)),
    VerifiedFamily(
        "enoch",
        "Verified by the 6.12 probe (evidence 6.12; QA 2026-10-16 16:57:11; Architect 2026-10-16 17:13:21).",
        _RAW_EVIDENCE_612,
        "Probe: runs R0 to R4 with global abundance +50 and four zones, on DayZ server 1.30.164078.38; medical off, on "
        "and the toggle after Apply were CE-loaded.",
        ("enoch has no stored medical baseline, so the probe used the installed file as the medical off state, as "
         "DayZ-ServerMan does without a baseline. No enoch mission with a stored medical baseline was probed.",
         _LIVE_QUANTITIES, _ALIAS_PAIR, _FIXED_DISPATCH, _WRITER_PIN,
         "The group Land_Underground_Storage_POX2 has a point without range and height, which CE counts as a group "
         "with wrong points. A generated copy keeps the points of its source group, so a zone over it adds one more "
         "such group to that CE count; no probe candidate copied it.")),
)


class FamilyNotVerified(ValueError):
    """Raised when Apply or Restore original is asked for on a mission whose family is not verified."""

    def __init__(self, gate: FamilyGate, operation: str) -> None:
        """Keep the gate and the refused operation; the visible reason is the error text."""
        self.gate = gate
        self.operation = operation
        super().__init__(gate.text)


@dataclass(frozen=True)
class FamilyGate:
    """Gate value of one mission for the capability snapshot: verified, or the reason code and visible text."""

    terrain_id: str | None
    entry: VerifiedFamily | None

    @property
    def verified(self) -> bool:
        """Return True when the family of the mission is in the reviewed list."""
        return self.entry is not None

    @property
    def reason(self) -> str | None:
        """Return the closed reason code of an unverified family, or None for a verified one."""
        if self.verified:
            return None
        return TERRAIN_FAMILY_UNKNOWN if self.terrain_id is None else FAMILY_NOT_VERIFIED

    @property
    def text(self) -> str | None:
        """Return the visible reason of an unverified family, or None for a verified one."""
        if self.verified:
            return None
        return FAMILY_UNKNOWN_TEXT if self.terrain_id is None else NOT_VERIFIED_TEXT.format(family=self.terrain_id)

    def to_dict(self) -> dict[str, Any]:
        """Return the family field of the capability snapshot (A11); it names no path."""
        return {"terrain_id": self.terrain_id, "verified": self.verified, "reason": self.reason,
                "text": self.text, "refused": [] if self.verified else list(GATED_OPERATIONS)}


def verified_family(terrain_id: str | None) -> VerifiedFamily | None:
    """Return the list entry of a terrain family, or None.

    Terrain IDs are ASCII. They compare without regard to case, as the plan merge compares them (D7).
    """
    if terrain_id is None:
        return None
    folded = terrain_id.casefold()
    return next((entry for entry in VERIFIED_FAMILIES if entry.family.casefold() == folded), None)


def family_gate(terrain_id: str | None) -> FamilyGate:
    """Return the gate value of a mission from the terrain family of its folder (3.5 capability read)."""
    return FamilyGate(terrain_id, verified_family(terrain_id))


def require_verified_family(terrain_id: str | None, operation: str) -> FamilyGate:
    """Return the gate of a verified family, or raise FamilyNotVerified for a gated operation (8.6 to 8.9).

    Only Apply and Restore original are gated. Any other operation name is a programming error.
    So this function never refuses save, import, export or Inspect.
    """
    if operation not in GATED_OPERATIONS:
        raise LookupError(f"{operation} is not an operation of the family gate")
    gate = family_gate(terrain_id)
    if not gate.verified:
        raise FamilyNotVerified(gate, operation)
    return gate
