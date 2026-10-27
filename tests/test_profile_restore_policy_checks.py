"""Interruption, rollback, staleness, schema 4 and the visible texts of a restore as a new profile (10.37)."""

from __future__ import annotations

import io
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_serverman.application.backups import EDITOR_RECORDS_OMITTED
from dayz_serverman.application.mission_map_restore_review import _original_answer, _restored_copy
from dayz_serverman.application.profile_restore_editor_plan import ProfileEditorPlanner
from dayz_serverman.cli.interrupts import Interrupts
from dayz_serverman.cli.map_restore_render import restore_blocks
from dayz_serverman.cli.output import Output, render_blocks
from dayz_serverman.cli.waiter import Waiter
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.domain.mission_map_restore_texts import RESTORED_COPY_TEXT, SHARED_ORIGINAL_TEXT
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.backup_catalog import list_catalog
from dayz_serverman.repositories.mission_map_preview_base import PreviewRecords
from dayz_serverman.repositories.profile_restore_editor_shapes import EditorFile
from dayz_serverman.repositories.profiles import ProfileNotFound
from dayz_serverman.repositories.selected_backup import SelectedBackups
from tests.profile_restore_editor_fixtures import Crash, original_fields
from tests.profile_restore_policy_fixtures import OTHER, PolicyFixture

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
OLDER = "This older backup lacks a complete profile definition."


class InterruptionTests(PolicyFixture):
    """The editor records commit with the profile record or roll back with it."""

    def tree(self) -> dict[str, bytes]:
        """Return every file of the editor area."""
        return {path.relative_to(self.area).as_posix(): path.read_bytes()
                for path in sorted(self.area.rglob("*")) if path.is_file()}

    def test_a_failure_rolls_the_whole_restore_back(self) -> None:
        self.write_records()
        backup, before = self.backup(), self.tree()
        self.storage.hook = lambda phase: (_ for _ in ()).throw(OSError("injected")) if phase == "FILES_PUBLISHED" else None
        with self.assertRaises(OSError):
            self.restore(backup, "allocate_new")
        self.assertEqual(self.tree(), before)
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")

    def test_an_interruption_is_rolled_back_by_recovery(self) -> None:
        self.write_records()
        backup, before = self.backup(), self.tree()
        self.storage.hook = lambda phase: (_ for _ in ()).throw(Crash()) if phase == "PUBLISHED_EDITOR" else None
        with self.assertRaises(Crash):
            self.restore(backup, "allocate_new")
        self.assertNotEqual(self.tree(), before)
        self.storage.hook = lambda _phase: None
        self.assertFalse(self.storage.inspect(self.dayz)["blocked"])
        self.assertEqual(self.tree(), before)

    def test_a_copy_that_differs_from_its_archived_source_refuses_before_the_journal(self) -> None:
        self.write_records()
        backup, before = self.backup(), self.tree()
        real = ProfileEditorPlanner.plan

        def tampered(planner, *arguments):  # type: ignore[no-untyped-def]
            """Give each restore copy another source original than the verified archive entry."""
            part = real(planner, *arguments)
            other = sealed(original_fields("e5" * 16, "mission", OTHER))
            from dayz_serverman.application.restore_editor_files import record_bytes
            files = tuple(EditorFile(file.kind, file.relative, file.data, record_bytes(other, 0))
                          if file.source else file for file in part.files)
            return type(part)(files, part.notes, part.summary)
        with patch.object(ProfileEditorPlanner, "plan", tampered), self.assertRaises(ValueError):
            self.restore(backup, "allocate_new")
        self.assertEqual(self.journals.records(), [])
        self.assertEqual(self.tree(), before)

    def test_an_original_that_appears_after_the_preview_makes_it_stale(self) -> None:
        self.write_records()
        backup = self.backup()
        request = self.request(backup, "allocate_new")
        preview = self.service.preview(request)
        _mission, runtime = self.destination(preview)
        self.write_original(runtime, "runtime", OTHER)
        with self.assertRaises(RevisionConflict):
            self.service.apply({**request, "expected_manifest_digest": preview["manifest_digest"],
                                "preview_fingerprint": preview["preview_fingerprint"],
                                "overwrite_confirmation": None}, "stale", lambda *_: None)
        self.assertEqual(self.journals.records(), [])


class SchemaFourTests(PolicyFixture):
    """A schema 4 archive has a complete profile definition everywhere."""

    def test_the_catalog_and_the_selection_accept_schema_4(self) -> None:
        self.write_records()
        backup = self.backup()
        archive = self.root / "backups" / f"{backup}.zip"
        catalog = list_catalog(self.root / "backups")
        entry = next(item for item in catalog["backups"] if item["backup_id"] == backup)
        self.assertEqual((entry["can_restore_profile"], entry["reconstruction_reason"]),
                         (True, "Complete profile metadata is available."))
        self.assertTrue(SelectedBackups().inspect(str(archive))["can_restore_profile"])
        self.assertNotIn(OLDER, str(catalog))
        self.assertEqual(self.service.preview(self.request(backup, "allocate_new"))["backup_id"], backup)


