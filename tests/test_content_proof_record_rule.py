"""Target proof records after a publication: a fingerprint is stored only when a hash justifies it."""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_mod_publication_application as fixtures  # noqa: E402
from applied_gate_fixtures import CountingLifecycle, applied_state_gate  # noqa: E402
from content_proof_fixtures import current, target_proof, write_manifest  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application import content_proof_records, mod_publication_start  # noqa: E402
from dayz_serverman.application.content_proof_records import ContentProofRecorder  # noqa: E402
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.application.mod_publication import (  # noqa: E402
    ModPublicationError, ModPublicationService,
)
from dayz_serverman.application.mod_publication_prestart import PrestartFingerprints  # noqa: E402
from dayz_serverman.domain.workshop import derive_required_items  # noqa: E402
from dayz_serverman.repositories import mod_publication_stage, mod_publication_staging  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402

# The three places that hash a server-folder tree in full
HASHERS = {"staging": mod_publication_staging, "records": content_proof_records,
           "prestart": mod_publication_start}


class _Context(fixtures._SyntheticContext):
    """Synthetic context that runs an action at the n-th occurrence of one phase."""

    def __init__(self, name: str, phase: str | None = None, occurrence: int = 1, action=None) -> None:
        """Store the phase, its occurrence and the action."""
        super().__init__(name)
        self._phase, self._occurrence, self._action = phase, occurrence, action
        self._seen = 0

    def checkpoint(self, phase: str, _percent: int) -> None:
        """Run the action when the phase occurs for the configured time."""
        if phase == self._phase:
            self._seen += 1
            if self._seen == self._occurrence and self._action is not None:
                self._action()


