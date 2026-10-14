"""Tests for the read-only classification of stored medical loot-zone baselines (6.4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.application import medical_features
from dayz_serverman.application.medical_features import EDITOR_MANAGED, MedicalFeatureService
from dayz_serverman.repositories.configuration_common import digest_bytes
from dayz_serverman.repositories.medical_features import legacy_zone_transform
from dayz_serverman.repositories.medical_zone_classification import (
    LEGACY, MANAGER, ZoneBaselineClass, classify_zone_baseline, line_endings,
)
from dayz_serverman.repositories.medical_zone_fragments import ADDED, DIFFERS, MISSING, patch_zones
from dayz_serverman.repositories.medical_zone_scan import LAYOUT, MIXED_LINES
from dayz_serverman.repositories.mission_map_layout import TargetClass, target_key
from tests.test_medical_features import ZONES, _toggle, _zone_service

# The fixture prototype with the feature on, as the 6.3 patcher writes it
ZONES_ON = patch_zones(ZONES, ZONES, True)
# The fixture prototype without its medical group, and with one more qualified group
EMPTY = b'<?xml version="1.0"?>\n<prototype>\n</prototype>\n'
ADDED_GROUP = ZONES.replace(b"</prototype>", b'    <group name="PlannerRich_00534"><usage name="Medic"/></group>\n</prototype>')


def _snapshot(root: Path) -> dict[str, tuple[bytes | None, int]]:
    """Return the bytes and the modification time of every file and folder under `root`."""
    return {str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None, path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*"))}


def _place(root: Path, location: str, data: bytes) -> None:
    """Write a whole-file loot-zone baseline in the manager store or in the legacy mission folder."""
    if location == MANAGER:
        path = root / "baselines" / "main" / "medical_loot_zones.original.xml"
    else:
        path = root / "mpmissions" / "dayzOffline.test" / ".dayz_manager_backups" / "mapgroupproto.xml"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)


class ZoneBaselineClassificationTests(unittest.TestCase):
    """Classification through the service: results, refusal reasons and no file change."""

    def _classify(self, root: Path, service: MedicalFeatureService) -> ZoneBaselineClass | None:
        """Classify twice, check that equal runs agree and that no file or folder changed, and return the result."""
        before = _snapshot(root)
        first, second = service.classify_zone_baseline("main"), service.classify_zone_baseline("main")
        self.assertEqual(first, second)
        self.assertEqual(_snapshot(root), before)
        return first

    def test_mappable_manager_baseline_wins_over_the_legacy_one(self) -> None:
        """A manager baseline of an on file is mappable; the legacy baseline is not used then."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, _target = _zone_service(root, ZONES_ON)
            _place(root, MANAGER, ZONES); _place(root, LEGACY, EMPTY)
            self.assertEqual(self._classify(root, service), ZoneBaselineClass(MANAGER, True, True, None, "LF", "LF"))

    def test_legacy_location_and_earlier_whole_file_form_are_mappable(self) -> None:
        """A legacy baseline maps an off file and the whole-file form that earlier versions wrote."""
        for current, enabled in ((ZONES, False), (legacy_zone_transform(ZONES), True)):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
                root = Path(temporary); service, _target = _zone_service(root, current); _place(root, LEGACY, ZONES)
                result = self._classify(root, service)
                self.assertEqual((result.location, result.mappable, result.enabled, result.reason),
                                 (LEGACY, True, enabled, None))

    def test_unmappable_baselines_give_the_refusal_of_the_toggle(self) -> None:
        """Each unmappable case names the 6.3 refusal, and the toggle refuses with the same sentence."""
        mixed = ZONES_ON.replace(b"?>\n", b"?>\r\n", 1)
        cases = (
            (ADDED_GROUP, ADDED.format(group="PlannerRich_00534")),
            (EMPTY, MISSING.format(group="Hospital_Main")),
            (ZONES.replace(b"Town", b"Village"), DIFFERS),
            (mixed, LAYOUT.format(cause=MIXED_LINES)),
        )
        for current, reason in cases:
            for location in (MANAGER, LEGACY):
                with self.subTest(reason=reason, location=location), \
                        tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
                    root = Path(temporary); service, target = _zone_service(root, current)
                    _place(root, location, ZONES)
                    result = self._classify(root, service)
                    self.assertEqual((result.location, result.mappable, result.enabled, result.reason),
                                     (location, False, None, reason))
                    with self.assertRaises(RuntimeError) as caught:
                        service.preview("main", "medical_loot_zones", False, 3, 4, digest_bytes(current))
                    self.assertEqual(str(caught.exception), reason)
                    self.assertEqual(target.read_bytes(), current)

    def test_editor_managed_mission_gives_the_refusal_of_the_toggle(self) -> None:
        """An editor group or an editor applied record makes a mappable baseline unmappable, as the toggle refuses."""
        editor = ZONES_ON.replace(b"</prototype>", b'<group name="ServerManMap_G_0123456789ab"/></prototype>')
        for current, record in ((editor, False), (ZONES_ON, True)):
            with self.subTest(record=record), tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
                root = Path(temporary); service, target = _zone_service(root, current); _place(root, LEGACY, ZONES)
                if record:
                    key = target_key(TargetClass.MISSION, target.parent.resolve(strict=True))
                    folder = root / "mission-map" / "profiles" / "other" / key
                    folder.mkdir(parents=True); (folder / "applied.json").write_text("{}", encoding="utf-8")
                self.assertEqual(self._classify(root, service),
                                 ZoneBaselineClass(LEGACY, False, None, EDITOR_MANAGED, "LF", "LF"))
                with self.assertRaisesRegex(ValueError, EDITOR_MANAGED):
                    service.preview("main", "medical_loot_zones", False, 3, 4, digest_bytes(current))

    def test_other_line_endings_are_information_and_off_keeps_the_current_form(self) -> None:
        """An LF baseline of a CRLF file is mappable; the 6.3 off write then gives the exact CRLF original."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, target = _zone_service(root, ZONES_ON.replace(b"\n", b"\r\n"))
            _place(root, LEGACY, ZONES)
            result = self._classify(root, service)
            self.assertEqual(result, ZoneBaselineClass(LEGACY, True, True, None, "LF", "CRLF"))
            self.assertTrue(result.line_endings_differ)
            _toggle(service, "medical_loot_zones", False)
            self.assertEqual(target.read_bytes(), ZONES.replace(b"\n", b"\r\n"))
            self.assertEqual(line_endings(target.read_bytes()), "CRLF")

    def test_no_baseline_gives_no_result_and_creates_nothing(self) -> None:
        """Without a stored baseline there is nothing to classify, and no baseline folder is created."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, _target = _zone_service(root, ZONES)
            self.assertIsNone(self._classify(root, service))
            self.assertFalse((root / "baselines").exists())

    def test_only_the_prototype_and_its_baseline_are_read(self) -> None:
        """Item-spawn baselines and `db/types.xml` are not read."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, _target = _zone_service(root, ZONES_ON); _place(root, MANAGER, ZONES)
            (root / "baselines" / "main" / "medical_item_spawns.original.xml").write_bytes(b"not xml")
            read = []
            original = medical_features.read_bytes_shared
            with mock.patch.object(medical_features, "read_bytes_shared",
                                   side_effect=lambda path: read.append(Path(path).name) or original(path)):
                self.assertTrue(self._classify(root, service).mappable)
            self.assertEqual(sorted(set(read)), ["mapgroupproto.xml", "medical_loot_zones.original.xml"])


class ZoneBaselineFunctionTests(unittest.TestCase):
    """The pure classification over bytes."""

    def test_equal_inputs_give_equal_results(self) -> None:
        """Equal runs over the same bytes give equal results, mappable or not."""
        for current in (ZONES_ON, ADDED_GROUP):
            with self.subTest(mappable=current is ZONES_ON):
                self.assertEqual(classify_zone_baseline(current, ZONES, LEGACY),
                                 classify_zone_baseline(current, ZONES, LEGACY))

    def test_a_file_without_a_changing_group_is_mappable_and_reads_on(self) -> None:
        """A baseline whose groups have equal on and off forms is mappable; the state reads on, as in 6.3."""
        data = b'<prototype><group name="Land_House"><usage name="Town"/></group></prototype>'
        self.assertEqual(classify_zone_baseline(data, data, MANAGER),
                         ZoneBaselineClass(MANAGER, True, True, None, "none", "none"))

    def test_line_ending_forms(self) -> None:
        """The forms are named in a fixed order, and bytes without a line break have none."""
        cases = ((b"", "none"), (b"a\nb\n", "LF"), (b"a\r\nb", "CRLF"), (b"a\rb\r\nc\n", "CRLF+LF+CR"))
        for data, form in cases:
            with self.subTest(form=form):
                self.assertEqual(line_endings(data), form)


if __name__ == "__main__":
    unittest.main()