class TextTests(unittest.TestCase):
    """The 8.8 restored-copy sentence and the 10.9 notice in the CLI and the window."""

    def test_restore_original_names_a_restored_copy_after_the_shared_text(self) -> None:
        original = sealed(original_fields("a1" * 16, "mission", b"v"))
        records = PreviewRecords(original, None, None)
        base = type("Base", (), {"source": "original", "files": {}})()
        answer = _original_answer(records, [{"profile_id": "other"}], base, True)  # type: ignore[arg-type]
        self.assertEqual(answer["text"], f"{SHARED_ORIGINAL_TEXT} {RESTORED_COPY_TEXT}")
        # The sentence shows only after the shared text, once (Product Owner 07:28:16)
        self.assertIsNone(_original_answer(records, [], base, True)["text"])  # type: ignore[arg-type,index]
        self.assertEqual(_original_answer(records, [{"profile_id": "other"}], base, False)["text"],  # type: ignore[arg-type,index]
                         SHARED_ORIGINAL_TEXT)
        self.assertEqual(RESTORED_COPY_TEXT, "This profile also has a different original from a restored backup. "
                         "Restore original does not use that original.")

    def test_the_cli_prints_the_restored_copy_sentence_once_after_the_shared_text(self) -> None:
        answer = {"mode": "original", "refused": False, "refusals": [], "other_profiles": [],
                  "original": {"shared": True, "text": f"{SHARED_ORIGINAL_TEXT} {RESTORED_COPY_TEXT}",
                               "kept_changes": None, "captured_at": "2026-10-10T12:00:00Z"},
                  "affected_files": [], "external_changes": []}
        text = render_blocks(restore_blocks({"display_name": "Restored"}, "original", answer))
        self.assertEqual(text.count(RESTORED_COPY_TEXT), 1)
        self.assertLess(text.index(SHARED_ORIGINAL_TEXT), text.index(RESTORED_COPY_TEXT))

    def test_a_restored_copy_in_the_association_folder_is_found(self) -> None:
        import tempfile
        from dayz_serverman.application.restore_editor_files import record_bytes
        with tempfile.TemporaryDirectory() as temporary:
            area, key = Path(temporary), "a1" * 16
            self.assertFalse(_restored_copy(area, "restored", key))
            folder = area / "profiles" / "restored" / key
            folder.mkdir(parents=True)
            (folder / "restored-mission-pr-1.json").write_bytes(
                record_bytes(sealed(original_fields(key, "mission", b"v")), 0))
            self.assertTrue(_restored_copy(area, "restored", key))

    def test_the_cli_prints_the_notice_after_the_success_line(self) -> None:
        cases = (("CREATE_BACKUP", {"editor_records_omitted": True}, "Backup created."),
                 ("STOP_SERVER", {"backup": {"editor_records_omitted": True}}, "Server stopped."),
                 ("RESTART_SERVER", {"backup": {"editor_records_omitted": True}}, "Server restarted."))
        for kind, result, success in cases + (("CREATE_BACKUP", {"editor_records_omitted": False}, "Backup created."),):
            with self.subTest(kind=kind, result=result):
                stderr = io.StringIO()
                record = {"operation_id": "op", "kind": kind, "state": "SUCCEEDED", "progress_phase": "complete",
                          "progress_percent": 100, "terminal_error": None, "last_working_phase": "complete",
                          "result": result}
                Waiter(lambda *_a, **_k: record, Output(io.StringIO(), stderr, json_mode=False), Interrupts(),
                       sleep=lambda _s: None, clock=lambda: 0.0, terminal=False).wait("op", kind)
                text = stderr.getvalue()
                shown = EDITOR_RECORDS_OMITTED in text
                self.assertEqual(shown, result != {"editor_records_omitted": False})
                if shown:
                    self.assertLess(text.index(success), text.index(EDITOR_RECORDS_OMITTED))

    def test_schema_4_backups_and_the_editor_plan_are_wired(self) -> None:
        package = FRONTEND.parent / "python" / "dayz_serverman"
        composition = (package / "composition.py").read_text(encoding="utf-8")
        wiring = "backups.bind_editor(mission_map.plans, mission_map.locks, paths.mission_map)"
        self.assertLess(composition.index("medical_features.bind_editor("), composition.index(wiring))
        self.assertIn("editor=ProfileEditorPlanner(paths)",
                      (package / "profile_restore_composition.py").read_text(encoding="utf-8"))

    def test_the_window_text_is_the_python_text(self) -> None:
        script = (FRONTEND / "operation_results.js").read_text(encoding="utf-8")
        found = re.search(r'const OPERATION_EDITOR_RECORDS_OMITTED = "([^"]*)"\s*\+\s*"([^"]*)";', script)
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1) + found.group(2), EDITOR_RECORDS_OMITTED)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
