"""Profile deletion and the map editor records: association folders, originals, the ledger and remnants (D2, R20).

Each test builds a DayZ root and an editor area in temporary folders and deletes a profile through the real
deletion service with stub collaborators. No real profile, DayZ installation or server is used.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dayz_serverman.application import profile_deletion_editor
from dayz_serverman.application.profile_deletion import ProfileDeletionError, ProfileDeletionService
from dayz_serverman.application.profile_deletion_editor import ProfileDeletionEditor
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.domain.profile_restore_mapping import MISSION_OWNER_MARKER
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.mission_map_layout import (
    TargetClass, association_folder, ledger_folder, original_folder, target_key,
)

# Fixed UTC time of the dated names in the deleted folder
MOMENT = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
STAMP = "20261016T120000Z"
# The mission that profile main owns completely, and the mission that both profiles use
OWN, SHARED = "mpmissions\\dayzOffline.own", "mpmissions\\dayzOffline.shared"


class Profiles:
    """Keep profile records in memory, as ProfileService lists, reads and deletes them."""

    def __init__(self, *records: ProfileRecord) -> None:
        """Index the records by profile ID; `fail` makes the record deletion raise."""
        self.records = {record.values.profile_id: record for record in records}
        self.fail: Exception | None = None

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every record."""
        return tuple(self.records.values())

    def read(self, profile_id: object) -> ProfileRecord:
        """Return the record of one profile."""
        return self.records[profile_id]

    def delete(self, profile_id: str, expected_revision: int) -> str:
        """Remove one record, or raise the injected failure."""
        if self.fail is not None:
            raise self.fail
        del self.records[profile_id]
        return profile_id


def record(profile_id: str, mission_root: str, runtime: str | None = None) -> ProfileRecord:
    """Build a profile with a generated folder below serverman and the given mission."""
    values = ProfileInput(profile_id, profile_id.title(), "DayZServer_x64.exe",
                          f"serverman\\{profile_id}\\serverDZ.cfg", mission_root, 2302, (), (),
                          runtime or f"serverman\\{profile_id}\\profile")
    return ProfileRecord(0, values)


