"""Publication with the proof store: records written, legacy file untouched, STORED_SOURCE gate."""
from __future__ import annotations

import json
import sys
import unicodedata
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_mod_publication_application as fixtures  # noqa: E402
from content_proof_fixtures import current, profile, write_manifest  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application.content_proof_records import ContentProofRecorder  # noqa: E402
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.application.mod_publication import (  # noqa: E402
    ModPublicationError, ModPublicationService,
)
from dayz_serverman.application.publication_identity import (  # noqa: E402
    authentication_identity_digest,
)
from dayz_serverman.application.target_proofs import TargetProofLookup  # noqa: E402
from dayz_serverman.domain.mod_row_state import TargetProof  # noqa: E402
from dayz_serverman.domain.workshop import (  # noqa: E402
    AuthenticationMode, CacheProof, derive_required_items,
)
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402

# Patch target of the full content hash of one Workshop item
FULL_HASH = "dayz_serverman.repositories.workshop_cache.WorkshopCacheVerifier._inventory"
# Bytes of a legacy file that a publication must leave alone
LEGACY_BYTES = b'{"profiles": {}, "schema_version": 1}\n'


class ProofStorePublicationTests(fixtures.PublicationApplicationFixture):
    """A committed publication writes the proof store and never the legacy file."""

    def setUp(self) -> None:
        """Wire the publication service to a proof store and add the details block."""
        super().setUp()
        # Add the latest-details block, then take the gate from the rewritten manifest
        write_manifest(self.root, {"111": ("1", 1), "222": ("2", 1)})
        self.gate_id = self._gate(complete=True)
        self.verifier = WorkshopCacheVerifier(self.cache)
        self.store = ContentProofStore(self.paths.content_proofs)
        self.legacy = AppliedModStateRepository(self.paths.applied_mod_state)
        self.resolver = ContentProofResolver(self.store, self.legacy)
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            fixtures._NoStartLifecycle(), ContentProofRecorder(self.store),
        )
        self.identity = dayz_root_identity(self.dayz)

    def gate_with(self, proofs: dict[str, CacheProof]) -> str:
        """Submit a succeeded update gate that carries the given proofs."""
        items = [{"item": item.to_dict(), "outcome": "VERIFIED_CURRENT",
                  "cache_proof": proofs[item.workshop_id].to_dict(), "error_code": None}
                 for item in derive_required_items(self.profile)]
        result = {
            "profile_id": "main", "profile_revision": self.profile.revision,
            "semantic_profile_digest": self.profile.semantic_digest,
            "settings_revision": self.settings_revision,
            "authentication_identity_digest": authentication_identity_digest(
                AuthenticationMode.ACCOUNT, "Operator"),
            "download_state": "VERIFIED", "items": items,
            "publication_state": "PENDING_PHASE_6_2", "start_authorized": False,
            "start_requested": False, "start_error": None, "process_id": 77,
            "steamcmd_exit_code": 0, "steamcmd_summary": None,
        }
        record = self.operations.submit("UPDATE_WORKSHOP_ITEMS", lambda _context: result)
        return self.wait(record.operation_id).operation_id

    def publish(self, gate_id: str | None = None, name: str = "publish") -> dict[str, object]:
        """Preview and publish the gate; return the publication result."""
        preview = self.service.preview(self.request(gate_id))
        return self.service.publish(
            self.request(gate_id), preview["publication_fingerprint"],
            fixtures._SyntheticContext(name),
        )

    def resolve(self, chosen=None) -> dict[str, CacheProof]:
        """Resolve both items for a profile with the full hash patched to fail."""
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            return self.resolver.resolve(
                self.settings.load(), chosen or self.profile,
                derive_required_items(self.profile), current("111", "222"), self.verifier,
            )

    def test_publication_writes_both_records_and_leaves_the_legacy_file_unchanged(self) -> None:
        """Source and target records land in the store; the legacy bytes stay as they were."""
        self.paths.applied_mod_state.write_bytes(LEGACY_BYTES)
        self.assertEqual(self.publish()["publication_state"], "VERIFIED")
        self.assertEqual(self.paths.applied_mod_state.read_bytes(), LEGACY_BYTES)
        document = self.store.load()
        self.assertEqual(set(document.sources), {"111", "222"})
        self.assertEqual(set(document.targets),
                         {(self.identity, "mods\\alpha"), (self.identity, "mods\\beta")})
        full = self.verifier.verify("111")
        source, target = document.sources["111"], document.targets[(self.identity, "mods\\alpha")]
        self.assertEqual(source.content_inventory_digest, full.content_inventory_digest)
        self.assertEqual(source.metadata_inventory_digest, full.metadata_inventory_digest)
        self.assertEqual(source.cache_root_identity, self.verifier.root_identity())
        self.assertEqual((target.workshop_id, target.installed_manifest_id), ("111", "1"))
        self.assertEqual(target.content_inventory_digest, full.content_inventory_digest)
        # The target fingerprint was measured after the publication
        self.assertEqual(target.target_metadata_digest,
                         tree_metadata_digest(self.dayz / "mods" / "alpha"))

    def test_publication_does_not_create_the_legacy_file(self) -> None:
        """Without a legacy file the publication creates none."""
        self.publish()
        self.assertFalse(self.paths.applied_mod_state.exists())
        self.assertTrue(self.paths.content_proofs.is_file())

    def test_published_proofs_serve_every_profile_and_the_row_rule(self) -> None:
        """After one publication each profile resolves APPLIED_STATE without a hash."""
        self.publish()
        other = profile({"MODS\\ALPHA": "111", "mods\\beta": "222"},
                        profile_id="second", digest="e" * 64)
        for chosen in (None, other):
            proofs = self.resolve(chosen)
            self.assertEqual({key: value.verification_kind for key, value in proofs.items()},
                             {"111": "APPLIED_STATE", "222": "APPLIED_STATE"})
        # The store is the first target-record source of the row rule
        rows = TargetProofLookup([self.store, self.legacy]).resolve(
            str(self.dayz), [("mods\\alpha", "111", "1"), ("mods\\beta", "222", "2"),
                             ("mods\\alpha", "111", "7")])
        self.assertEqual(rows["mods\\beta"], TargetProof.PROVEN)
        self.assertEqual(
            TargetProofLookup([self.store]).resolve(
                str(self.dayz), [("Mods\\Alpha", "111", "1")]),
            {"Mods\\Alpha": TargetProof.PROVEN},
        )
        # A second publication from the stored proofs keeps the records valid
        self.assertEqual(self.publish(self.gate_with(self.resolve()), "again")
                         ["publication_state"], "VERIFIED")
        self.assertEqual(self.resolve()["111"].verification_kind, "APPLIED_STATE")

    def test_stored_source_gate_is_accepted_and_publishes(self) -> None:
        """A STORED_SOURCE proof passes the gate; staging then verifies the target itself."""
        stored = {key: replace(self.verifier.verify(key), verification_kind="STORED_SOURCE")
                  for key in ("111", "222")}
        result = self.publish(self.gate_with(stored))
        self.assertEqual(result["publication_state"], "VERIFIED")
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), b"alpha")
        self.assertEqual(self.resolve()["111"].verification_kind, "APPLIED_STATE")

    def test_stored_source_with_a_target_digest_is_refused(self) -> None:
        """That kind requires a null target digest, at the gate before any storage."""
        stored = {key: replace(self.verifier.verify(key), verification_kind="STORED_SOURCE")
                  for key in ("111", "222")}
        stored["222"] = replace(stored["222"], target_metadata_digest="a" * 64)
        with self.assertRaises(ModPublicationError) as raised:
            self.service.preview(self.request(self.gate_with(stored)))
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertIn("Stored source proof", str(raised.exception))
        self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])

    def test_row_rule_and_legacy_records_meet_by_letter_case_only(self) -> None:
        """A legacy record proves its own directory in any letter case, and no other name."""
        composed = "@Mód"
        decomposed = unicodedata.normalize("NFD", composed)
        for name in (composed, decomposed, "@Strasse", "@Straße"):
            (self.dayz / name).mkdir()
        self.paths.applied_mod_state.write_text(json.dumps({
            "schema_version": 1, "profiles": {"old": {"mods": {
                "111": {"dayz_root_identity": self.identity, "target_relative": decomposed,
                        "installed_manifest_id": "1"},
                "222": {"dayz_root_identity": self.identity, "target_relative": "@Strasse",
                        "installed_manifest_id": "1"}}}}}), encoding="utf-8")
        self.assertEqual(self.legacy.target_records(), {
            (self.identity, decomposed.lower(), "111", "1"),
            (self.identity, "@strasse", "222", "1")})
        lookup = TargetProofLookup([self.store, self.legacy])
        expected = {
            (decomposed, "111"): TargetProof.PROVEN,
            (decomposed.upper(), "111"): TargetProof.PROVEN,
            ("@STRASSE", "222"): TargetProof.PROVEN,
            # Another NTFS directory is never proven by the record of its look-alike
            (composed, "111"): TargetProof.UNPROVEN,
            ("@Straße", "222"): TargetProof.UNPROVEN,
        }
        for (spelling, workshop_id), proof in expected.items():
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    lookup.resolve(str(self.dayz), [(spelling, workshop_id, "1")]),
                    {spelling: proof})


if __name__ == "__main__":
    unittest.main()
