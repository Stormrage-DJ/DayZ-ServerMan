"""Verify files: every source and target state, the proof effects and the result shape."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_proof_fixtures import build_target, write_manifest  # noqa: E402
from test_content_proof_reuse import profile_record  # noqa: E402
from verify_files_fixtures import (  # noqa: E402
    SOURCE_HASH, TARGET_HASH, FakeContext, VerifyFilesFixture,
)
from dayz_serverman.application.mod_publication_gate import (  # noqa: E402
    PublicationGateError, parse_publication_gate,
)
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.repositories.mod_publication_inventory import inventory_tree  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402


class VerifyStatesTests(VerifyFilesFixture):
    """Design section 10: state tables and proof effects of one verification."""

    def test_verified_sources_and_matching_targets_write_both_records(self) -> None:
        """A clean set is VERIFIED / MATCHES_SOURCE, in profile order, and fully recorded."""
        before = self.tree_state()
        items = self.verify()
        self.assertEqual(list(items), ["111", "222"])
        for workshop_id in ("111", "222"):
            self.assertEqual(self.states(items, workshop_id),
                             ("VERIFIED", "MATCHES_SOURCE", None))
            self.assertEqual(set(items[workshop_id]), {
                "workshop_id", "source_state", "target_state", "verified_at", "error_code"})
            self.assertRegex(items[workshop_id]["verified_at"], r"^\d{4}-\d\d-\d\dT.*\+00:00$")
        document = self.store.load()
        self.assertEqual(set(document.sources), {"111", "222"})
        self.assertEqual(set(document.targets),
                         {self.target_key("@Alpha"), self.target_key("@Bravo")})
        target = document.targets[self.target_key("@Alpha")]
        source = document.sources["111"]
        self.assertEqual(
            (target.workshop_id, target.installed_manifest_id, target.content_inventory_digest),
            ("111", "9", source.content_inventory_digest))
        # The record names the full hash of this verification as its basis
        self.assertEqual(target.basis, "VERIFIED")
        # The digest equals the one the update operation computes
        proof = WorkshopCacheVerifier(self.cache).verify("111")
        self.assertEqual(source.content_inventory_digest, proof.content_inventory_digest)
        # Mod and server folders are only read
        self.assertEqual(self.tree_state(), before)

    def test_changed_source_replaces_the_record_and_drops_the_target_record(self) -> None:
        """The same manifest id with another digest is CHANGED; the copy then DIFFERS."""
        self.verify()
        old = self.store.load().sources["111"].content_inventory_digest
        (self.cache / "111" / "Addons" / "mod.pbo").write_bytes(b"ALPHA")
        items = self.verify()
        self.assertEqual(self.states(items, "111"), ("CHANGED", "DIFFERS", None))
        self.assertEqual(self.states(items, "222"), ("VERIFIED", "MATCHES_SOURCE", None))
        document = self.store.load()
        self.assertNotEqual(document.sources["111"].content_inventory_digest, old)
        self.assertNotIn(self.target_key("@Alpha"), document.targets)
        self.assertIn(self.target_key("@Bravo"), document.targets)
        # The replaced record is the new truth: the next run reports VERIFIED
        self.assertEqual(self.states(self.verify(), "111"), ("VERIFIED", "DIFFERS", None))

    def test_new_manifest_id_is_verified_not_changed(self) -> None:
        """Another digest under another manifest id is an update, not a change."""
        self.verify()
        (self.cache / "111" / "Addons" / "mod.pbo").write_bytes(b"alpha-2")
        write_manifest(self.base, {"111": ("10", 200), "222": ("9", 100)})
        items = self.verify()
        self.assertEqual(self.states(items, "111"), ("VERIFIED", "DIFFERS", None))
        self.assertEqual(self.store.load().sources["111"].installed_manifest_id, "10")

    def test_missing_source_removes_the_records(self) -> None:
        """No directory and no installed record are MISSING; their records are removed."""
        self.verify()
        (self.cache / "111" / "Addons" / "mod.pbo").unlink()
        (self.cache / "111" / "Addons").rmdir()
        (self.cache / "111").rmdir()
        # Item 333 has no installed record and no server-folder copy
        self.record = profile_record("main", 2302, {**self.MODS, "@Charlie": "333"})
        items = self.verify()
        # The copy of 111 exists, but the source has no digest to compare with
        self.assertEqual(self.states(items, "111"),
                         ("MISSING", "FAILED", "TARGET_VERIFICATION_FAILED"))
        self.assertEqual(self.states(items, "333"), ("MISSING", "NOT_APPLIED", None))
        document = self.store.load()
        self.assertEqual(set(document.sources), {"222"})
        self.assertEqual(set(document.targets), {self.target_key("@Bravo")})

    def test_failed_source_removes_the_record(self) -> None:
        """A read error and an incomplete manifest are FAILED with the cache error code."""
        self.verify()
        with patch(SOURCE_HASH, side_effect=PermissionError("synthetic read error")):
            items = self.verify()
        for workshop_id in ("111", "222"):
            self.assertEqual(self.states(items, workshop_id),
                             ("FAILED", "FAILED", "CACHE_VERIFICATION_FAILED"))
        self.assertEqual((self.store.load().sources, self.store.load().targets), ({}, {}))
        # Steam reports pending work: no proof can be built
        self.verify()
        manifest = self.base / "steamapps" / "workshop" / "appworkshop_221100.acf"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            '"NeedsUpdate" "0"', '"NeedsUpdate" "1"'), encoding="utf-8")
        self.assertEqual(self.states(self.verify(), "111")[0], "FAILED")
        self.assertEqual(self.store.load().sources, {})

    def test_target_states_and_their_proof_effects(self) -> None:
        """NOT_APPLIED, DIFFERS and FAILED remove the target record; the source stays."""
        self.verify()
        (self.dayz / "@Alpha" / "Addons" / "mod.pbo").write_bytes(b"other")
        (self.dayz / "@Bravo" / "Addons" / "mod.pbo").unlink()
        (self.dayz / "@Bravo" / "Addons").rmdir()
        (self.dayz / "@Bravo").rmdir()
        items = self.verify()
        self.assertEqual(self.states(items, "111"), ("VERIFIED", "DIFFERS", None))
        self.assertEqual(self.states(items, "222"), ("VERIFIED", "NOT_APPLIED", None))
        document = self.store.load()
        self.assertEqual((set(document.sources), document.targets), ({"111", "222"}, {}))
        # A file in place of the directory is an unsafe target
        (self.dayz / "@Bravo").write_bytes(b"not a directory")
        self.assertEqual(self.states(self.verify(), "222"),
                         ("VERIFIED", "FAILED", "TARGET_VERIFICATION_FAILED"))
        # Files are never repaired
        self.assertEqual((self.dayz / "@Alpha" / "Addons" / "mod.pbo").read_bytes(), b"other")

    def test_target_read_error_is_failed_and_removes_the_record(self) -> None:
        """A read error in the server folder is FAILED with the target error code."""
        self.verify()
        with patch(TARGET_HASH, side_effect=PermissionError("synthetic read error")):
            items = self.verify()
        self.assertEqual(self.states(items, "111"),
                         ("VERIFIED", "FAILED", "TARGET_VERIFICATION_FAILED"))
        document = self.store.load()
        self.assertEqual((set(document.sources), document.targets), ({"111", "222"}, {}))

    def test_target_that_changes_during_the_hash_is_failed(self) -> None:
        """A fingerprint that moves while the copy is hashed never yields a target proof."""
        self.verify()
        file = self.dayz / "@Alpha" / "Addons" / "mod.pbo"

        def hash_then_touch(target, cancelled):
            """Hash the real tree, then move the file time of the first copy."""
            digest = inventory_tree(target, cancelled)
            if target.name == "@Alpha":
                state = file.stat()
                os.utime(file, ns=(state.st_atime_ns, state.st_mtime_ns + 2_000_000_000))
            return digest

        with patch(TARGET_HASH, side_effect=hash_then_touch):
            items = self.verify()
        # The content still equals the source, yet the moving copy is not proven
        self.assertEqual(self.states(items, "111"),
                         ("VERIFIED", "FAILED", "TARGET_VERIFICATION_FAILED"))
        self.assertNotIn(self.target_key("@Alpha"), self.store.load().targets)
        self.assertEqual(self.states(items, "222"), ("VERIFIED", "MATCHES_SOURCE", None))
        self.assertIn(self.target_key("@Bravo"), self.store.load().targets)

    def test_restored_target_gets_its_record_back(self) -> None:
        """MATCHES_SOURCE writes the target record again after a repair by the operator."""
        (self.dayz / "@Alpha" / "Addons" / "mod.pbo").write_bytes(b"other")
        self.assertEqual(self.states(self.verify(), "111")[1], "DIFFERS")
        (self.dayz / "@Alpha" / "Addons" / "mod.pbo").write_bytes(b"alpha")
        self.assertEqual(self.states(self.verify(), "111")[1], "MATCHES_SOURCE")
        self.assertIn(self.target_key("@Alpha"), self.store.load().targets)

    def test_profile_without_workshop_mods_succeeds_with_no_items(self) -> None:
        """An empty item set is a successful run that touches neither cache nor store."""
        self.record = profile_record("main", 2302, {})
        self.settings.workshop_content_root = str(self.base / "absent")
        self.assertEqual(self.verify(), {})
        self.assertFalse(self.store._path.exists())

    def test_result_is_never_a_publication_gate(self) -> None:
        """The gate accepts only UPDATE_WORKSHOP_ITEMS, whatever the verification says."""
        result = self.service.verify(
            SimpleNamespace(profile_id="main", expected_profile_revision=self.record.revision,
                            expected_settings_revision=1), FakeContext())
        gate = SimpleNamespace(kind="VERIFY_WORKSHOP_FILES", state=OperationState.SUCCEEDED,
                               result=result)
        with self.assertRaises(PublicationGateError):
            parse_publication_gate(gate, SimpleNamespace(), self.record, self.settings)

    def test_new_copy_in_the_server_folder_is_proven_without_a_copy(self) -> None:
        """A copy made outside the manager is recorded when its content matches."""
        build_target(self.dayz, "@Extra", b"alpha")
        self.record = profile_record("main", 2302, {"@Extra": "111"})
        self.assertEqual(self.states(self.verify(), "111"),
                         ("VERIFIED", "MATCHES_SOURCE", None))
        self.assertIn(self.target_key("@Extra"), self.store.load().targets)


if __name__ == "__main__":
    unittest.main()
