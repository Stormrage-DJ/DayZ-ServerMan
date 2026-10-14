"""Pin the isolated editor API and its strict profile-bound request envelopes."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS, OWNER_ONLY_METHODS
from dayz_serverman.composition import build_composition
from dayz_serverman.bridge.facade import ApplicationCallError, BridgeFacade
from dayz_serverman.host.map_editor_window_api import MissionMapEditorApi


class MissionMapEditorApiTests(unittest.TestCase):
    """Cover binding, unchanged guards, refusal translation and native isolation."""

    def test_public_surface_and_signatures_exclude_other_profile_and_path(self) -> None:
        """Only currently implemented named calls are exposed to the editor."""
        methods = {name for name, member in inspect.getmembers(MissionMapEditorApi, inspect.isfunction)
                   if not name.startswith("_")}
        self.assertEqual(methods, {"load_plan", "save_plan", "toggle_full_screen", "read_tier_layer",
                                   "read_background", "select_background", "set_aside_plan", "inspect",
                                   "continue_inspection", "cancel_inspection"})
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.save_plan).parameters),
                         ("self", "plan", "expected_revision", "background_selection_id"))
        # The display reads name the mission key; none takes a profile ID or a path
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.read_tier_layer).parameters),
                         ("self", "mission_key", "tier"))
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.read_background).parameters),
                         ("self", "mission_key", "selection_id", "part"))
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.select_background).parameters),
                         ("self", "mission_key"))
        # Set aside names only the loaded mission key (5.3)
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.set_aside_plan).parameters),
                         ("self", "mission_key"))
        # Inspect names the draft and its target; continue and cancel name only the job (4.6)
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.inspect).parameters), ("self", "plan", "target"))
        for name in ("continue_inspection", "cancel_inspection"):
            self.assertEqual(tuple(inspect.signature(getattr(MissionMapEditorApi, name)).parameters), ("self", "job_id"))
        api = MissionMapEditorApi(Mock(), "alpha", None)
        with self.assertRaises(TypeError):
            api.load_plan("bravo")
        with self.assertRaises(TypeError):
            api.save_plan({}, None, None, profile_id="bravo")

    def test_bridge_backed_calls_reach_registered_methods_of_their_a11_class(self) -> None:
        """Each bridge-backed editor call names a method of the real handler table (3.17 display reads, 5.3)."""
        bridge = Mock()
        api = MissionMapEditorApi(bridge, "alpha", None)
        api.load_plan()
        api.save_plan({}, None, None)
        api.read_tier_layer("key", "Tier1")
        api.read_background("key", None, 0)
        api.set_aside_plan("key")
        self.assertEqual(bridge.dispatch.call_args_list[-1].args[0]["parameters"],
                         {"mission_key": "key", "profile_id": "alpha"})
        api.inspect(None, {"object": "zone"})
        self.assertEqual(bridge.dispatch.call_args_list[-1].args[0]["parameters"],
                         {"plan": None, "target": {"object": "zone"}, "profile_id": "alpha"})
        api.continue_inspection("job")
        api.cancel_inspection("job")
        self.assertEqual(bridge.dispatch.call_args_list[-1].args[0]["parameters"], {"job_id": "job", "profile_id": "alpha"})
        called = [call.args[0]["method"] for call in bridge.dispatch.call_args_list]
        self.assertEqual(called, ["load_mission_map", "save_mission_map_plan", "read_mission_map_tier_layer",
                                  "read_mission_map_background", "set_aside_mission_map_plan", "inspect_mission_map",
                                  "continue_mission_map_inspection", "cancel_mission_map_inspection"])
        with tempfile.TemporaryDirectory(prefix="serverman_editor_api_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                self.assertLessEqual(set(called), composition.bridge.allowed_methods)
            finally:
                composition.operations.shutdown(2)
        # Load and the three Inspect calls are observer reads; save, both display reads and set aside are owner methods
        self.assertEqual([name in OBSERVER_READ_METHODS for name in called],
                         [True, False, False, False, False, True, True, True])
        self.assertLessEqual(set(called[1:5]), OWNER_ONLY_METHODS)

    def test_load_and_save_bind_profile_and_preserve_parameters(self) -> None:
        """Host binding stays alpha even when a draft contains another association."""
        bridge = Mock()
        response = {"success": False, "error": {"code": "INVALID_REQUEST"}}
        bridge.dispatch.return_value = response
        api = MissionMapEditorApi(bridge, "alpha", None)
        plan = {"association": {"profile_id": "bravo"}, "background": {"path": "forbidden"}}
        self.assertIs(api.load_plan(), response)
        self.assertIs(api.save_plan(plan, None, None), response)
        self.assertIs(api.save_plan(plan, 9, "selection"), response)
        requests = [call.args[0] for call in bridge.dispatch.call_args_list]
        self.assertEqual(requests[0], {"contract_version": 1, "request_id": "editor-1",
                                      "method": "load_mission_map", "parameters": {"profile_id": "alpha"}})
        self.assertEqual(requests[1]["parameters"], {"profile_id": "alpha", "plan": plan,
                         "expected_revision": None, "background_selection_id": None})
        self.assertEqual(requests[2]["parameters"]["expected_revision"], 9)
        self.assertEqual(requests[2]["parameters"]["background_selection_id"], "selection")
        self.assertEqual(requests[1]["method"], "save_mission_map_plan")

    def test_real_bridge_preserves_error_details_and_revision_refusal(self) -> None:
        """The adapter keeps the existing bridge's strict error envelope intact."""
        def refuse(parameters: dict) -> object:
            """Model a backend revision refusal for the bound profile."""
            self.assertEqual(parameters["profile_id"], "alpha")
            raise ApplicationCallError(ErrorCode.REVISION_CONFLICT, "Reload the plan.",
                                       retryable=True, details={"reason": "stale_revision"})
        api = MissionMapEditorApi(BridgeFacade({"save_mission_map_plan": refuse}), "alpha", None)
        result = api.save_plan({}, 2, None)
        self.assertFalse(result["success"])
        self.assertEqual(result["request_id"], "editor-1")
        self.assertEqual(result["error"]["code"], "REVISION_CONFLICT")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["error"]["details"], {"reason": "stale_revision"})

    def test_concurrent_callbacks_get_unique_request_ids(self) -> None:
        """Counter allocation cannot collide across concurrent browser calls."""
        bridge = Mock()
        api = MissionMapEditorApi(bridge, "alpha", None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: api.load_plan(), range(32)))
        identifiers = {call.args[0]["request_id"] for call in bridge.dispatch.call_args_list}
        self.assertEqual(identifiers, {f"editor-{number}" for number in range(1, 33)})

    def test_full_screen_is_bound_and_native_failures_do_not_leak(self) -> None:
        """An unbound or failed editor call cannot touch another native window."""
        bridge = Mock()
        api = MissionMapEditorApi(bridge, "alpha", None)
        self.assertFalse(api.toggle_full_screen()["success"])
        window = Mock()
        api._bind_window(window)
        self.assertTrue(api.toggle_full_screen()["success"])
        window.toggle_fullscreen.assert_called_once_with()
        window.toggle_fullscreen.side_effect = RuntimeError("private path and traceback")
        refused = api.toggle_full_screen()
        self.assertFalse(refused["success"])
        self.assertNotIn("private", str(refused))
        bridge.dispatch.assert_not_called()
