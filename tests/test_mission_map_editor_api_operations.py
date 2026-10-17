"""Pin the three lane reads of the editor API: no profile, a fixed allowlist and the reduced record (8.18).

Rule: Architect 2026-10-15 15:46:42, ruling 4 in evidence 8.10. `operation_events`, `operation` and
`check_recovery` send no `profile_id`. They go through one dispatch path with a fixed allowlist of three methods.
`operation` keeps only five fields of a record of another kind.
"""

from __future__ import annotations

import inspect
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from dayz_serverman.composition import build_composition
from dayz_serverman.host.map_editor_window_api import (
    MAP_OPERATION_KINDS, REDUCED_FIELDS, UNBOUND_METHODS, MissionMapEditorApi,
)

# A full record of another kind; its result holds a path that must not reach the editor document
BACKUP_RECORD = {"schema_version": 1, "revision": 3, "operation_id": "b1", "kind": "CREATE_BACKUP",
                 "state": "SUCCEEDED", "accepted_at": "2026-10-16T10:00:00Z", "started_at": None, "finished_at": None,
                 "cancellation_requested": False, "progress_percent": 100, "progress_phase": "complete",
                 "result": {"archive_path": "C:\\Backups\\alpha.zip"}, "terminal_error": None,
                 "progress_detail": None, "target_profile_id": "alpha", "last_working_phase": "HASH"}


def answer(value: object) -> dict:
    """Return a successful bridge envelope."""
    return {"contract_version": 1, "request_id": "r", "success": True, "value": value}