def write(path: Path, text: str = "{}") -> None:
    """Write one small record file and its parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(folder: Path) -> dict[str, bytes]:
    """Return the bytes of every file below a folder by relative path."""
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


class DeletionFixture(unittest.TestCase):
    """A DayZ root with two missions, two profiles and an editor area in temporary folders."""

    def setUp(self) -> None:
        """Create the folders; profile main owns mission OWN through its owner marker."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_deletion_editor_")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "DayZ Root"
        self.area = base / "Manager" / "data" / "mission-map"
        for relative in (OWN, SHARED):
            (self.root / relative).mkdir(parents=True)
        write(self.root / OWN / MISSION_OWNER_MARKER,
              json.dumps({"profile_id": "main", "mission_root": OWN, "operation_id": "op-1"}))
        for profile_id in ("main", "second"):
            write(self.root / "serverman" / profile_id / "serverDZ.cfg", "instanceId = 1;\n")
            (self.root / "serverman" / profile_id / "profile").mkdir()
        self.profiles = Profiles(record("main", OWN), record("second", SHARED))
        self.schedules = SimpleNamespace(delete_profile=Mock())

    def key(self, relative: str, target_class: TargetClass = TargetClass.MISSION) -> str:
        """Return the target key of a folder of the DayZ root."""
        return target_key(target_class, (self.root / relative).resolve(strict=True))

    def records(self, profile_id: str, mission_key: str) -> Path:
        """Write a plan, a baseline and one content file into an association folder."""
        folder = association_folder(self.area, profile_id, mission_key)
        for name in ("plan.json", "baseline.json", "files/" + "a" * 64):
            write(folder / name, f'{{"name": "{name}"}}')
        return folder

    def original(self, key: str) -> Path:
        """Write a protected original with one content file."""
        folder = original_folder(self.area, key)
        write(folder / "original.json")
        write(folder / "files" / ("b" * 64), "content")
        return folder

    def delete(self, profile_id: str) -> dict:
        """Delete a profile through the deletion service with the editor area."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.root)))
        lifecycle = SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.STOPPED))
        others = SimpleNamespace(delete_profile=Mock())
        service = ProfileDeletionService(self.profiles, settings, lifecycle, others, self.schedules, others,
                                         editor=ProfileDeletionEditor(self.area, clock=lambda: MOMENT))
        return service.delete(profile_id, 0)


class DeletionEditorTests(DeletionFixture):
    """Deletion removes this profile's association folders and keeps what other profiles share."""

    def test_unshared_mission_removes_records_and_originals_and_keeps_the_ledger(self) -> None:
        """An owned mission goes with its original; the runtime original goes; the ledger and old targets stay."""
        own, runtime = self.key(OWN), self.key("serverman\\main\\profile", TargetClass.RUNTIME)
        old = "c" * 32
        self.records("main", own)
        self.records("main", old)
        for key in (own, runtime, old):
            self.original(key)
        write(ledger_folder(self.area, own) / "op-1.json")
        ledger = snapshot(self.area / "ledger")
        result = self.delete("main")
        self.assertTrue(result["removed_owned_mission"])
        self.assertEqual(result["editor"], {"removed_association_folders": True, "removed_originals": [own, runtime],
                                            "kept_originals": [], "preserved_ledger": True, "remnants": []})
        self.assertFalse((self.area / "profiles" / "main").exists())
        self.assertFalse(original_folder(self.area, own).exists() or original_folder(self.area, runtime).exists())
        # The original of a target that this deletion does not remove stays, also with no profile on it
        self.assertTrue(original_folder(self.area, old).is_dir())
        self.assertEqual(snapshot(self.area / "ledger"), ledger)
        self.assertFalse((self.area / "deleted").exists())
        self.assertNotIn("main", self.profiles.records)

    def test_shared_mission_keeps_its_original_and_the_other_profiles_records(self) -> None:
        """A mission that another profile uses stays, and so do its original and the other association."""
        self.profiles.records["main"] = record("main", SHARED)
        shared, runtime = self.key(SHARED), self.key("serverman\\main\\profile", TargetClass.RUNTIME)
        self.records("main", shared)
        other = self.records("second", shared)
        self.original(shared)
        self.original(runtime)
        write(ledger_folder(self.area, shared) / "op-1.json")
        kept = {path: snapshot(path) for path in (other, original_folder(self.area, shared), self.area / "ledger")}
        result = self.delete("main")
        self.assertFalse(result["removed_owned_mission"])
        self.assertEqual((result["editor"]["removed_originals"], result["editor"]["kept_originals"]),
                         ([runtime], [shared]))
        self.assertTrue((self.root / SHARED).is_dir())
        self.assertFalse((self.area / "profiles" / "main").exists())
        self.assertEqual({path: snapshot(path) for path in kept}, kept)

    def test_mission_that_stays_keeps_its_original_also_without_another_profile(self) -> None:
        """A mission without an owner marker stays, so its original stays for the next profile on it."""
        alone = "mpmissions\\dayzOffline.alone"
        (self.root / alone).mkdir()
        self.profiles.records["main"] = record("main", alone)
        self.records("main", self.key(alone))
        self.original(self.key(alone))
        result = self.delete("main")
        self.assertFalse(result["removed_owned_mission"])
        self.assertEqual((result["editor"]["removed_originals"], result["editor"]["kept_originals"]),
                         ([], [self.key(alone)]))
        self.assertTrue((original_folder(self.area, self.key(alone)) / "original.json").is_file())

    def test_another_profiles_association_keeps_the_original_of_a_removed_mission(self) -> None:
        """An owned mission goes, but its original stays while another profile has an association for it."""
        own = self.key(OWN)
        self.records("main", own)
        self.records("second", own)
        self.original(own)
        result = self.delete("main")
        self.assertTrue(result["removed_owned_mission"])
        self.assertEqual(result["editor"]["kept_originals"], [own])
        self.assertTrue((original_folder(self.area, own) / "original.json").is_file())
        self.assertTrue(association_folder(self.area, "second", own).is_dir())

    def test_runtime_original_stays_when_another_profile_resolves_to_its_folder(self) -> None:
        """A runtime profile folder that another profile names keeps its original."""
        runtime = self.key("serverman\\main\\profile", TargetClass.RUNTIME)
        self.profiles.records["second"] = record("second", SHARED, "serverman\\main\\profile")
        self.original(runtime)
        result = self.delete("main")
        self.assertEqual((result["editor"]["removed_originals"], result["editor"]["kept_originals"]), ([], [runtime]))
        self.assertTrue(original_folder(self.area, runtime).is_dir())

    def test_no_editor_area_creates_nothing(self) -> None:
        """A deletion without editor records reports nothing removed and creates no editor folder."""
        result = self.delete("main")
        self.assertEqual(result["editor"], {"removed_association_folders": False, "removed_originals": [],
                                            "kept_originals": [], "preserved_ledger": True, "remnants": []})
        self.assertFalse(self.area.exists())

    @unittest.skipUnless(os.name == "nt", "a junction is a Windows reparse point")
    def test_link_in_the_editor_records_refuses_before_the_first_change(self) -> None:
        """A junction inside the profile's editor folder refuses the deletion; nothing changes."""
        import _winapi
        folder = self.records("main", self.key(OWN))
        _winapi.CreateJunction(str(self.root / SHARED), str(folder / "link"))
        before = snapshot(self.area)
        with self.assertRaisesRegex(ProfileDeletionError, "unsafe link or reparse point"):
            self.delete("main")
        self.schedules.delete_profile.assert_not_called()
        self.assertEqual(snapshot(self.area), before)
        self.assertTrue((self.root / OWN).is_dir())
        self.assertIn("main", self.profiles.records)


