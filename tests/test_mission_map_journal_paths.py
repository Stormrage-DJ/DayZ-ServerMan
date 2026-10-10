"""Cover the D4 path-safety rule of editor journals: derived paths, equal keys, containment and reparse points."""

from __future__ import annotations

import dataclasses
import shutil
import unittest
from pathlib import Path

from dayz_serverman.application.folder_writer_scope import WriterScope
from dayz_serverman.repositories.mission_map_journal import MapJournal, MapRecord
from dayz_serverman.repositories.mission_map_journal_paths import journal_paths_safe
from dayz_serverman.repositories.mission_map_journal_store import recovery_folder
from dayz_serverman.repositories.mission_map_layout import association_folder, original_folder
from dayz_serverman.repositories.mission_map_publication import EDITOR_POLICY, MissionMapPublication
from dayz_serverman.repositories.publication_contracts import RecoveryHooks
from tests.test_mission_map_journal import NEW, OPERATION, JournalFixture, sha


def link_folder(link: Path, target: Path) -> None:
    """Create a directory junction or symbolic link at link, or skip the test when neither is possible."""
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    except (ImportError, OSError):
        pass
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        raise unittest.SkipTest("neither a junction nor a directory link can be created") from None


def snapshot(root: Path) -> dict[str, bytes]:
    """Return the bytes of every file below a folder, keyed by relative path."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class JournalPathTests(JournalFixture):
    """journal_paths_safe accepts only paths that it derives again itself."""

    def full_journal(self) -> MapJournal:
        """Return a journal with a replaced file, a new file in a new folder, a removed file and a runtime file."""
        return self.journal([
            self.group("mission", "areaflags.map", NEW["areaflags.map"]),
            self.group("mission", "env/zombie_territories.xml", NEW["env/zombie_territories.xml"]),
            self.group("mission", "mapgrouppos.xml", None),
            self.group("runtime", "AI_Bandits/DynamicAIB.json", NEW["AI_Bandits/DynamicAIB.json"]),
        ])

    def safe(self, journal: MapJournal) -> bool:
        """Return the path-safety verdict against the fixture DayZ root and manager layout."""
        return journal_paths_safe(journal, self.dayz, self.paths)

    def changed_group(self, index: int, **changes: object) -> MapJournal:
        """Return the full journal with one group changed."""
        journal = self.full_journal()
        journal.groups[index] = dataclasses.replace(journal.groups[index], **changes)  # type: ignore[arg-type]
        return journal

    def test_derived_journal_is_safe(self) -> None:
        """A journal built from the derivation rules passes, with both target classes and every record kind."""
        self.assertTrue(self.safe(self.full_journal()))

    def test_group_staging_and_recovery_name_patterns(self) -> None:
        """Staging sits beside the target or in the operation stage folder; recovery copies sit by class."""
        flags, zombies, positions, bandits = self.full_journal().groups
        self.assertEqual(Path(flags.staging_path), self.mission / f".areaflags.map.{OPERATION}.mission-map-stage")
        self.assertEqual(Path(positions.staging_path), self.mission / f".mapgrouppos.xml.{OPERATION}.mission-map-stage")
        stage_folder = f".serverman-{OPERATION}-stage"
        self.assertEqual(Path(zombies.staging_path), self.mission / stage_folder / "env" / "zombie_territories.xml")
        self.assertEqual(Path(bandits.staging_path), self.runtime / stage_folder / "AI_Bandits" / "DynamicAIB.json")
        recovery = recovery_folder(self.paths).resolve() / OPERATION
        self.assertEqual(Path(flags.recovery_path or ""), recovery / "mission" / "areaflags.map")
        self.assertEqual(Path(positions.recovery_path or ""), recovery / "mission" / "mapgrouppos.xml")
        self.assertIsNone(zombies.recovery_path)
        self.assertEqual(zombies.created_ancestors, (str(self.mission / "env"),))

    def test_any_changed_group_path_is_unsafe(self) -> None:
        """Another target, staging name, recovery copy or created folder fails the derivation."""
        other = str(self.mission / "mapgroupproto.xml")
        self.assertFalse(self.safe(self.changed_group(0, target_path=other)))
        self.assertFalse(self.safe(self.changed_group(0, staging_path=other + ".stage")))
        self.assertFalse(self.safe(self.changed_group(0, recovery_path=str(self.dayz / "copy"))))
        self.assertFalse(self.safe(self.changed_group(1, created_ancestors=(str(self.mission / "db"),))))
        self.assertFalse(self.safe(self.changed_group(1, relative_path="env/wolf_territories.xml")))
        self.assertFalse(self.safe(self.changed_group(3, target_class="mission")))

    def test_keys_must_equal_the_keys_derived_from_the_folders(self) -> None:
        """A changed mission or runtime key, or a mission root of another folder, fails."""
        self.assertFalse(self.safe(dataclasses.replace(self.full_journal(), mission_key="0" * 32)))
        self.assertFalse(self.safe(dataclasses.replace(self.full_journal(), runtime_profile_key="0" * 32)))
        (self.dayz / "mpmissions" / "other").mkdir()
        self.assertFalse(self.safe(dataclasses.replace(self.full_journal(), mission_root="mpmissions\\other")))

    def test_the_dayz_root_must_be_the_configured_one(self) -> None:
        """A journal of another DayZ root, or a configured root that moved, is unsafe."""
        elsewhere = self.dayz.parent / "Other"
        elsewhere.mkdir()
        self.assertFalse(self.safe(dataclasses.replace(self.full_journal(), dayz_root=str(elsewhere))))
        self.assertFalse(journal_paths_safe(self.full_journal(), elsewhere, self.paths))

    def test_containment(self) -> None:
        """A target, staging path or created folder outside its target folder or the DayZ root is unsafe."""
        outside = self.dayz.parent / "outside"
        self.assertFalse(self.safe(self.changed_group(0, target_path=str(outside / "areaflags.map"))))
        self.assertFalse(self.safe(self.changed_group(0, staging_path=str(outside / "stage"))))
        self.assertFalse(self.safe(self.changed_group(1, created_ancestors=(str(self.dayz / "env"),))))

    def test_reparse_points_are_unsafe(self) -> None:
        """A linked mission folder, a linked created folder or a linked recovery folder fails the check."""
        journal = self.full_journal()
        real = self.dayz.parent / "real-env"
        real.mkdir()
        link_folder(self.mission / "env", real)
        self.assertFalse(self.safe(journal))
        (self.mission / "env").rmdir()
        self.assertTrue(self.safe(journal))
        # A mission folder that is itself a link
        moved = self.dayz.parent / "moved-mission"
        shutil.move(str(self.mission), str(moved))
        link_folder(self.mission, moved)
        self.assertFalse(self.safe(journal))

    def test_linked_recovery_folder_is_unsafe(self) -> None:
        """Recovery copies behind a link are never trusted."""
        journal = self.full_journal()
        real = self.dayz.parent / "real-recovery"
        real.mkdir()
        recovery_folder(self.paths).parent.mkdir(parents=True, exist_ok=True)
        link_folder(recovery_folder(self.paths), real)
        self.assertFalse(self.safe(journal))

    def test_record_paths_are_derived_from_kind_profile_and_keys(self) -> None:
        """Each record path must be the one its kind, the profile and a journal key derive; staging included."""
        area = self.paths.mission_map
        journal = self.full_journal()
        swapped = dataclasses.replace(journal.records[1], kind="applied")
        other_profile = self.record("baseline", association_folder(area, "other", self.mission_key()) / "baseline.json")
        unknown_key = self.record("original", original_folder(area, "f" * 32) / "original.json")
        staged = dataclasses.replace(journal.records[0], staging_path=journal.records[0].target_path + ".tmp")
        for record in (swapped, other_profile, unknown_key, staged):
            with self.subTest(record=record.target_path):
                self.assertFalse(self.safe(dataclasses.replace(journal, records=[*journal.records[1:], record])))
        runtime_original = self.record(
            "original", original_folder(area, journal.runtime_profile_key or "") / "original.json")
        self.assertTrue(self.safe(dataclasses.replace(journal, records=[*journal.records, runtime_original])))

    def test_unsafe_journal_blocks_recovery_without_a_write(self) -> None:
        """A key mismatch in an interrupted journal blocks under the editor policy and changes no file."""
        journal = self.full_journal()
        self.stage(journal)
        journal.mission_key = "0" * 32
        journal.phase, journal.publication_started = "PUBLISHING", True
        self.repository.save(journal)
        before = snapshot(Path(self.temp.name))
        calls: list[str] = []
        hooks = RecoveryHooks[MapJournal](lambda _j: calls.append("forward") or True,
                                          lambda _j: calls.append("back") or True)
        scope = WriterScope(None)
        with scope.held_for():
            report = MissionMapPublication(self.paths, self.repository, scope).inspect(self.dayz, hooks)
        self.assertTrue(report["blocked"])
        self.assertEqual(report["diagnostics"], [{"code": "RECOVERY_REQUIRED", "usable": False,
                                                  "message": EDITOR_POLICY.invalid_message}])
        self.assertEqual(calls, [])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_record_entry_shape_used_by_the_check(self) -> None:
        """Record entries carry kind, manager-relative paths and both manifest digests."""
        record = self.full_journal().records[2]
        self.assertEqual(record, MapRecord("applied", record.target_path, record.staging_path, True, sha(b"previous"),
                                           sha(b"applied")))
        self.assertFalse(Path(record.target_path).is_absolute())


if __name__ == "__main__":
    unittest.main()
