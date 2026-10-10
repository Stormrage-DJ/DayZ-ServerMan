"""Cover the editor publication journal of D4: fields, groups, record entries, phases, storage and retirement."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from dayz_serverman.repositories.mission_map_journal import MapGroup, MapJournal, MapRecord, record_staging
from dayz_serverman.repositories.mission_map_journal_paths import group_staging_path, recovery_copy_path
from dayz_serverman.repositories.mission_map_journal_store import (
    MissionMapJournalError, MissionMapJournalRepository, journal_folder, parse_journal, recovery_folder,
)
from dayz_serverman.repositories.mission_map_layout import (
    TargetClass, association_folder, ledger_folder, original_folder, target_key,
)
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.restore_paths import missing_ancestors


# Operation identifier of the fixture journals
OPERATION = "op-1a2b3c4d"
# Old bytes of the existing mission files, and new bytes for each managed file
OLD = {"areaflags.map": b"old-flags", "mapgroupproto.xml": b"<old-proto/>", "mapgrouppos.xml": b"<old-pos/>"}
NEW = {"areaflags.map": b"new-flags", "env/zombie_territories.xml": b"<zones/>",
       "AI_Bandits/DynamicAIB.json": b"{\"groups\": []}"}


def sha(data: bytes) -> str:
    """Return the SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