class InterruptedDeletionTests(DeletionFixture):
    """An interrupted deletion leaves no surviving profile that names removed editor state."""

    def test_failure_before_the_profile_record_keeps_every_shared_original(self) -> None:
        """The profile survives without an association folder; the other profile keeps its original."""
        self.profiles.records["main"] = record("main", SHARED)
        shared = self.key(SHARED)
        self.records("main", shared)
        self.records("second", shared)
        self.original(shared)
        self.profiles.fail = OSError("injected before the profile record")
        with self.assertRaises(OSError):
            self.delete("main")
        self.assertIn("main", self.profiles.records)
        self.assertFalse((self.area / "profiles" / "main").exists())
        self.assertTrue((original_folder(self.area, shared) / "original.json").is_file())
        self.assertFalse((self.area / "deleted").exists())

    def test_failed_original_move_keeps_the_original_and_identifies_the_remnant(self) -> None:
        """A rename failure after the association move keeps the originals; the moved folder is a remnant."""
        own, runtime = self.key(OWN), self.key("serverman\\main\\profile", TargetClass.RUNTIME)
        self.records("main", own)
        self.original(own)
        self.original(runtime)
        real, calls = profile_deletion_editor.rename_directory, []
        def rename(source: Path, target: Path) -> None:
            """Fail the second move into the deleted folder."""
            calls.append(source)
            if len(calls) == 2:
                raise OSError("injected rename failure")
            real(source, target)
        with patch.object(profile_deletion_editor, "rename_directory", rename), self.assertRaises(OSError):
            self.delete("main")
        self.assertIn("main", self.profiles.records)
        self.assertTrue(original_folder(self.area, own).is_dir() and original_folder(self.area, runtime).is_dir())
        editor = ProfileDeletionEditor(self.area)
        self.assertEqual(editor.remnants(self.profiles.records), [f"deleted/profile-main-{STAMP}"])
        # The next deletion removes the leftover of the interrupted one
        self.assertEqual(self.delete("second")["editor"]["remnants"], [])
        self.assertFalse((self.area / "deleted").exists())

    def test_failed_removal_after_the_move_is_reported_as_a_remnant(self) -> None:
        """A folder that cannot be removed after its move stays in the deleted folder and is reported."""
        own = self.key(OWN)
        self.records("main", own)
        self.original(own)
        real = profile_deletion_editor.shutil.rmtree
        deleted = self.area / "deleted"
        def rmtree(path, *args, **kwargs):
            """Keep every folder of the deleted folder, as a locked file would."""
            if Path(path).parent != deleted:
                real(path, *args, **kwargs)
        with patch.object(profile_deletion_editor.shutil, "rmtree", rmtree):
            result = self.delete("main")
        self.assertEqual(result["editor"]["remnants"], [f"deleted/original-{own}-{STAMP}",
                                                        f"deleted/profile-main-{STAMP}"])
        self.assertNotIn("main", self.profiles.records)
        self.assertFalse(original_folder(self.area, own).exists())

    def test_profile_folder_without_a_record_is_identified(self) -> None:
        """An editor folder of a profile ID with no record, from an older build, is reported and kept."""
        self.records("ghost", "d" * 32)
        result = self.delete("main")
        self.assertEqual(result["editor"]["remnants"], ["profiles/ghost"])
        self.assertTrue((self.area / "profiles" / "ghost").is_dir())


if __name__ == "__main__":
    unittest.main()
