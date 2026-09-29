"""Staging and publication tests for mod and key targets on the DayZ root."""
from __future__ import annotations

import sys
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage
from dayz_serverman.repositories.mod_publication_stage import PublicationStorageError
from dayz_serverman.domain.mod_publication import GroupState
from test_mod_publication_inventory import PublicationFixture


class PublicationStageTests(PublicationFixture):
    """Contract of publication staging: preview, copy or verify, then live swap."""
    def setUp(self) -> None:
        """Seed live mods, keys, and a local mod that publication must not touch."""
        super().setUp()
        self.dayz = self.base / "DayZ Root"
        # Seed live mods with prior bytes
        for relative, old in (("mods/alpha", b"old-a"), ("mods/beta", b"old-b")):
            target = self.dayz / relative
            target.mkdir(parents=True)
            (target / "old.pbo").write_bytes(old)
        (self.dayz / "keys").mkdir()
        (self.dayz / "keys" / "Unrelated.bikey").write_bytes(b"preserve")
        # A local mod beside the managed targets must stay untouched
        (self.dayz / "mods/local").mkdir()
        (self.dayz / "mods/local/local.pbo").write_bytes(b"never-touch")
        self.repository = PublicationJournalRepository(self.base / "journals")

    def test_two_populated_mods_and_keys_publish_as_one_intent(self) -> None:
        """Stage and publish two mods plus keys as a single intent."""
        # Stage the intent and check every target exists before publish
        storage = ModPublicationStorage()
        journal = storage.stage(self.build(self.dayz), self.dayz)
        self.assertEqual(len(journal.groups), 3)
        self.assertTrue(all((self.dayz / group.target_relative.replace("\\", "/")).exists()
                            for group in journal.groups))
        # Publish and confirm target bytes, preserved entries, and retirement
        storage.publish(journal, self.dayz, self.repository)
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), b"alpha")
        self.assertEqual((self.dayz / "mods/beta/Addons/222.pbo").read_bytes(), b"beta")
        self.assertEqual((self.dayz / "keys/Unrelated.bikey").read_bytes(), b"preserve")
        self.assertEqual((self.dayz / "keys/Shared.BIKEY").read_bytes(), b"same-key")
        self.assertEqual((self.dayz / "mods/local/local.pbo").read_bytes(), b"never-touch")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))
        self.assertTrue((self.repository.retired_root / "publication-1.json").is_file())

    def test_source_drift_after_preview_leaves_no_residue(self) -> None:
        """Detect source drift after preview and leave no staging residue."""
        # Preview the intent, then change the cached source bytes
        intent = self.build(self.dayz)
        (self.cache / "111" / "Addons/111.pbo").write_bytes(b"changed-after-preview")
        # Staging must fail closed with no residue beside the live targets
        with self.assertRaisesRegex(Exception, "source changed"):
            ModPublicationStorage().stage(intent, self.dayz)
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_matching_mods_and_keys_are_verified_without_copy_or_live_mutation(self) -> None:
        """Verify matching content without copying and without live mutation."""
        # Replace live mods with exact copies of the verified cache
        shutil.rmtree(self.dayz / "mods/alpha")
        shutil.rmtree(self.dayz / "mods/beta")
        shutil.copytree(self.cache / "111", self.dayz / "mods/alpha")
        shutil.copytree(self.cache / "222", self.dayz / "mods/beta")
        shutil.copy2(self.cache / "111/keys/Shared.BIKEY", self.dayz / "keys/Shared.BIKEY")
        # Staging must verify without copying any tree
        with patch(
            "dayz_serverman.repositories.mod_publication_staging.copy_tree",
            side_effect=AssertionError("unchanged content must not be copied"),
        ):
            journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        self.assertTrue(all(
            group.state == GroupState.UNCHANGED_VERIFIED for group in journal.groups
        ))
        # Publishing verified content must also leave no residue
        ModPublicationStorage().publish(journal, self.dayz, self.repository)
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_existing_different_key_blocks_before_live_mutation_and_cleans_stage(self) -> None:
        """Block an existing differing key before live mutation and clean the stage."""
        # Plant a conflicting key beside the live target keys
        (self.dayz / "keys/shared.bikey").write_bytes(b"conflict")
        before = (self.dayz / "mods/alpha/old.pbo").read_bytes()
        # Staging must block before any live mutation and remove its stage
        with self.assertRaisesRegex(Exception, "key collision"):
            ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), before)
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_existing_keys_subdirectory_is_rejected_before_publication(self) -> None:
        """Reject an unsupported keys subdirectory before publication starts."""
        # Add an unsupported directory inside the live keys folder
        (self.dayz / "keys/unsupported").mkdir()
        # Staging must reject it before publication and leave targets untouched
        with self.assertRaisesRegex(Exception, "unsupported entry"):
            ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_root_object_replacement_is_rejected_before_artifact_creation(self) -> None:
        """Reject a replaced DayZ root before any publication artifact is created."""
        # Replace the DayZ root object after the intent was previewed
        intent = self.build(self.dayz)
        original = self.base / "Original DayZ"
        self.dayz.rename(original)
        self.dayz.mkdir()
        # The stale preview must be rejected before any artifact is created
        with self.assertRaises(PublicationStorageError) as raised:
            ModPublicationStorage().stage(intent, self.dayz)
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertEqual(tuple(self.dayz.iterdir()), ())
        self.assertTrue((original / "mods/alpha/old.pbo").is_file())


if __name__ == "__main__":
    unittest.main()
