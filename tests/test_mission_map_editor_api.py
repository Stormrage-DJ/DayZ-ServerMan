"""Pin the isolated editor API and its strict profile-bound request envelopes."""

from __future__ import annotations

import inspect
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError, BridgeFacade
from dayz_serverman.host.map_editor_window_api import MissionMapEditorApi


class MissionMapEditorApiTests(unittest.TestCase):
    """Cover binding, unchanged guards, refusal translation and native isolation."""

    def test_public_surface_and_signatures_exclude_other_profile_and_path(self) -> None:
        """Only currently implemented named calls are exposed to the editor."""
        methods = {name for name, member in inspect.getmembers(MissionMapEditorApi, inspect.isfunction)
                   if not name.startswith("_")}
        self.assertEqual(methods, {"load_plan", "save_plan", "toggle_full_screen"})
        self.assertEqual(tuple(inspect.signature(MissionMapEditorApi.save_plan).parameters),
                         ("self", "plan", "expected_revision", "background_selection_id"))
        api = MissionMapEditorApi(Mock(), "alpha", None)
        with self.assertRaises(TypeError):
            api.load_plan("bravo")
        with self.assertRaises(TypeError):
            api.save_plan({}, None, None, profile_id="bravo")

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
