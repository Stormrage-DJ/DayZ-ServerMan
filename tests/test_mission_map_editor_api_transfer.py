"""Leaf 9.1: Export plan and Import plan of the editor API on a real composition, with a fake dialog window.

Rules: Architect 2026-10-17 17:10:42 in evidence 9.1, sections 2 to 5 and 11. The page never gets a path. The
export writes the draft that the page sends. The import keeps the file text in the host behind a selection ID.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from folder_lock_fixtures import tree_hashes  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture  # noqa: E402
from test_mission_map_plan import circle, stored_plan  # noqa: E402
from tests.test_mission_map_prototype import export as prototype_export  # noqa: E402

from dayz_serverman.adapters.windows import shared_files  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.mission_map_plan import to_portable  # noqa: E402
from dayz_serverman.host.map_editor_transfer_api import protected_roots  # noqa: E402
from dayz_serverman.host.map_editor_window_api import MissionMapEditorApi  # noqa: E402


class DialogWindow:
    """A native window whose file dialogs give the queued answers and record their options."""

    def __init__(self) -> None:
        """Start with no answer."""
        self.answers: list[object] = []
        self.calls: list[tuple[object, dict]] = []

    def create_file_dialog(self, dialog_type, **options):
        """Give the next answer; an exception answer is raised."""
        self.calls.append((dialog_type, options))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class EditorTransferFixture(MapFixture):
    """The fixture profile with a saved plan of one circle (revision 0), a bound editor API and a user folder."""

    def setUp(self) -> None:
        """Build an owner composition and bind an editor API to a fake window."""
        super().setUp()
        self.files = self.manager.parent / "user files"
        self.files.mkdir()
        self.composition = build_composition(self.manager)
        self.addCleanup(self.close)
        settings = self.composition.settings
        self.api = MissionMapEditorApi(self.composition.bridge, PROFILE_ID, None, dialogs=("open", "save"),
                                       protected=lambda: protected_roots(self.manager, settings))
        self.window = DialogWindow()
        self.api._bind_window(self.window)

    def close(self) -> None:
        """Stop the composition."""
        self.composition.shutdown.request_shutdown()
        self.composition.shutdown.wait_for_close(5)

    def loaded(self) -> dict:
        """Return the load answer of the bound profile."""
        return self.api.load_plan()["value"]

    def file(self, content: bytes | str | dict, name: str = "plan.json") -> Path:
        """Write one user file and return its path."""
        data = json.dumps(content) if isinstance(content, dict) else content
        path = self.files / name
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def select(self, path: Path) -> dict:
        """Choose one file in the open dialog and return the answer."""
        self.window.answers.append((str(path),))
        return self.api.select_import_file()

    def assert_no_path(self, answer: dict) -> None:
        """No answer names the folder of the user file."""
        self.assertNotIn(str(self.files), json.dumps(answer))
        self.assertNotIn(json.dumps(str(self.files))[1:-1], json.dumps(answer))


class ExportTests(EditorTransferFixture):
    """`export_plan`: the draft, the D1 check, the dialog and the D17 rules of the window."""

    def export(self, plan: dict, *answers: object) -> dict:
        """Export one draft with the queued dialog answers."""
        self.window.answers.extend(answers)
        return self.api.export_plan(plan)

    def test_the_file_holds_the_text_of_map_export_and_writes_no_game_file(self) -> None:
        """Same bytes as the bridge export of the saved plan; the save dialog options; no game file, no log line."""
        before = tree_hashes(self.dayz, self.manager / "logs")
        target = self.files / "chosen.json"
        answer = self.export(self.loaded()["plan"], (str(target),))
        self.assertEqual(answer["value"], {"file_name": "chosen.json"})
        bridge = self.composition.bridge.dispatch({"contract_version": 1, "request_id": "t", "method":
                                                   "export_mission_map_plan", "parameters": {"profile_id": PROFILE_ID}})
        self.assertEqual(target.read_bytes(), bridge["value"]["text"].encode("utf-8"))
        self.assertEqual(self.window.calls, [("save", {"save_filename": "test.terrain-map-plan.json",
                                                       "file_types": ("Map plans (*.json)",)})])
        self.assertEqual(tree_hashes(self.dayz, self.manager / "logs"), before)
        self.assert_no_path(answer)

    def test_the_draft_is_exported_also_at_a_stale_revision(self) -> None:
        """A dirty draft exports its own objects, also after another save; a local image path never leaves."""
        draft = self.loaded()["plan"]
        self.composition.mission_map.plans.save(PROFILE_ID, dict(draft, objects=[circle("newer")]), 0)
        draft = dict(draft, objects=[circle("mine", 300.0, 300.0)], background=stored_plan()["background"])
        target = self.files / "draft.json"
        self.assertTrue(self.export(draft, (str(target),))["success"])
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in written["objects"]], ["mine"])
        self.assertNotIn("path", written["background"])
        self.assertNotIn("C:\\maps", target.read_text(encoding="utf-8"))
        self.assertEqual(self.loaded()["revision"], 1)

    def test_a_draft_that_fails_d1_is_refused_before_the_dialog(self) -> None:
        """Each problem names its object; the dialog does not open and no file is written."""
        draft = self.loaded()["plan"]
        bad = dict(draft, objects=[dict(circle("far"), centre={"x": 5000.0, "z": 5000.0})])
        answer = self.export(bad)
        error = answer["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("INVALID_REQUEST", "invalid_document"))
        self.assertEqual([problem["object_id"] for problem in error["details"]["problems"]], ["far"])
        self.assertEqual((self.window.calls, list(self.files.iterdir())), ([], []))

    def test_cancel_and_a_dialog_exception(self) -> None:
        """A cancel is {cancelled: true}; an exception is INTERNAL_FAILURE with the fixed text; nothing is written."""
        draft = self.loaded()["plan"]
        self.assertEqual(self.export(draft, None)["value"], {"cancelled": True})
        self.assertEqual(self.export(draft, ())["value"], {"cancelled": True})
        error = self.export(draft, RuntimeError("native"))["error"]
        self.assertEqual((error["code"], error["message"]), ("INTERNAL_FAILURE",
                                                             "The map editor could not open the file dialog."))
        self.assertEqual(list(self.files.iterdir()), [])

    def test_protected_roots_folders_links_and_a_failed_write(self) -> None:
        """Manager root, backup folder, DayZ root and a junction into one are protected; a folder is refused."""
        draft = self.loaded()["plan"]
        settings = self.composition.settings
        backups = Path(settings.backup_root(settings.load()))
        backups.mkdir(parents=True, exist_ok=True)
        cases = [(self.manager / "config" / "x.json", "protected"), (backups / "x.json", "protected"),
                 (self.dayz / "x.json", "protected"), (self.files, "folder_or_link")]
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(self.dayz), str(self.files / "link"))
            cases.append((self.files / "link" / "x.json", "protected"))
        before = tree_hashes(self.manager, self.dayz)
        for path, reason in cases:
            with self.subTest(path=path.name):
                error = self.export(draft, (str(path),))["error"]
                self.assertEqual((error["details"]["reason"], error["details"]["file_name"]), (reason, path.name))
                self.assert_no_path(error)
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)
        with patch.object(shared_files, "replace_file", side_effect=PermissionError(13, "denied")):
            error = self.export(draft, (str(self.files / "plan.json"),))["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("STORAGE_FAILURE", "not_written"))
        self.assertEqual(sorted(path.name for path in self.files.iterdir()), ["link"] if os.name == "nt" else [])

    def test_protected_roots_follow_the_rule_of_map_export(self) -> None:
        """The manager root, the backup folder and the DayZ root of the settings."""
        settings = self.composition.settings
        current = settings.load()
        self.assertEqual(protected_roots(self.manager, settings),
                         (self.manager, Path(settings.backup_root(current)), Path(current.dayz_root)))


class ImportTests(EditorTransferFixture):
    """`select_import_file`, `preview_import` and `import_plan`."""

    def portable(self, objects: list[dict], **changes) -> dict:
        """Return a portable plan of the fixture terrain with these objects."""
        return dict(to_portable(stored_plan(objects)), **changes)

    def test_cancel_and_the_file_problems(self) -> None:
        """Cancel; unreadable, too large, not UTF-8 and not JSON have their kind and the base file name only."""
        self.window.answers.append(None)
        self.assertEqual(self.api.select_import_file()["value"], {"cancelled": True})
        cases = [(self.files / "missing.json", "unreadable"), (self.file(b"\xff\xfe", "bytes.json"), "not_utf8"),
                 (self.file('{"a": NaN}', "nan.json"), "not_json"), (self.file('{"a": 1, "a": 2}', "twice.json"), "not_json"),
                 (self.file(b" " * (8 * 1024 * 1024 + 1), "large.json"), "too_large")]
        for path, kind in cases:
            with self.subTest(kind=kind, name=path.name):
                answer = self.select(path)
                self.assertEqual((answer["error"]["details"]["kind"], answer["error"]["details"]["file_name"]),
                                 (kind, path.name))
                self.assert_no_path(answer)
        self.assertEqual(self.window.calls[0], ("open", {"allow_multiple": False,
                                                         "file_types": ("Map plans (*.json)", "All files (*.*)")}))

    def test_both_previews_one_revision_and_one_guarded_save(self) -> None:
        """Both previews at revision 0; a file change after the selection does not change the commit."""
        path = self.file(self.portable([circle("imp", 400.0, 400.0)]))
        answer = self.select(path)["value"]
        self.assert_no_path(answer)
        previews = answer["previews"]
        self.assertEqual((previews["replace"]["mode"], previews["merge"]["mode"]), ("replace", "merge"))
        self.assertEqual({previews["replace"]["destination_revision"], previews["merge"]["destination_revision"]}, {0})
        self.file(self.portable([circle("other")]))
        again = self.api.preview_import(answer["selection_id"])["value"]
        self.assertEqual(again["previews"]["merge"]["object_count"], 1)
        saved = self.api.import_plan(answer["selection_id"], "merge", 0, False)["value"]
        self.assertEqual(saved["revision"], 1)
        self.assertEqual([item["id"] for item in self.loaded()["plan"]["objects"]], ["imp", "observer-loot"])
        # A success drops the pending file
        error = self.api.import_plan(answer["selection_id"], "merge", 1, False)["error"]
        self.assertEqual(error["details"]["reason"], "import_selection_unknown")

    def test_unknown_selection_stale_revision_and_confirmation(self) -> None:
        """An unknown ID; a stale revision refuses before the confirmation; a refusal keeps the pending file."""
        for call in (lambda: self.api.preview_import("x" * 32), lambda: self.api.import_plan(None, "replace", 0, True)):
            self.assertEqual(call()["error"]["details"]["reason"], "import_selection_unknown")
        answer = self.select(self.file(self.portable([circle("imp")], terrain=dict(
            stored_plan()["terrain"], terrain_id="other.terrain"))))["value"]
        self.assertTrue(answer["previews"]["replace"]["confirmation_required"])
        error = self.api.import_plan(answer["selection_id"], "replace", 0, False)["error"]
        self.assertEqual(error["details"]["reason"], "confirmation_required")
        self.composition.mission_map.plans.save(PROFILE_ID, self.loaded()["plan"] | {"global_abundance": 25}, 0)
        error = self.api.import_plan(answer["selection_id"], "replace", 0, True)["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("REVISION_CONFLICT", "stale_revision"))
        self.assertEqual(self.api.import_plan(answer["selection_id"], "replace", 1, True)["value"]["revision"], 2)

    def test_a_prototype_file_lists_its_unmapped_items_and_saves_only_when_confirmed(self) -> None:
        """R21: the host passes the text unchanged; the unmapped items need the confirmation."""
        answer = self.select(self.file(prototype_export(), "pripyat-export.json"))["value"]
        report = answer["previews"]["replace"]
        self.assertEqual((len(report["unmapped"]), report["placement_review"]), (4, True))
        error = self.api.import_plan(answer["selection_id"], "replace", 0, False)["error"]
        self.assertEqual(error["details"]["reason"], "confirmation_required")
        self.assertEqual(self.loaded()["revision"], 0)
        self.assertEqual(self.api.import_plan(answer["selection_id"], "replace", 0, True)["value"]["revision"], 1)

    def test_an_invalid_document_keeps_the_selection_for_the_refused_dialog(self) -> None:
        """D1 problems with the selection ID and the file name; a plan limit stays in the report of its mode."""
        bad = self.portable([dict(circle("bad"), radius=-1)])
        error = self.select(self.file(bad))["error"]
        self.assertEqual(error["details"]["reason"], "invalid_document")
        self.assertEqual((len(error["details"]["selection_id"]), error["details"]["file_name"]), (32, "plan.json"))

    def test_two_thousand_objects_preview_in_one_host_call_within_two_seconds(self) -> None:
        """The 2,000-object file: both previews in one call; Merge is over the limit and has the bridge text."""
        objects = [dict(circle(f"c{index}", 20.0 + index % 40 * 20, 20.0 + index // 40 * 18, 5.0),
                        name=f"Loot circle {index + 1}") for index in range(2000)]
        path = self.file(self.portable(objects))
        start = time.perf_counter()
        answer = self.select(path)["value"]
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0, f"one host call took {elapsed:.3f} s")
        merge = answer["previews"]["merge"]
        self.assertIsNone(answer["previews"]["replace"]["refusal"])
        self.assertEqual(merge["refusal_text"], "The result would hold 2001 objects and 0 polygon vertices. The limits "
                         "are 2,000 objects and 50,000 vertices. Nothing was changed.")
        self.assertEqual(answer["previews"]["replace"]["object_count"], 2000)


if __name__ == "__main__":
    unittest.main()
