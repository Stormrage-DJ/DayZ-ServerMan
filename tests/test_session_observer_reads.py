"""Observer sessions (A4, criterion 14): every read command leaves the manager root and the DayZ root unchanged."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import PROFILE_ID, populate, read_calls, tree_hashes  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderReader  # noqa: E402
from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.session_observer import BridgeCallFailed, open_observer_session, pending_recoveries  # noqa: E402


def run_reads(session, calls) -> dict[str, object]:
    """Run every call through the observer runner; return each value or the bridge error code."""
    answers: dict[str, object] = {}
    for method, parameters in calls:
        try:
            answers[method] = session.call(method, parameters)
        except BridgeCallFailed as error:
            answers[method] = error.error["code"]
    return answers


class ObserverReadTests(unittest.TestCase):
    """A populated root with a server-folder lock file; the reader side is real where it applies."""

    @classmethod
    def setUpClass(cls) -> None:
        """Populate one manager root and one DayZ root for the read-only tests of this class."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_observer_")
        base = Path(cls.temporary.name)
        cls.manager, cls.dayz = populate(base)
        cls.legacy = base / "Legacy"
        cls.legacy.mkdir()
        if os.name == "nt":
            FolderLockFile.create(cls.manager / "data" / "server-folders.lock").close()
        cls.archive = next((cls.manager / "backups").glob("*.zip"))

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove both roots."""
        cls.temporary.cleanup()

    def test_every_read_command_leaves_both_roots_unchanged(self) -> None:
        """Tree, sizes and hashes of the manager root, the DayZ root and the legacy folder stay equal."""
        before = tree_hashes(self.manager, self.dayz, self.legacy)
        calls = read_calls(self.dayz, self.archive, self.legacy)
        self.assertEqual({method for method, _ in calls}, OBSERVER_READ_METHODS)
        session = open_observer_session(self.manager)
        try:
            if os.name == "nt":
                self.assertIsInstance(session.reader, FolderReader)
            answers = run_reads(session, calls)
        finally:
            session.close()
        self.assertEqual(tree_hashes(self.manager, self.dayz, self.legacy), before)
        # The reads answered from the populated records
        self.assertEqual([item["profile_id"] for item in answers["list_profiles"]], [PROFILE_ID])
        self.assertEqual(answers["read_profile"]["profile_id"], PROFILE_ID)
        self.assertEqual(answers["get_ui_preferences"]["selected_profile_id"], PROFILE_ID)
        self.assertEqual(len(answers["list_backups"]["backups"]), 1)
        self.assertIn("digest", answers["load_mission_configuration"])
        self.assertTrue(answers["inspect_backup_archive"]["backup_id"].startswith("selected-"))
        self.assertNotIn("INTERNAL_FAILURE", {value for value in answers.values() if isinstance(value, str)})

    def test_an_owner_composition_on_the_same_root_still_writes_its_log(self) -> None:
        """Contrast: the owner path is unchanged, so the observer's empty trace is the observer's own."""
        log = self.manager / "data" / "logs" / "manager.jsonl"
        self.assertTrue(log.is_file())
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(record["event"] == "shutdown.closed" for record in records))


class ObserverOnBareRootsTests(unittest.TestCase):
    """A root without layout, and a root with interrupted journals."""

    def setUp(self) -> None:
        """Create a disposable base folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_observer_bare_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_a_root_without_layout_stays_without_layout(self) -> None:
        """An empty folder and a missing folder: every read answers, nothing is created."""
        for root in (self.base / "Empty", self.base / "Missing"):
            with self.subTest(root=root.name):
                if root.name == "Empty":
                    root.mkdir()
                before = tree_hashes(self.base)
                session = open_observer_session(root)
                try:
                    answers = run_reads(session, read_calls(self.base / "DayZ", None, self.base))
                finally:
                    session.close()
                self.assertEqual(tree_hashes(self.base), before)
                self.assertEqual(root.exists(), root.name == "Empty")
                self.assertEqual(answers["list_profiles"], [])
                self.assertIsInstance(answers["get_application_snapshot"], dict)
                self.assertEqual(pending_recoveries(session.paths), ())

    def test_a_root_with_interrupted_journals_is_read_and_reported_but_not_recovered(self) -> None:
        """Journals stay as they are; the pending recoveries are reported as facts."""
        manager = self.base / "Manager"
        composition = build_composition(manager)
        composition.operations.shutdown(2)
        journals = manager / "data" / "operations" / "restore-journals"
        (journals / "restore-1.json").write_text('{"interrupted": true}\n', encoding="utf-8")
        (manager / "data" / "publication" / "journals" / "publication-1.json").write_text("{}\n", encoding="utf-8")
        (manager / "data" / "operations" / "update-1.json").write_text(
            json.dumps({"kind": "UPDATE_WORKSHOP_ITEMS", "state": "RUNNING"}), encoding="utf-8")
        before = tree_hashes(manager)
        session = open_observer_session(manager)
        try:
            answers = run_reads(session, read_calls(self.base / "DayZ", None, self.base))
            pending = pending_recoveries(session.paths)
        finally:
            session.close()
        self.assertEqual(tree_hashes(manager), before)
        self.assertEqual(pending, ("RESTORE_BACKUP", "PUBLISH_MODS_AND_KEYS", "UPDATE_WORKSHOP_ITEMS"))
        # Observers skip recoveries, so the snapshot shows no block
        self.assertIsNone(answers["get_application_snapshot"]["mutation_block"])


if __name__ == "__main__":
    unittest.main()
