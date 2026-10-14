"""Verify native provider registration, exact offered events and observer-safe capability discovery."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator
from dayz_serverman.bridge.contracts import ensure_json_value
from dayz_serverman.domain.mission_map_providers import AI_BANDITS_WORKSHOP_ID, NO_VERIFIED_HEIGHT_REASON
from dayz_serverman.domain.profiles import ModReference, ModSource
from dayz_serverman.repositories.json_store import StagingPolicy
from tests.test_mission_map_editor_load_save import CoordinatorFixture


def tree_state(root: Path) -> dict[str, str | None]:
    """Record directories and exact file digests for a temporary root before and after a read."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for path in root.rglob("*")}


class ProviderFixture(CoordinatorFixture):
    """Provide synthetic native and AI configuration over the existing temporary profile fixture."""

    def seed_native(self, registration: str = 'env/zombie_territories.xml', *,
                    zones: tuple[str, ...] = ("InfectedCity", "CustomPatrol", "NotAnEvent", "InfectedCity"),
                    events: tuple[str, ...] = ("CustomPatrol", "InfectedCity", "UnusedEvent")) -> None:
        """Create minimal registered territory and event documents without copying live mission data."""
        (self.mission / "env").mkdir(exist_ok=True)
        (self.mission / "db").mkdir(exist_ok=True)
        (self.mission / "cfgenvironment.xml").write_text(
            f'<env><territories><file path="{registration}"/></territories></env>', encoding="utf-8")
        zone_text = "".join(f'<zone name="{name}" x="10" z="20" r="30"/>' for name in zones)
        (self.mission / "env" / "zombie_territories.xml").write_text(
            f"<territory-type><territory>{zone_text}</territory></territory-type>", encoding="utf-8")
        event_text = "".join(f'<event name="{name}"/>' for name in events)
        (self.mission / "db" / "events.xml").write_text(f"<events>{event_text}</events>", encoding="utf-8")

    def seed_bandits(self, *, workshop_id: str = AI_BANDITS_WORKSHOP_ID, source: str = "workshop") -> None:
        """Create an installed provider, a tiny PBO and empty runtime configuration for profile main."""
        mod = self.root / "@SyntheticProvider"
        (mod / "addons").mkdir(parents=True)
        (mod / "addons" / "AI_Bandits.pbo").write_bytes(b"synthetic provider build")
        config = self.root / "profile_main" / "AI_Bandits"
        config.mkdir()
        (config / "DynamicAIB.json").write_text('{"GroupLocations": []}', encoding="utf-8")
        record = self.profiles.records["main"]
        reference = ModReference("@SyntheticProvider", "server",
                                 ModSource(source, workshop_id if source == "workshop" else None))
        self.profiles.records["main"] = replace(record, values=replace(record.values, mods=(reference,)))

    def providers(self) -> dict:
        """Read both additive provider entries through the coordinator."""
        answer = self.load()
        ensure_json_value(answer)
        return answer["capabilities"]["encounter_providers"]


