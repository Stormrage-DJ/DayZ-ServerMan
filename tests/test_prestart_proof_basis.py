"""Rule D9 and the basis of a target record: only a record that a full hash wrote stands in for a hash."""
from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_mod_publication_application as fixtures  # noqa: E402
from applied_gate_fixtures import CountingLifecycle, applied_state_gate  # noqa: E402
from content_proof_fixtures import target_proof, write_manifest  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application import mod_publication_start  # noqa: E402
from dayz_serverman.application.content_proof_records import ContentProofRecorder  # noqa: E402
from dayz_serverman.application.mod_publication import (  # noqa: E402
    ModPublicationError, ModPublicationService,
)
from dayz_serverman.application.mod_publication_prestart import PrestartFingerprints  # noqa: E402
from dayz_serverman.application.target_proofs import TargetProofLookup  # noqa: E402
from dayz_serverman.domain.mod_row_state import TargetProof  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402


class ProofBasisTests(fixtures.PublicationApplicationFixture):
    """A legacy record and a record without a basis never pass rule D9; a hashed record does."""

    def setUp(self) -> None:
        """Apply both mods once through a service with a proof store, rule D9 and a lifecycle."""
        super().setUp()
        write_manifest(self.root, {"111": ("1", 1), "222": ("2", 1)})
        self.store = ContentProofStore(self.paths.content_proofs)
        self.legacy = AppliedModStateRepository(self.paths.applied_mod_state)
        self.lifecycle = CountingLifecycle()
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            self.lifecycle, ContentProofRecorder(self.store), PrestartFingerprints(self.store),
        )
        self.identity = dayz_root_identity(self.dayz)
        self.keys = [(self.identity, "mods\\alpha"), (self.identity, "mods\\beta")]
        self.alpha = self.dayz / "mods/alpha/Addons/111.pbo"
        self.hashed: list[str] = []
        self.publish(self._gate(complete=True), "first")

    def publish(self, gate_id: str, name: str) -> dict[str, object]:
        """Publish the gate and record every full hash of the pre-start check."""
        preview = self.service.preview(self.request(gate_id))
        original = mod_publication_start.inventory_tree

        def recording(path, *arguments):
            """Record the folder, then hash the tree."""
            self.hashed.append(path.name)
            return original(path, *arguments)

        self.hashed.clear()
        with patch.object(mod_publication_start, "inventory_tree", side_effect=recording):
            return self.service.publish(
                self.request(gate_id), preview["publication_fingerprint"],
                fixtures._SyntheticContext(name))

    def tamper(self) -> None:
        """Change content, size and modification time of one applied mod file."""
        self.alpha.write_bytes(b"TAMPERED-CONTENT-OF-ANOTHER-SIZE")
        state = self.alpha.stat()
        os.utime(self.alpha, ns=(state.st_atime_ns, state.st_mtime_ns + 7_000_000_000))

    def make_legacy_only(self) -> None:
        """Write the legacy file as the earlier version did after a publication, and drop the store."""
        intent = self.service._rebuild(self.request(self._gate(complete=True)), "publication-legacy")
        self.legacy.record(intent, self.dayz)
        self.paths.content_proofs.unlink()

    def bases(self) -> list[str | None]:
        """Return the basis of the stored target record of alpha and beta; "none" without a record."""
        targets = self.store.load().targets
        return [targets[key].basis if key in targets else "none" for key in self.keys]

    def start(self, name: str, legacy: bool = False) -> dict[str, object]:
        """Run one start-requested publication from the proofs that the resolver gives now."""
        gate = applied_state_gate(self, self.store, True, legacy=self.legacy if legacy else None)
        return self.publish(gate, name)

    def test_copied_groups_are_recorded_with_their_basis(self) -> None:
        """The publication that copied both folders names the copy as the basis."""
        self.assertEqual(self.bases(), ["COPIED", "COPIED"])
        result = self.start("accept")
        self.assertEqual((self.hashed, result["prestart_check"]),
                         (["keys"], {"hashed": 1, "fingerprint_accepted": 2}))

    def test_legacy_only_targets_are_hashed_at_the_first_start_and_accepted_at_the_second(self) -> None:
        """A legacy hit proves the resolution, seeds no target record, and never passes rule D9."""
        self.make_legacy_only()
        first = self.start("first-start", legacy=True)
        self.assertEqual(self.hashed, ["alpha", "beta", "keys"])
        self.assertEqual(first["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})
        self.assertEqual((first["start_state"], self.lifecycle.calls), ("STARTED", 1))
        # The check hashed both trees between two equal fingerprints and recorded that
        self.assertEqual(self.bases(), ["PRESTART", "PRESTART"])
        record = self.store.load().targets[self.keys[0]]
        self.assertEqual(record.target_metadata_digest, tree_metadata_digest(self.dayz / "mods/alpha"))
        second = self.start("second-start", legacy=True)
        self.assertEqual(self.hashed, ["keys"])
        self.assertEqual(second["prestart_check"], {"hashed": 1, "fingerprint_accepted": 2})
        self.assertEqual((second["start_state"], self.lifecycle.calls), ("STARTED", 2))

    def test_legacy_only_resolution_and_plain_apply_write_no_target_record(self) -> None:
        """The legacy proof serves the update and the apply as before; the row rule still shows "applied"."""
        self.make_legacy_only()
        gate = applied_state_gate(self, self.store, False, legacy=self.legacy)
        self.assertEqual(set(self.store.load().sources), {"111", "222"})
        self.assertEqual(self.bases(), ["none", "none"])
        preview = self.service.preview(self.request(gate))
        self.assertEqual([target["current"] for target in preview["targets"]], [True, True])
        result = self.publish(gate, "plain")
        self.assertEqual((result["publication_state"], self.hashed), ("VERIFIED", []))
        self.assertEqual(self.bases(), ["none", "none"])
        rows = TargetProofLookup([self.store, self.legacy]).resolve(
            str(self.dayz), [("mods\\alpha", "111", "1")])
        self.assertEqual(rows, {"mods\\alpha": TargetProof.PROVEN})

    def test_legacy_only_target_that_was_changed_before_the_seeding_cannot_start(self) -> None:
        """A legacy fingerprint that follows a changed tree is never trusted: full hash, start refused."""
        self.tamper()
        self.make_legacy_only()
        gate = applied_state_gate(self, self.store, True, legacy=self.legacy)
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, "refused")
        self.assertEqual((raised.exception.code, self.lifecycle.calls),
                         ("PUBLICATION_VERIFICATION_FAILED", 0))
        self.assertEqual(self.hashed, ["alpha"])
        # A failed check records nothing
        self.assertEqual(self.bases(), ["none", "none"])

    def strip_bases(self) -> None:
        """Rewrite both target records without a basis, as an earlier version stored them."""
        targets = self.store.load().targets
        self.assertTrue(self.store.record(
            targets={key: replace(targets[key], basis=None) for key in self.keys}))
        raw = json.loads(self.paths.content_proofs.read_text(encoding="utf-8"))
        self.assertNotIn("basis", raw["targets"][self.identity]["mods\\alpha"])

    def test_record_without_a_basis_is_hashed_once_and_then_carries_one(self) -> None:
        """An existing store keeps its records for the update decision; rule D9 asks for one hash."""
        self.strip_bases()
        first = self.start("first-start")
        self.assertEqual(self.hashed, ["alpha", "beta", "keys"])
        self.assertEqual(first["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})
        self.assertEqual((first["start_state"], self.bases()), ("STARTED", ["PRESTART", "PRESTART"]))
        second = self.start("second-start")
        self.assertEqual((self.hashed, second["prestart_check"]),
                         (["keys"], {"hashed": 1, "fingerprint_accepted": 2}))

    def test_record_without_a_basis_over_a_changed_tree_cannot_start(self) -> None:
        """A record without a basis that follows a changed tree: full hash, start refused."""
        self.tamper()
        targets = self.store.load().targets
        self.store.record(targets={self.keys[0]: replace(
            targets[self.keys[0]], basis=None,
            target_metadata_digest=tree_metadata_digest(self.dayz / "mods/alpha"))})
        gate = applied_state_gate(self, self.store, True)
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, "refused")
        self.assertEqual((raised.exception.code, self.lifecycle.calls, self.hashed),
                         ("PUBLICATION_VERIFICATION_FAILED", 0, ["alpha"]))
        self.assertEqual(self.bases(), [None, "COPIED"])

    def test_group_that_passed_gets_no_record_when_a_later_group_fails(self) -> None:
        """QF-024: the check records nothing before every group passed."""
        self.strip_bases()
        beta = self.dayz / "mods/beta/Addons/222.pbo"
        gate = applied_state_gate(self, self.store, True)
        # Change the content of the second group while size and time stay the same
        state = beta.stat()
        beta.write_bytes(b"X" * state.st_size)
        os.utime(beta, ns=(state.st_atime_ns, state.st_mtime_ns))
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, "second-fails")
        self.assertEqual((raised.exception.code, self.lifecycle.calls),
                         ("PUBLICATION_VERIFICATION_FAILED", 0))
        # The first group was hashed and matched; it still has no basis
        self.assertEqual(self.hashed, ["alpha", "beta"])
        self.assertEqual(self.bases(), [None, None])

    def test_tree_that_moves_during_the_prestart_hash_gets_no_record(self) -> None:
        """The pre-start record needs the same fingerprint before and after its hash."""
        self.strip_bases()
        gate = applied_state_gate(self, self.store, True)
        preview = self.service.preview(self.request(gate))
        original = mod_publication_start.inventory_tree

        def hash_then_touch(path, *arguments):
            """Hash the tree, then move a file time of alpha."""
            digest = original(path, *arguments)
            if path.name == "alpha":
                state = self.alpha.stat()
                os.utime(self.alpha, ns=(state.st_atime_ns, state.st_mtime_ns + 3_000_000_000))
            return digest

        with patch.object(mod_publication_start, "inventory_tree", side_effect=hash_then_touch):
            result = self.service.publish(self.request(gate), preview["publication_fingerprint"],
                                          fixtures._SyntheticContext("moving"))
        self.assertEqual(result["start_state"], "STARTED")
        self.assertEqual(self.bases(), [None, "PRESTART"])

    def test_basis_is_an_additive_field_of_schema_1(self) -> None:
        """A record without the field stays readable; an unknown basis is a malformed record."""
        plain, hashed = target_proof(), target_proof(basis="HASHED")
        self.assertEqual(set(hashed.to_dict()) - set(plain.to_dict()), {"basis"})
        self.assertEqual((plain.justified, hashed.justified), (False, True))
        store = ContentProofStore(self.root / "basis" / "content-proofs.json")
        store.record(targets={(self.identity, "@a"): plain, (self.identity, "@b"): hashed,
                              (self.identity, "@c"): target_proof(basis="LEGACY")})
        document = store.load()
        self.assertEqual(document.targets, {(self.identity, "@a"): plain, (self.identity, "@b"): hashed})
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