class JournalFixture(unittest.TestCase):
    """A disposable DayZ root with one mission and one runtime profile folder, and a manager root."""

    def setUp(self) -> None:
        """Create the DayZ root, the old mission files and the manager layout; no real server folder is used."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_map_journal_")
        base = Path(self.temp.name).resolve(strict=True)
        self.dayz = base / "DayZ"
        self.mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        self.runtime = self.dayz / "serverman" / "main" / "profile"
        self.mission.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        for relative, data in OLD.items():
            (self.mission / relative).write_bytes(data)
        self.paths = PortablePaths.from_root(base / "manager")
        self.repository = MissionMapJournalRepository(journal_folder(self.paths))

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temp.cleanup()

    def group(self, target_class: str, relative: str, new: bytes | None) -> MapGroup:
        """Return a group derived as the commit step derives it; new None means the file must end absent."""
        folder = self.mission if target_class == "mission" else self.runtime
        target = folder / relative
        old = target.read_bytes() if target.exists() else None
        ancestors = missing_ancestors(self.dayz, target)
        recovery = recovery_copy_path(recovery_folder(self.paths).resolve(), OPERATION, target_class, relative)
        return MapGroup(
            target_class, relative, str(target), str(group_staging_path(target, OPERATION, ancestors)),
            str(recovery) if old is not None else None, old is not None, None if old is None else sha(old),
            new is not None, None if new is None else sha(new),
            created_ancestors=tuple(str(path) for path in ancestors),
        )

    def record(self, kind: str, target: Path, *, old: str | None = None) -> MapRecord:
        """Return a record entry for an absolute record path below the editor area."""
        relative = self.paths.relative(target)
        return MapRecord(kind, relative, record_staging(relative, OPERATION), old is not None, old, sha(kind.encode()))

    def records(self) -> list[MapRecord]:
        """Return one staged record of each kind for the fixture association."""
        area = self.paths.mission_map
        key = self.mission_key()
        association = association_folder(area, "main", key)
        return [
            self.record("original", original_folder(area, key) / "original.json"),
            self.record("baseline", association / "baseline.json"),
            self.record("applied", association / "applied.json", old=sha(b"previous")),
            self.record("ledger", ledger_folder(area, key) / f"{OPERATION}.json"),
        ]

    def mission_key(self) -> str:
        """Return the target key of the fixture mission folder."""
        return target_key(TargetClass.MISSION, self.mission.resolve(strict=True))

    def journal(self, groups: list[MapGroup] | None = None, records: list[MapRecord] | None = None) -> MapJournal:
        """Return a PREPARED apply journal of the fixture; by default it replaces areaflags.map only."""
        return MapJournal(
            OPERATION, "apply", "main", "mpmissions\\dayzOffline.chernarusplus", self.mission_key(),
            "serverman\\main\\profile", target_key(TargetClass.RUNTIME, self.runtime.resolve(strict=True)),
            str(self.dayz), "plan-1", 4, sha(b"plan"), sha(b"preview"), 2, 7,
            groups if groups is not None else [self.group("mission", "areaflags.map", NEW["areaflags.map"])],
            records if records is not None else self.records(),
        )

    def stage(self, journal: MapJournal) -> None:
        """Write the staged new files and the recovery copies, as D4 step 4 does."""
        for group in journal.groups:
            if group.recovery_path is not None:
                Path(group.recovery_path).parent.mkdir(parents=True, exist_ok=True)
                Path(group.recovery_path).write_bytes(Path(group.target_path).read_bytes())
            if group.new_exists:
                folder = self.mission if group.target_class == "mission" else self.runtime
                Path(group.staging_path).parent.mkdir(parents=True, exist_ok=True)
                Path(group.staging_path).write_bytes(NEW[group.relative_path])
                self.assertTrue(Path(group.staging_path).resolve().is_relative_to(folder))


class JournalStoreTests(JournalFixture):
    """The journal persists below data/operations/mission-map-journals/ and round-trips exactly."""

    def test_location_below_the_operations_folder(self) -> None:
        """Journals, retired journals and recovery copies have the folders of the implementation notes."""
        operations = self.paths.root / "data" / "operations"
        self.assertEqual(journal_folder(self.paths), operations / "mission-map-journals")
        self.assertEqual(recovery_folder(self.paths), operations / "mission-map-recovery")
        self.assertEqual(self.repository.path_for(OPERATION), operations / "mission-map-journals" / f"{OPERATION}.json")
        self.assertEqual(self.repository.retired_path_for(OPERATION).parent.name, "completed")
        with self.assertRaises(MissionMapJournalError):
            self.repository.path_for("../escape")

    def test_round_trip_records_and_retirement(self) -> None:
        """A saved journal loads equal, records() lists it, and a resolved journal retires into completed/."""
        journal = self.journal()
        path = self.repository.save(journal)
        self.assertEqual(self.repository.load(path), journal)
        self.assertEqual(self.repository.records(), ((path, journal),))
        self.assertTrue(path.read_text(encoding="utf-8").endswith("}\n"))
        # A journal that is not resolved never retires
        with self.assertRaises(MissionMapJournalError):
            self.repository.retire(journal)
        journal.phase, journal.resolved, journal.result = "ROLLED_BACK", True, "ROLLED_BACK"
        self.repository.save(journal)
        retired = self.repository.retire(journal)
        self.assertFalse(path.exists())
        self.assertEqual(self.repository.load(retired), journal)
        self.assertEqual(self.repository.records(), ())

    def test_untrusted_journal_is_listed_without_a_record(self) -> None:
        """An unreadable or tampered journal is reported as None, so recovery blocks on it."""
        self.repository.root.joinpath("broken.json").write_text("{", encoding="utf-8")
        raw = self.journal().to_dict()
        raw["phase"] = "COMMITTED"
        self.repository.root.joinpath(f"{OPERATION}.json").write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual([journal for _path, journal in self.repository.records()], [None, None])

    def test_record_staging_name_pattern(self) -> None:
        """A staged record sits beside its target as <record>.json.staged-<operation_id>."""
        baseline, ledger = self.records()[1], self.records()[3]
        self.assertEqual(baseline.staging_path, baseline.target_path + f".staged-{OPERATION}")
        self.assertTrue(baseline.staging_path.endswith(f"/baseline.json.staged-{OPERATION}"))
        self.assertTrue(ledger.staging_path.endswith(f"/{OPERATION}.json.staged-{OPERATION}"))


class JournalShapeTests(JournalFixture):
    """parse_journal refuses every field, group, record and state that D4 does not allow."""

    def refused(self, change: Callable[[dict], object]) -> None:
        """Assert that the changed document of the fixture journal is refused."""
        raw = self.journal([
            self.group("mission", "areaflags.map", NEW["areaflags.map"]),
            self.group("mission", "mapgrouppos.xml", None),
            self.group("runtime", "AI_Bandits/DynamicAIB.json", NEW["AI_Bandits/DynamicAIB.json"]),
        ]).to_dict()
        parse_journal(json.loads(json.dumps(raw)), OPERATION)
        change(raw)
        with self.assertRaises(MissionMapJournalError):
            parse_journal(raw, OPERATION)

    def test_journal_fields(self) -> None:
        """Unknown fields, another schema, another file name, kinds, keys, roots and revisions are refused."""
        self.refused(lambda raw: raw.update(extra=1))
        self.refused(lambda raw: raw.update(schema_version=2))
        self.refused(lambda raw: raw.update(operation_id="other"))
        self.refused(lambda raw: raw.update(operation_kind="profile_restore"))
        self.refused(lambda raw: raw.update(mission_key="A" * 32))
        self.refused(lambda raw: raw.update(runtime_profile=None))
        self.refused(lambda raw: raw.update(runtime_profile_key=None))
        self.refused(lambda raw: raw.update(mission_root="..\\outside"))
        self.refused(lambda raw: raw.update(dayz_root="relative\\DayZ"))
        self.refused(lambda raw: raw.update(profile_revision=True))
        self.refused(lambda raw: raw.update(groups=[], records=[]))

    def test_plan_identity_belongs_to_an_apply_only(self) -> None:
        """An apply needs the plan identity; a restore has none."""
        self.refused(lambda raw: raw.update(plan_fingerprint=None))
        self.refused(lambda raw: raw.update(operation_kind="restore_original"))
        raw = self.journal().to_dict()
        raw.update(operation_kind="restore_previous", plan_id=None, plan_revision=None, plan_fingerprint=None,
                   preview_fingerprint=None)
        self.assertEqual(parse_journal(raw, OPERATION).operation_kind, "restore_previous")

    def test_group_shape(self) -> None:
        """Groups stay in the managed file set and keep consistent existence, digests and folders."""
        self.refused(lambda raw: raw["groups"][0].update(relative_path="db/types.xml"))
        self.refused(lambda raw: raw["groups"][0].update(target_class="other"))
        self.refused(lambda raw: raw["groups"][1].update(new_digest=sha(b"x")))
        self.refused(lambda raw: raw["groups"][1].update(old_existed=False, old_digest=None, recovery_path=None))
        self.refused(lambda raw: raw["groups"][0].update(recovery_path=None))
        self.refused(lambda raw: raw["groups"][1].update(created_ancestors=["C:\\x"]))
        self.refused(lambda raw: raw["groups"][0].update(new_exists=False))
        self.refused(lambda raw: raw["groups"].append(dict(raw["groups"][0])))

    def test_record_shape(self) -> None:
        """Record entries have a known kind, a manager-relative path and the staging name with the operation ID."""
        self.refused(lambda raw: raw["records"][0].update(kind="plan"))
        self.refused(lambda raw: raw["records"][0].update(staging_path=raw["records"][0]["target_path"] + ".tmp"))
        self.refused(lambda raw: raw["records"][0].update(target_path="../outside.json"))
        self.refused(lambda raw: raw["records"][0].update(old_existed=True))
        self.refused(lambda raw: raw["records"][2].update(old_manifest_sha256=None))
        self.refused(lambda raw: raw["records"][0].update(state="PUBLISHING"))

    def test_phase_and_state_combinations(self) -> None:
        """Each phase allows only the flags and states of the D4 commit order."""
        self.refused(lambda raw: raw.update(publication_started=True))
        self.refused(lambda raw: raw.update(phase="COMMITTED", publication_started=True, resolved=True,
                                            result="COMMITTED"))
        self.refused(lambda raw: raw.update(phase="COMMITTING", publication_started=True))

        def committing(raw: dict) -> None:
            """Move the document to COMMITTING with records published out of order."""
            raw.update(phase="COMMITTING", publication_started=True)
            for group in raw["groups"]:
                group["state"] = "PUBLISHED"
            raw["records"][1]["state"] = "PUBLISHED"
        self.refused(committing)
        # The same journal with records published in order is valid at COMMITTING and then at COMMITTED
        raw = self.journal().to_dict()
        raw.update(phase="COMMITTING", publication_started=True)
        raw["groups"][0]["state"] = "PUBLISHED"
        raw["records"][0]["state"] = "PUBLISHED"
        self.assertEqual(parse_journal(raw, OPERATION).phase, "COMMITTING")
        for record in raw["records"]:
            record["state"] = "PUBLISHED"
        raw.update(phase="COMMITTED", resolved=True, result="COMMITTED")
        self.assertTrue(parse_journal(raw, OPERATION).committed)


if __name__ == "__main__":
    unittest.main()
