"""Rule D9 in isolation: every condition, negated alone, turns the acceptance into a miss."""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import VERIFIED_AT, target_proof  # noqa: E402
from dayz_serverman.application.mod_publication_prestart import PrestartFingerprints  # noqa: E402
from dayz_serverman.domain.content_proofs import TARGET_BASES, ProofDocument  # noqa: E402
from dayz_serverman.domain.mod_publication import (  # noqa: E402
    GroupState, ManagedModSource, PublicationGroup, PublicationIntent, TargetRole,
)
from dayz_serverman.domain.workshop import CacheProof  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402

# Identity of the DayZ root in the intent, and the content digest of the mod
ROOT = "d" * 64
CONTENT = "2" * 64
# Target directory as the profile spells it; the store key is its lower-case form
DIRECTORY = "Mods\\Alpha"


class PrestartRuleTests(unittest.TestCase):
    """The rule accepts only when all inputs of design section 13.5 agree."""

    def setUp(self) -> None:
        """Create one target tree and the inputs that make the rule accept it."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.target = Path(self.temporary.name) / "Mods" / "Alpha"
        (self.target / "Addons").mkdir(parents=True)
        (self.target / "Addons" / "mod.pbo").write_bytes(b"alpha")
        self.fingerprint = tree_metadata_digest(self.target)
        self.proof = CacheProof(
            "111", "a" * 64, CONTENT, 1, 5, VERIFIED_AT, "9", "3" * 64,
            "APPLIED_STATE", self.fingerprint,
        )
        self.record = target_proof(
            content_inventory_digest=CONTENT, target_metadata_digest=self.fingerprint,
            basis="VERIFIED",
        )
        self.group = PublicationGroup(
            TargetRole.MANAGED_MOD_DIRECTORY, 0, DIRECTORY, "stage", "recovery",
            True, CONTENT, CONTENT, GroupState.UNCHANGED_VERIFIED,
        )

    def accepted(self, *, proof=None, record=None, group=None, key=None, target=None,
                 sources=None, load=None) -> bool:
        """Evaluate the rule with one input replaced."""
        targets = {key or (ROOT, DIRECTORY.lower()): record or self.record}
        store = SimpleNamespace(load=load or (lambda: ProofDocument(targets=targets)))
        source = ManagedModSource(
            "111", 0, DIRECTORY, "unused", proof or self.proof, CONTENT, True,
        )
        intent = PublicationIntent(
            "publication-rule", "main", 1, "c" * 64, 1, ROOT,
            (source,) if sources is None else sources, (),
        )
        return PrestartFingerprints(store).rule(intent)(group or self.group, target or self.target)

    def test_all_matching_inputs_are_accepted(self) -> None:
        """The unchanged mod folder with a matching record and proof needs no hash."""
        self.assertTrue(self.accepted())
        # Each write point that hashes the tree gives a basis that the rule accepts
        for basis in sorted(TARGET_BASES):
            self.assertTrue(self.accepted(record=replace(self.record, basis=basis)), basis)
        self.assertEqual(TARGET_BASES, {"COPIED", "HASHED", "PRESTART", "VERIFIED"})

    def test_each_negated_condition_is_a_miss(self) -> None:
        """One changed input at a time; each one alone prevents the acceptance."""
        def failing_load():
            """Stand in for a store read that fails."""
            raise OSError("synthetic read failure")

        cases = {
            "copied group": {"group": replace(self.group, state=GroupState.OUTPUT_VERIFIED)},
            "keys directory": {"group": replace(self.group, role=TargetRole.SERVER_KEYS_DIRECTORY)},
            "no managed source": {"sources": ()},
            "no record": {"key": (ROOT, "mods\\other")},
            "record of another root": {"key": ("e" * 64, DIRECTORY.lower())},
            "record of another item": {"record": replace(self.record, workshop_id="222")},
            "record without a basis": {"record": replace(self.record, basis=None)},
            "record with a legacy basis": {"record": replace(self.record, basis="LEGACY")},
            "record of another manifest id": {
                "record": replace(self.record, installed_manifest_id="7")},
            "proof without a manifest id": {"proof": replace(self.proof, installed_manifest_id=None)},
            "record of another digest": {
                "record": replace(self.record, content_inventory_digest="a" * 64)},
            "group with another digest": {"group": replace(self.group, output_digest="a" * 64)},
            "recorded fingerprint differs": {
                "record": replace(self.record, target_metadata_digest="b" * 64)},
            "reviewed fingerprint differs": {
                "proof": replace(self.proof, target_metadata_digest="b" * 64)},
            "no reviewed fingerprint": {"proof": replace(self.proof, target_metadata_digest=None)},
            "target cannot be fingerprinted": {"target": self.target / "missing"},
            "store read fails": {"load": failing_load},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.assertFalse(self.accepted(**changes))

    def test_log_writes_one_record_and_never_raises(self) -> None:
        """The counts go to one log record; a failing or missing logger changes nothing."""
        records = []
        logger = SimpleNamespace(emit=lambda event, **options: records.append((event, options)))
        counts = {"hashed": 1, "fingerprint_accepted": 2}
        PrestartFingerprints(None, logger).log(counts, "operation-1")
        self.assertEqual(records, [("mod_publication.prestart_check",
                                    {"operation_id": "operation-1", "fields": counts})])

        def failing_emit(_event, **_options):
            """Stand in for a log write that fails."""
            raise OSError("synthetic log failure")

        PrestartFingerprints(None, SimpleNamespace(emit=failing_emit)).log(counts, "operation-2")
        PrestartFingerprints(None).log(counts, "operation-3")


if __name__ == "__main__":
    unittest.main()
