"""Verify the editor's display-read calls and the host image dialog of the editor API object (3.15, 3.16)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from dayz_serverman.application.mission_map_images import ImageRefusal
from dayz_serverman.host.map_editor_window_api import IMAGE_FILE_TYPES, MissionMapEditorApi


class EditorDisplayApiTests(unittest.TestCase):
    """The profile is bound by the host; the image path never reaches the document."""

    def setUp(self) -> None:
        """Build an API object over a fake bridge, a fake selection service and a fake window."""
        self.bridge = Mock()
        self.bridge.dispatch.return_value = {"success": True, "value": {"available": True, "part": 0, "parts": 2}}
        self.selections = Mock()
        self.selections.register.return_value = ("token-1", object())
        self.window = Mock()
        self.window.create_file_dialog.return_value = ("C:\\private\\maps\\map.png",)
        self.api = MissionMapEditorApi(self.bridge, "alpha", self.selections)
        self.api._bind_window(self.window)

    def requests(self) -> list[dict]:
        """Return the requests that reached the bridge."""
        return [call.args[0] for call in self.bridge.dispatch.call_args_list]

    def test_display_reads_bind_the_profile_and_forward_parameters(self) -> None:
        """Both reads name only the mission key, the tier, the token and the part."""
        self.api.read_tier_layer("key", "Tier1")
        self.api.read_background("key", None, 3)
        first, second = self.requests()
        self.assertEqual((first["method"], first["parameters"]), (
            "read_mission_map_tier_layer", {"mission_key": "key", "tier": "Tier1", "profile_id": "alpha"}))
        self.assertEqual((second["method"], second["parameters"]), (
            "read_mission_map_background", {"mission_key": "key", "selection_id": None, "part": 3,
                                            "profile_id": "alpha"}))

    def test_choice_registers_reads_part_zero_and_adds_the_token(self) -> None:
        """The dialog path goes to the selection service; the answer carries the token, not the path."""
        answer = self.api.select_background("key")
        self.window.create_file_dialog.assert_called_once_with(allow_multiple=False, file_types=IMAGE_FILE_TYPES)
        self.selections.register.assert_called_once_with("alpha", "C:\\private\\maps\\map.png")
        self.assertEqual(self.requests()[0]["parameters"], {"mission_key": "key", "selection_id": "token-1", "part": 0,
                                                           "profile_id": "alpha"})
        self.assertEqual(answer["value"]["selection_id"], "token-1")
        self.assertNotIn("private", str(answer))

    def test_cancel_refusals_and_unbound_dialog(self) -> None:
        """A cancel reads nothing; register refusals keep their reason and budget details."""
        self.window.create_file_dialog.return_value = None
        self.assertEqual(self.api.select_background("key")["value"], {"cancelled": True})
        self.window.create_file_dialog.return_value = "C:\\private\\big.png"
        cases = (("over_budget", "UNSUPPORTED_SNAPSHOT_CONTENT", {"limit": 1, "size": 2, "unit": "bytes"}),
                 ("unsupported_image_type", "UNSUPPORTED_SNAPSHOT_CONTENT", {}),
                 ("not_a_file", "PATH_INVALID", {}), ("reparse_point", "PATH_INVALID", {}), ("unreadable", "PATH_INVALID", {}))
        for reason, code, details in cases:
            self.selections.register.side_effect = ImageRefusal(reason, "Refused.", **details)
            answer = self.api.select_background("key")
            self.assertEqual((answer["success"], answer["error"]["code"]), (False, code))
            self.assertEqual(answer["error"]["details"], {"reason": reason, **details})
            self.assertNotIn("private", str(answer))
        self.bridge.dispatch.assert_not_called()
        unbound = MissionMapEditorApi(self.bridge, "alpha", self.selections)
        self.assertEqual(unbound.select_background("key")["error"]["code"], "INTERNAL_FAILURE")
        self.window.create_file_dialog.side_effect = RuntimeError("C:\\private")
        failed = self.api.select_background("key")
        self.assertEqual(failed["error"]["code"], "INTERNAL_FAILURE")
        self.assertNotIn("private", str(failed))

    def test_refused_part_zero_is_returned_without_a_token(self) -> None:
        """A refused first read stays the bridge answer; no token is added to a failure."""
        self.bridge.dispatch.return_value = {"success": False, "error": {"code": "SOURCE_CHANGED"}}
        answer = self.api.select_background("key")
        self.assertEqual(answer, {"success": False, "error": {"code": "SOURCE_CHANGED"}})


if __name__ == "__main__":
    unittest.main()
