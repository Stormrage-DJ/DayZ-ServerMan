"""Startup recovery tests for interrupted mod publication journals."""
from __future__ import annotations

import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.domain.mod_publication import (
    GroupState, PublicationPhase, publication_fingerprint,
)
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository
from dayz_serverman.repositories.mod_publication_recovery import PublicationRecovery
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage
from test_mod_publication_inventory import PublicationFixture


class PublicationRecoveryTests(PublicationFixture):
    """Contract of publication recovery: compensate or block without partial targets."""
    def setUp(self) -> None:
        """Seed live mod targets and keys plus an empty journal repository."""
        super().setUp()
        self.dayz = self.base / "DayZ"
        # Seed live mods and an unrelated key that recovery must preserve
        for relative, content in (("mods/alpha", b"old-a"), ("mods/beta", b"old-b")):
            target = self.dayz / relative
            target.mkdir(parents=True)
            (target / "old.pbo").write_bytes(content)
        (self.dayz / "keys").mkdir()
        (self.dayz / "keys" / "keep.bikey").write_bytes(b"keep")
        self.repository = PublicationJournalRepository(self.base / "journals")

    @staticmethod
    def _save_new(repository, journal) -> None:
        """Bind the journal authority and persist it as the current record."""
        repository.bind_authority(journal, journal.authority_intent)
        repository.save(journal)

    def _interrupt_after_first(self):
        """Stage a publication and simulate an interrupt after the first target swap."""
        journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        journal.phase = PublicationPhase.PUBLISHING
        journal.publication_started = True
        group = journal.groups[0]
        # Move the live prior aside and swap the staged output into place
        target = self.dayz / "mods/alpha"
        recovery = target.with_name(group.recovery_name)
        stage = target.with_name(group.stage_name)
        target.replace(recovery)
        stage.replace(target)
        # Record the verified output and persist the journal
        group.state = GroupState.OUTPUT_VERIFIED
        self._save_new(self.repository, journal)
        return journal

    def test_startup_mixed_prior_and_output_compensates_whole_set(self) -> None:
        """Roll back every target when startup finds mixed prior and output states."""
        # Interrupt the publication after the first target swap
        self._interrupt_after_first()
        # Startup recovery must compensate the whole set back to prior state
        result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertFalse(result["blocked"])
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertEqual((self.dayz / "mods/beta/old.pbo").read_bytes(), b"old-b")
        self.assertTrue((self.repository.retired_root / "publication-1.json").is_file())
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_third_state_blocks_without_mutating_any_other_target(self) -> None:
        """Block on a third target state without mutating the remaining live mods."""
        # Create a third state inside the first target after the interrupt
        self._interrupt_after_first()
        (self.dayz / "mods/alpha/Addons/111.pbo").write_bytes(b"third-state")
        beta_before = (self.dayz / "mods/beta/old.pbo").read_bytes()
        # The blocked recovery must leave every target untouched
        result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertTrue(result["blocked"])
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), b"third-state")
        self.assertEqual((self.dayz / "mods/beta/old.pbo").read_bytes(), beta_before)
        loaded = self.repository.load(self.repository.path_for("publication-1"))
        self.assertEqual(loaded.phase, PublicationPhase.RECOVERY_REQUIRED)

    def test_prepared_startup_cleanup_has_no_live_change_or_residue(self) -> None:
        """Clean up a prepared-only publication with no live change or residue."""
        # Persist a staged but unfinished publication journal
        journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        self._save_new(self.repository, journal)
        # Startup cleanup must remove the journal without touching live mods
        result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertFalse(result["blocked"])
        self.assertFalse(self.repository.path_for("publication-1").exists())
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))

    def test_missing_live_prior_moved_restores_prior_and_never_commits(self) -> None:
        """Restore the prior target when a moved-live prior is lost, never committing."""
        # Stage the publication and move the live prior aside
        journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        journal.phase = PublicationPhase.PUBLISHING
        journal.publication_started = True
        group = journal.groups[0]
        target = self.dayz / "mods/alpha"
        target.replace(target.with_name(group.recovery_name))
        group.state = GroupState.PRIOR_MOVED
        self._save_new(self.repository, journal)
        # Recovery must restore the prior and retire the journal as rolled back
        result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertFalse(result["blocked"])
        self.assertEqual((target / "old.pbo").read_bytes(), b"old-a")
        retired = self.repository.load(self.repository.retired_root / "publication-1.json")
        self.assertEqual(retired.result, "ROLLED_BACK")

    def test_missing_recovery_blocks_before_any_output_mutation(self) -> None:
        """Block before any output mutation when a recovery directory is missing."""
        # Verify output for the first two groups
        journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        journal.phase = PublicationPhase.PUBLISHING
        journal.publication_started = True
        for group in journal.groups[:2]:
            target = self.dayz / group.target_relative.replace("\\", "/")
            recovery = target.with_name(group.recovery_name)
            stage = target.with_name(group.stage_name)
            target.replace(recovery)
            stage.replace(target)
            group.state = GroupState.OUTPUT_VERIFIED
        self._save_new(self.repository, journal)
        missing = (self.dayz / "mods/alpha").with_name(journal.groups[0].recovery_name)
        import shutil
        shutil.rmtree(missing)
        # Capture both live outputs before recovery runs
        alpha_output = (self.dayz / "mods/alpha/Addons/111.pbo").read_bytes()
        beta_output = (self.dayz / "mods/beta/Addons/222.pbo").read_bytes()
        # Missing recovery evidence must block without touching the outputs
        result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertTrue(result["blocked"])
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), alpha_output)
        self.assertEqual((self.dayz / "mods/beta/Addons/222.pbo").read_bytes(), beta_output)

    def test_self_consistent_forged_target_is_rejected_before_target_read(self) -> None:
        """Reject a self-consistent forged target before the target inventory is read."""
        # Persist a staged journal, then forge a self-consistent target
        journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
        self._save_new(self.repository, journal)
        path = self.repository.path_for(journal.publication_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["groups"][0]["target_relative"] = "mpmissions\\forged"
        journal.groups[0].target_relative = "mpmissions\\forged"
        raw["publication_fingerprint"] = publication_fingerprint(
            journal.intent_fingerprint, journal.groups,
        )
        path.write_text(json.dumps(raw), encoding="utf-8")
        # The forged record must be rejected before the target inventory is read
        with patch(
            "dayz_serverman.repositories.mod_publication_recovery.classify_all",
            side_effect=AssertionError("target inventory must not be read"),
        ) as classify:
            result = PublicationRecovery(self.repository).inspect(self.dayz)
        self.assertTrue(result["blocked"])
        classify.assert_not_called()
        self._remove_staged_artifacts()

    def test_signed_authority_rejects_target_near_miss_and_swap(self) -> None:
        """Reject target near-miss and group swap tampering before any target read."""
        # Probe each tampering variant with a freshly signed journal
        for mutation in ("near-miss", "swap"):
            with self.subTest(mutation=mutation):
                repository = PublicationJournalRepository(self.base / f"journals-{mutation}")
                journal = ModPublicationStorage().stage(self.build(self.dayz), self.dayz)
                self._save_new(repository, journal)
                path = repository.path_for(journal.publication_id)
                raw = json.loads(path.read_text(encoding="utf-8"))
                # Apply either a look-alike target name or a group order swap
                if mutation == "near-miss":
                    raw["groups"][0]["target_relative"] = "mods\\alphaa"
                    journal.groups[0].target_relative = "mods\\alphaa"
                else:
                    raw["groups"][0], raw["groups"][1] = raw["groups"][1], raw["groups"][0]
                    for ordinal, group in enumerate(raw["groups"]):
                        group["ordinal"] = ordinal
                        group["stage_name"] = f".serverman-publication-1-{ordinal}.stage"
                        group["recovery_name"] = f".serverman-publication-1-{ordinal}.recovery"
                    journal.groups[0], journal.groups[1] = journal.groups[1], journal.groups[0]
                    for ordinal, group in enumerate(journal.groups):
                        group.ordinal = ordinal
                        group.stage_name = f".serverman-publication-1-{ordinal}.stage"
                        group.recovery_name = f".serverman-publication-1-{ordinal}.recovery"
                raw["publication_fingerprint"] = publication_fingerprint(
                    journal.intent_fingerprint, journal.groups,
                )
                path.write_text(json.dumps(raw), encoding="utf-8")
                # The forgery must be rejected before any target is read
                with patch(
                    "dayz_serverman.repositories.mod_publication_recovery.classify_all",
                    side_effect=AssertionError("target inventory must not be read"),
                ) as classify:
                    result = PublicationRecovery(repository).inspect(self.dayz)
                self.assertTrue(result["blocked"])
                classify.assert_not_called()
                self._remove_staged_artifacts()

    def _remove_staged_artifacts(self) -> None:
        """Delete remaining staging and recovery directories from the DayZ root."""
        for path in self.dayz.rglob(".serverman-*"):
            if path.is_dir():
                shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main()