class EditorLaneReadTests(unittest.TestCase):
    """The three calls with a fake bridge: parameters, allowlist and record forms."""

    def test_the_three_calls_send_no_profile_and_name_only_the_allowlist(self) -> None:
        """Each call names its method with its own parameters and no profile_id; the signatures take no profile."""
        bridge = Mock()
        bridge.dispatch.return_value = answer({})
        api = MissionMapEditorApi(bridge, "alpha", None)
        api.operation_events(7)
        api.operation("op1")
        api.check_recovery()
        requests = [call.args[0] for call in bridge.dispatch.call_args_list]
        self.assertEqual([(item["method"], item["parameters"]) for item in requests], [
            ("read_operation_events", {"after_sequence": 7, "maximum": 100}),
            ("get_operation", {"operation_id": "op1"}),
            ("inspect_mission_map_recovery", {})])
        self.assertTrue(all("profile_id" not in item["parameters"] for item in requests))
        self.assertEqual(len({item["request_id"] for item in requests}), 3)
        # The profile list is the fourth unbound read (9.2); the host reduces its records
        self.assertEqual(UNBOUND_METHODS, frozenset(("get_operation", "read_operation_events",
                                                     "inspect_mission_map_recovery", "list_profiles")))
        for name, parameters in (("operation_events", ("self", "after_sequence")),
                                 ("operation", ("self", "operation_id")), ("check_recovery", ("self",))):
            self.assertEqual(tuple(inspect.signature(getattr(MissionMapEditorApi, name)).parameters), parameters)
        with self.assertRaises(TypeError):
            api.check_recovery(profile_id="bravo")  # type: ignore[call-arg]

    def test_the_unbound_path_refuses_every_other_method(self) -> None:
        """The one dispatch path without a profile refuses a method outside the allowlist and sends nothing."""
        bridge = Mock()
        api = MissionMapEditorApi(bridge, "alpha", None)
        for method in ("save_mission_map_plan", "apply_mission_map", "request_operation_cancellation",
                       "get_application_snapshot"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                api._invoke_unbound(method, {})
        bridge.dispatch.assert_not_called()

    def test_a_record_of_another_kind_keeps_only_the_reduced_fields(self) -> None:
        """Another kind gives five fields; the four map-affecting kinds give the full record; a failure is unchanged."""
        bridge = Mock()
        api = MissionMapEditorApi(bridge, "alpha", None)
        bridge.dispatch.return_value = answer(dict(BACKUP_RECORD))
        reduced = api.operation("b1")
        self.assertEqual(reduced["value"], {"operation_id": "b1", "kind": "CREATE_BACKUP", "state": "SUCCEEDED",
                                            "revision": 3, "progress_phase": "complete"})
        self.assertEqual(tuple(reduced["value"]), REDUCED_FIELDS)
        self.assertEqual({key: reduced[key] for key in ("contract_version", "request_id", "success")},
                         {"contract_version": 1, "request_id": "r", "success": True})
        self.assertEqual(MAP_OPERATION_KINDS, frozenset(("APPLY_MISSION_MAP", "RESTORE_MISSION_MAP_ORIGINAL",
                                                         "RESTORE_MISSION_MAP_PREVIOUS", "APPLY_MEDICAL_FEATURE",
                                                         "ADOPT_MISSION_MAP")))
        for kind in sorted(MAP_OPERATION_KINDS):
            full = dict(BACKUP_RECORD, kind=kind)
            bridge.dispatch.return_value = answer(dict(full))
            self.assertEqual(api.operation("b1")["value"], full, kind)
        failure = {"contract_version": 1, "request_id": "r", "success": False,
                   "error": {"code": "NOT_FOUND", "message": "operation not found", "retryable": False}}
        bridge.dispatch.return_value = dict(failure)
        self.assertEqual(api.operation("b1"), failure)
        events = answer({"session_id": "s", "events": [], "next_cursor": 0})
        bridge.dispatch.return_value = dict(events)
        self.assertEqual(api.operation_events(0), events)


class EditorLaneReadBridgeTests(unittest.TestCase):
    """The three calls through the real composed bridge and lane on a temporary manager root."""

    def test_the_real_bridge_serves_the_three_calls(self) -> None:
        """Events and the recovery answer come back unchanged; a backup-like record is reduced, a map record is not."""
        with tempfile.TemporaryDirectory(prefix="serverman_editor_lane_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                self.assertLessEqual(UNBOUND_METHODS, composition.bridge.allowed_methods)
                api = MissionMapEditorApi(composition.bridge, "alpha", None)
                other = composition.operations.submit("CREATE_BACKUP", lambda _context: {"archive_path": "C:\\x.zip"})
                mapped = composition.operations.submit("APPLY_MEDICAL_FEATURE", lambda _context: {"message": "done"})
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and any(
                        composition.operations.get(record.operation_id).state.value != "SUCCEEDED"
                        for record in (other, mapped)):
                    time.sleep(0.05)
                reduced = api.operation(other.operation_id)
                self.assertTrue(reduced["success"], reduced)
                self.assertEqual(set(reduced["value"]), set(REDUCED_FIELDS))
                self.assertEqual(reduced["value"]["state"], "SUCCEEDED")
                full = api.operation(mapped.operation_id)
                self.assertEqual(full["value"]["result"], {"message": "done"})
                self.assertEqual(full["value"]["kind"], "APPLY_MEDICAL_FEATURE")
                events = api.operation_events(0)
                self.assertTrue(events["success"], events)
                self.assertEqual(set(events["value"]), {"session_id", "events", "next_cursor"})
                self.assertEqual({event["operation_id"] for event in events["value"]["events"]},
                                 {other.operation_id, mapped.operation_id})
                ahead = api.operation_events(events["value"]["next_cursor"] + 1)
                self.assertEqual(ahead["error"]["code"], "INVALID_REQUEST")
                recovery = api.check_recovery()
                self.assertTrue(recovery["success"], recovery)
                self.assertIs(recovery["value"]["blocked"], False)
                missing = api.operation("f" * 32)
                self.assertEqual(missing["error"]["code"], "NOT_FOUND")
            finally:
                composition.operations.shutdown(2)


if __name__ == "__main__":
    unittest.main()
