"""Cover legacy manager import preview and apply semantics."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics  # noqa: E402
from dayz_serverman.application.arguments import build_launch_command  # noqa: E402
from dayz_serverman.application.migration_preview import (  # noqa: E402
    MigrationConflictError,
)
from dayz_serverman.application.migrations import MigrationService  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.application.profiles import ProfileService  # noqa: E402
from dayz_serverman.application.settings import SettingsService  # noqa: E402
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.migrations import MigrationStorage  # noqa: E402
from dayz_serverman.repositories.profiles import ProfileRepository  # noqa: E402
from dayz_serverman.repositories.legacy_backup_index import LegacyBackupIndexRepository  # noqa: E402
from dayz_serverman.application.legacy_backups import LegacyBackupService  # noqa: E402
try:  # noqa: E402
    from tests.migration_test_support import FakeContext, MigrationTestCase
except ModuleNotFoundError:  # bundled discovery adds tests directly to sys.path
    from migration_test_support import FakeContext, MigrationTestCase


class MigrationTests(MigrationTestCase):
    """Verify legacy import preview and apply semantics across profiles and settings."""

    def test_preview_imports_verified_legacy_workshop_identities(self) -> None:
        """Verify preview resolves workshop identities from metadata and cache manifests."""
        self.write_profile(self.usable_profile(
            mods="@Metadata;@Cache Match;@Local", args="-doLogs -adminLog",
        ))
        # Create a metadata mod with a published id plus a cache-matched mod
        metadata = self.legacy / "@Metadata"
        metadata.mkdir()
        (metadata / "meta.cpp").write_text("publishedid = 111;", encoding="utf-8")
        cached = self.legacy / "@Cache Match"
        cached.mkdir()
        (cached / "mod.cpp").write_text('name = "Cache Match";', encoding="utf-8")
        local = self.legacy / "@Local"
        local.mkdir()
        cache = self.legacy / "steamcmd/steamapps/workshop/content/221100/222"
        cache.mkdir(parents=True)
        (cache / "mod.cpp").write_text('name = "Cache Match";', encoding="utf-8")

        # Preview the legacy root and inspect the imported mod identities
        preview, _item = self.select_preview()

        mods = preview["profiles"][0]["profile"]["mods"]
        self.assertEqual(
            [(item["directory"], item["source"]) for item in mods[:3]],
            [
                ("@Metadata", {"kind": "workshop", "workshop_id": "111"}),
                ("@Cache Match", {"kind": "workshop", "workshop_id": "222"}),
                ("@Local", {"kind": "external"}),
            ],
        )
        self.assertEqual(
            len([item for item in preview["inventory"] if item["role"] == "MOD_METADATA"]),
            3,
        )

    def test_workshop_metadata_change_invalidates_import_preview(self) -> None:
        """Verify a workshop metadata change invalidates an earlier preview."""
        self.write_profile(self.usable_profile(mods="@Metadata", args="-doLogs"))
        metadata = self.legacy / "@Metadata"
        metadata.mkdir()
        source = metadata / "meta.cpp"
        source.write_text("publishedid = 111;", encoding="utf-8")
        preview, item = self.select_preview()
        # Change the published id after the preview was taken
        source.write_text("publishedid = 222;", encoding="utf-8")

        with self.assertRaises(MigrationConflictError):
            self.service.apply(
                preview["preview_id"], preview["preview_fingerprint"], [item], FakeContext(),
            )
        self.assertEqual(self.profiles.list(), ())

    def test_preview_and_apply_preserve_profile_v2_launch_semantics(self) -> None:
        """Verify preview and apply preserve profile v2 launch semantics and sources."""
        source = self.write_profile(self.usable_profile())
        # Lay out an archive, an auth file, and a prior manager state file
        backups = self.legacy / "dayz_server_manager-backups"
        backups.mkdir()
        archive = backups / "old.zip"
        archive.write_bytes(b"not imported")
        auth = self.legacy / "dayz_server_manager-steam.json"
        auth.write_text('{"account":"private-account","use_anonymous":false}', encoding="utf-8")
        state = self.legacy / "dayz_server_manager-state.json"
        state.write_text('{"last_profile":"Máin Server"}', encoding="utf-8")
        # Snapshot every source file with its modification time
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (source, auth, state, archive)
        }

        preview, profile_item = self.select_preview()
        self.assertEqual(preview["backup_inventory"]["status"], "EXTERNAL_REFERENCE")
        self.assertTrue(preview["backup_inventory"]["selectable"])
        self.assertNotIn(str(self.legacy), json.dumps(preview, ensure_ascii=False))
        converted = preview["profiles"][0]["profile"]
        self.assertEqual(converted["runtime_profile"], "runtime profile")
        self.assertEqual(
            [(item["launch_scope"], item["source"]["kind"], item["directory"])
             for item in converted["mods"]],
            [("client", "external", "@Client One"),
             ("client", "external", "@Ünicode"),
             ("server", "external", "@Server Tools")],
        )
        # Apply the selected settings, profile, and backup index items
        result = self.service.apply(
            preview["preview_id"], preview["preview_fingerprint"],
            ["settings:dayz-installation", profile_item, "backups:external-index"], FakeContext(),
        )
        self.assertEqual(result["backup_policy"], "EXTERNAL_REFERENCE")
        self.assertEqual(self.profiles.read("main").values.extra_arguments, ("-doLogs", "-adminLog"))
        self.create_launch_paths()
        command = build_launch_command(self.profiles.read("main"), str(self.legacy))
        self.assertEqual(command.argv[1:], (
            "-config=serverDZ.cfg", "-port=2402", "-profiles=runtime profile",
            r"-mission=mpmissions\dayzOffline.test",
            "-mod=@Client One;@Ünicode", "-serverMod=@Server Tools",
            "-doLogs", "-adminLog",
        ))
        # Verify no source file changed during the import
        for path, evidence in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), evidence)
        # Scan the stored report for leaked legacy paths and credentials
        report = next((self.paths.migrations / "reports").glob("*.json")).read_text(encoding="utf-8")
        self.assertNotIn(str(self.legacy), report)
        self.assertNotIn("private-account", report)
        self.assertTrue((self.paths.migrations / "legacy-backup-index.json").is_file())
        self.assertFalse(any(self.paths.migrations.glob(".*.stage")))

    def test_contained_absolute_runtime_path_normalizes_and_outside_blocks(self) -> None:
        """Verify contained absolute runtime paths normalize and outside targets block."""
        absolute = str(self.legacy / "runtime profile")
        self.write_profile(self.usable_profile(
            profiles=absolute,
            args=f'-profiles="{absolute}" -doLogs',
        ))
        preview, _item = self.select_preview()
        self.assertEqual(preview["profiles"][0]["profile"]["runtime_profile"], "runtime profile")
        # Point the profile at a tree outside the manager root to force a conflict
        outside = str(self.root / "outside")
        self.write_profile(self.usable_profile(
            profiles=outside,
            args=f'-profiles="{outside}" -doLogs',
        ))
        preview, _item = self.select_preview()
        self.assertFalse(preview["profiles"][0]["selectable"])
        self.assertEqual(preview["profiles"][0]["conflicts"][0]["code"], "NEEDS_REVIEW")

    def test_source_and_destination_tamper_fail_before_publication(self) -> None:
        """Verify source and destination tampering fails before any publication."""
        profile = self.write_profile(self.usable_profile())
        preview, item = self.select_preview()
        # Tamper the source after the preview so apply must refuse
        profile.write_text(json.dumps(self.usable_profile(port="2502")), encoding="utf-8")
        with self.assertRaises(MigrationConflictError):
            self.service.apply(preview["preview_id"], preview["preview_fingerprint"], [item], FakeContext())
        self.assertEqual(self.profiles.list(), ())

        profile.write_text(json.dumps(self.usable_profile()), encoding="utf-8")
        self.service = self.make_service()
        preview, item = self.select_preview()
        # Move the destination settings so apply must refuse again
        self.settings_repository.save({
            "dayz_root": None, "dayz_executable": None, "steamcmd_root": None,
            "steamcmd_executable": None, "workshop_content_root": None,
            "custom_backup_root": None, "last_validated_paths": {},
        }, None)
        with self.assertRaises(MigrationConflictError):
            self.service.apply(preview["preview_id"], preview["preview_fingerprint"], [item], FakeContext())
        self.assertEqual(self.profiles.list(), ())

    def test_conflicts_unsafe_and_sensitive_arguments_never_leak(self) -> None:
        """Verify conflicting and sensitive arguments never leak through preview."""
        self.write_profile(self.usable_profile(args="-port=2402 -port=2502"))
        preview, _item = self.select_preview()
        self.assertEqual(preview["profiles"][0]["conflicts"][0]["code"], "DUPLICATE_KNOWN_ARGUMENT")
        # Introduce a sensitive argument and require redaction of its value
        self.write_profile(self.usable_profile(args="-password=TOPSECRET"))
        preview, _item = self.select_preview()
        rendered = json.dumps(preview)
        self.assertIn("SENSITIVE_ARGUMENT", rendered)
        self.assertNotIn("TOPSECRET", rendered)

    def test_existing_profile_blocks_without_overwrite_or_rename(self) -> None:
        """Verify an existing profile blocks import without overwrite or rename."""
        self.write_profile(self.usable_profile())
        first, item = self.select_preview()
        self.service.apply(first["preview_id"], first["preview_fingerprint"], [item], FakeContext())
        # Publish once, then re-preview against the existing destination
        before = (self.paths.profiles / "main.json").read_bytes()
        self.service = self.make_service()
        second, _item = self.select_preview()
        self.assertFalse(second["profiles"][0]["selectable"])
        self.assertEqual(second["profiles"][0]["conflicts"][0]["code"], "DESTINATION_CONFLICT")
        self.assertEqual((self.paths.profiles / "main.json").read_bytes(), before)

    def test_multi_profile_publication_uses_canonical_profile_id_order(self) -> None:
        """Verify multi-profile publication orders records by canonical identifier."""
        self.write_profile(self.usable_profile(name="Zulu"), "A-source.json")
        self.write_profile(self.usable_profile(name="Alpha"), "Z-source.json")
        selected = self.service.select_root(str(self.legacy))
        preview = self.service.preview(selected["selection_id"])
        profile_items = [item["item_id"] for item in preview["profiles"]]
        # Publish both profiles and verify the canonical ordering
        self.service.apply(
            preview["preview_id"], preview["preview_fingerprint"],
            profile_items, FakeContext(),
        )
        self.assertEqual(
            [record.values.profile_id for record in self.profiles.list()],
            ["a-source", "z-source"],
        )

    def test_existing_different_settings_are_never_selected_for_overwrite(self) -> None:
        """Verify existing different settings are never selected for overwrite."""
        self.write_profile(self.usable_profile())
        other = self.root / "Other DayZ"
        other.mkdir()
        executable = other / "DayZServer_x64.exe"
        executable.write_bytes(b"other")
        self.settings_repository.save({
            "dayz_root": str(other), "dayz_executable": str(executable),
            "steamcmd_root": None, "steamcmd_executable": None,
            "workshop_content_root": None, "custom_backup_root": None,
            "last_validated_paths": {},
        }, None)
        # Preview against the differently configured installation
        _selected = self.service.select_root(str(self.legacy))
        preview = self.service.preview(_selected["selection_id"])
        self.assertFalse(preview["settings"]["selectable"])
        self.assertEqual(
            {item["code"] for item in preview["settings"]["conflicts"]},
            {"DESTINATION_CONFLICT"},
        )
        self.assertTrue(Path(self.settings.load().dayz_root).samefile(other))

    def test_cancel_and_publication_fault_leave_no_partial_data(self) -> None:
        """Verify cancellation and publication faults leave no partial data."""
        self.write_profile(self.usable_profile())
        preview, item = self.select_preview()
        # Cancel at each phase and verify nothing partial remains
        for phase in ("DISCOVER", "COPY_SOURCE", "CONVERT", "VERIFY"):
            with self.subTest(cancel_at=phase), self.assertRaises(OperationCancelled):
                self.service.apply(
                    preview["preview_id"], preview["preview_fingerprint"],
                    ["settings:dayz-installation", item],
                    FakeContext(phase),
                )
            self.assertEqual(self.profiles.list(), ())
            self.assertFalse(any(self.paths.migrations.glob(".*.stage")))

        # Inject a publication fault at each phase and verify full rollback
        for failure_phase in (
            "COPY_SOURCE", "CONVERT", "VERIFY", "PUBLISH_SETTINGS", "PUBLISH_PROFILE", "REPORT",
        ):
            def fail_at(phase: str, expected: str = failure_phase) -> None:
                """Raise the synthetic fault when the failure phase is reached."""
                if phase == expected:
                    raise OSError("synthetic fault")

            self.service = self.make_service(fail_at)
            preview, item = self.select_preview()
            with self.subTest(fault_at=failure_phase), self.assertRaises(OSError):
                self.service.apply(
                    preview["preview_id"], preview["preview_fingerprint"],
                    ["settings:dayz-installation", item], FakeContext(),
                )
            self.assertEqual(self.profiles.list(), ())
            self.assertIsNone(self.settings.load().revision)
            self.assertFalse(any(self.paths.migrations.glob(".*.stage")))
            reports = self.paths.migrations / "reports"
            self.assertFalse(reports.exists() and any(reports.glob("*.json")))

    def test_external_index_repeat_source_tamper_and_destination_revision_conflict(self) -> None:
        """Verify external index repeats, source tampering, and stale revisions conflict."""
        backups = self.legacy / "dayz_server_manager-backups"
        backups.mkdir()
        archive = backups / "nested name.zip"
        archive.write_bytes(b"version one")
        selected = self.service.select_root(str(self.legacy))
        preview = self.service.preview(selected["selection_id"])
        self.service.apply(
            preview["preview_id"], preview["preview_fingerprint"],
            ["backups:external-index"], FakeContext(),
        )
        before = archive.read_bytes()
        self.service = self.make_service()
        selected = self.service.select_root(str(self.legacy))
        repeated = self.service.preview(selected["selection_id"])
        self.assertFalse(repeated["backup_inventory"]["selectable"])
        self.assertEqual(repeated["backup_inventory"]["action"], "UNCHANGED")
        self.assertEqual(archive.read_bytes(), before)

        # Re-preview after a new archive version and require selectability
        archive.write_bytes(b"version two")
        self.service = self.make_service()
        selected = self.service.select_root(str(self.legacy))
        changed = self.service.preview(selected["selection_id"])
        self.assertTrue(changed["backup_inventory"]["selectable"])
        # Tamper the archive after preview so apply must conflict
        archive.write_bytes(b"version three")
        with self.assertRaises(MigrationConflictError):
            self.service.apply(
                changed["preview_id"], changed["preview_fingerprint"],
                ["backups:external-index"], FakeContext(),
            )

        # Advance the stored index so the older preview must conflict
        archive.write_bytes(b"version two")
        self.service = self.make_service()
        selected = self.service.select_root(str(self.legacy))
        stale = self.service.preview(selected["selection_id"])
        repository = LegacyBackupIndexRepository(
            self.paths.migrations / "legacy-backup-index.json",
        )
        current = repository.load_optional()
        LegacyBackupService(repository).revalidate(current.revision)
        with self.assertRaises(MigrationConflictError):
            self.service.apply(
                stale["preview_id"], stale["preview_fingerprint"],
                ["backups:external-index"], FakeContext(),
            )


if __name__ == "__main__":
    unittest.main()
