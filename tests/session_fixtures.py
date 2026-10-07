"""A populated manager root and DayZ root for the session tests, and the read calls of an observer."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from folder_lock_fixtures import tree_hashes  # noqa: E402
from profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
from test_mission_configuration_workflow import economy_xml  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402

# The profile of the fixture and the folders that an owner step renames or removes (3.4)
PROFILE_ID = "livonia-main"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


def protected_folders(dayz: Path) -> tuple[Path, ...]:
    """Return the @Mod folders, keys, the generated profile folder and the mission of the fixture."""
    return (dayz / "@Community Framework", dayz / "@Server Tools", dayz / "keys",
            dayz / "serverman" / PROFILE_ID, dayz / "mpmissions" / "dayzOffline.enoch")


def seed_dayz(dayz: Path) -> None:
    """Create the DayZ root: executable, mods, keys, a generated profile folder and a populated mission."""
    create_profile_paths(dayz)
    generated = dayz / "serverman" / PROFILE_ID
    (generated / "profile").mkdir(parents=True)
    (generated / "serverDZ.cfg").write_text(
        'hostname = "Fixture";\ninstanceId = 17;\nclass Missions { class DayZ { template="dayzOffline.enoch"; }; };\n',
        encoding="utf-8")
    (dayz / "keys").mkdir()
    (dayz / "keys" / "cf.bikey").write_bytes(b"key")
    (dayz / "@Community Framework" / "mod.cpp").write_text('name = "CF";\n', encoding="utf-8")
    mission = dayz / "mpmissions" / "dayzOffline.enoch"
    (mission / "db").mkdir()
    (mission / "db" / "globals.xml").write_text(economy_xml(), encoding="utf-8")
    (mission / "storage_17").mkdir()
    (mission / "storage_17" / "players.db").write_bytes(b"world")


def dispatch(composition, method: str, parameters: dict) -> dict:
    """Dispatch one bridge call and return the envelope."""
    return composition.bridge.dispatch(
        {"contract_version": 1, "request_id": "fixture", "method": method, "parameters": parameters})


def run_lane(composition, method: str, parameters: dict):
    """Dispatch one lane call and return its terminal record."""
    answer = dispatch(composition, method, parameters)
    assert answer["success"], answer
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        record = composition.operations.get(answer["value"]["operation_id"])
        if record.state.value in TERMINAL:
            return record
        time.sleep(0.02)
    raise AssertionError(f"{method} did not finish")


def populate(base: Path) -> tuple[Path, Path]:
    """Build an owner root with settings, a profile, a selection, a backup and log lines; return both roots."""
    manager, dayz = base / "Manager", base / "DayZ Root"
    seed_dayz(dayz)
    composition = build_composition(manager)
    try:
        composition.settings.save(SettingsInput(
            dayz_root=str(dayz), dayz_executable=str(dayz / "Bin" / "DayZ Server_x64.exe")), None)
        saved = run_lane(composition, "save_profile", {"profile": profile_payload(
            server_config=rf"serverman\{PROFILE_ID}\serverDZ.cfg",
            runtime_profile=rf"serverman\{PROFILE_ID}\profile"), "expected_revision": None})
        assert saved.state.value == "SUCCEEDED", saved
        assert dispatch(composition, "save_selected_profile", {"profile_id": PROFILE_ID})["success"]
        backup = run_lane(composition, "create_backup", {
            "profile_id": PROFILE_ID, "expected_profile_revision": 0,
            "expected_settings_revision": composition.settings.load().revision})
        assert backup.state.value == "SUCCEEDED", backup
    finally:
        composition.shutdown.request_shutdown()
        composition.shutdown.wait_for_close(5)
    return manager, dayz


def read_calls(dayz: Path, archive: Path | None, legacy: Path) -> list[tuple[str, dict]]:
    """Return one or more calls of every observer read method with the fixture's values."""
    profile = {"profile_id": PROFILE_ID}
    return [
        ("get_application_snapshot", {}), ("get_server_status", {}), ("get_online_players", {}),
        ("get_lifecycle_schedule", profile), ("get_update_status", profile), ("list_mod_inventory", profile),
        ("list_backups", profile), ("list_backup_catalog", {}), ("list_profiles", {}), ("read_profile", profile),
        ("preview_profile_command", profile), ("list_profile_missions", {}), ("get_ui_preferences", {}),
        ("load_configuration", {**profile, "target": "server"}),
        ("load_configuration", {**profile, "target": "gameplay"}),
        ("load_mission_configuration", {**profile, "target": "economy"}), ("load_medical_features", profile),
        ("read_log", {"source": "manager", "maximum_lines": 50}),
        ("read_log", {"source": "manager_diagnostics", "maximum_lines": 50}),
        ("read_log", {"source": "server", "maximum_lines": 50}),
        ("validate_settings_path_selection", {"role": "dayz_root", "path": str(dayz)}),
        ("select_legacy_root", {"root": str(legacy)}), ("preview_legacy_import", {"selection_id": "unknown"}),
        ("inspect_backup_archive", {"path": str(archive or dayz / "missing.zip")}),
    ]


__all__ = ["PROFILE_ID", "dispatch", "populate", "protected_folders", "read_calls", "run_lane", "seed_dayz",
           "tree_hashes"]
