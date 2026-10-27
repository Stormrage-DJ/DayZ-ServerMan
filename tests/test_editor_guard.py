"""Deletion reservations, registry races and the no-op composition guard."""

from __future__ import annotations

import threading
import tempfile
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from dayz_serverman.application.editor_guard import DelegatingEditorGuard, NoEditorGuard
from dayz_serverman.application.profile_deletion import ProfileDeletionError, ProfileDeletionService
from dayz_serverman.host.api import HostApi
from dayz_serverman.composition import build_composition
from dayz_serverman.cli.exit_codes import REFUSED, record_exit
from dayz_serverman.cli.flow import operation_failure
from tests.test_editor_windows import registry_fixture


class EditorGuardTests(unittest.TestCase):
    """Preserve the guard from the first lane step through success or failure."""

    def test_open_editor_refuses_deletion_with_exact_text(self):
        """All lifecycle states retain profile ownership until native closure."""
        registry, _, _, _ = registry_fixture()
        registry.open("alpha")
        for state in ("OPEN", "SETTLING", "CLOSING"):
            registry._set_state("alpha", state)
            with self.subTest(state=state):
                with self.assertRaisesRegex(ProfileDeletionError,
                                            r"^Close the map editor of this profile first\.$"):
                    with registry.hold_for_deletion("alpha"):
                        self.fail("deletion must not begin")
        self.assertFalse(registry._deleting)

    def test_failed_deletion_releases_and_successful_deletion_becomes_not_found(self):
        """The reservation blocks only its profile and always releases on exit."""
        registry, _, profiles, _ = registry_fixture()
        api = HostApi(None)
        api._set_map_editor_opener(registry.open)
        with self.assertRaises(OSError):
            with registry.hold_for_deletion("alpha"):
                answer = api.open_map_editor("alpha")
                self.assertEqual(answer["error"], {"code": "MUTATION_CONFLICT",
                    "retryable": True, "message": "This profile is being deleted.",
                    "details": {"reason": "profile_deleting"}})
                self.assertTrue(api.open_map_editor("beta")["success"])
                raise OSError("failed deletion")
        self.assertTrue(api.open_map_editor("alpha")["success"])
        registry._entries["alpha"].close.destroy_approved()
        with registry.hold_for_deletion("alpha"):
            del profiles.names["alpha"]
        self.assertEqual(api.open_map_editor("alpha")["error"]["code"], "NOT_FOUND")

    def test_deletion_wins_open_race(self):
        """A held reservation must refuse a concurrent opener until failure releases it."""
        registry, webview, _, _ = registry_fixture()
        entered, release = threading.Event(), threading.Event()
        def deletion():
            """Retain the reservation across simulated lane work."""
            with registry.hold_for_deletion("alpha"):
                entered.set()
                release.wait(2)
        worker = threading.Thread(target=deletion)
        worker.start()
        self.assertTrue(entered.wait(1))
        api = HostApi(None)
        api._set_map_editor_opener(registry.open)
        try:
            self.assertEqual(api.open_map_editor("alpha")["error"]["details"]["reason"], "profile_deleting")
            self.assertEqual(webview.windows, [])
        finally:
            release.set()
            worker.join(1)
        self.assertTrue(api.open_map_editor("alpha")["success"])

    def test_creation_wins_delete_race(self):
        """Deletion cannot pass between native creation and registry publication."""
        registry, webview, _, _ = registry_fixture()
        entered, release = threading.Event(), threading.Event()
        create = webview.create_window
        def slow_create(*args, **kwargs):
            """Hold native creation before the entry exists."""
            entered.set()
            release.wait(2)
            return create(*args, **kwargs)
        webview.create_window = slow_create
        opening = threading.Thread(target=lambda: registry.open("alpha"))
        opening.start()
        self.assertTrue(entered.wait(1))
        failures, deleted = [], threading.Event()
        def deletion():
            """Capture the refusal after opening releases the registry lock."""
            try:
                with registry.hold_for_deletion("alpha"):
                    deleted.set()
            except ProfileDeletionError as error:
                failures.append(str(error))
        deleting = threading.Thread(target=deletion)
        deleting.start()
        self.assertFalse(deleted.wait(0.03))
        release.set()
        opening.join(1)
        deleting.join(1)
        self.assertEqual(failures, ["Close the map editor of this profile first."])
        self.assertFalse(deleted.is_set())

    def test_service_enters_guard_before_profile_read_and_retains_it(self):
        """No preflight or change can occur outside the deletion reservation."""
        events = []
        @contextmanager
        def held(profile_id):
            """Record the first and last guarded lane boundaries."""
            events.append(("enter", profile_id))
            try:
                yield
            finally:
                events.append(("exit", profile_id))
        profiles = SimpleNamespace(read=Mock(side_effect=AssertionError("unguarded read")))
        service = ProfileDeletionService(profiles, None, None, None, None, None,
                                         editor_guard=SimpleNamespace(hold_for_deletion=held))
        def work(*args, **kwargs):
            """Check the guard at the existing lane implementation boundary."""
            self.assertEqual(events, [("enter", "alpha")])
            raise OSError("failed preflight")
        with patch.object(service, "_delete_guarded", side_effect=work), self.assertRaises(OSError):
            service.delete("alpha", 0)
        profiles.read.assert_not_called()
        self.assertEqual(events, [("enter", "alpha"), ("exit", "alpha")])

    def test_delegating_guard_is_noop_until_runtime_binding(self):
        """Commands and observers retain their accepted no-window deletion semantics."""
        guard = DelegatingEditorGuard()
        with NoEditorGuard().hold_for_deletion("alpha"), guard.hold_for_deletion("alpha"):
            pass
        registry, _, _, _ = registry_fixture()
        registry.open("alpha")
        guard.bind(registry)
        with self.assertRaises(ProfileDeletionError):
            with guard.hold_for_deletion("alpha"):
                self.fail("bound guard must refuse")

    def test_owner_composition_maps_guard_refusal_through_operation_and_cli(self):
        """The first guarded lane step becomes DELETION_BLOCKED and the existing CLI refusal."""
        with tempfile.TemporaryDirectory(prefix="serverman_editor_guard_") as root:
            composition = build_composition(Path(root))
            self.addCleanup(composition.operations.shutdown, 2)
            registry, _, _, _ = registry_fixture()
            registry.open("alpha")
            composition.editor_guard.bind(registry)
            answer = composition.bridge.dispatch({"contract_version": 1, "request_id": "guard",
                "method": "delete_profile", "parameters": {"profile_id": "alpha", "expected_revision": 0}})
            self.assertTrue(answer["success"])
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                record = composition.operations.get(answer["value"]["operation_id"])
                if record.state.value == "FAILED":
                    break
                time.sleep(0.01)
            self.assertEqual(record.state.value, "FAILED")
            self.assertEqual(record.terminal_error.code, "DELETION_BLOCKED")
            self.assertEqual(record.terminal_error.message, "Close the map editor of this profile first.")
            document = record.to_dict()
            self.assertEqual(record_exit(document), REFUSED)
            self.assertNotEqual(REFUSED, 0)
            failure = operation_failure(document, "DELETE_PROFILE", REFUSED)
            self.assertEqual(failure.code, "DELETION_BLOCKED")
            self.assertIn("Close the map editor of this profile first.",
                          "".join(part.text for part in failure.message.parts))
            composition.operations.shutdown(2)
