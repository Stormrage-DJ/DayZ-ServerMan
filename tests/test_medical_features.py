"""Tests for medical feature transforms and manager-owned baseline restoration."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.medical_features import EDITOR_MANAGED, NO_ZONE_CHANGE, MedicalFeatureService
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.medical_features import legacy_zone_transform, transform_medical_feature
from dayz_serverman.repositories.mission_map_layout import TargetClass, target_key

# An original prototype with one medical group, an unrelated comment and ceilings
ZONES = (b'<?xml version="1.0"?>\n<prototype>\n    <!-- vanilla -->\n'
         b'    <group name="Hospital_Main" lootmax="9">\n        <usage name="Medic"/>\n        <usage name="Town"/>\n'
         b'        <container name="lootFloor" lootmax="3">\n            <category name="tools"/>\n'
         b'            <category name="weapons"/>\n        </container>\n    </group>\n</prototype>\n')


class FakeProfiles:
    """Profile port stub serving the medical feature fixture profile."""
    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the fixture profile regardless of the requested identifier."""
        values = ProfileInput("main", "Main", "DayZServer_x64.exe", "serverDZ.cfg",
                              "mpmissions/dayzOffline.test", 2302, (), ())
        return ProfileRecord(3, values)


class FakeSettings:
    """Settings port stub exposing a fixed root and revision."""
    def __init__(self, root: Path) -> None:
        """Capture the DayZ root used by the medical feature service."""
        self._value = SimpleNamespace(dayz_root=str(root), revision=4)
    def load(self) -> object:
        """Return the captured settings namespace."""
        return self._value


def _zone_service(root: Path, data: bytes = ZONES) -> tuple[MedicalFeatureService, Path]:
    """Return a service over a temporary mission whose prototype holds `data`."""
    mission = root / "mpmissions" / "dayzOffline.test"
    (mission / "db").mkdir(parents=True)
    (mission / "db" / "types.xml").write_bytes(b"<types/>")
    (mission / "mapgroupproto.xml").write_bytes(data)
    service = MedicalFeatureService(FakeProfiles(), FakeSettings(root), root / "baselines",  # type: ignore[arg-type]
                                    mission_map=root / "mission-map")
    return service, mission / "mapgroupproto.xml"


def _toggle(service: MedicalFeatureService, feature: str, enabled: bool) -> None:
    """Apply one toggle with the current revisions and digest."""
    digest = service.load("main")["features"][feature]["digest"]
    service.apply("main", feature, enabled, 3, 4, digest, lambda *_: None)


