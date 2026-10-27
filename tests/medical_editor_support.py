"""The coordinated medical loot-zone toggle of leaf 8.13 over the 8.7 Apply fixture."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dayz_serverman.application.medical_editor_toggle import MedicalEditorToggle
from dayz_serverman.repositories.medical_zone_scan import scan_groups
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import PROTOTYPE, zone

# Fixed clock of every toggle, after the Apply clock of the fixture
TOGGLE_NOW = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone.utc)
ZONES = "medical_loot_zones"
# The item-type file that the medical service reads next to the loot zones
TYPES = b"<types />"
PROTO = "mapgroupproto.xml"


class ToggleFixture(ApplyFixture):
    """A verified mission with the 8.7 Apply service and the medical service bound to the D4 toggle."""

    def setUp(self) -> None:
        """Bind the toggle over the fixture's commit step, guard ports and association locks."""
        super().setUp()
        # The medical service also loads the item-spawn file
        (self.mission / "db" / "types.xml").write_bytes(TYPES)
        self.toggle = MedicalEditorToggle(self.paths, self.commit, clock=lambda: TOGGLE_NOW)
        self.medical.bind_editor(self.toggle)
        self.toggles = 0

    def applied_with_medical(self, abundance: str = "rich", global_value: int = 25) -> bytes:
        """Store the medical baseline, apply a capacity plan with medical off, and return mapgroupproto.xml."""
        self.set_medical_baseline()
        self.save([zone(abundance)], global_value)
        self.run_apply()
        return self.proto()

    def proto(self) -> bytes:
        """Return the current mapgroupproto.xml of the verified mission."""
        return (self.mission / PROTO).read_bytes()

    def request(self, enabled: bool) -> tuple[Any, ...]:
        """Return the six request fields of a zone toggle with the loaded revisions and digest."""
        loaded = self.medical.load("main")
        return ("main", ZONES, enabled, loaded["profile_revision"], loaded["settings_revision"],
                loaded["features"][ZONES]["digest"])

    def zone_preview(self, enabled: bool) -> dict[str, Any]:
        """Return the zone preview of the main profile."""
        return self.medical.preview(*self.request(enabled))

    def run_toggle(self, enabled: bool, fingerprint: object = "review", **kwargs: Any) -> dict[str, Any]:
        """Review and apply one zone toggle; the reviewed fingerprint unless a test gives another."""
        self.toggles += 1
        request = self.request(enabled)
        if fingerprint == "review":
            fingerprint = self.medical.preview(*request).get("preview_fingerprint")
        return self.medical.apply(*request, lambda *_: None, preview_fingerprint=fingerprint,
                                  operation_id=kwargs.pop("operation_id", f"medical{self.toggles:04d}"), **kwargs)

    def applied_record(self) -> tuple[dict[str, Any], int]:
        """Return the last-applied record of the main profile and its revision."""
        found = self.records()[2]
        assert found is not None
        return found

    def generated(self, data: bytes | None = None) -> list[bytes]:
        """Return the bytes of every generated group of mapgroupproto.xml."""
        data = self.proto() if data is None else data
        return [data[node.start:node.end] for node in scan_groups(data)
                if node.attributes.get("name", "").startswith("ServerManMap_G_")]

    def ceilings(self, data: bytes | None = None) -> list[tuple[str, ...]]:
        """Return the name and lootmax of every group and container, in document order."""
        data = self.proto() if data is None else data
        result = []
        for node in scan_groups(data):
            result.append((node.attributes.get("name", ""), node.attributes.get("lootmax", "")))
            result += [(child.attributes.get("name", ""), child.attributes.get("lootmax", ""))
                       for child in node.children if child.tag == "container"]
        return result

    def other_files(self) -> dict[str, bytes]:
        """Return every mission file other than mapgroupproto.xml."""
        return {str(path.relative_to(self.mission)): path.read_bytes() for path in self.mission.rglob("*")
                if path.is_file() and path.name != PROTO}

    def medical_baseline_path(self) -> Path:
        """Return the manager-owned loot-zone baseline of the main profile."""
        return self.baselines / "main" / "medical_loot_zones.original.xml"


__all__ = ["PROTOTYPE", "PROTO", "TOGGLE_NOW", "ToggleFixture", "ZONES"]
