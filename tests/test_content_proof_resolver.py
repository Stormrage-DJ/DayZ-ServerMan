"""Content proof resolver: validity rules, result order, legacy seeding and error handling."""
from __future__ import annotations

import os
import sys
import tempfile
import unicodedata
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import (  # noqa: E402
    build_cache, build_target, current, profile, required, write_manifest,
)
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application.content_proof_records import (  # noqa: E402
    ContentProofRecorder,
)
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.domain.content_proofs import TargetProofRecord  # noqa: E402
from dayz_serverman.domain.mod_publication import (  # noqa: E402
    ManagedModSource, PublicationIntent,
)
from dayz_serverman.domain.workshop import ItemOutcome  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402


class ContentProofResolverTests(unittest.TestCase):
    """Resolver results 1 to 4 for one cached item and its server-folder copy."""

    def setUp(self) -> None:
        """Create a cache item, an equal server-folder copy and an empty store."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = build_cache(self.base, {"111": b"alpha"})
        self.dayz = self.base / "DayZ Server"
        self.target = build_target(self.dayz, "@Alpha", b"alpha")
        self.settings = SimpleNamespace(dayz_root=str(self.dayz))
        self.profile = profile({"@Alpha": "111"})
        self.verifier = WorkshopCacheVerifier(self.cache)
        self.store = ContentProofStore(self.base / "data" / "content-proofs.json")
        self.legacy_path = self.base / "data" / "applied-mod-state.json"
        self.resolver = ContentProofResolver(
            self.store, AppliedModStateRepository(self.legacy_path))
        self.root_identity = dayz_root_identity(self.dayz)

    def resolve(self, chosen=None, outcomes=None):
        """Resolve item 111 for the given profile and return its proof or None."""
        return self.resolver.resolve(
            self.settings, chosen or self.profile, required("111"),
            outcomes or current("111"), self.verifier,
        ).get("111")

    def record_source(self):
        """Hash the item fully and record its source proof; return the full proof."""
        proof = self.verifier.verify("111")
        self.resolver.record_full_hash(self.verifier, proof)
        return proof

    def record_target(self, proof, directory: str = "@Alpha", **changes) -> None:
        """Record a target proof for the current copy with optional field changes."""
        values = {
            "workshop_id": "111", "installed_manifest_id": proof.installed_manifest_id,
            "content_inventory_digest": proof.content_inventory_digest,
            "target_metadata_digest": tree_metadata_digest(self.target),
            "verified_at": proof.verified_at,
        }
        self.store.record(targets={
            (self.root_identity, directory): TargetProofRecord(**{**values, **changes}),
        })

    def test_no_record_and_no_legacy_hit_gives_no_stored_proof(self) -> None:
        """Result 4: nothing stored means the caller hashes the tree."""
        self.assertIsNone(self.resolve())
        self.assertFalse(self.store.load().sources)

    def test_valid_source_only_gives_stored_source_with_null_target_digest(self) -> None:
        """Result 2: a valid source record without a target record."""
        full = self.record_source()
        with patch.object(self.verifier, "verify", side_effect=AssertionError("no hash")):
            proof = self.resolve()
        self.assertEqual(proof.verification_kind, "STORED_SOURCE")
        self.assertIsNone(proof.target_metadata_digest)
        self.assertEqual(replace(proof, verification_kind="FULL_CONTENT"), full)
        self.assertEqual(self.store.load().sources["111"].time_updated, 100)

    def test_valid_source_and_target_give_applied_state(self) -> None:
        """Result 1: both records valid; the proof carries the target fingerprint."""
        full = self.record_source()
        self.record_target(full)
        proof = self.resolve()
        self.assertEqual(proof.verification_kind, "APPLIED_STATE")
        self.assertEqual(proof.target_metadata_digest, tree_metadata_digest(self.target))
        self.assertEqual(proof.content_inventory_digest, full.content_inventory_digest)
        # Another profile id and another profile digest reuse the same proof
        other = profile({"@alpha": "111"}, profile_id="second", digest="e" * 64)
        self.assertEqual(self.resolve(other), proof)

    def test_each_source_validity_rule_misses_when_broken(self) -> None:
        """Another cache root identity, manifest id or fingerprint invalidates the source."""
        self.record_source()
        record = self.store.load().sources["111"]
        for name, changed in (
            ("cache-root", replace(record, cache_root_identity="0" * 64)),
            ("manifest-id", replace(record, installed_manifest_id="8")),
            ("fingerprint", replace(record, metadata_inventory_digest="0" * 64)),
        ):
            with self.subTest(name=name):
                self.store.record(sources={"111": changed})
                self.assertIsNone(self.resolve())
        self.store.record(sources={"111": record})
        self.assertIsNotNone(self.resolve())
        # A changed installed manifest id or a changed tree misses as well
        write_manifest(self.base, {"111": ("10", 200)})
        self.assertIsNone(self.resolve())
        write_manifest(self.base, {"111": ("9", 100)})
        (self.cache / "111" / "Addons" / "mod.pbo").write_bytes(b"other")
        self.assertIsNone(self.resolve())

    def test_each_target_validity_rule_falls_back_to_stored_source(self) -> None:
        """A target record that does not match leaves a source-only proof."""
        full = self.record_source()
        for name, changes in (
            ("workshop-id", {"workshop_id": "222"}),
            ("manifest-id", {"installed_manifest_id": "8"}),
            ("content-digest", {"content_inventory_digest": "0" * 64}),
            ("fingerprint", {"target_metadata_digest": "0" * 64}),
        ):
            with self.subTest(name=name):
                self.record_target(full, **changes)
                self.assertEqual(self.resolve().verification_kind, "STORED_SOURCE")
        self.record_target(full)
        self.assertEqual(self.resolve().verification_kind, "APPLIED_STATE")
        # A changed copy, another server folder or no usable server folder: source only
        (self.target / "Addons" / "mod.pbo").write_bytes(b"other")
        self.assertEqual(self.resolve().verification_kind, "STORED_SOURCE")
        self.settings.dayz_root = str(self.base / "nowhere")
        self.assertEqual(self.resolve().verification_kind, "STORED_SOURCE")
        self.settings.dayz_root = None
        self.assertEqual(self.resolve().verification_kind, "STORED_SOURCE")

    def test_target_record_serves_its_own_directory_in_any_letter_case_only(self) -> None:
        """Two distinct NTFS folders never share a record; "@CF" and "@cf" do."""
        full = self.record_source()
        decomposed = unicodedata.normalize("NFD", "@Mód")
        for proven, look_alike in (("@Strasse", "@Straße"), ("@Mód", decomposed)):
            with self.subTest(proven=proven):
                self.target = build_target(self.dayz, proven, b"alpha")
                self.record_target(full, directory=proven)
                # The look-alike folder has other content but equal sizes and times
                other = build_target(self.dayz, look_alike, b"OTHER")
                self.assertNotEqual(self.target.resolve(), other.resolve())
                state = (self.target / "Addons" / "mod.pbo").stat()
                os.utime(other / "Addons" / "mod.pbo", ns=(state.st_atime_ns, state.st_mtime_ns))
                self.assertEqual(self.resolve(profile({proven: "111"})).verification_kind,
                                 "APPLIED_STATE")
                self.assertEqual(self.resolve(profile({look_alike: "111"})).verification_kind,
                                 "STORED_SOURCE")
        # One folder in another letter case meets its record
        self.target = build_target(self.dayz, "@CF", b"alpha")
        self.record_target(full, directory="@CF")
        self.assertEqual(self.resolve(profile({"@cf": "111"})).verification_kind,
                         "APPLIED_STATE")

    def test_publication_of_another_server_folder_records_nothing(self) -> None:
        """The recorder writes only when the folder identity equals the intent's."""
        # A target that is not current is recorded after the recorder hashed it itself
        full = self.verifier.verify("111")
        source = SimpleNamespace(
            workshop_id="111", target_relative="@Alpha", target_current=False, cache_proof=full,
            output_digest=full.content_inventory_digest)
        recorder = ContentProofRecorder(self.store)
        # The intent names another server folder: no source and no target record
        recorder.record_publication(
            SimpleNamespace(managed_sources=(source,), dayz_root_identity="f" * 64),
            self.dayz, self.cache)
        document = self.store.load()
        self.assertEqual((document.sources, document.targets), ({}, {}))
        # The same publication for this server folder records both
        recorder.record_publication(
            SimpleNamespace(managed_sources=(source,), dayz_root_identity=self.root_identity),
            self.dayz, self.cache)
        document = self.store.load()
        self.assertEqual(set(document.sources), {"111"})
        self.assertEqual(document.targets[(self.root_identity, "@alpha")].basis, "HASHED")

    def test_failed_outcomes_and_uninstalled_items_are_not_resolved(self) -> None:
        """Only a success outcome with an installed record can have a stored proof."""
        self.record_source()
        self.assertIsNone(self.resolve(outcomes={"111": ItemOutcome.CONTENT_FAILED}))
        write_manifest(self.base, {"222": ("9", 100)})
        self.assertIsNone(self.resolve())

    def test_resolver_errors_mean_no_stored_proof(self) -> None:
        """A read, path or fingerprint error never raises; the item has no proof."""
        self.record_source()
        with patch("dayz_serverman.application.content_proofs.tree_metadata_digest",
                   side_effect=OSError("synthetic")):
            self.assertIsNone(self.resolve())
        self.store._path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.resolve())
        (self.base / "steamapps" / "workshop" / "appworkshop_221100.acf").unlink()
        self.assertIsNone(self.resolve())

    def test_legacy_hit_seeds_the_source_record_only_and_leaves_the_legacy_file_unchanged(self) -> None:
        """Result 3: the profile-bound legacy rule answers each resolution; no target record is seeded."""
        full = self.verifier.verify("111")
        legacy = AppliedModStateRepository(self.legacy_path)
        legacy.record(PublicationIntent(
            "publication-1", "main", 1, "c" * 64, 1, self.root_identity,
            (ManagedModSource("111", 0, "@Alpha", str(self.cache / "111"), full,
                              full.content_inventory_digest),), (),
        ).signed(), self.dayz)
        before = self.legacy_path.read_bytes()
        # Another profile digest misses the legacy rule and seeds nothing
        self.assertIsNone(self.resolve(profile({"@Alpha": "111"}, digest="e" * 64)))
        self.assertFalse(self.store.load().sources)
        proof = self.resolve()
        self.assertEqual(proof.verification_kind, "APPLIED_STATE")
        document = self.store.load()
        self.assertEqual(document.sources["111"].content_inventory_digest,
                         full.content_inventory_digest)
        self.assertEqual(document.sources["111"].cache_root_identity,
                         self.verifier.root_identity())
        # No hash of this version stands behind the legacy target fingerprint
        self.assertEqual(document.targets, {})
        self.assertEqual(self.legacy_path.read_bytes(), before)
        # The legacy rule keeps answering beside the seeded source record
        again = self.resolve()
        self.assertEqual((again.verification_kind, again.target_metadata_digest),
                         ("APPLIED_STATE", tree_metadata_digest(self.target)))
        self.assertEqual(self.store.load().targets, {})
        # Another profile, or no legacy file, has the seeded source proof only
        edited = self.resolve(profile({"@Alpha": "111"}, digest="e" * 64))
        self.assertEqual((edited.verification_kind, edited.target_metadata_digest),
                         ("STORED_SOURCE", None))
        self.legacy_path.unlink()
        self.assertEqual(self.resolve().verification_kind, "STORED_SOURCE")
        self.assertEqual(self.resolve().content_inventory_digest, full.content_inventory_digest)


if __name__ == "__main__":
    unittest.main()
