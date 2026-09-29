"""Tests for applied mod state matching, mutation misses, and staging."""
from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.mod_publication import ManagedModSource, PublicationIntent
from dayz_serverman.domain.workshop import CacheProof
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository
from dayz_serverman.repositories.mod_publication_staging import stage_mod_group
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest


class AppliedModStateTests(unittest.TestCase):
    """Applied-state verification contracts for recorded mod publications."""
    def setUp(self) -> None:
        """Create source and target trees with a recorded publication intent."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "cache" / "111"
        self.target = self.root / "dayz" / "mods" / "alpha"
        self.source.mkdir(parents=True)
        self.target.mkdir(parents=True)
        (self.source / "mod.pbo").write_bytes(b"current")
        (self.target / "mod.pbo").write_bytes(b"current")
        self.source_metadata = tree_metadata_digest(self.source)
        self.target_metadata = tree_metadata_digest(self.target)
        self.proof = CacheProof(
            "111", "a" * 64, "b" * 64, 1, 7,
            "2026-09-28T16:00:00.000+00:00", "9", self.source_metadata,
        )
        self.intent = PublicationIntent(
            "publication-1", "main", 1, "c" * 64, 1, "d" * 64,
            (ManagedModSource(
                "111", 0, "mods\\alpha", str(self.source), self.proof, "b" * 64,
            ),), (),
        ).signed()
        self.repository = AppliedModStateRepository(self.root / "state.json")

    def tearDown(self) -> None:
        """Remove the temporary mod tree."""
        self.temporary.cleanup()

    def test_matching_state_returns_applied_proof_and_mutation_misses(self) -> None:
        """A recorded publication verifies until the target content changes."""
        # Verify the recorded publication, then mutate the target content
        self.repository.record(self.intent, self.root / "dayz")
        found = self._find()
        self.assertIsNotNone(found)
        self.assertEqual(found.verification_kind, "APPLIED_STATE")
        (self.target / "mod.pbo").write_bytes(b"changed")
        self.assertIsNone(self._find())

    def test_malformed_state_fails_closed(self) -> None:
        """Malformed persisted state disables applied-state verification."""
        (self.root / "state.json").write_text("not-json", encoding="utf-8")
        self.assertIsNone(self._find())

    def test_applied_target_staging_uses_metadata_not_content_inventory(self) -> None:
        """Target staging verifies with metadata and never re-inventories content."""
        # Staging must not fall back to a full content inventory
        with patch(
            "dayz_serverman.repositories.mod_publication_staging.inventory_tree",
            side_effect=AssertionError("content inventory must not run"),
        ):
            group = stage_mod_group(
                operation="publication-1", ordinal=0, relative="mods\\alpha",
                source=self.source, target=self.target, expected_digest="b" * 64,
                expected_source_metadata=self.source_metadata,
                expected_target_metadata=self.target_metadata, target_current=True,
                checkpoint=lambda _phase, _index: None,
                fault=lambda _phase, _index: None,
            )
        self.assertEqual(group.state.value, "UNCHANGED_VERIFIED")

    def _find(self):
        """Look up the applied verification for the recorded publication intent."""
        return self.repository.find(
            profile_id="main", semantic_profile_digest="c" * 64,
            dayz_root_identity="d" * 64, workshop_id="111",
            target_relative="mods\\alpha", source=self.source, target=self.target,
            manifest_record_digest="a" * 64, installed_manifest_id="9",
        )


if __name__ == "__main__":
    unittest.main()