class NativeProviderTests(ProviderFixture):
    """Cover registration and event identity separately from terrain availability."""

    def test_registered_native_provider_offers_only_matching_names_once(self) -> None:
        """The exact zone/event intersection includes custom names and excludes missing events."""
        self.seed_native()
        native = self.providers()["native_infected"]
        self.assertTrue(native["detected"])
        self.assertTrue(native["available"])
        self.assertIsNone(native["reason"])
        self.assertEqual(native["event_names"], ["CustomPatrol", "InfectedCity"])
        self.assertTrue(native["evidence"]["registered"])
        self.assertFalse(self.area.exists())
        self.assertIsNone(self.load()["plan"])

    def test_existing_unregistered_territory_is_unavailable(self) -> None:
        """Only the supported direct file registration counts, with Windows separator/case spelling."""
        self.seed_native()
        invalid = ('<env><territories/></env>',
                   '<env><territories><territory><file path="env/zombie_territories.xml"/></territory></territories></env>',
                   '<env><territories><file usable="zombie_territories"/></territories></env>',
                   '<env><territories><file path="../env/zombie_territories.xml"/></territories></env>')
        for text in invalid:
            with self.subTest(registration=text):
                (self.mission / "cfgenvironment.xml").write_text(text, encoding="utf-8")
                native = self.providers()["native_infected"]
                self.assertFalse(native["detected"])
                self.assertEqual(native["reason"], "territory_not_registered")
                self.assertEqual(native["event_names"], [])
        self.seed_native("ENV\\ZOMBIE_TERRITORIES.XML")
        self.assertTrue(self.providers()["native_infected"]["available"])

    def test_no_matching_or_invalid_event_names_never_advertise_placements(self) -> None:
        """Event spelling is exact, and names must satisfy the actual D1 event-name boundary."""
        self.seed_native(zones=("a" * 64,), events=("a" * 64,))
        self.assertEqual(self.providers()["native_infected"]["event_names"], ["a" * 64])
        # The first value above the D1 length limit is not offered even if both files use it
        for zones, events in ((("InfectedCity",), ("infectedcity",)),
                              (("Bad-Name", "x" * 65), ("Bad-Name", "x" * 65)), ((), ())):
            with self.subTest(zones=zones):
                self.seed_native(zones=zones, events=events)
                native = self.providers()["native_infected"]
                self.assertTrue(native["detected"])
                self.assertFalse(native["available"])
                self.assertEqual(native["reason"], "no_matching_events")
                self.assertEqual(native["event_names"], [])

    def test_missing_files_and_unreadable_data_leave_plan_usable(self) -> None:
        """An unavailable native provider cannot invalidate a valid saved plan."""
        plan = self.draft()
        self.save(plan)
        self.seed_native()
        territory = self.mission / "env" / "zombie_territories.xml"
        territory.unlink()
        self.assertEqual(self.providers()["native_infected"]["reason"], "provider_file_missing")
        self.seed_native()
        (self.mission / "db" / "events.xml").unlink()
        self.assertEqual(self.providers()["native_infected"]["reason"], "provider_file_missing")
        # Shared read failures expose a safe capability reason without raw filesystem details
        with mock.patch("dayz_serverman.repositories.mission_map_providers_paths.read_bytes_shared",
                        side_effect=PermissionError("private absolute path")):
            self.assertEqual(self.providers()["native_infected"]["reason"], "provider_file_unreadable")
            loaded = self.load()
            self.assertEqual(loaded["state"], "VALID")
            self.assertEqual(loaded["plan"], plan)

    def test_invalid_xml_and_encoding_report_unavailable_provider(self) -> None:
        """Malformed, namespaced and wrong-root XML never advertise native support."""
        self.seed_native()
        cases = (("cfgenvironment.xml", b"<env>"),
                 ("cfgenvironment.xml", b'\xff'),
                 ("env/zombie_territories.xml", b'<other/>'),
                 ("db/events.xml", b'<events xmlns="unsafe"><event name="InfectedCity"/></events>'))
        for relative, content in cases:
            with self.subTest(relative=relative, content=content):
                self.seed_native()
                (self.mission / relative).write_bytes(content)
                self.assertFalse(self.providers()["native_infected"]["available"])
                self.assertEqual(self.providers()["native_infected"]["reason"], "provider_configuration_invalid")

    def test_final_native_file_reparse_point_is_refused_before_open(self) -> None:
        """The existing reparse checker also guards final file components."""
        self.seed_native()
        target = (self.mission / "env" / "zombie_territories.xml").resolve(strict=True)
        with mock.patch("dayz_serverman.repositories.mission_map_providers_paths.is_reparse",
                        side_effect=lambda path: path == target):
            self.assertEqual(self.providers()["native_infected"]["reason"], "provider_path_invalid")

    def test_observer_load_of_present_and_absent_providers_preserves_both_roots(self) -> None:
        """Read-only staging changes neither files nor folders while discovering either provider."""
        self.seed_native()
        self.seed_bandits()
        self.service.save("main", self.draft(), None)
        self.coordinator = MissionMapCoordinator(self.make_service(StagingPolicy.OBSERVER))
        before = (tree_state(self.root), tree_state(self.area.parent.parent))
        providers = self.providers()
        self.assertTrue(providers["native_infected"]["available"])
        self.assertTrue(providers["ai_bandits"]["detected"])
        self.assertEqual(providers["ai_bandits"]["reason"], NO_VERIFIED_HEIGHT_REASON)
        self.assertEqual((tree_state(self.root), tree_state(self.area.parent.parent)), before)
        # A second profile without this mod still reads safely and cannot inherit another profile's configuration
        self.assertFalse(self.load("second")["capabilities"]["encounter_providers"]["ai_bandits"]["detected"])
        self.assertEqual((tree_state(self.root), tree_state(self.area.parent.parent)), before)

    def test_save_result_stays_unchanged_by_additive_provider_metadata(self) -> None:
        """Provider metadata belongs only to load, and wolf/bear support is not introduced here."""
        self.seed_native()
        answer = self.save(self.draft())
        # Leaf 4.2 adds the planned evaluation field; provider metadata stays out of save
        self.assertEqual(set(answer), {"association", "revision", "draft_changes", "fingerprint", "interrupted_evidence",
                                       "evaluation"})
        self.assertNotIn("encounter_providers", answer["evaluation"])
        self.assertEqual(set(self.providers()), {"native_infected", "ai_bandits"})


if __name__ == "__main__":
    unittest.main()
