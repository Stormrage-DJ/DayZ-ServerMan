"""Synthetic missions, profiles, records and a simulated Apply for the apply preview tests of leaf 8.6."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.medical_features import MedicalFeatureService
from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.application.mission_map_preview import MissionMapPreview, PreviewReview
from dayz_serverman.domain.mission_map_plan import configuration_fingerprint
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, target_key
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_owned import owned_digest
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from tests.mission_map_inspection_support import mission_bytes
from tests.mission_map_mapgroup_support import BASELINE, PROTOTYPE
from tests.test_mission_map_infected import BASELINE as TERRITORIES, EVENTS
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_plan_service import Profiles, profile
from tests.test_mission_map_terrain_files import limits_text

# Folder of the verified mission and of an unverified one (6.11)
VERIFIED = "dayzOffline.chernarusplus"
UNVERIFIED = "dayzOffline.deerisle"
# Globals with both shown values readable and above 0, and another value that the preview never binds
GLOBALS = (b'<variables>\n<var name="ZombieMaxCount" type="0" value="1000"/>\n'
           b'<var name="ZoneSpawnDist" type="0" value="300"/>\n<var name="AnimalMaxCount" type="0" value="5"/>\n'
           b'</variables>\n')
ENVIRONMENT = b'<env><territories><file path="env/zombie_territories.xml"/></territories></env>'
WOLVES = b'<territory-type><territory><zone name="Wolf" x="1" z="2" r="3"/></territory></territory-type>'
# Fixed time and operation of a simulated Apply
APPLIED_AT = "2026-10-14T19:00:00Z"


def sha(data: bytes | None) -> str | None:
    """Return SHA-256 of bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()


class ListedProfiles(Profiles):
    """Profile reads by identifier and the profile list of the A4 naming."""

    def list(self) -> tuple[Any, ...]:
        """Return every profile."""
        return tuple(self.records.values())


