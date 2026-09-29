"""Security tests for the publication-to-start handshake and its authority records."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.application import mod_publication_start
from dayz_serverman.application.mod_publication import ModPublicationError
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState
from dayz_serverman.domain.mod_publication import canonical_digest
from test_mod_publication_application import PublicationApplicationFixture, _SyntheticContext


class ModPublicationStartSecurityTests(PublicationApplicationFixture):
    """Contract: a start request proceeds only from untampered, current evidence."""
    def setUp(self) -> None:
        """Open a start-requested gate and substitute a counting lifecycle."""
        super().setUp()
        self.gate_id = self._gate(complete=True, start_requested=True)
        self.lifecycle = _Lifecycle()
        self.service._lifecycle = self.lifecycle

    def test_missing_persisted_authority_blocks_start_and_keeps_publication(self) -> None:
        """Block start when the persisted authority record was lost after publication."""
        # Arm a tamper step that deletes the persisted authority record
        context = _SyntheticContext("missing-authority")

        def remove_authority(journal) -> None:
            """Delete the authority record persisted for the journal."""
            (self.paths.publication_journals / "authority" /
             f"{journal.publication_id}.json").unlink()

        # The missing authority must block start while publication stays verified
        self._tamper_after_publish(remove_authority)
        self._assert_postpublication_failure(context)
        self.assertEqual(len(list(self.paths.publication_journals.glob("retired/*.json"))), 1)

    def test_recomputed_forged_retired_target_blocks_start(self) -> None:
        """Block start when a recomputed fingerprint still hides a forged retired target."""
        # Arm a tamper step that forges the retired record and recomputes its fingerprint
        context = _SyntheticContext("forged-retired")

        def forge_retired(journal) -> None:
            """Rewrite the retired record with a forged target and matching fingerprint."""
            path = self.paths.publication_journals / "retired" / f"{journal.publication_id}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["groups"][0]["target_relative"] = "mods\\forged"
            targets = []
            for group in raw["groups"]:
                target = dict(group); target.pop("state")
                targets.append(target)
            raw["publication_fingerprint"] = canonical_digest({
                "intent_fingerprint": raw["intent_fingerprint"], "targets": targets,
            })
            path.write_text(json.dumps(raw), encoding="utf-8")

        # The forged record must block start while the published target stays live
        self._tamper_after_publish(forge_retired)
        self._assert_postpublication_failure(context)
        self.assertTrue((self.dayz / "mods/alpha/Addons/111.pbo").is_file())

    def test_review_fingerprint_binds_start_request_both_directions(self) -> None:
        """Bind the review fingerprint so either review/start mismatch blocks publication."""
        # Exercise both review/start mismatch directions
        for reviewed, tampered in ((False, True), (True, False)):
            with self.subTest(reviewed=reviewed, tampered=tampered):
                self.gate_id = self._gate(complete=True, start_requested=reviewed)
                preview = self.service.preview(self.request())
                # Flip the recorded start request after the preview
                result = self.operations._operations[self.gate_id].record.result
                result["start_requested"] = tampered
                result["start_error"] = "PUBLICATION_REQUIRED" if tampered else None
                with self.assertRaises(ModPublicationError) as raised:
                    self.service.publish(
                        self.request(), preview["publication_fingerprint"],
                        _SyntheticContext(f"review-{reviewed}-{tampered}"),
                    )
                self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        # No start may be recorded and no journal may remain
        self.assertEqual(self.lifecycle.calls, 0)
        self.assertEqual(list(self.paths.publication_journals.rglob("*.json")), [])

    def test_cancellation_during_postpublication_rebuild_prevents_start(self) -> None:
        """Prevent start when cancellation arrives during the postpublication rebuild."""
        # Intercept profile reads so the third read triggers cancellation
        context = _MutableContext("cancel-final-rebuild")
        preview = self.service.preview(self.request())
        original = self.profiles.read
        calls = 0

        def read(profile_id):
            """Count profile reads and mark the context cancelled on the third read."""
            nonlocal calls
            calls += 1
            profile = original(profile_id)
            if calls == 3:
                context.cancelled = True
            return profile

        # The cancelled publication must not attempt a start
        with patch.object(self.profiles, "read", side_effect=read):
            result = self.service.publish(
                self.request(), preview["publication_fingerprint"], context,
            )
        self._assert_cancelled(result)

    def test_cancellation_during_live_hashing_prevents_start(self) -> None:
        """Prevent start when cancellation arrives during live target hashing."""
        # Intercept live hashing so the first call triggers cancellation
        context = _MutableContext("cancel-live-hash")
        preview = self.service.preview(self.request())
        original = mod_publication_start.inventory_tree
        calls = 0

        def inventory(path):
            """Hash the tree, then mark the context cancelled on the first call."""
            nonlocal calls
            digest = original(path); calls += 1
            if calls == 1:
                context.cancelled = True
            return digest

        # The cancelled publication must not attempt a start
        with patch.object(mod_publication_start, "inventory_tree", side_effect=inventory):
            result = self.service.publish(
                self.request(), preview["publication_fingerprint"], context,
            )
        self._assert_cancelled(result)

    def _assert_postpublication_failure(self, context) -> None:
        """Assert the publish fails closed and no start is attempted."""
        preview = self.service.preview(self.request())
        with self.assertRaises(ModPublicationError) as raised:
            self.service.publish(self.request(), preview["publication_fingerprint"], context)
        self.assertEqual(raised.exception.code, "PUBLICATION_VERIFICATION_FAILED")
        self.assertEqual(self.lifecycle.calls, 0)
        self.assertEqual(context.evidence["publication_state"], "VERIFIED")

    def _assert_cancelled(self, result) -> None:
        """Assert the publication verified but the start was cancelled."""
        self.assertEqual(result["publication_state"], "VERIFIED")
        self.assertEqual(result["start_state"], "CANCELLED")
        self.assertEqual(self.lifecycle.calls, 0)

    def _tamper_after_publish(self, action) -> None:
        """Wrap the storage factory so action runs right after publish succeeds."""
        original_factory = self.service._storage_factory

        def factory(checkpoint):
            """Build a storage whose publish is followed by the tamper action."""
            storage = original_factory(checkpoint); publish = storage.publish

            def publish_then_tamper(journal, root, repository):
                """Publish the journal, then run the tampering action."""
                publish(journal, root, repository); action(journal)

            storage.publish = publish_then_tamper
            return storage

        # Replace the factory so every publish is followed by the tamper action
        self.service._storage_factory = factory


class _Lifecycle:
    """Lifecycle double that counts start attempts and returns a managed snapshot."""
    def __init__(self) -> None:
        """Start with zero recorded start calls."""
        self.calls = 0

    def start(self, _profile_id: str, _profile_revision: int, _settings_revision: int):
        """Record the start attempt and return a managed running snapshot."""
        self.calls += 1
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, process_id=443)


class _MutableContext(_SyntheticContext):
    """Synthetic context with a settable cancellation flag."""
    def __init__(self, operation_id: str) -> None:
        """Create the context in the uncancelled state."""
        super().__init__(operation_id); self.cancelled = False

    @property
    def cancellation_requested(self) -> bool:
        """Report whether the injected cancellation flag was set."""
        return self.cancelled


if __name__ == "__main__":
    unittest.main()
