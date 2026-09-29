"""Cover mod publication preview authority, staleness, and cancellation."""

from __future__ import annotations

import tempfile
import time
import unittest
import sys
import copy
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics
from dayz_serverman.application.mod_publication import (
    ModPublicationError, ModPublicationService, PublicationRequest,
)
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationCancelled
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.profiles import ProfileService
from dayz_serverman.application.publication_identity import authentication_identity_digest
from dayz_serverman.application.settings import SettingsService
from dayz_serverman.domain.models import SettingsInput
from dayz_serverman.domain.profiles import ProfileInput
from dayz_serverman.domain.workshop import AuthenticationMode, derive_required_items
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profiles import ProfileRepository
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier
from dayz_serverman.composition import build_composition


class PublicationApplicationFixture(unittest.TestCase):
    """Prepare a manager root, verified workshop cache, and publication services."""

    def setUp(self) -> None:
        """Build the manager layout, settings, profile, and publication service."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = PortablePaths.from_root(self.root / "Manager")
        self.paths.create_layout()
        # Create the DayZ root with server, mod, and key directories
        self.dayz = self.root / "DayZ Root"
        self.dayz.mkdir()
        executable = self.dayz / "DayZServer_x64.exe"
        executable.write_bytes(b"server")
        for relative in ("mods/alpha", "mods/beta", "keys"):
            (self.dayz / relative).mkdir(parents=True)
        # Populate the workshop cache with two verified items
        self.cache = self.root / "steamapps/workshop/content/221100"
        for item, content in (("111", b"alpha"), ("222", b"beta")):
            source = self.cache / item
            (source / "Addons").mkdir(parents=True)
            (source / "Addons" / f"{item}.pbo").write_bytes(content)
            (source / "keys").mkdir()
            (source / "keys/Shared.bikey").write_bytes(b"shared")
        (self.root / "steamapps/workshop/appworkshop_221100.acf").write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" "NeedsDownload" "0" '
            '"WorkshopItemsInstalled" { "111" { "manifest" "1" "size" "5" '
            '"timeupdated" "1" } "222" { "manifest" "2" "size" "4" '
            '"timeupdated" "1" } } }', encoding="utf-8",
        )
        # Build settings, profile, and publication services over the layout
        self.settings = SettingsService(
            VersionedJsonRepository(self.paths.manager_config), self.paths,
            WindowsPathDiagnostics(),
        )
        current = self.settings.save(SettingsInput(
            dayz_root=str(self.dayz), dayz_executable=str(executable),
            workshop_content_root=str(self.cache), steam_account_name="Operator",
            steam_authentication_mode="ACCOUNT",
        ), None)
        self.profiles = ProfileService(ProfileRepository(self.paths.profiles), self.settings)
        self.profile = self.profiles.save(ProfileInput.parse({
            "profile_id": "main", "display_name": "Main",
            "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
            "runtime_profile": None, "mission_root": None, "game_port": 2302,
            "mods": [
                {"directory": "mods\\alpha", "launch_scope": "client",
                 "source": {"kind": "workshop", "workshop_id": "111"}},
                {"directory": "mods\\local", "launch_scope": "client",
                 "source": {"kind": "external"}},
                {"directory": "mods\\beta", "launch_scope": "server",
                 "source": {"kind": "workshop", "workshop_id": "222"}},
            ], "extra_arguments": [],
        }), None)
        self.operations = OperationManager(OperationStore(self.paths.operations))
        self.settings_revision = current.revision
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            _NoStartLifecycle(),
        )
        self.gate_id = self._gate(complete=True)

    def tearDown(self) -> None:
        """Shut down operations and remove the temporary root."""
        self.operations.shutdown(3)
        self.temporary.cleanup()

    def request(self, gate_id: str | None = None) -> PublicationRequest:
        """Build a publication request bound to the verified gate operation."""
        return PublicationRequest(
            "main", self.profile.revision, self.profile.semantic_digest,
            self.settings_revision, gate_id or self.gate_id,
        )

    def wait(self, operation_id: str):
        """Wait for an operation to reach a terminal state and return its record."""
        for _ in range(300):
            record = self.operations.get(operation_id)
            if record.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                return record
            time.sleep(0.01)
        self.fail("operation did not finish")

    def _gate(self, *, complete: bool, start_requested: bool = False) -> str:
        """Run a workshop verification gate and return its operation identifier."""
        verifier = WorkshopCacheVerifier(self.cache)
        items = []
        for item in derive_required_items(self.profile):
            items.append({"item": item.to_dict(), "outcome": "UPDATED_VERIFIED",
                          "cache_proof": verifier.verify(item.workshop_id).to_dict(),
                          "error_code": None})
        result = {"profile_id": "main", "download_state": "VERIFIED" if items else "EMPTY",
                  "items": items,
                  "publication_state": "PENDING_PHASE_6_2", "start_authorized": False,
                  "start_requested": start_requested,
                  "start_error": "PUBLICATION_REQUIRED" if start_requested else None,
                  "process_id": 77 if items else None,
                  "steamcmd_exit_code": 0 if items else None,
                  "steamcmd_summary": None}
        if complete:
            result.update({
                "profile_revision": self.profile.revision,
                "semantic_profile_digest": self.profile.semantic_digest,
                "settings_revision": self.settings_revision,
                "authentication_identity_digest": authentication_identity_digest(
                    AuthenticationMode.ACCOUNT, "Operator"),
            })
        record = self.operations.submit("UPDATE_WORKSHOP_ITEMS", lambda _context: result)
        return self.wait(record.operation_id).operation_id


class ModPublicationApplicationTests(PublicationApplicationFixture):
    """Verify publication previews and rebuilds reject stale or drifted authority."""

    def test_preview_rebuilds_authority_and_exposes_no_absolute_paths(self) -> None:
        """Verify preview rebuilds target authority and exposes no absolute paths."""
        preview = self.service.preview(self.request())
        self.assertEqual([item["target_relative"] for item in preview["targets"]],
                         ["mods\\alpha", "mods\\beta"])
        self.assertNotIn(str(self.cache), str(preview))
        self.assertNotIn("Operator", str(preview))

    def test_incomplete_older_gate_fails_without_inference(self) -> None:
        """Verify an incomplete older gate fails without inferring authority."""
        old_gate = self._gate(complete=False)
        with self.assertRaises(ModPublicationError) as raised:
            self.service.preview(self.request(old_gate))
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")

    def test_unverified_download_gate_reports_verification_failure(self) -> None:
        """Verify an unverified download gate reports a verification failure."""
        gate = self.operations._operations[self.gate_id].record
        baseline = copy.deepcopy(gate.result)
        # Overwrite the gate result with an unverified download state
        gate.result = {**baseline, "download_state": "UNKNOWN"}
        try:
            with self.assertRaises(ModPublicationError) as raised:
                self.service.preview(self.request())
            self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
            self.assertIn("did not produce a verified cache set", str(raised.exception))
        finally:
            gate.result = baseline

    def test_authentication_digest_is_additive_and_non_secret(self) -> None:
        """Verify the authentication digest is additive and carries no account name."""
        gate = self.operations.get(self.gate_id)
        self.assertRegex(gate.result["authentication_identity_digest"], r"^[0-9a-f]{64}$")
        self.assertNotIn("Operator", str(gate.result))

    def test_startup_corrupt_publication_blocks_mutations_but_keeps_queries(self) -> None:
        """Verify a corrupt publication journal blocks mutations but keeps queries."""
        corrupt = self.paths.publication_journals / "corrupt.json"
        corrupt.write_text("{broken", encoding="utf-8")
        # Open a fresh composition over the corrupt publication journal
        composition = build_composition(self.paths.root)
        try:
            self.assertIsNotNone(composition.operations.recovery_block)
            response = composition.bridge.dispatch({
                "contract_version": 1, "request_id": "snapshot",
                "method": "get_application_snapshot", "parameters": {},
            })
            self.assertTrue(response["success"])
        finally:
            composition.operations.shutdown(2)

    def test_malformed_gate_matrix_is_stale_before_publication_storage(self) -> None:
        """Verify every malformed gate shape is stale before storage is touched."""
        baseline = copy.deepcopy(self.operations._operations[self.gate_id].record.result)
        mutations = {
            "unknown-top": lambda value: value.update(extra=True),
            "bool-process": lambda value: value.update(process_id=True),
            "bool-exit": lambda value: value.update(steamcmd_exit_code=True),
            "failed-exit": lambda value: value.update(steamcmd_exit_code=1),
            "success-summary": lambda value: value.update(steamcmd_summary="unexpected"),
            "bad-publication": lambda value: value.update(publication_state="VERIFIED"),
            "authorized-start": lambda value: value.update(start_authorized=True),
            "bad-start-request": lambda value: value.update(start_requested="yes"),
            "bad-start-error": lambda value: value.update(start_error="OTHER"),
            "unknown-item": lambda value: value["items"][0].update(extra=True),
            "success-error": lambda value: value["items"][0].update(error_code="FAILED"),
            "digest-type": lambda value: value["items"][0]["cache_proof"].update(
                manifest_record_digest=7),
            "bool-count": lambda value: value["items"][0]["cache_proof"].update(
                regular_file_count=True),
            "bad-time": lambda value: value["items"][0]["cache_proof"].update(
                verified_at="not-utc"),
        }
        # Apply each malformed shape and require a stale preview
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                malformed = copy.deepcopy(baseline)
                mutate(malformed)
                self.operations._operations[self.gate_id].record.result = malformed
                with self.assertRaises(ModPublicationError) as raised:
                    self.service.preview(self.request())
                self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
                self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])
        self.operations._operations[self.gate_id].record.result = baseline

    def test_final_rebuild_cancellation_has_zero_publication_changes(self) -> None:
        """Verify final rebuild cancellation leaves no publication changes."""
        preview = self.service.preview(self.request())
        # Cancel at each final rebuild phase and verify nothing changed
        for phase in ("DISCOVER_ITEM", "CACHE_PROOF_RECHECK"):
            with self.subTest(phase=phase):
                context = _SyntheticContext(
                    f"cancel-{phase.casefold().replace('_', '-')}", cancel_phase=phase,
                )
                with self.assertRaises(OperationCancelled):
                    self.service.publish(
                        self.request(), preview["publication_fingerprint"], context,
                    )
                self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])
                self.assertFalse(any(path.name.startswith(".serverman-")
                                     for path in self.dayz.rglob("*")))

    def test_root_replacement_at_last_context_boundary_is_stale(self) -> None:
        """Verify a root replacement at the last boundary is stale."""
        preview = self.service.preview(self.request())
        original = self.root / "Original Root"
        def replace_root() -> None:
            """Swap the DayZ root just before the publication boundary."""
            self.dayz.rename(original)
            self.dayz.mkdir()
        context = _SyntheticContext("root-swap", action_phase="BEFORE_PUBLICATION",
                                    action=replace_root)
        with self.assertRaises(ModPublicationError) as raised:
            self.service.publish(self.request(), preview["publication_fingerprint"], context)
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertTrue((original / "mods/alpha").is_dir())
        self.assertEqual(tuple(self.dayz.iterdir()), ())

    def test_final_rebuild_rejects_auth_and_cache_drift_without_staging(self) -> None:
        """Verify auth and cache drift at rebuild reject without staging."""
        preview = self.service.preview(self.request())
        current = self.settings.load()
        # Swap the account name between preview and publish
        alternate_auth = replace(current, steam_account_name="OtherOperator")
        with patch.object(self.settings, "load", side_effect=[current, alternate_auth]):
            with self.assertRaises(ModPublicationError) as auth_error:
                self.service.publish(
                    self.request(), preview["publication_fingerprint"],
                    _SyntheticContext("auth-swap"),
                )
        self.assertEqual(auth_error.exception.code, "PUBLICATION_PREVIEW_STALE")

        def mutate_cache() -> None:
            """Change the verified cache bytes before the publication boundary."""
            (self.cache / "111/Addons/111.pbo").write_bytes(b"changed")
        with self.assertRaises(ModPublicationError) as cache_error:
            self.service.publish(
                self.request(), preview["publication_fingerprint"],
                _SyntheticContext("cache-drift", action_phase="BEFORE_PUBLICATION",
                                  action=mutate_cache),
            )
        self.assertEqual(cache_error.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])

    def test_same_revision_alternate_root_is_rejected_by_core_stage(self) -> None:
        """Verify an alternate root at the same revision is rejected by the core stage."""
        preview = self.service.preview(self.request())
        current = self.settings.load()
        alternate_root = self.root / "Alternate DayZ"
        alternate_root.mkdir()
        # Serve an alternate root with an unchanged revision
        alternate = replace(current, dayz_root=str(alternate_root))
        with patch.object(self.settings, "load", side_effect=[current, current, alternate]):
            with self.assertRaises(ModPublicationError) as raised:
                self.service.publish(
                    self.request(), preview["publication_fingerprint"],
                    _SyntheticContext("alternate-root"),
                )
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertEqual(tuple(alternate_root.iterdir()), ())
        self.assertTrue((self.dayz / "mods/alpha").is_dir())


class _SyntheticContext:
    """Provide a controllable operation context for cancellation and boundary hooks."""

    def __init__(self, operation_id: str, *, cancel_phase: str | None = None,
                 action_phase: str | None = None, action=None) -> None:
        """Store the operation identity and optional phase hooks."""
        self.operation_id = operation_id
        self.cancel_phase = cancel_phase
        self.action_phase = action_phase
        self.action = action
        self.evidence = None

    @property
    def cancellation_requested(self) -> bool:
        """Report that synthetic contexts never request cancellation."""
        return False

    def checkpoint(self, phase: str, _percent: int) -> None:
        """Run the phase action and raise cancellation at the configured phase."""
        if phase == self.action_phase and self.action is not None:
            self.action()
        if phase == self.cancel_phase:
            raise OperationCancelled("synthetic cancellation")

    def record_evidence(self, evidence) -> None:
        """Capture the latest evidence snapshot for assertions."""
        self.evidence = dict(evidence)


class _NoStartLifecycle:
    """Fail if update-only publication ever attempts to start the server."""

    def start(self, _profile_id: str, _profile_revision: int, _settings_revision: int):
        """Raise because update-only publication must not start the server."""
        raise AssertionError("update-only publication must not start the server")


if __name__ == "__main__":
    unittest.main()
