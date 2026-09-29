"""Cover publication intent, journal validation, authority binding, and retirement."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.domain.mod_publication import (
    GroupState,
    ManagedModSource,
    PublicationGroup,
    PublicationIntent,
    PublicationJournal,
    PublicationPhase,
    PublicationValidationError,
    TargetRole,
    publication_fingerprint,
    validate_intent,
)
from dayz_serverman.domain.workshop import CacheProof
from dayz_serverman.repositories.mod_publication_journal import (
    PublicationJournalError,
    PublicationJournalRepository,
    validate_journal,
)


# Placeholder SHA-256 digest used by the domain fixtures
DIGEST = "a" * 64


def proof(item: str = "123") -> CacheProof:
    """Build a cache proof with fixed digests for intent fixtures."""
    return CacheProof(
        item, "b" * 64, "c" * 64, 2, 20, "2026-09-27T00:00:00Z",
        None, "f" * 64,
    )


def intent() -> PublicationIntent:
    """Build a signed publication intent with one managed source."""
    value = PublicationIntent(
        "publish-1", "main", 4, DIGEST, 3, "d" * 64,
        (ManagedModSource("123", 0, "mods\\alpha", "C:\\cache\\123", proof(), "e" * 64),),
        (),
    ).signed()
    validate_intent(value)
    return value


def journal() -> PublicationJournal:
    """Build a bound publication journal in the prepared phase."""
    value = PublicationJournal(
        "publish-1", intent().fingerprint, PublicationPhase.PREPARED,
        False, False, False, None,
        [PublicationGroup(
            TargetRole.MANAGED_MOD_DIRECTORY, 0, "mods\\alpha",
            ".serverman-publish-1-0.stage", ".serverman-publish-1-0.recovery",
            True, "f" * 64, "e" * 64,
        )],
    )
    value.publication_fingerprint = publication_fingerprint(
        value.intent_fingerprint, value.groups,
    )
    value.updated_at = "2026-09-27T00:00:00.000Z"
    value.authority_intent = intent()
    return value


class PublicationDomainTests(unittest.TestCase):
    """Verify fingerprints, strict schemas, binding, and retirement rules."""

    def test_intent_fingerprint_is_stable_and_source_path_is_not_exposed(self) -> None:
        """Verify the fingerprint ignores source paths and never exposes them."""
        first = intent()
        # Rebuild the intent around a different source path
        changed_path = PublicationIntent(
            **{**vars(first), "managed_sources": (
                ManagedModSource("123", 0, "mods\\alpha", "Z:\\other", proof(), "e" * 64),
            ), "fingerprint": ""},
        ).signed()
        self.assertEqual(first.fingerprint, changed_path.fingerprint)
        self.assertNotIn("source_path", json.dumps(first.body()))

    def test_intent_rejects_duplicate_target_and_noncanonical_key_order(self) -> None:
        """Verify a duplicate managed target is rejected."""
        base = intent()
        duplicate = ManagedModSource("456", 1, "MODS\\ALPHA", "C:\\cache\\456", proof("456"), "1" * 64)
        with self.assertRaises(PublicationValidationError):
            validate_intent(PublicationIntent(**{
                **vars(base), "managed_sources": (*base.managed_sources, duplicate), "fingerprint": "",
            }).signed())

    def test_journal_rejects_illegal_state_without_mutation(self) -> None:
        """Verify an illegal group state is rejected without mutating the journal."""
        value = journal()
        original = value.to_dict()
        # Force a state that cannot appear before publication starts
        value.groups[0].state = GroupState.OUTPUT_VERIFIED
        with self.assertRaises(PublicationValidationError):
            validate_journal(value)
        value.groups[0].state = GroupState.STAGED
        self.assertEqual(original, value.to_dict())

    def test_journal_rejects_boolean_schema_and_swapped_artifact_identity(self) -> None:
        """Verify boolean schemas and swapped stage identities are rejected."""
        value = journal()
        value.schema_version = True
        with self.assertRaises(PublicationValidationError):
            validate_journal(value)
        value.schema_version = 1
        # Swap the stage name against the recorded fingerprint
        value.groups[0].stage_name = ".serverman-other-0.stage"
        with self.assertRaises(PublicationValidationError):
            validate_journal(value)

    def test_atomic_roundtrip_and_unknown_field_rejection(self) -> None:
        """Verify a saved journal reloads unchanged and rejects unknown fields."""
        with tempfile.TemporaryDirectory() as temporary:
            repository = PublicationJournalRepository(Path(temporary))
            value = journal()
            repository.bind_authority(value, value.authority_intent)
            path = repository.save(value)
            self.assertEqual(value.to_dict(), repository.load(path).to_dict())
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Corrupt the stored document with an unknown field
            raw["unexpected"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(PublicationJournalError):
                repository.load(path)

    def test_publication_identifier_rejects_traversal_before_any_storage_write(self) -> None:
        """Verify traversal identifiers are rejected before any storage write."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = PublicationJournalRepository(base / "journals")
            value = journal()
            value.publication_id = "../escape"
            with self.assertRaisesRegex(PublicationJournalError, "identifier"):
                repository.save(value)
            with self.assertRaisesRegex(PublicationJournalError, "identifier"):
                repository.path_for("../escape")
            self.assertFalse((base / "escape.json").exists())
            self.assertFalse(repository.root.exists())

    def test_authority_binding_rejects_self_consistent_non_profile_target(self) -> None:
        """Verify binding rejects a self-consistent non-profile target plan."""
        with tempfile.TemporaryDirectory() as temporary:
            repository = PublicationJournalRepository(Path(temporary) / "journals")
            value = journal()
            # Forge the target, then recompute the fingerprint to stay self-consistent
            value.groups[0].target_relative = "mpmissions\\forged"
            value.publication_fingerprint = publication_fingerprint(
                value.intent_fingerprint, value.groups,
            )
            with self.assertRaisesRegex(PublicationJournalError, "target plan"):
                repository.bind_authority(value, value.authority_intent)
            self.assertFalse(repository.root.exists())

    def test_committed_journal_retires_only_after_strict_reload(self) -> None:
        """Verify a committed journal retires only after a strict reload."""
        with tempfile.TemporaryDirectory() as temporary:
            repository = PublicationJournalRepository(Path(temporary))
            value = journal()
            value.phase = PublicationPhase.COMMITTED
            value.publication_started = value.committed = value.resolved = True
            value.result = "COMMITTED"
            value.groups[0].state = GroupState.OUTPUT_VERIFIED
            repository.bind_authority(value, value.authority_intent)
            repository.save(value)
            # Retire the committed journal and verify the active file moved
            retired = repository.retire(value)
            self.assertTrue(retired.is_file())
            self.assertFalse(repository.path_for(value.publication_id).exists())

    def test_target_binding_timestamp_and_state_grammar_are_strict(self) -> None:
        """Verify target binding, timestamps, and state grammar stay strict."""
        value = journal()
        # Forge the group target binding
        value.groups[0].target_relative = "mods\\forged"
        with self.assertRaisesRegex(PublicationValidationError, "binding"):
            validate_journal(value)
        value = journal()
        # Use a numeric offset instead of the canonical UTC timestamp form
        value.updated_at = "2026-09-27T00:00:00+02:00"
        with self.assertRaisesRegex(PublicationValidationError, "timestamp"):
            validate_journal(value)
        value = journal()
        value.phase = PublicationPhase.PUBLISHING
        value.publication_started = True
        value.groups[0].state = GroupState.PRIOR_MOVED
        validate_journal(value)
        # Append a second group and recompute the fingerprint
        value.groups.append(PublicationGroup(
            TargetRole.MANAGED_MOD_DIRECTORY, 1, "mods\\beta",
            ".serverman-publish-1-1.stage", ".serverman-publish-1-1.recovery",
            True, "1" * 64, "2" * 64, GroupState.PRIOR_MOVED,
        ))
        value.publication_fingerprint = publication_fingerprint(
            value.intent_fingerprint, value.groups,
        )
        with self.assertRaisesRegex(PublicationValidationError, "state"):
            validate_journal(value)

    def test_unresolved_terminal_journals_are_valid_but_cannot_retire(self) -> None:
        """Verify unresolved terminal journals validate but cannot retire."""
        value = journal()
        value.phase = PublicationPhase.COMMITTED
        value.publication_started = value.committed = True
        value.groups[0].state = GroupState.OUTPUT_VERIFIED
        validate_journal(value)
        with tempfile.TemporaryDirectory() as temporary:
            repository = PublicationJournalRepository(Path(temporary))
            repository.bind_authority(value, value.authority_intent)
            repository.save(value)
            # Require refusal when retirement sees an unresolved commit
            with self.assertRaises(PublicationJournalError):
                repository.retire(value)


if __name__ == "__main__":
    unittest.main()