class TargetRecordRuleTests(fixtures.PublicationApplicationFixture):
    """A folder that changed after its check never becomes proven by a record write."""

    def setUp(self) -> None:
        """Wire the service to a proof store, rule D9 and a counting lifecycle."""
        super().setUp()
        write_manifest(self.root, {"111": ("1", 1), "222": ("2", 1)})
        self.store = ContentProofStore(self.paths.content_proofs)
        self.lifecycle = CountingLifecycle()
        # Optional fault hook of the storage: called with each publication phase
        self.fault = None
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(
                fault_hook=lambda phase, index: self.fault(phase) if self.fault else None,
                checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            self.lifecycle, ContentProofRecorder(self.store), PrestartFingerprints(self.store),
        )
        self.alpha = self.dayz / "mods/alpha/Addons/111.pbo"
        self.alpha_key = (dayz_root_identity(self.dayz), "mods\\alpha")
        self.beta_key = (dayz_root_identity(self.dayz), "mods\\beta")
        # Full hashes per place, as (place, folder name)
        self.hashed: list[tuple[str, str]] = []

    def publish(self, gate_id: str, context: _Context) -> dict[str, object]:
        """Preview and publish the gate while every full hash of a tree is recorded."""
        preview = self.service.preview(self.request(gate_id))
        with ExitStack() as stack:
            for place, module in HASHERS.items():
                original = module.inventory_tree

                def recording(path, *arguments, _place=place, _original=original):
                    """Record the place and the folder, then hash the tree."""
                    self.hashed.append((_place, path.name))
                    return _original(path, *arguments)

                stack.enter_context(patch.object(module, "inventory_tree", side_effect=recording))
            return self.service.publish(
                self.request(gate_id), preview["publication_fingerprint"], context)

    def tamper(self) -> None:
        """Change content, size and modification time of one applied mod file."""
        self.alpha.write_bytes(b"TAMPERED-CONTENT-OF-ANOTHER-SIZE")
        state = self.alpha.stat()
        os.utime(self.alpha, ns=(state.st_atime_ns, state.st_mtime_ns + 7_000_000_000))

    def touch(self) -> None:
        """Move only the modification time of the alpha file."""
        state = self.alpha.stat()
        os.utime(self.alpha, ns=(state.st_atime_ns, state.st_mtime_ns + 3_000_000_000))

    def kinds(self) -> dict[str, str]:
        """Return the proof kind that the resolver gives each item now."""
        items = derive_required_items(self.profile)
        proofs = ContentProofResolver(self.store).resolve(
            self.settings.load(), self.profile, items,
            current(*(item.workshop_id for item in items)), WorkshopCacheVerifier(self.cache))
        return {key: proof.verification_kind for key, proof in proofs.items()}

    def assert_next_start_hashes_and_replaces_alpha(self) -> None:
        """Assert that the next start run hashes the changed tree and starts with proven content."""
        self.assertEqual(self.kinds(), {"111": "STORED_SOURCE", "222": "APPLIED_STATE"})
        gate = applied_state_gate(self, self.store, True, require_applied=False)
        self.hashed.clear()
        result = self.publish(gate, _Context("next"))
        # The changed tree is hashed in full at staging, replaced, and hashed again before the start
        self.assertIn(("staging", "alpha"), self.hashed)
        self.assertIn(("prestart", "alpha"), self.hashed)
        self.assertEqual(self.alpha.read_bytes(), b"alpha")
        self.assertEqual((result["start_state"], self.lifecycle.calls), ("STARTED", 1))
        self.assertEqual(result["prestart_check"], {"hashed": 2, "fingerprint_accepted": 1})

    def test_plain_apply_does_not_prove_a_folder_that_changed_after_its_check(self) -> None:
        """QF-017, path 1: the changed folder gets no record, and no later start trusts it."""
        self.publish(self._gate(complete=True), _Context("first"))
        # A reviewed start plan from before the change
        earlier = applied_state_gate(self, self.store, True)
        plain = applied_state_gate(self, self.store, False)
        # The second staging step is beta: the fingerprint check of alpha has passed
        result = self.publish(plain, _Context("second", "STAGE_TARGET", 2, self.tamper))
        self.assertEqual(result["publication_state"], "VERIFIED")
        targets = self.store.load().targets
        self.assertNotIn(self.alpha_key, targets)
        self.assertIn(self.beta_key, targets)
        # The earlier plan names the old fingerprint: the run is refused, and nothing starts
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(earlier, _Context("stale"))
        self.assertEqual((raised.exception.code, self.lifecycle.calls),
                         ("PUBLICATION_PREVIEW_STALE", 0))
        self.assertEqual(self.alpha.read_bytes(), b"TAMPERED-CONTENT-OF-ANOTHER-SIZE")
        self.assert_next_start_hashes_and_replaces_alpha()

    def test_refused_start_run_leaves_no_record_of_the_changed_folder(self) -> None:
        """QF-017, path 2: the run that detects the change refuses and stores no proof of it."""
        self.publish(self._gate(complete=True), _Context("first"))
        gate = applied_state_gate(self, self.store, True)
        self.hashed.clear()
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, _Context("second", "STAGE_TARGET", 2, self.tamper))
        self.assertEqual((raised.exception.code, self.lifecycle.calls),
                         ("PUBLICATION_VERIFICATION_FAILED", 0))
        # The pre-start check hashed the changed tree in full
        self.assertIn(("prestart", "alpha"), self.hashed)
        self.assertNotIn(self.alpha_key, self.store.load().targets)
        self.assert_next_start_hashes_and_replaces_alpha()

    def test_changed_time_alone_also_drops_the_record(self) -> None:
        """An unchanged target whose fingerprint moved is no longer the reviewed tree."""
        self.publish(self._gate(complete=True), _Context("first"))
        plain = applied_state_gate(self, self.store, False)
        self.publish(plain, _Context("second", "STAGE_TARGET", 2, self.touch))
        self.assertNotIn(self.alpha_key, self.store.load().targets)
        self.assertEqual(self.kinds()["111"], "STORED_SOURCE")

    def test_unchanged_target_keeps_the_reviewed_fingerprint(self) -> None:
        """Without a change the record holds the fingerprint of the proof; no mod folder is hashed."""
        self.publish(self._gate(complete=True), _Context("first"))
        before = self.store.load().targets[self.alpha_key]
        self.hashed.clear()
        self.publish(applied_state_gate(self, self.store, False), _Context("second"))
        self.assertEqual(self.hashed, [("staging", "keys")])
        after = self.store.load().targets[self.alpha_key]
        self.assertEqual(after.target_metadata_digest, before.target_metadata_digest)
        self.assertEqual(after.content_inventory_digest, before.content_inventory_digest)

    def test_copied_target_is_recorded_without_another_hash(self) -> None:
        """A copied group was hashed by the publication; the recorder compares fingerprints only."""
        self.publish(self._gate(complete=True), _Context("first"))
        self.assertNotIn("records", {place for place, _name in self.hashed})
        record = self.store.load().targets[self.alpha_key]
        self.assertEqual(record.basis, "COPIED")
        self.assertEqual(record.target_metadata_digest,
                         tree_metadata_digest(self.dayz / "mods/alpha"))

    def assert_copied_change_is_never_proven(self, phase: str, start: bool) -> None:
        """Change a copied folder after its last hash, at the given phase; no record may follow."""
        # A stored record of the folder must also go when the tree is not the hashed one
        self.store.record(targets={self.alpha_key: target_proof(basis="VERIFIED")})
        self.fault = lambda name: self.tamper() if name == phase else None
        gate = self._gate(complete=True, start_requested=start)
        if start:
            # The run that meets the change hashes the copied folder and refuses the start
            with self.assertRaises(ModPublicationError) as raised:
                self.publish(gate, _Context("first"))
            self.assertEqual((raised.exception.code, self.lifecycle.calls),
                             ("PUBLICATION_VERIFICATION_FAILED", 0))
            self.assertIn(("prestart", "alpha"), self.hashed)
        else:
            self.assertEqual(self.publish(gate, _Context("first"))["publication_state"], "VERIFIED")
        self.fault = None
        self.assertEqual(self.alpha.read_bytes(), b"TAMPERED-CONTENT-OF-ANOTHER-SIZE")
        targets = self.store.load().targets
        self.assertNotIn(self.alpha_key, targets)
        self.assertEqual(targets[self.beta_key].basis, "COPIED")
        self.assert_next_start_hashes_and_replaces_alpha()

    def test_copied_folder_changed_after_cleanup_in_a_plain_apply_gets_no_record(self) -> None:
        """QF-017, copied group: a change after the post-commit hash is not proven (plain apply)."""
        self.assert_copied_change_is_never_proven("AFTER_CLEANUP", False)

    def test_copied_folder_changed_after_retirement_in_a_plain_apply_gets_no_record(self) -> None:
        """QF-017, copied group: a change after the journal retired is not proven (plain apply)."""
        self.assert_copied_change_is_never_proven("AFTER_RETIREMENT", False)

    def test_copied_folder_changed_after_cleanup_in_a_start_run_gets_no_record(self) -> None:
        """QF-017, copied group: the start run refuses and leaves no record of the change."""
        self.assert_copied_change_is_never_proven("AFTER_CLEANUP", True)

    def test_copied_folder_changed_after_retirement_in_a_start_run_gets_no_record(self) -> None:
        """QF-017, copied group: the same for a change after the journal retired."""
        self.assert_copied_change_is_never_proven("AFTER_RETIREMENT", True)

    def test_copied_folder_that_moves_during_its_post_commit_hash_gets_no_record(self) -> None:
        """QF-025: the kept fingerprint is the one from before the post-commit hash."""
        original, calls = mod_publication_stage.inventory_tree, []

        def hash_then_touch(path, *arguments):
            """Hash the tree; after the second hash of alpha, the post-commit one, move a file time."""
            digest = original(path, *arguments)
            calls.append(path.name)
            if calls.count("alpha") == 2 and path.name == "alpha":
                self.touch()
            return digest

        gate = self._gate(complete=True)
        preview = self.service.preview(self.request(gate))
        with patch.object(mod_publication_stage, "inventory_tree", side_effect=hash_then_touch):
            self.service.publish(self.request(gate), preview["publication_fingerprint"], _Context("moving"))
        self.assertEqual(calls.count("alpha"), 2)
        targets = self.store.load().targets
        self.assertNotIn(self.alpha_key, targets)
        self.assertEqual(targets[self.beta_key].basis, "COPIED")

    def test_copied_folder_without_a_kept_fingerprint_is_hashed_by_the_recorder(self) -> None:
        """A copy whose fingerprint could not be measured before its hash gets the recorder's bracket."""
        with patch.object(mod_publication_stage, "measured_fingerprint", return_value=None):
            self.publish(self._gate(complete=True), _Context("first"))
        self.assertEqual([name for place, name in self.hashed if place == "records"], ["alpha", "beta"])
        self.assertEqual(self.store.load().targets[self.alpha_key].basis, "HASHED")

    def test_target_found_equal_by_staging_is_recorded_only_after_its_own_hash(self) -> None:
        """Audit: an equal folder without a reviewed fingerprint is hashed again for its record."""
        self.publish(self._gate(complete=True), _Context("first"))
        self.store.record(remove_targets=[self.alpha_key, self.beta_key])
        self.hashed.clear()
        # A full-content gate names no target fingerprint; staging finds both folders equal
        self.publish(self._gate(complete=True), _Context("equal"))
        self.assertEqual([name for place, name in self.hashed if place == "records"],
                         ["alpha", "beta"])
        self.assertEqual(self.kinds(), {"111": "APPLIED_STATE", "222": "APPLIED_STATE"})

    def test_target_changed_after_the_staging_hash_gets_no_record(self) -> None:
        """Audit: a change between the staging hash and the record write is found by the hash."""
        self.publish(self._gate(complete=True), _Context("first"))
        self.publish(self._gate(complete=True),
                     _Context("equal", "STAGE_TARGET", 2, self.tamper))
        self.assertNotIn(self.alpha_key, self.store.load().targets)
        self.assertIn(self.beta_key, self.store.load().targets)
        self.assert_next_start_hashes_and_replaces_alpha()

    def test_target_that_moves_during_the_record_hash_gets_no_record(self) -> None:
        """Audit: the fingerprint must be the same before and after the record hash."""
        self.publish(self._gate(complete=True), _Context("first"))
        original = content_proof_records.inventory_tree

        def hash_then_touch(path, *arguments):
            """Hash the tree, then move a file time before the second fingerprint."""
            digest = original(path, *arguments)
            if path.name == "alpha":
                self.touch()
            return digest

        gate = self._gate(complete=True)
        preview = self.service.preview(self.request(gate))
        with patch.object(content_proof_records, "inventory_tree", side_effect=hash_then_touch):
            self.service.publish(self.request(gate), preview["publication_fingerprint"],
                                 _Context("moving"))
        self.assertNotIn(self.alpha_key, self.store.load().targets)
        self.assertIn(self.beta_key, self.store.load().targets)


if __name__ == "__main__":
    unittest.main()
