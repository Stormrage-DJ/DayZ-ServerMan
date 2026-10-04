"""Write guard of a publication: mutex and stopped server for a writing run, none otherwise."""
from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_mod_publication_application as fixtures  # noqa: E402
from applied_gate_fixtures import CountingLifecycle, applied_state_gate  # noqa: E402
from content_proof_fixtures import write_manifest  # noqa: E402
from dayz_serverman.application import installation_guard  # noqa: E402
from dayz_serverman.application.activity_wording import error_text  # noqa: E402
from dayz_serverman.application.content_proof_records import ContentProofRecorder  # noqa: E402
from dayz_serverman.application.installation_guard import InstallationGuard  # noqa: E402
from dayz_serverman.application.mod_publication import (  # noqa: E402
    ModPublicationError, ModPublicationService,
)
from dayz_serverman.application.mod_publication_coordinator import (  # noqa: E402
    ModPublicationCoordinator,
)
from dayz_serverman.application.mod_restart_coordinator import ModRestartCoordinator  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_stage import (  # noqa: E402
    ModPublicationStorage, PublicationStorageError,
)

# Refusal code per server state that is not STOPPED
REFUSALS = {
    ServerState.RUNNING_MANAGED: "CONTROL_CONFLICT", ServerState.STARTING: "CONTROL_CONFLICT",
    ServerState.STOPPING: "CONTROL_CONFLICT", ServerState.RUNNING_EXTERNAL: "EXTERNAL_PROCESS",
    ServerState.UNKNOWN: "PROCESS_STATE_UNKNOWN", ServerState.AMBIGUOUS: "PROCESS_STATE_UNKNOWN",
}


class _Mutex:
    """Installation mutex double that records every acquisition and can be busy."""

    def __init__(self) -> None:
        """Start free, with no recorded events."""
        self.held = False
        self.busy = False
        self.events: list[str] = []

    @contextmanager
    def guard(self, dayz_root: str):
        """Refuse when busy; else hold the mutex for the block."""
        if self.busy:
            raise LifecycleFailure("CONTROL_CONFLICT", "Another manager controls this DayZ installation.")
        self.held = True
        self.events.append("acquire")
        try:
            yield
        finally:
            self.held = False
            self.events.append("release")


