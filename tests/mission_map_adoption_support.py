"""Moved, copied, retired and restored missions for the adoption review tests of leaf 10.13."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dayz_serverman.application.mission_map_adoption_review import AdoptionReader
from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator
from dayz_serverman.domain.mission_map_records import copied_original
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import association_folder, invalidated_name
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_preview_support import VERIFIED, PreviewFixture
from tests.test_mission_map_plan_service import profile

# Time of the review clock, and of the dated history name
MOMENT = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
# Folder of a copy of the verified mission in the same DayZ root, and its relative mission root
COPY = "copy.chernarusplus"
COPY_ROOT = f"mpmissions\\{COPY}"


class AdoptionFixture(PreviewFixture):
    """The preview fixture with the adoption reader, the load coordinator and moved or copied missions."""

    def setUp(self) -> None:
        """Build the reader with a fixed clock and the coordinator of the load answer."""
        super().setUp()
        self.reader = AdoptionReader(self.plans, self.profiles, clock=lambda: MOMENT)
        self.coordinator = MissionMapCoordinator(self.plans)
        self.base = Path(self.temp.name)

    def load(self, identifier: str = "main") -> dict[str, Any]:
        """Return the load answer of the bridge method."""
        return self.coordinator.load_mission_map({"profile_id": identifier})

    def answer(self, identifier: str = "main") -> dict[str, Any]:
        """Return the adoption answer of a profile."""
        return self.reader.review(identifier).answer

    def applied(self) -> str:
        """Save a plan of profile main and simulate its first Apply; return the operation ID."""
        self.save([zone("rich")], 25)
        return self.apply(self.review())

    def move_root(self, *, keep_old: bool = False, name: str = "DayZMoved") -> Path:
        """Move or copy the DayZ root and point the settings at the new root; return the old mission folder."""
        old, new = self.mission, self.base / name
        if keep_old:
            shutil.copytree(self.root, new)
        else:
            self.root.rename(new)
        self.settings.dayz_root = str(new)
        self.root, self.mission = new, new / "mpmissions" / VERIFIED
        return old

    def copy_mission(self, identifier: str = "third", source: Path | None = None, name: str = COPY) -> Path:
        """Copy a mission folder inside the DayZ root and add a profile with a saved plan on the copy."""
        target = self.root / "mpmissions" / name
        shutil.copytree(source or self.mission, target)
        self.profiles.records[identifier] = profile(identifier, f"mpmissions\\{name}")
        self.save([zone("rich")], identifier=identifier)
        return target

    def retire(self, identifier: str, old_key: str, new_key: str) -> None:
        """Write the retired marker of an earlier adoption into an association folder."""
        marker = VersionedJsonRepository(association_folder(self.area, identifier, old_key) / "retired.json", 1)
        marker.save({"operation_id": "adopt-1", "retired_at": "2026-10-16T13:00:00Z", "new_mission_key": new_key},
                     None)

    def invalidate(self, identifier: str = "main") -> None:
        """Write an invalidated last-applied record into the active folder, as a restore leaves it (AR-6)."""
        folder = association_folder(self.area, identifier, self.plans.active_association(identifier)["mission_key"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / invalidated_name(MOMENT, 1)).write_text("{}", encoding="utf-8")

    def adopted_copy(self, source_key: str, key: str, display: Path, operation: str) -> None:
        """Simulate the records of an adopted copy (10.35): a copied original and the repeated output entry."""
        source = OriginalStore(self.area, source_key)
        original = source.read()
        record = copied_original(original, target_key=key, display_path=str(display), mission_root=COPY_ROOT,
                                 case="copy", operation_id="adopt-1")
        OriginalStore(self.area, key).create(record, source.stored_contents(original))
        entry = dict(LedgerStore(self.area).read(source_key, operation), target_key=key, display_path=str(display),
                     mission_root=COPY_ROOT, operation_id="adopt-1", recorded_at="2026-10-16T13:00:00Z",
                     profile_id="third")
        entry.pop("manifest_sha256")
        LedgerStore(self.area).create(entry)
