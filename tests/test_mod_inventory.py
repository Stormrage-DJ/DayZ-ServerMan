"""Cover mod inventory ordering and bounded local workshop metadata."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable/src/python"))

from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity
from dayz_serverman.application.mod_inventory import ModInventoryService
from dayz_serverman.application.target_proofs import TargetProofLookup
from dayz_serverman.application.update_check import CheckSnapshot
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.domain.update_check import RemoteFact, RemoteItemResult
from dayz_serverman.domain.update_check_rules import CheckState


class _Profiles:
    """Provide a minimal profile reader for inventory tests."""

    def __init__(self, profile):
        """Store the profile record returned by read."""
        self.profile = profile

    def read(self, _profile_id):
        """Return the fixed profile record regardless of identifier."""
        return self.profile


class _Settings:
    """Provide a minimal settings reader pointing at the workshop cache."""

    def __init__(self, root: Path):
        """Store the workshop content root exposed through settings."""
        self.root = root

    def load(self):
        """Return a settings value carrying the workshop content root."""
        return SimpleNamespace(workshop_content_root=str(self.root))


class ModInventoryTests(unittest.TestCase):
    """Verify inventory rows are ordered and enriched from local metadata."""

    def test_inventory_is_ordered_and_uses_bounded_local_metadata(self) -> None:
        """Verify workshop and external rows keep order and report derived states."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "steamapps/workshop/content/221100"
            # Create one cached item with its own mod.cpp identity
            mod = root / "111"
            mod.mkdir(parents=True)
            (mod / "mod.cpp").write_text(
                'name = "Friendly Name";\nversion = "1.2.3";\n', encoding="utf-8")
            # Record a newer published manifest so the row reports an update
            (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
                '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { '
                '"111" { "manifest" "8" "size" "1" "timeupdated" "5" } } '
                '"WorkshopItemDetails" { "111" { "latest_manifest" "9" '
                '"latest_timeupdated" "6" } } }', encoding="utf-8")
            profile = ProfileRecord(1, ProfileInput.parse({
                "profile_id": "main", "display_name": "Main",
                "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
                "runtime_profile": None, "mission_root": None, "game_port": 2302,
                "mods": [
                    {"directory": "@Friendly", "launch_scope": "client",
                     "source": {"kind": "workshop", "workshop_id": "111"}},
                    {"directory": "@Local", "launch_scope": "server",
                     "source": {"kind": "external"}},
                ], "extra_arguments": [],
            }))
            # Inventory the profile's workshop and external mods
            rows = ModInventoryService(_Profiles(profile), _Settings(root)).list("main")
            self.assertEqual([row["order"] for row in rows], [1, 2])
            self.assertEqual(rows[0]["name"], "Friendly Name")
            self.assertEqual(rows[0]["version"], "1.2.3")
            self.assertEqual(rows[0]["state"], "UPDATE_AVAILABLE")
            self.assertEqual(rows[1]["state"], "LOCAL")
            self.assertIsNone(rows[1]["version"])

    def test_manifest_without_details_block_reports_unavailable(self) -> None:
        """Verify a manifest without the latest-details block degrades to UNAVAILABLE."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "steamapps/workshop/content/221100"
            (root / "111").mkdir(parents=True)
            # Record the installed item only; Steam wrote no WorkshopItemDetails block
            (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
                '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { '
                '"111" { "manifest" "8" "size" "1" "timeupdated" "5" } } }', encoding="utf-8")
            profile = ProfileRecord(1, ProfileInput.parse({
                "profile_id": "main", "display_name": "Main",
                "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
                "runtime_profile": None, "mission_root": None, "game_port": 2302,
                "mods": [{"directory": "@Friendly", "launch_scope": "client",
                          "source": {"kind": "workshop", "workshop_id": "111"}}],
                "extra_arguments": [],
            }))
            rows = ModInventoryService(_Profiles(profile), _Settings(root)).list("main")
            self.assertEqual([row["state"] for row in rows], ["UNAVAILABLE"])

    def test_wired_sources_merge_remote_facts_and_target_proofs(self) -> None:
        """Verify rows gain the remote date, the check value and the pending reason."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "steamapps/workshop/content/221100"
            (root / "111").mkdir(parents=True)
            dayz = base / "DayZ Server"
            dayz.mkdir()
            # Record one installed item without any latest-manifest detail
            (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
                '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { '
                '"111" { "manifest" "8" "size" "1" "timeupdated" "5" } } '
                '"WorkshopItemDetails" { "111" { "manifest" "8" } } }', encoding="utf-8")
            profile = ProfileRecord(1, ProfileInput.parse({
                "profile_id": "main", "display_name": "Main",
                "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
                "runtime_profile": None, "mission_root": None, "game_port": 2302,
                "mods": [{"directory": "@Friendly", "launch_scope": "client",
                          "source": {"kind": "workshop", "workshop_id": "111"}}],
                "extra_arguments": [],
            }))
            settings = SimpleNamespace(
                load=lambda: SimpleNamespace(workshop_content_root=str(root), dayz_root=str(dayz)))
            check = SimpleNamespace(remote_time=5)
            check.snapshot = lambda: CheckSnapshot(
                CheckState.OK, None, None, None, False, 0, {"111": RemoteFact(
                    RemoteItemResult.OK, check.remote_time, 1, "2026-10-03T12:00:00.000+00:00")})
            records = SimpleNamespace(found=set())
            records.target_records = lambda: records.found
            service = ModInventoryService(
                _Profiles(profile), settings, check_source=check,
                target_proofs=TargetProofLookup([records]))

            def row() -> tuple:
                """Return the state and the additive fields of the only row."""
                value = service.list("main")[0]
                return (value["state"], value["remote_time_updated"],
                        value["remote_check"], value["pending_reason"])

            # The copy is missing, then unproven, then proven by a target record
            self.assertEqual(row(), ("PENDING_APPLY", 5, "OK", "TARGET_MISSING"))
            (dayz / "@Friendly").mkdir()
            self.assertEqual(row(), ("PENDING_APPLY", 5, "OK", "TARGET_UNPROVEN"))
            records.found = {(dayz_root_identity(dayz), "@friendly", "111", "8")}
            self.assertEqual(row(), ("CURRENT", 5, "OK", None))
            # A newer remote time outranks the proven copy
            check.remote_time = 6
            self.assertEqual(row(), ("UPDATE_AVAILABLE", 6, "OK", None))
            self.assertEqual(service.report("main")[1].check_state, CheckState.OK)
            # QF-054: the content folder is gone while the manifest record and the proven copy remain
            check.remote_time = 5
            (root / "111").rmdir()
            self.assertEqual(row(), ("NOT_DOWNLOADED", None, "NOT_APPLICABLE", None))
            # The name falls back to the directory, as before; the folder coming back restores the state
            self.assertEqual(service.list("main")[0]["name"], "@Friendly")
            (root / "111").mkdir()
            self.assertEqual(row(), ("CURRENT", 5, "OK", None))

    def test_a_content_path_that_is_a_file_or_unreadable_is_not_downloaded(self) -> None:
        """QF-062: only a readable content folder counts; a file or an unreadable path never reads "Current"."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "steamapps/workshop/content/221100"
            content = root / "111"
            content.mkdir(parents=True)
            (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
                '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { '
                '"111" { "manifest" "8" "size" "1" "timeupdated" "5" } } '
                '"WorkshopItemDetails" { "111" { "manifest" "8" } } }', encoding="utf-8")
            profile = ProfileRecord(1, ProfileInput.parse({
                "profile_id": "main", "display_name": "Main",
                "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
                "runtime_profile": None, "mission_root": None, "game_port": 2302,
                "mods": [{"directory": "@Friendly", "launch_scope": "client",
                          "source": {"kind": "workshop", "workshop_id": "111"}}],
                "extra_arguments": [],
            }))
            # A fresh, equal remote answer makes the item current while its folder is there
            check = SimpleNamespace(snapshot=lambda: CheckSnapshot(
                CheckState.OK, None, None, None, False, 0, {"111": RemoteFact(
                    RemoteItemResult.OK, 5, 1, "2026-10-03T12:00:00.000+00:00")}))
            service = ModInventoryService(_Profiles(profile), _Settings(root), check_source=check)
            state = lambda: service.list("main")[0]["state"]  # noqa: E731
            self.assertEqual(state(), "CURRENT")
            # A file in place of the folder is no content
            content.rmdir()
            content.write_text("not a folder", encoding="utf-8")
            self.assertEqual(state(), "NOT_DOWNLOADED")
            content.unlink()
            content.mkdir()
            # A folder that cannot be read degrades to "Not downloaded" instead of failing the list
            original = Path.stat

            def refuse(path, *args, **kwargs):
                """Refuse the content folder only, like a sharing violation."""
                if Path(path) == content:
                    raise PermissionError(13, "Access is denied", str(path))
                return original(path, *args, **kwargs)

            with patch.object(Path, "stat", refuse):
                self.assertEqual(state(), "NOT_DOWNLOADED")
            self.assertEqual(state(), "CURRENT")


if __name__ == "__main__":
    unittest.main()