class PublicationGuardTests(fixtures.PublicationApplicationFixture):
    """A writing publication runs under the mutex with a stopped server; a refusal changes nothing."""

    def setUp(self) -> None:
        """Wire the service to a proof store, a lifecycle status and a mutex double."""
        super().setUp()
        write_manifest(self.root, {"111": ("1", 1), "222": ("2", 1)})
        self.gate_id = self._gate(complete=True)
        self.store = ContentProofStore(self.paths.content_proofs)
        self.lifecycle = CountingLifecycle()
        self.mutex = _Mutex()
        self.service = self.build_service()

    def build_service(self, **options) -> ModPublicationService:
        """Return a publication service with the guard; `options` may set the policy."""
        return ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            self.lifecycle, ContentProofRecorder(self.store),
            guard=InstallationGuard(self.lifecycle, self.mutex), **options,
        )

    def publish(self, gate_id: str, name: str, **context_options):
        """Preview and publish the gate with a synthetic context."""
        preview = self.service.preview(self.request(gate_id))
        return self.service.publish(
            self.request(gate_id), preview["publication_fingerprint"],
            fixtures._SyntheticContext(name, **context_options))

    def assert_refused(self, gate_id: str, name: str, code: str) -> None:
        """Assert that the run fails with the code and leaves no trace."""
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate_id, name)
        self.assertEqual((raised.exception.code, raised.exception.recovery_required), (code, False))
        self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])
        self.assertEqual([path for path in self.dayz.rglob("*") if path.is_file()],
                         [self.dayz / "DayZServer_x64.exe"])
        self.assertEqual((self.lifecycle.calls, self.mutex.held), (0, False))

    def test_each_state_other_than_stopped_refuses_a_writing_start(self) -> None:
        """The refusal code follows the server state, and nothing is staged."""
        gate = self._gate(complete=True, start_requested=True)
        for index, (state, code) in enumerate(REFUSALS.items()):
            with self.subTest(state=state.value):
                self.lifecycle.state = state
                self.assert_refused(gate, f"refused-{index}", code)
                # The backend catalogue words the refusal as a sentence about the state
                self.assertIn("This cannot be done while the server is",
                              error_text(code, f"Applying mods requires STOPPED; current state is {state.value}."))

    def test_busy_mutex_refuses_before_the_state_is_read(self) -> None:
        """Another manager holds the installation: control conflict, no status read."""
        self.mutex.busy = True
        self.lifecycle.status = lambda: self.fail("the state must not be read")
        self.assert_refused(self._gate(complete=True, start_requested=True), "busy", "CONTROL_CONFLICT")

    def test_guard_codes_pass_through_the_coordinator_as_a_failed_operation(self) -> None:
        """The operation fails with the lifecycle code and sets no recovery block."""
        self.lifecycle.state = ServerState.RUNNING_EXTERNAL
        gate = self._gate(complete=True, start_requested=True)
        preview = self.service.preview(self.request(gate))
        accepted = ModPublicationCoordinator(self.service, self.operations).publish({
            "profile_id": "main", "expected_profile_revision": self.profile.revision,
            "expected_semantic_profile_digest": self.profile.semantic_digest,
            "expected_settings_revision": self.settings_revision, "update_operation_id": gate,
            "publication_fingerprint": preview["publication_fingerprint"]})
        terminal = self.wait(accepted["operation_id"])
        self.assertEqual((terminal.state.value, terminal.terminal_error.code),
                         ("FAILED", "EXTERNAL_PROCESS"))
        self.assertIsNone(self.operations.recovery_block)

    def test_guard_covers_staging_replace_and_proofs_and_is_released_before_the_check(self) -> None:
        """The mutex is held while files and proofs are written, and free at the pre-start check."""
        seen: dict[str, bool] = {}
        original = self.service._storage_factory

        def factory(checkpoint):
            """Build a storage that notes whether the mutex is held at stage and publish."""
            storage = original(checkpoint)
            stage, publish = storage.stage, storage.publish
            storage.stage = lambda *a, **k: (seen.__setitem__("stage", self.mutex.held), stage(*a, **k))[1]
            storage.publish = lambda *a: (seen.__setitem__("publish", self.mutex.held), publish(*a))[1]
            return storage

        self.service._storage_factory = factory
        record = self.service._content_proofs.record_publication
        self.service._content_proofs.record_publication = lambda *a: (
            seen.__setitem__("proofs", self.mutex.held), record(*a))[1]
        result = self.publish(
            self._gate(complete=True, start_requested=True), "guarded",
            action_phase="VERIFY_BEFORE_START",
            action=lambda: seen.__setitem__("check", self.mutex.held))
        self.assertEqual(seen, {"stage": True, "publish": True, "proofs": True, "check": False})
        self.assertEqual(self.mutex.events, ["acquire", "release"])
        self.assertEqual((result["start_state"], self.lifecycle.calls), ("STARTED", 1))

    def test_run_that_writes_nothing_takes_no_guard_in_any_state(self) -> None:
        """Applied mods and present keys: the run needs neither the mutex nor a stopped server."""
        self.publish(self._gate(complete=True), "first")
        self.assertEqual(self.service.preview(self.request())["missing_key_count"], 0)
        # The first apply wrote, so it took the guard (D10); the run under test must not
        self.mutex.events.clear()
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        self.mutex.busy = True
        result = self.publish(applied_state_gate(self, self.store, False), "no-write")
        self.assertEqual((result["publication_state"], result["prestart_check"]), ("VERIFIED", None))
        self.assertEqual(self.mutex.events, [])

    def test_unguarded_run_never_writes_when_a_copy_becomes_necessary(self) -> None:
        """A key file that disappears after the decision ends the run as stale, with no write."""
        self.publish(self._gate(complete=True), "first")
        gate = applied_state_gate(self, self.store, False)
        key = self.dayz / "keys" / "Shared.bikey"
        with self.assertRaises(ModPublicationError) as raised:
            self.publish(gate, "late-copy", action_phase="STAGE_TARGET",
                         action=lambda: key.unlink(missing_ok=True))
        self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        self.assertEqual(list((self.dayz / "keys").iterdir()), [])
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))
        # The storage itself refuses a copy of a mod folder in a read-only run
        intent = self.service._rebuild(self.request(self._gate(complete=True)), "publication-readonly")
        with self.assertRaises(PublicationStorageError) as refused:
            ModPublicationStorage().stage(intent, self.dayz, writes_allowed=False)
        self.assertEqual(refused.exception.code, "PUBLICATION_PREVIEW_STALE")

    def test_plain_apply_policy_has_one_switch(self) -> None:
        """Decision D10, refuse: the default guards a plain apply. Switched off: it is attempted."""
        self.assertIs(installation_guard.PLAIN_APPLY_REQUIRES_GUARD, True)
        self.assertIs(self.service._guard_plain_apply, installation_guard.PLAIN_APPLY_REQUIRES_GUARD)
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        # Policy on (default): the writing plain apply needs a stopped server
        self.assert_refused(self._gate(complete=True), "plain-refused", "CONTROL_CONFLICT")
        self.mutex.events.clear()
        # Policy off: the same apply runs on a running server, as before the guard
        self.service = self.build_service(guard_plain_apply=False)
        result = self.publish(self._gate(complete=True), "plain-attempt")
        self.assertEqual((result["start_state"], self.mutex.events), ("NOT_REQUESTED", []))
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), b"alpha")

    def test_plain_writing_apply_is_refused_in_each_state_that_is_not_stopped(self) -> None:
        """D10 with the default policy: the refusal code follows the state, and nothing is written."""
        gate = self._gate(complete=True)
        for index, (state, code) in enumerate(REFUSALS.items()):
            with self.subTest(state=state.value):
                self.lifecycle.state = state
                self.assert_refused(gate, f"plain-refused-{index}", code)
        # A busy installation mutex refuses before the state is read
        self.lifecycle.state = ServerState.STOPPED
        self.mutex.busy = True
        self.assert_refused(gate, "plain-busy", "CONTROL_CONFLICT")

    def test_plain_apply_on_a_stopped_server_runs_inside_the_guard(self) -> None:
        """Default policy and a stopped server: the plain apply works, under the mutex."""
        result = self.publish(self._gate(complete=True), "plain-guarded")
        self.assertEqual((result["publication_state"], result["start_state"], self.mutex.events),
                         ("VERIFIED", "NOT_REQUESTED", ["acquire", "release"]))
        self.assertEqual((self.dayz / "mods/alpha/Addons/111.pbo").read_bytes(), b"alpha")

    def test_preview_counts_missing_keys(self) -> None:
        """The preview names the key files that an apply would add; an unreadable folder counts all."""
        first = self.service.preview(self.request())
        self.assertEqual((first["missing_key_count"], first["plain_apply_guarded"]), (1, True))
        self.assertEqual([target["current"] for target in first["targets"]], [False, False])
        # The page reads the effective plain-apply policy from the preview
        self.assertIs(self.build_service(guard_plain_apply=False).preview(self.request())["plain_apply_guarded"], False)
        self.publish(self._gate(complete=True), "first")
        preview = self.service.preview(self.request(applied_state_gate(self, self.store, False)))
        self.assertEqual((preview["key_count"], preview["missing_key_count"]), (1, 0))
        self.assertEqual([target["current"] for target in preview["targets"]], [True, True])
        (self.dayz / "keys" / "folder").mkdir()
        self.assertEqual(self.service.preview(self.request())["missing_key_count"], 1)

    def test_restart_plan_check_needs_the_reviewed_plan_with_a_start(self) -> None:
        """The plan check accepts only the reviewed fingerprint of a gate that asked for a start."""
        gate = self._gate(complete=True, start_requested=True)
        reviewed = self.service.preview(self.request(gate))["publication_fingerprint"]
        self.assertIs(self.service.confirm_restart_plan(self.request(gate), reviewed), True)
        plain = self._gate(complete=True)
        for request, fingerprint in ((self.request(gate), "0" * 64), (self.request(plain),
                self.service.preview(self.request(plain))["publication_fingerprint"])):
            with self.assertRaises(ModPublicationError) as raised:
                self.service.confirm_restart_plan(request, fingerprint)
            self.assertEqual(raised.exception.code, "PUBLICATION_PREVIEW_STALE")
        # The check changes nothing, and a plan that writes nothing says so
        self.assertEqual(list(self.paths.publication_journals.rglob("*")), [])
        self.publish(plain, "first")
        applied = applied_state_gate(self, self.store, True)
        reviewed = self.service.preview(self.request(applied))["publication_fingerprint"]
        self.assertIs(self.service.confirm_restart_plan(self.request(applied), reviewed), False)

    def test_restart_of_a_running_server_stops_applies_under_the_guard_and_starts(self) -> None:
        """Real publication service under the restart coordinator: the stop makes the guard pass."""
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        gate = self._gate(complete=True, start_requested=True)
        preview = self.service.preview(self.request(gate))
        accepted = ModRestartCoordinator(self.service, self.lifecycle, None, self.operations).apply({
            "profile_id": "main", "expected_profile_revision": self.profile.revision,
            "expected_semantic_profile_digest": self.profile.semantic_digest,
            "expected_settings_revision": self.settings_revision, "update_operation_id": gate,
            "publication_fingerprint": preview["publication_fingerprint"], "backup_after_stop": False})
        terminal = self.wait(accepted["operation_id"])
        self.assertEqual((terminal.state.value, terminal.result["start_state"], terminal.result["backup"]),
                         ("SUCCEEDED", "STARTED", None))
        self.assertEqual((self.mutex.events, self.lifecycle.calls), (["acquire", "release"], 1))
        self.assertEqual(terminal.result["prestart_check"], {"hashed": 3, "fingerprint_accepted": 0})

    def test_composition_wires_the_guard_and_the_default_policy(self) -> None:
        """The running application always has the guard; the policy is the one constant."""
        composition = build_composition(self.paths.root)
        try:
            self.assertIsInstance(composition.mod_publication._guard, InstallationGuard)
            # Decision D10: the running application refuses a writing plain apply unless the server is stopped
            self.assertIs(composition.mod_publication._guard_plain_apply, True)
        finally:
            composition.operations.shutdown(2)


if __name__ == "__main__":
    unittest.main()