class MedicalFeatureTransformTests(unittest.TestCase):
    """Transform and restore contracts of the medical feature service."""
    def test_loot_zone_transform_keeps_only_medical_usage_and_categories(self) -> None:
        """The loot zone transform keeps medical usage and categories only."""
        source = b'''<?xml version="1.0"?><groups><group name="Hospital_Main">
        <usage name="Medic"/><usage name="Town"/><container><category name="tools"/>
        <category name="weapons"/></container></group></groups>'''
        result = transform_medical_feature("medical_loot_zones", source).decode("utf-8")
        self.assertIn('usage name="Medic"', result)
        self.assertNotIn('usage name="Town"', result)
        self.assertIn('category name="tools"', result)
        self.assertNotIn('category name="weapons"', result)

    def test_item_spawns_are_no_medical_feature_any_more(self) -> None:
        """The medical item supply is a loot supply group: load lists only the zones, and item spawns refuse."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, _target = _zone_service(root)
            loaded = service.load("main")
            self.assertEqual(set(loaded["features"]), {"medical_loot_zones"})
            with self.assertRaisesRegex(ValueError, "medical feature request is invalid"):
                service.preview("main", "medical_item_spawns", True, 3, 4, "0" * 64)
        with self.assertRaisesRegex(ConfigurationFileError, "not supported"):
            transform_medical_feature("medical_item_spawns", b"<types/>")


class MedicalLootZoneServiceTests(unittest.TestCase):
    """Element-level loot-zone toggles through the service, with the editor guard."""

    def test_zone_toggle_keeps_unrelated_bytes_and_restores_the_original(self) -> None:
        """On changes only medical children; off restores the original bytes from the stored baseline."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, target = _zone_service(root)
            _toggle(service, "medical_loot_zones", True)
            enabled = target.read_bytes()
            self.assertIn(b"<!-- vanilla -->", enabled)
            self.assertIn(b'lootmax="9"', enabled)
            self.assertNotIn(b"weapons", enabled)
            self.assertTrue(service.load("main")["features"]["medical_loot_zones"]["enabled"])
            self.assertEqual((root / "baselines" / "main" / "medical_loot_zones.original.xml").read_bytes(), ZONES)
            _toggle(service, "medical_loot_zones", False)
            self.assertEqual(target.read_bytes(), ZONES)

    def test_legacy_baseline_and_earlier_whole_file_form_are_recognized(self) -> None:
        """A legacy in-mission baseline gives the off children, and the earlier enabled form reads as on."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, target = _zone_service(root, legacy_zone_transform(ZONES))
            (target.parent / ".dayz_manager_backups").mkdir()
            (target.parent / ".dayz_manager_backups" / "mapgroupproto.xml").write_bytes(ZONES)
            self.assertTrue(service.load("main")["features"]["medical_loot_zones"]["enabled"])
            _toggle(service, "medical_loot_zones", False)
            self.assertEqual(target.read_bytes(), ZONES)

    def test_editor_managed_missions_refuse_the_zone_toggle_only(self) -> None:
        """An editor group or an editor apply record refuses zones; load keeps working."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary)
            editor = ZONES.replace(b"</prototype>", b'<group name="ServerManMap_G_0123456789ab"/></prototype>')
            service, target = _zone_service(root, editor)
            digest = service.load("main")["features"]["medical_loot_zones"]["digest"]
            with self.assertRaisesRegex(ValueError, EDITOR_MANAGED):
                service.preview("main", "medical_loot_zones", True, 3, 4, digest)
            target.write_bytes(ZONES)
            key = target_key(TargetClass.MISSION, target.parent.resolve(strict=True))
            folder = root / "mission-map" / "profiles" / "other" / key
            folder.mkdir(parents=True); (folder / "applied.json").write_text("{}", encoding="utf-8")
            digest = service.load("main")["features"]["medical_loot_zones"]["digest"]
            with self.assertRaisesRegex(ValueError, EDITOR_MANAGED):
                service.apply("main", "medical_loot_zones", True, 3, 4, digest, lambda *_: None)
            self.assertEqual(target.read_bytes(), ZONES)
            self.assertFalse(service.load("main")["features"]["medical_loot_zones"]["enabled"])

    def test_zone_toggle_without_a_changing_group_succeeds_and_writes_nothing(self) -> None:
        """Enable and Disable succeed with the no-change message; the state reads on and no file is written."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); service, target = _zone_service(root, b"<prototype><group name=\"Land_House\"/></prototype>")
            before = target.read_bytes()
            self.assertTrue(service.load("main")["features"]["medical_loot_zones"]["enabled"])
            for enabled in (True, False, True):
                with self.subTest(enabled=enabled):
                    digest = service.load("main")["features"]["medical_loot_zones"]["digest"]
                    preview = service.preview("main", "medical_loot_zones", enabled, 3, 4, digest)
                    self.assertEqual((preview["changed_fields"], preview["message"], preview["proposed_digest"]),
                                     ([], NO_ZONE_CHANGE, digest))
                    result = service.apply("main", "medical_loot_zones", enabled, 3, 4, digest, lambda *_: None)
                    self.assertEqual((result["changed_fields"], result["message"], result["digest"]),
                                     ([], NO_ZONE_CHANGE, digest))
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse((root / "baselines" / "main").exists())

    def test_layouts_that_cannot_be_spliced_name_the_cause_and_the_file(self) -> None:
        """A UTF-16 file and a group with another element between its usages refuse with the named cause."""
        split = ZONES.replace(b'<usage name="Town"/>', b'<value name="Tier1"/><usage name="Town"/>')
        cases = ((split, "group Hospital_Main has another element between usage and category"),
                 (ZONES.decode("utf-8").encode("utf-16"), "the file is not UTF-8"))
        for data, cause in cases:
            with self.subTest(cause=cause), tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
                service, target = _zone_service(Path(temporary), data)
                expected = f"mapgroupproto.xml has a layout that this setting cannot change safely: {cause}. No file was changed."
                with self.assertRaises(ConfigurationFileError) as caught:
                    digest = service.load("main")["features"]["medical_loot_zones"]["digest"]
                    service.preview("main", "medical_loot_zones", True, 3, 4, digest)
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(target.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