class PreviewFixture(unittest.TestCase):
    """Temporary DayZ root with a verified and an unverified mission, three profiles and the preview service."""

    def setUp(self) -> None:
        """Write both missions and build the plan, medical and preview services over temporary folders."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_preview_")
        base = Path(self.temp.name)
        self.root = base / "DayZServer"
        for name in (VERIFIED, UNVERIFIED):
            self.write_mission(self.root / "mpmissions" / name)
        (self.root / "profile_main").mkdir()
        self.mission = self.root / "mpmissions" / VERIFIED
        self.area = base / "manager" / "data" / "mission-map"
        self.baselines = base / "manager" / "data" / "tweak-baselines"
        self.profiles = ListedProfiles(profile("main", f"mpmissions\\{VERIFIED}", "profile_main"),
                                       profile("second", f"mpmissions\\{VERIFIED}"),
                                       profile("other", f"mpmissions\\{UNVERIFIED}"))
        self.settings = SimpleNamespace(dayz_root=str(self.root), revision=1)
        settings = SimpleNamespace(load=lambda: self.settings)
        self.plans = MissionMapPlanService(self.profiles, settings, self.area)  # type: ignore[arg-type]
        self.medical = MedicalFeatureService(self.profiles, settings, self.baselines,  # type: ignore[arg-type]
                                             mission_map=self.area)
        self.preview = MissionMapPreview(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path)
        self.operations = 0

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temp.cleanup()

    @staticmethod
    def write_mission(mission: Path) -> None:
        """Write every input of a synthetic mission whose header matches the 1,000 m plan bounds."""
        (mission / "env").mkdir(parents=True)
        (mission / "db").mkdir()
        files = {"areaflags.map": mission_bytes(), "cfglimitsdefinition.xml": limits_text(
                     "Tier1", "Tier2", "Tier3", "Tier4").encode(), **BASELINE,
                 "env/zombie_territories.xml": TERRITORIES, "env/wolf_territories.xml": WOLVES,
                 "db/events.xml": EVENTS, "db/globals.xml": GLOBALS, "cfgenvironment.xml": ENVIRONMENT}
        for relative, data in files.items():
            (mission / relative).write_bytes(data)

    def key(self, folder: str = VERIFIED) -> str:
        """Return the mission key of a mission folder."""
        return target_key(TargetClass.MISSION, (self.root / "mpmissions" / folder).resolve())

    def save(self, objects: list[dict[str, Any]], global_value: int = 0, identifier: str = "main",
             expected: int | None = None, **terrain: Any) -> int:
        """Save a plan of the profile's active association and return its revision."""
        plan = stored_plan(objects)
        plan.update(association=self.plans.active_association(identifier), global_abundance=global_value,
                    background=None)
        plan["terrain"].update(terrain)
        return self.plans.save(identifier, plan, expected)["revision"]

    def review(self, identifier: str = "main") -> PreviewReview:
        """Return a complete review of the profile's saved plan."""
        return self.preview.review(identifier)

    def write(self, relative: str, data: bytes | None) -> None:
        """Replace or remove one file of the verified mission."""
        path = self.mission / relative
        if data is None:
            path.unlink()
        else:
            path.write_bytes(data)

    def set_medical_baseline(self, data: bytes = PROTOTYPE) -> None:
        """Store the whole-file loot-zone baseline of profile main in the manager location (6.3)."""
        folder = self.baselines / "main"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "medical_loot_zones.original.xml").write_bytes(data)

    def apply(self, review: PreviewReview, identifier: str = "main") -> str:
        """Simulate a committed Apply of the review: files, original, baseline, applied record and ledger entry.

        The records use the shapes of 2.5 and 8.4; the commit step itself has its own tests.
        """
        self.operations += 1
        operation = f"op{self.operations:04d}"
        key, area, before = self.key(), self.area, review.inputs.managed
        after = review.candidates.contents()
        written = {path: data for path, data in after.items() if data != before[path]}
        root = f"mpmissions\\{VERIFIED}"
        originals = OriginalStore(area, key)
        if originals.read() is None:
            originals.create({"target_class": "mission", "target_key": key, "display_path": str(self.mission),
                              "mission_root": root, "terrain_id": "chernarusplus", "captured_at": APPLIED_AT,
                              "profile_id": identifier, "operation_id": operation,
                              "files": [self.entry(path, before[path], None) for path in MISSION_FILE_SET]},
                             {sha(data): data for data in before.values() if data is not None})
        original = originals.read()
        start = {item["path"]: originals.contents.read(item["sha256"]) if item["existed"] else None
                 for item in original["files"]}
        baseline = AssociationRecordStore(area, identifier, key, "baseline")
        if baseline.read() is None:
            baseline.save({"profile_id": identifier, "mission_key": key, "mission_root": root, "target_keys": [key],
                           "originals": [{"target_key": key, "manifest_sha256": original["manifest_sha256"]}],
                           "files": [self.entry(path, data, key) for path, data in start.items()],
                           "provenance": []}, {sha(data): data for data in start.values() if data is not None}, None)
        found = baseline.read()
        plan = self.plans.load(identifier)["plan"]
        applied = AssociationRecordStore(area, identifier, key, "applied")
        previous = applied.read()
        applied.save({"profile_id": identifier, "mission_key": key, "mission_root": root, "operation_id": operation,
                      "operation_kind": "apply", "applied_at": APPLIED_AT, "plan_id": plan["plan_id"],
                      "plan_revision": review.answer["plan"]["revision"],
                      "configuration_fingerprint": configuration_fingerprint(plan), "baseline_revision": found[1],
                      "baseline_manifest_sha256": found[0]["manifest_sha256"],
                      "files_after": [{"target_key": key, "path": path, "existed": data is not None, "sha256": sha(data),
                                       "owned_sha256": self.owned(path, data)} for path, data in after.items()],
                      "files_before": [self.entry(path, before[path], key) for path in written],
                      "excluded_encounters": []},
                     {sha(before[path]): before[path] for path in written if before[path] is not None},
                     None if previous is None else previous[1])
        for path, data in written.items():
            self.write(path, data)
        if written:
            LedgerStore(area).create({
                "operation_id": operation, "operation_kind": "apply", "recorded_at": APPLIED_AT,
                "profile_id": identifier, "target_class": "mission", "target_key": key,
                "display_path": str(self.mission), "mission_root": root, "output": True, "carried_operation_id": None,
                "files": [{"path": path, "before_sha256": sha(before[path]), "after_sha256": sha(data),
                           "baseline_sha256": sha(start[path]), "owned_before_sha256": self.owned(path, before[path]),
                           "owned_after_sha256": self.owned(path, data),
                           "owned_baseline_sha256": self.owned(path, start[path])} for path, data in written.items()]})
        return operation

    @staticmethod
    def entry(path: str, data: bytes | None, key: str | None) -> dict[str, Any]:
        """Return one stored file entry; an original entry has no target key."""
        item = {"path": path, "existed": data is not None, "sha256": sha(data),
                "size": None if data is None else len(data)}
        return item if key is None else dict(item, target_key=key)

    @staticmethod
    def owned(path: str, data: bytes | None) -> str | None:
        """Return the owned-value digest of a mission file."""
        return owned_digest(TargetClass.MISSION, path, data)
