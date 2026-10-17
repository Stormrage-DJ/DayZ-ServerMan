"""Leaf 9.2: Copy from another profile in the editor API on a real composition (host unit tests).

Rules: Architect 2026-10-17 17:10:42 in evidence 9.1, sections 2 and 6. The source list reads `list_profiles` and
keeps three fields. The copy uses the owner methods, binds the destination in the host and only reads the source.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from folder_lock_fixtures import tree_hashes  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID  # noqa: E402
from map_cli_fixtures import PROFILE_ID  # noqa: E402
from test_mission_map_editor_api_transfer import EditorTransferFixture  # noqa: E402
from test_mission_map_plan import circle, stored_plan  # noqa: E402

from dayz_serverman.host.map_editor_window_api import UNBOUND_METHODS, MissionMapEditorApi  # noqa: E402

FAR = {"x_min": 0.0, "z_min": 0.0, "width": 10000.0, "height": 10000.0}


class CopySourceListTests(unittest.TestCase):
    """`copy_sources` with a fake bridge: the dispatch, the reduction and the marks."""

    def test_the_list_keeps_three_fields_and_marks_self_and_a_missing_mission(self) -> None:
        """One unbound `list_profiles` call without a profile; no other profile field reaches the page."""
        bridge = Mock()
        records = [{"profile_id": "alpha", "display_name": "Alpha", "mission_root": "m.enoch", "game_port": 2302,
                    "server_executable": "C:\\secret\\DayZServer_x64.exe", "revision": 3},
                   {"profile_id": "bravo", "display_name": "Bravo", "mission_root": None, "mods": ["@x"]},
                   {"profile_id": "charlie", "display_name": "Charlie", "mission_root": "m.chernarusplus"}]
        bridge.dispatch.return_value = {"success": True, "request_id": "r", "value": records}
        answer = MissionMapEditorApi(bridge, "alpha", None).copy_sources()
        request = bridge.dispatch.call_args.args[0]
        self.assertEqual((request["method"], request["parameters"]), ("list_profiles", {}))
        self.assertIn("list_profiles", UNBOUND_METHODS)
        self.assertEqual(answer["value"]["profiles"], [
            {"profile_id": "alpha", "display_name": "Alpha", "mission_root": "m.enoch", "disabled": "self"},
            {"profile_id": "bravo", "display_name": "Bravo", "mission_root": None, "disabled": "no_mission"},
            {"profile_id": "charlie", "display_name": "Charlie", "mission_root": "m.chernarusplus", "disabled": None}])
        self.assertNotIn("secret", json.dumps(answer))

    def test_a_failed_list_read_is_returned_unchanged(self) -> None:
        """A bridge failure reaches the page as it is."""
        bridge = Mock()
        failure = {"success": False, "request_id": "r", "error": {"code": "STORAGE_FAILURE", "message": "x"}}
        bridge.dispatch.return_value = failure
        self.assertEqual(MissionMapEditorApi(bridge, "alpha", None).copy_sources(), failure)


class CopyTests(EditorTransferFixture):
    """`preview_copy` and `copy_plan` into the fixture profile from the second profile of the fixture."""

    def save_source(self, objects: list[dict], **terrain: object) -> None:
        """Save the first plan of the second profile, with terrain changes."""
        plans = self.composition.mission_map.plans
        plan = stored_plan(objects)
        plan["terrain"].update(terrain)
        plan["association"] = plans.active_association(OTHER_ID)
        plan["background"] = None
        plans.save(OTHER_ID, plan, None)

    def source_state(self) -> tuple[dict, int]:
        """Return the hashes of the source folders and the source revision."""
        folders = {path.parent for path in (self.manager / "data").rglob("plan.json") if OTHER_ID in path.parts}
        return tree_hashes(*folders), self.composition.mission_map.plans.load(OTHER_ID)["revision"]

    def test_the_real_list_marks_the_bound_profile(self) -> None:
        """The fixture has two profiles; the editor of the first lists itself disabled and the second enabled."""
        profiles = self.api.copy_sources()["value"]["profiles"]
        self.assertEqual({item["profile_id"]: item["disabled"] for item in profiles},
                         {PROFILE_ID: "self", OTHER_ID: None})
        self.assertTrue(all(set(item) == {"profile_id", "display_name", "mission_root", "disabled"} for item in profiles))

    def test_same_terrain_copies_in_one_save_and_leaves_the_source_unchanged(self) -> None:
        """Both previews at revision 0; Merge saves revision 1; the source folder and revision stay."""
        self.save_source([circle("src1", 400.0, 400.0)])
        before = self.source_state()
        answer = self.api.preview_copy(OTHER_ID)["value"]
        self.assertEqual(answer["source_profile_id"], OTHER_ID)
        previews = answer["previews"]
        self.assertEqual((previews["replace"]["mode"], previews["merge"]["mode"]), ("replace", "merge"))
        self.assertEqual({previews["replace"]["destination_revision"], previews["merge"]["destination_revision"]}, {0})
        self.assertFalse(previews["merge"]["placement_review"])
        self.assertEqual(self.source_state(), before)
        saved = self.api.copy_plan(OTHER_ID, "merge", 0, False)["value"]
        self.assertEqual(saved["revision"], 1)
        self.assertEqual([item["id"] for item in self.loaded()["plan"]["objects"]], ["src1", "observer-loot"])
        self.assertEqual(self.source_state(), before)
        self.assert_no_path(answer)
        self.assertNotIn("plan", saved)

    def test_a_different_terrain_needs_the_confirmation_and_excludes_nothing_silently(self) -> None:
        """Placement review and the left-out object; no confirmation gives the refusal and changes nothing."""
        self.save_source([circle("inside"), circle("far", x=5000.0)], terrain_id="other.terrain", bounds=FAR)
        before = self.source_state()
        report = self.api.preview_copy(OTHER_ID)["value"]["previews"]["replace"]
        self.assertTrue(report["placement_review"] and report["confirmation_required"])
        self.assertEqual([item["id"] for item in report["excluded"]], ["far"])
        error = self.api.copy_plan(OTHER_ID, "replace", 0, False)["error"]
        self.assertEqual(error["details"]["reason"], "confirmation_required")
        self.assertEqual(self.loaded()["revision"], 0)
        self.assertEqual(self.api.copy_plan(OTHER_ID, "replace", 0, True)["value"]["revision"], 1)
        self.assertEqual([item["id"] for item in self.loaded()["plan"]["objects"]], ["inside"])
        self.assertEqual(self.source_state(), before)

    def test_a_stale_revision_is_refused_before_the_confirmation(self) -> None:
        """Another save after the preview gives the stale code, and the source stays."""
        self.save_source([circle("src1")])
        before = self.source_state()
        self.api.preview_copy(OTHER_ID)
        self.composition.mission_map.plans.save(PROFILE_ID, self.loaded()["plan"] | {"global_abundance": 25}, 0)
        error = self.api.copy_plan(OTHER_ID, "replace", 0, True)["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("REVISION_CONFLICT", "stale_revision"))
        self.assertEqual(self.source_state(), before)

    def test_the_source_refusals_have_their_reasons(self) -> None:
        """No saved plan, the bound profile as the source and an unknown profile; nothing is written."""
        before = self.loaded()["revision"]
        error = self.api.preview_copy(OTHER_ID)["error"]
        self.assertEqual(error["details"]["reason"], "no_valid_plan")
        self.assertEqual(error["message"], "The source profile has no valid saved map plan to copy. Nothing was changed.")
        self.assertEqual(error["details"]["source_profile_id"], OTHER_ID)
        self.assertEqual(self.api.preview_copy(PROFILE_ID)["error"]["details"]["reason"], "source_is_destination")
        self.assertFalse(self.api.preview_copy("missing-profile")["success"])
        self.assertFalse(self.api.copy_plan(OTHER_ID, "merge", 0, True)["success"])
        self.assertEqual(self.loaded()["revision"], before)

    def test_the_page_cannot_name_a_destination(self) -> None:
        """The bound profile is the destination; a call with an extra destination argument is a type error."""
        with self.assertRaises(TypeError):
            self.api.preview_copy(OTHER_ID, profile_id=OTHER_ID)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            self.api.copy_plan(OTHER_ID, "merge", 0, True, OTHER_ID)  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
