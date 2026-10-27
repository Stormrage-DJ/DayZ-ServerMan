"""`profile delete` with map editor records: the CLI removes this profile's records and meets the editor guard.

Real compositions over the edit fixture in temporary folders; the fixture profile has a saved map plan, and the
other profile uses the same mission. No DayZ server, window or real profile is used.
"""

from __future__ import annotations

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, PROFILE_ID, EditRoot  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID, saved_state  # noqa: E402
from dayz_serverman.application.editor_guard import NoEditorGuard  # noqa: E402
from dayz_serverman.application.profile_deletion import ProfileDeletionError  # noqa: E402
from dayz_serverman.repositories.mission_map_layout import (  # noqa: E402
    TargetClass, association_folder, ledger_folder, original_folder, target_key,
)

DELETE = ["profile", "delete", *PROFILE]
# The refusal text of an open map editor window (3.9)
GUARD_TEXT = "Close the map editor of this profile first."


def files(folder: Path) -> dict[str, bytes]:
    """Return the bytes of every file below a folder by relative path."""
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


class ProfileDeleteEditorTests(EditRoot):
    """The command deletes the editor records of its profile only and keeps the shared ones."""

    def setUp(self) -> None:
        """Add an original of each target, a ledger entry and an association of the other profile."""
        super().setUp()
        self.area = self.manager / "data" / "mission-map"
        self.mission_key = target_key(TargetClass.MISSION, self.mission.resolve(strict=True))
        runtime = self.dayz / "serverman" / PROFILE_ID / "profile"
        self.runtime_key = target_key(TargetClass.RUNTIME, runtime.resolve(strict=True))
        for key in (self.mission_key, self.runtime_key):
            folder = original_folder(self.area, key)
            folder.mkdir(parents=True)
            (folder / "original.json").write_text("{}", encoding="utf-8")
        ledger_folder(self.area, self.mission_key).mkdir(parents=True)
        (ledger_folder(self.area, self.mission_key) / "op-1.json").write_text("{}", encoding="utf-8")
        other = association_folder(self.area, OTHER_ID, self.mission_key)
        other.mkdir(parents=True)
        (other / "plan.json").write_text("{}", encoding="utf-8")

    def test_delete_removes_this_profiles_records_and_keeps_the_shared_ones(self) -> None:
        """The plan folder and the runtime original go; the mission original, the ledger and the other plan stay."""
        self.assertTrue(association_folder(self.area, PROFILE_ID, self.mission_key).is_dir())
        kept = {path: files(path) for path in (original_folder(self.area, self.mission_key), self.area / "ledger",
                                               self.area / "profiles" / OTHER_ID)}
        code, _stdout, stderr = self.cli(*DELETE, "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Profile deleted.", stderr)
        self.assertFalse((self.area / "profiles" / PROFILE_ID).exists())
        self.assertFalse(original_folder(self.area, self.runtime_key).exists())
        self.assertEqual({path: files(path) for path in kept}, kept)
        self.assertFalse((self.area / "deleted").exists())
        self.assertTrue(self.mission.is_dir())

    def test_delete_that_meets_the_editor_guard_refuses_with_its_text(self) -> None:
        """A guard that holds the profile refuses the CLI deletion with the window's text and exit 3."""
        @contextmanager
        def held(_self, profile_id):
            """Refuse as the window registry does while a map editor of the profile is open."""
            raise ProfileDeletionError(GUARD_TEXT)
            yield  # pragma: no cover
        before, editor = saved_state(self.manager, self.dayz), files(self.area)
        with patch.object(NoEditorGuard, "hold_for_deletion", held):
            code, _stdout, stderr = self.cli(*DELETE, "--yes")
            self.assertEqual(code, 3, stderr)
            self.assertIn(GUARD_TEXT, stderr)
            code, stdout, _stderr = self.cli(*DELETE, "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (3, "DELETION_BLOCKED"))
        self.assertIn(GUARD_TEXT, error["message"])
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertEqual(files(self.area), editor)


if __name__ == "__main__":
    unittest.main()
