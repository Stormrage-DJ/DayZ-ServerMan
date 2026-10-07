"""Observer staging-file rule (4.2.1, 4.2.2, R-4): staging files are ignored and no read migrates a profile."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import PROFILE_ID, dispatch, run_lane, seed_dayz, tree_hashes  # noqa: E402
from profile_fixtures import profile_payload  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import RecordState, SettingsInput  # noqa: E402
from dayz_serverman.repositories.json_store import StagingPolicy, VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.profiles import ProfileRepository  # noqa: E402
from dayz_serverman.session_observer import BridgeCallFailed, open_observer_session  # noqa: E402

# A schema 1 profile record, as an older build stored it
SCHEMA_1 = {
    "schema_version": 1, "revision": 4, "profile_id": "old-main", "display_name": "Old Main",
    "server_executable": r"Bin\DayZ Server_x64.exe", "server_config": r"Config Files\serverDZ.cfg",
    "mission_root": None, "game_port": 2402,
    "mods": [{"workshop_id": "1559212036", "directory": "@Community Framework"}], "extra_arguments": [],
}
# Observer reads that touch every record with a staging rule
READS = (("get_application_snapshot", {}), ("get_ui_preferences", {}), ("get_lifecycle_schedule",
         {"profile_id": PROFILE_ID}), ("list_profiles", {}), ("read_profile", {"profile_id": PROFILE_ID}))


def staging_name(record: Path) -> Path:
    """Return a staging file name beside a record, as VersionedJsonRepository writes one."""
    return record.with_name(f".{record.name}.{uuid.uuid4().hex}.tmp")


class ObserverStagingTests(unittest.TestCase):
    """Records with staging files beside them, a schema 1 profile and a staging file without a record."""

    def setUp(self) -> None:
        """Build an owner root with settings, a profile, a selection and a schedule record."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_staging_")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.manager, self.dayz = base / "Manager", base / "DayZ Root"
        seed_dayz(self.dayz)
        composition = build_composition(self.manager)
        try:
            composition.settings.save(SettingsInput(
                dayz_root=str(self.dayz), dayz_executable=str(self.dayz / "Bin" / "DayZ Server_x64.exe")), None)
            self.assertEqual(run_lane(composition, "save_profile", {"profile": profile_payload(
                server_config=rf"serverman\{PROFILE_ID}\serverDZ.cfg",
                runtime_profile=rf"serverman\{PROFILE_ID}\profile"), "expected_revision": None}).state.value,
                "SUCCEEDED")
            self.assertTrue(dispatch(composition, "save_selected_profile", {"profile_id": PROFILE_ID})["success"])
            VersionedJsonRepository(composition.paths.schedules).save({"schedules": {}}, None)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)
        self.data = self.manager / "data"

    def observe(self, reads=READS) -> dict[str, object]:
        """Run the reads in an observer session and return each value or bridge error code."""
        session = open_observer_session(self.manager)
        answers: dict[str, object] = {}
        try:
            for method, parameters in reads:
                try:
                    answers[f"{method} {parameters.get('profile_id', '')}".strip()] = session.call(method, parameters)
                except BridgeCallFailed as error:
                    answers[method] = error.error["code"]
        finally:
            session.close()
        return answers

    def test_a_schema_1_profile_is_shown_without_a_migration_on_read(self) -> None:
        """profile list and profile show see it; its bytes, the tree and no .migration.tmp change (4.2.2)."""
        record = self.data / "profiles" / "old-main.json"
        record.write_text(json.dumps(SCHEMA_1, indent=2) + "\n", encoding="utf-8")
        before = tree_hashes(self.manager)
        answers = self.observe(READS + (("read_profile", {"profile_id": "old-main"}),))
        self.assertEqual(tree_hashes(self.manager), before)
        self.assertEqual(sorted(item["profile_id"] for item in answers["list_profiles"]), ["livonia-main", "old-main"])
        shown = answers["read_profile old-main"]
        self.assertEqual((shown["display_name"], shown["game_port"], shown["revision"]), ("Old Main", 2402, 4))
        self.assertFalse(record.with_name(".old-main.json.migration.tmp").exists())
        # An owner still migrates it at its next use, as today
        ProfileRepository(self.data / "profiles").load("old-main")
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["schema_version"], 2)

    def test_staging_files_beside_records_are_ignored(self) -> None:
        """Every read returns the authoritative values, reports no recovery, and leaves the staging files alone."""
        records = (self.manager / "config" / "manager.json", self.data / "ui-preferences.json",
                   self.data / "schedules.json", self.data / "profiles" / f"{PROFILE_ID}.json")
        for record in records:
            staging_name(record).write_text('{"partial": ', encoding="utf-8")
        migration = self.data / "profiles" / f".{PROFILE_ID}.json.migration.tmp"
        migration.write_text('{"partial": ', encoding="utf-8")
        before = tree_hashes(self.manager)
        answers = self.observe()
        self.assertEqual(tree_hashes(self.manager), before)
        snapshot = answers["get_application_snapshot"]
        self.assertEqual(Path(snapshot["settings"]["dayz_root"]).resolve(), self.dayz.resolve())
        self.assertIsNone(snapshot["mutation_block"])
        self.assertEqual(answers["get_ui_preferences"]["selected_profile_id"], PROFILE_ID)
        self.assertIsInstance(answers[f"get_lifecycle_schedule {PROFILE_ID}"], dict)
        self.assertEqual([item["profile_id"] for item in answers["list_profiles"]], [PROFILE_ID])
        self.assertEqual(answers[f"read_profile {PROFILE_ID}"]["profile_id"], PROFILE_ID)
        text = json.dumps(answers)
        for marker in ("INTERRUPTED_WRITE", "RECOVERY_REQUIRED", "STORAGE_FAILURE"):
            self.assertNotIn(marker, text)
        # Owner sessions keep today's rule: the same file is an interrupted write
        self.assertEqual(VersionedJsonRepository(records[0]).inspect().state, RecordState.INTERRUPTED_WRITE)
        self.assertEqual(VersionedJsonRepository(records[0], staging=StagingPolicy.OBSERVER).inspect().state,
                         RecordState.VALID)

    def test_a_staging_file_without_a_record_reads_as_absent(self) -> None:
        """No record: absent for the observer, an interrupted write for the owner."""
        record = self.data / "ui-preferences.json"
        record.unlink()
        staging_name(record).write_text('{"partial": ', encoding="utf-8")
        before = tree_hashes(self.manager)
        answers = self.observe((("get_ui_preferences", {}),))
        self.assertEqual(tree_hashes(self.manager), before)
        self.assertIsNone(answers["get_ui_preferences"]["selected_profile_id"])
        self.assertEqual(VersionedJsonRepository(record, staging=StagingPolicy.OBSERVER).inspect().state,
                         RecordState.MISSING)
        self.assertEqual(VersionedJsonRepository(record).inspect().state, RecordState.INTERRUPTED_WRITE)


if __name__ == "__main__":
    unittest.main()
