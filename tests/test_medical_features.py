"""Tests for medical feature transforms and manager-owned baseline restoration."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.medical_features import MedicalFeatureService
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.medical_features import transform_medical_feature


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

    def test_service_enables_and_restores_from_manager_owned_baseline(self) -> None:
        """Enabling stores a baseline and disabling restores the original bytes."""
        with tempfile.TemporaryDirectory(prefix="serverman_medical_") as temporary:
            root = Path(temporary); mission = root / "mpmissions" / "dayzOffline.test"
            (mission / "db").mkdir(parents=True)
            source = b'''<?xml version="1.0"?><types><type name="BandageDressing">
            <nominal>1</nominal><min>1</min></type></types>'''
            target = mission / "db" / "types.xml"; target.write_bytes(source)
            (mission / "mapgroupproto.xml").write_text("<groups/>", encoding="utf-8")
            service = MedicalFeatureService(FakeProfiles(), FakeSettings(root), root / "baselines")  # type: ignore[arg-type]
            loaded = service.load("main")
            feature = loaded["features"]["medical_item_spawns"]
            service.apply("main", "medical_item_spawns", True, 3, 4, feature["digest"], lambda *_: None)
            enabled = service.load("main")["features"]["medical_item_spawns"]
            self.assertTrue(enabled["enabled"])
            service.apply("main", "medical_item_spawns", False, 3, 4, enabled["digest"], lambda *_: None)
            self.assertEqual(target.read_bytes(), source)
            self.assertTrue((root / "baselines" / "main" / "medical_item_spawns.original.xml").is_file())
    def test_item_spawn_transform_matches_legacy_counts_and_adds_usage(self) -> None:
        """The item spawn transform applies legacy counts and the Medic usage."""
        source = b'''<?xml version="1.0"?><types><type name="BandageDressing">
        <nominal>1</nominal><min>1</min><usage name="Town"/></type></types>'''
        result = transform_medical_feature("medical_item_spawns", source).decode("utf-8")
        self.assertIn("<nominal>120</nominal>", result)
        self.assertIn("<min>60</min>", result)
        self.assertIn('usage name="Medic"', result)


if __name__ == "__main__":
    unittest.main()
