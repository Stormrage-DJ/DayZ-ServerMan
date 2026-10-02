"""Verify occupied IDs include custom missions, orphan worlds and bound endpoints."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.profile_restore_occupancy import inspect_restore_occupancy
from dayz_serverman.domain.profile_restore_destinations import select_restore_destination
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord, ProfileValidationError
from tests.profile_fixtures import profile_payload


class RestoreOccupancyTests(unittest.TestCase):
    """Do not mistake unregistered or unreadable installation data for free IDs."""

    def setUp(self) -> None:
        """Own an isolated installation root for each test."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_occupancy_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def record(self, *, mission_root: str | None = r"custom\world.enoch") -> ProfileRecord:
        """Build a normal profile record pointing to a readable scalar and mission config."""
        config = self.root / "serverDZ.cfg"
        config.write_text('instanceId=2; steamQueryPort=2700; '
                          'class Missions { class DayZ { template="world.enoch"; }; };', encoding="utf-8")
        return ProfileRecord(0, ProfileInput.parse(profile_payload(
            profile_id="owner", server_config="serverDZ.cfg", mission_root=mission_root,
        )))

    def test_empty_installation_has_no_occupied_ids(self) -> None:
        """A deleted-profile installation can preserve an absent original mission."""
        result = inspect_restore_occupancy(self.root, (), frozenset())
        self.assertEqual(result.instance_ids, ())
        self.assertEqual(result.profile_ids, ())
        self.assertTrue(result.ownership_known)

    def test_custom_and_orphan_worlds_generated_folders_and_ports_are_occupied(self) -> None:
        """Allocate past every discovered storage and config ID, including custom roots."""
        record = self.record()
        (self.root / "custom" / "world.enoch" / "storage_1").mkdir(parents=True)
        (self.root / "mpmissions" / "orphan.enoch" / "STORAGE_3").mkdir(parents=True)
        (self.root / "serverman" / "orphan-folder").mkdir(parents=True)
        result = inspect_restore_occupancy(self.root, (record,), frozenset({2405}))
        self.assertEqual(result.instance_ids, (1, 2, 3))
        self.assertEqual(result.generated_ids, ("orphan-folder",))
        self.assertTrue({2302, 2303, 2304, 2305, 2405, 2700} <= result.reserved_ports)
        destination = select_restore_destination(r"custom\world.enoch", 2, "new", result)
        self.assertEqual(destination.instance_id, 4)
        self.assertEqual(destination.mission_root, r"mpmissions\serverman-new.enoch")

    def test_default_mission_reference_resolves_from_config(self) -> None:
        """Null profile mission metadata still reserves the config's actual destination."""
        record = self.record(mission_root=None)
        result = inspect_restore_occupancy(self.root, (record,), frozenset())
        self.assertEqual(result.referenced_missions, (r"mpmissions\world.enoch",))

    def test_missing_config_and_contradictory_mission_fail_closed(self) -> None:
        """Block ownership-dependent mapping when any consumer cannot be resolved."""
        record = self.record()
        (self.root / "serverDZ.cfg").unlink()
        with self.assertRaises(ProfileValidationError):
            inspect_restore_occupancy(self.root, (record,), frozenset())
        record = self.record(mission_root=r"custom\different.enoch")
        with self.assertRaisesRegex(ProfileValidationError, "disagree"):
            inspect_restore_occupancy(self.root, (record,), frozenset())

    def test_permission_denial_never_becomes_an_absent_candidate(self) -> None:
        """Fail allocation if an occupied mission cannot be inspected."""
        mission = self.root / "mpmissions" / "orphan.enoch"
        mission.mkdir(parents=True)
        original = Path.lstat

        def denied(path: Path, *args, **kwargs):
            """Simulate inaccessible metadata on one otherwise occupied mission."""
            if path == mission:
                raise PermissionError("fixture denial")
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", denied):
            with self.assertRaisesRegex(ProfileValidationError, "readable"):
                inspect_restore_occupancy(self.root, (), frozenset())

    def test_reparse_candidate_is_rejected_without_following_it(self) -> None:
        """Model Windows reparse metadata without requiring symlink privileges."""
        target = self.root / "mpmissions" / "unsafe.enoch"
        target.mkdir(parents=True)
        original = Path.lstat

        def reparse(path: Path, *args, **kwargs):
            """Return a reparse attribute for the unsafe candidate only."""
            if path == target:
                class Metadata:
                    """Minimal directory metadata carrying the Windows reparse bit."""
                    st_mode = 0o040755
                    st_file_attributes = 0x400
                return Metadata()
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", reparse):
            with self.assertRaisesRegex(ProfileValidationError, "reparse"):
                inspect_restore_occupancy(self.root, (), frozenset())


if __name__ == "__main__":
    unittest.main()
