"""Verify honest AI_Bandits detection, build evidence and all path-containment failure states."""

from __future__ import annotations

import hashlib
import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_serverman.domain.mission_map_providers import AI_BANDITS_WORKSHOP_ID, NO_VERIFIED_HEIGHT_REASON
from dayz_serverman.domain.profiles import ModSource
from tests.test_mission_map_providers import ProviderFixture


class BanditProviderTests(ProviderFixture):
    """Observe provider presence without enabling placement or modifying customer configuration."""

    def bandits(self) -> dict:
        """Require the exact unavailable placement contract in every observed detection state."""
        result = self.providers()["ai_bandits"]
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], NO_VERIFIED_HEIGHT_REASON)
        self.assertFalse(result["evidence"]["build_verified"])
        return result

    def test_all_three_presence_facts_detect_provider_and_record_current_unverified_build(self) -> None:
        """A matching Workshop mod and its runtime file prove presence, while placement stays unavailable."""
        self.seed_bandits()
        result = self.bandits()
        evidence = result["evidence"]
        self.assertTrue(result["detected"])
        self.assertEqual(evidence["workshop_id"], AI_BANDITS_WORKSHOP_ID)
        self.assertTrue(evidence["configured"])
        self.assertTrue(evidence["mod_present"])
        self.assertTrue(evidence["configuration_present"])
        self.assertEqual(evidence["pbo_sha256"], hashlib.sha256(b"synthetic provider build").hexdigest())
        self.assertEqual(evidence["build_reason"], "provider_build_unverified")
        self.assertEqual(evidence["diagnostics"], [])
        self.assertNotIn(str(self.root), str(result))
        # Changed bytes are observed as a different unverified build; no expected digest is fabricated
        (self.root / "@SyntheticProvider" / "addons" / "AI_Bandits.pbo").write_bytes(b"another build")
        self.assertNotEqual(self.bandits()["evidence"]["pbo_sha256"], evidence["pbo_sha256"])

    def test_folder_and_runtime_file_without_matching_workshop_source_do_not_detect(self) -> None:
        """A similar folder name, wrong Workshop ID or external source cannot imply provider support."""
        self.seed_bandits(workshop_id="123456789")
        self.assertFalse(self.bandits()["detected"])
        record = self.profiles.records["main"]
        mod = record.values.mods[0]
        external = replace(mod, source=ModSource("external", None))
        self.profiles.records["main"] = replace(record, values=replace(record.values, mods=(external,)))
        self.assertFalse(self.bandits()["detected"])
        self.profiles.records["main"] = replace(record, values=replace(record.values, mods=()))
        self.assertFalse(self.bandits()["detected"])
        self.assertEqual(self.bandits()["evidence"]["diagnostics"], ["provider_not_configured"])

    def test_missing_mod_runtime_folder_or_configuration_file_refuses_presence(self) -> None:
        """Each absent required fact has a safe diagnostic and keeps placement unavailable."""
        self.seed_bandits()
        record = self.profiles.records["main"]
        mod = record.values.mods[0]
        for change, diagnostic in (({"mods": (replace(mod, directory="@Missing"),)}, "provider_file_missing"),
                                   ({"runtime_profile": None}, "runtime_profile_unresolved"),
                                   ({"runtime_profile": "missing-profile"}, "provider_file_missing")):
            with self.subTest(change=change):
                self.profiles.records["main"] = replace(record, values=replace(record.values, **change))
                result = self.bandits()
                self.assertFalse(result["detected"])
                self.assertIn(diagnostic, result["evidence"]["diagnostics"])
        self.profiles.records["main"] = record
        (self.root / "profile_main" / "AI_Bandits" / "DynamicAIB.json").unlink()
        self.assertFalse(self.bandits()["detected"])

    def test_invalid_provider_data_and_read_failures_are_visible(self) -> None:
        """Malformed or unreadable runtime configuration never proves provider presence."""
        self.seed_bandits()
        path = self.root / "profile_main" / "AI_Bandits" / "DynamicAIB.json"
        for data in (b"bad JSON", b"[]", b'{"GroupLocations": {}}', b'{"GroupLocations": [], "x": NaN}',
                     b'{"GroupLocations": [], "GroupLocations": []}', b"\xff"):
            with self.subTest(data=data):
                path.write_bytes(data)
                result = self.bandits()
                self.assertFalse(result["detected"])
                self.assertIn("provider_configuration_invalid", result["evidence"]["diagnostics"])
        path.write_bytes(b'{"GroupLocations": []}')
        with mock.patch("dayz_serverman.repositories.mission_map_providers_paths.read_bytes_shared",
                        side_effect=OSError("private absolute path")):
            result = self.bandits()
            self.assertFalse(result["detected"])
            self.assertIn("provider_file_unreadable", result["evidence"]["diagnostics"])
            self.assertNotIn("private absolute path", str(result))

    def test_missing_or_unreadable_pbo_keeps_build_unverified(self) -> None:
        """Build inspection fails separately from the three required provider-presence facts."""
        self.seed_bandits()
        pbo = self.root / "@SyntheticProvider" / "addons" / "AI_Bandits.pbo"
        pbo.unlink()
        result = self.bandits()
        self.assertTrue(result["detected"])
        self.assertIsNone(result["evidence"]["pbo_sha256"])
        self.assertIn("provider_file_missing", result["evidence"]["diagnostics"])
        pbo.write_bytes(b"build")
        with mock.patch("dayz_serverman.repositories.mission_map_providers_paths.sha256_file",
                        side_effect=PermissionError("read refused")):
            result = self.bandits()
            self.assertIsNone(result["evidence"]["pbo_sha256"])
            self.assertIn("provider_file_unreadable", result["evidence"]["diagnostics"])

    def test_unsafe_mod_and_runtime_paths_are_refused_before_open(self) -> None:
        """Rooted and traversing inputs cannot direct provider reads outside the configured installation."""
        self.seed_bandits()
        record = self.profiles.records["main"]
        mod = record.values.mods[0]
        for unsafe in ("..\\outside", "C:\\outside", "\\\\server\\share"):
            for change in ({"mods": (replace(mod, directory=unsafe),)}, {"runtime_profile": unsafe}):
                with self.subTest(change=change):
                    self.profiles.records["main"] = replace(record, values=replace(record.values, **change))
                    result = self.bandits()
                    self.assertFalse(result["detected"])
                    self.assertIn("provider_path_invalid", result["evidence"]["diagnostics"])

    def test_reparse_mod_runtime_and_final_config_components_are_refused(self) -> None:
        """No configured mod, runtime folder or final provider file may be a reparse target."""
        self.seed_bandits()
        targets = (self.root / "@SyntheticProvider", self.root / "profile_main",
                   self.root / "profile_main" / "AI_Bandits" / "DynamicAIB.json")
        for target in targets:
            target = target.resolve(strict=True)
            with self.subTest(target=target), mock.patch(
                    "dayz_serverman.repositories.mission_map_providers_paths.is_reparse",
                    side_effect=lambda path: path == target):
                result = self.bandits()
                self.assertFalse(result["detected"])
                self.assertIn("provider_path_invalid", result["evidence"]["diagnostics"])

    def test_intermediate_runtime_directory_reparse_is_refused_by_existing_containment_owner(self) -> None:
        """A reparse directory inside the runtime profile cannot bypass the existing ancestor walk."""
        self.seed_bandits()
        target = (self.root / "profile_main" / "AI_Bandits").resolve(strict=True)
        original_lstat = os.lstat

        def reparse_metadata(path: object, *args: object, **kwargs: object) -> object:
            """Expose a synthetic Windows reparse attribute only on the intermediate directory."""
            info = original_lstat(path, *args, **kwargs)
            if Path(path) == target:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        with mock.patch("dayz_serverman.application.mission_configuration.os.lstat", side_effect=reparse_metadata):
            result = self.bandits()
            self.assertFalse(result["detected"])
            self.assertIn("provider_path_invalid", result["evidence"]["diagnostics"])


if __name__ == "__main__":
    unittest.main()
