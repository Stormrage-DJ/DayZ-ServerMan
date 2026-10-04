"""Pre-start check with rule D9: fingerprint acceptance, fallback to the full hash, counts."""
from __future__ import annotations

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
from content_proof_fixtures import write_manifest  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application import mod_publication_start  # noqa: E402
from dayz_serverman.application.content_proof_records import ContentProofRecorder  # noqa: E402
from dayz_serverman.application.mod_publication import (  # noqa: E402
    ModPublicationError, ModPublicationService,
)
from dayz_serverman.application.mod_publication_prestart import PrestartFingerprints  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402

# Store key of the mod folder that the miss cases change
ALPHA = "mods\\alpha"


class PrestartCheckTests(fixtures.PublicationApplicationFixture):
    """Rule D9: only an unchanged mod folder with every matching input skips the full hash."""

    def setUp(self) -> None:
        """Wire the service to a proof store, the pre-start rule and a counting lifecycle."""
        super().setUp()
        write_manifest(self.root, {"111": ("1", 1), "222": ("2", 1)})
        self.store = ContentProofStore(self.paths.content_proofs)
        self.logger = _Logger()
        self.lifecycle = CountingLifecycle()
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            self.lifecycle, ContentProofRecorder(self.store),
            PrestartFingerprints(self.store, self.logger),
        )
        self.key = (dayz_root_identity(self.dayz), ALPHA)
        self.hashed: list[str] = []

    def applied_gate(self, start_requested: bool = True) -> str:
        """Apply both mods once, then return a gate whose proofs carry the target fingerprints."""
        self.publish(self._gate(complete=True), "first")
        self.assertEqual(self.lifecycle.calls, 0)
        return applied_state_gate(self, self.store, start_requested)

    def publish(self, gate_id: str, name: str, before_check=None):
        """Publish the gate; `before_check` runs when the pre-start check begins."""
        preview = self.service.preview(self.request(gate_id))
        context = _RecordingContext(name, action_phase="VERIFY_BEFORE_START", action=before_check)
        original = mod_publication_start.inventory_tree

        def recording(path, *arguments):
            """Hash the tree and record that the pre-start check hashed it in full."""
            self.hashed.append(path.name)
            return original(path, *arguments)

        # Only the pre-start check reads the hash through this module name
        with patch.object(mod_publication_start, "inventory_tree", side_effect=recording):
            result = self.service.publish(
                self.request(gate_id), preview["publication_fingerprint"], context)
        return result, context

    def assert_alpha_falls_back(self, before_check) -> None:
        """Assert that the changed input sends alpha to the full hash and the start still runs."""
        result, _context = self.publish(self.applied_gate(), "miss", before_check)
        self.assertEqual(self.hashed, ["alpha", "keys"])
        self.assertEqual(result["prestart_check"], {"hashed": 2, "fingerprint_accepted": 1})
        self.assertEqual((result["start_state"], self.lifecycle.calls), ("STARTED", 1))

    def rewrite(self, **changes: object) -> None:
        """Replace fields of the stored target record of alpha."""
        record = self.store.load().targets[self.key]
        self.assertTrue(self.store.record(targets={self.key: replace(record, **changes)}))

    def touch(self) -> None:
        """Move the modification time of one alpha file, so its fingerprint changes."""
        path = self.dayz / "mods/alpha/Addons/111.pbo"
        state = path.stat()
        os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns + 5_000_000_000))

    def test_unchanged_groups_are_accepted_without_a_hash(self) -> None:
        """Both unchanged mods pass by fingerprint; only the keys directory is hashed."""
        result, context = self.publish(self.applied_gate(), "accept")
        self.assertEqual(self.hashed, ["keys"])
        self.assertEqual(result["prestart_check"], {"hashed": 1, "fingerprint_accepted": 2})
        self.assertEqual((result["start_state"], result["start_authorized"]), ("STARTED", True))
        self.assertEqual(self.lifecycle.calls, 1)
        self.assertEqual(context.phases[-2:], ["VERIFY_BEFORE_START", "START_SERVER"])
        self.assertEqual(self.logger.records, [("mod_publication.prestart_check", "INFO", "accept",
                                                {"hashed": 1, "fingerprint_accepted": 2})])
        self.assertEqual(context.evidence["prestart_check"], result["prestart_check"])

    def test_no_record_falls_back(self) -> None:
        """Without a target record the group is hashed."""
        self.assert_alpha_falls_back(lambda: self.store.record(remove_targets=[self.key]))

    def test_record_of_another_manifest_id_falls_back(self) -> None:
        """A record for another manifest id is a miss."""
        self.assert_alpha_falls_back(lambda: self.rewrite(installed_manifest_id="7"))

    def test_record_of_another_digest_falls_back(self) -> None:
        """A record for another content digest is a miss."""
        self.assert_alpha_falls_back(lambda: self.rewrite(content_inventory_digest="a" * 64))

    def test_record_of_another_item_falls_back(self) -> None:
        """A record for another Workshop item is a miss."""
        self.assert_alpha_falls_back(lambda: self.rewrite(workshop_id="222"))

    def test_fingerprint_changed_since_the_record_falls_back(self) -> None:
        """A tree whose fingerprint left the recorded one is hashed."""
        self.assert_alpha_falls_back(self.touch)

    def test_recorded_fingerprint_that_differs_falls_back(self) -> None:
        """A record with another fingerprint than the tree is a miss."""
        self.assert_alpha_falls_back(lambda: self.rewrite(target_metadata_digest="b" * 64))

    def test_reviewed_fingerprint_that_differs_from_the_current_one_falls_back(self) -> None:
        """A record that follows the changed tree does not help: the reviewed fingerprint is old."""
        def change_tree_and_record() -> None:
            """Change the fingerprint and store the new one, as a later write would."""
            self.touch()
            self.rewrite(target_metadata_digest=tree_metadata_digest(self.dayz / "mods/alpha"))

        self.assert_alpha_falls_back(change_tree_and_record)

    def assert_every_group_is_hashed(self, before_check) -> None:
        """Assert that a store that cannot be read is a miss for every group, never a failure."""
        result, _context = self.publish(self.applied_gate(), "unreadable", before_check)
        self.assertEqual(self.hashed, ["alpha", "beta", "keys"])
        self.assertEqual(result["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})
        self.assertEqual((result["start_state"], self.lifecycle.calls), ("STARTED", 1))

    def test_corrupt_store_hashes_every_group(self) -> None:
        """A store file that is no document gives no record."""
        self.assert_every_group_is_hashed(
            lambda: self.paths.content_proofs.write_text("{broken", encoding="utf-8"))

    def test_failing_store_read_hashes_every_group(self) -> None:
        """A store read that raises gives no record."""
        def fail_read() -> None:
            """Make every later read of the store raise."""
            self.store.load = _raise_os_error

        self.assert_every_group_is_hashed(fail_read)

    def test_groups_copied_in_this_run_and_the_keys_are_always_hashed(self) -> None:
        """A copied group is hashed although its fresh record matches the current fingerprint."""
        gate = self._gate(complete=True, start_requested=True)
        result, _context = self.publish(gate, "copied")
        self.assertEqual(self.hashed, ["alpha", "beta", "keys"])
        self.assertEqual(result["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})
        record = self.store.load().targets[self.key]
        self.assertEqual(record.target_metadata_digest,
                         tree_metadata_digest(self.dayz / "mods/alpha"))

    def test_unchanged_group_without_a_reviewed_fingerprint_is_hashed(self) -> None:
        """A full-content proof names no target fingerprint, so an equal folder is still hashed."""
        self.publish(self._gate(complete=True), "first")
        self.hashed.clear()
        result, _context = self.publish(self._gate(complete=True, start_requested=True), "full")
        self.assertEqual(self.hashed, ["alpha", "beta", "keys"])
        self.assertEqual(result["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})

    def test_start_is_refused_when_the_fallback_hash_differs(self) -> None:
        """After a miss the full hash decides: changed content blocks the start."""
        def remove_record_and_tamper() -> None:
            """Drop the record and change content while size and time stay the same."""
            path = self.dayz / "mods/alpha/Addons/111.pbo"
            state = path.stat()
            path.write_bytes(b"ALPHA")
            os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns))
            self.store.record(remove_targets=[self.key])

        gate = self.applied_gate()
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, "refused", remove_record_and_tamper)
        self.assertEqual(raised.exception.code, "PUBLICATION_VERIFICATION_FAILED")
        self.assertEqual((self.lifecycle.calls, self.logger.records), (0, []))

    def test_publication_without_a_requested_start_performs_no_check(self) -> None:
        """Without a start request neither the rule nor a pre-start hash runs."""
        gate = self.applied_gate(start_requested=False)
        with patch.object(PrestartFingerprints, "rule", side_effect=AssertionError("no check")):
            result, context = self.publish(gate, "no-start")
        self.assertEqual((self.hashed, self.logger.records, self.lifecycle.calls), ([], [], 0))
        self.assertIsNone(result["prestart_check"])
        self.assertEqual(result["start_state"], "NOT_REQUESTED")
        self.assertNotIn("VERIFY_BEFORE_START", context.phases)


def _raise_os_error():
    """Stand in for a store read that fails."""
    raise OSError("synthetic read failure")


class _RecordingContext(fixtures._SyntheticContext):
    """Synthetic context that also records every checkpoint phase."""

    def __init__(self, operation_id: str, **options) -> None:
        """Start with an empty phase list."""
        super().__init__(operation_id, **options)
        self.phases: list[str] = []

    def checkpoint(self, phase: str, percent: int) -> None:
        """Record the phase, then run the configured action."""
        self.phases.append(phase)
        super().checkpoint(phase, percent)


class _Logger:
    """Logger double that keeps every emitted record."""

    def __init__(self) -> None:
        """Start with no records."""
        self.records: list[tuple[str, str, str | None, dict[str, object]]] = []

    def emit(self, event, *, level="INFO", correlation_id=None, operation_id=None, fields=None):
        """Keep the event name, level, operation and fields."""
        self.records.append((event, level, operation_id, dict(fields or {})))


if __name__ == "__main__":
    unittest.main()
