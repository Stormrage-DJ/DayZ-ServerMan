"""Workflow tests for the configuration service and its bridge operations."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.configuration import (  # noqa: E402
    ConfigurationPathError,
    ConfigurationService,
    GameplayPrerequisiteError,
)
from dayz_serverman.application.configuration_coordinator import (  # noqa: E402
    ConfigurationCoordinator,
)
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled, OperationState  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.domain.models import RevisionConflict  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories.atomic_file import ContentChangedError  # noqa: E402
from dayz_serverman.repositories.configuration_files import ConfigurationFileError  # noqa: E402


class FakeProfiles:
    """Profile port stub returning one fixed profile record."""
    def __init__(self, profile: ProfileRecord) -> None:
        """Store the profile record returned by every read."""
        self.profile = profile

    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the stored profile regardless of the requested identifier."""
        return self.profile


class FakeSettings:
    """Settings port stub exposing a fixed root and revision."""
    def __init__(self, root: Path, revision: int = 4) -> None:
        """Capture the DayZ root and settings revision used by the workflow."""
        self.value = SimpleNamespace(dayz_root=str(root), revision=revision)

    def load(self) -> object:
        """Return the captured settings namespace."""
        return self.value


def profile(server_config: str = "serverDZ.cfg") -> ProfileRecord:
    """Build the reference profile record used by the workflow scenarios."""
    return ProfileRecord(
        3,
        ProfileInput(
            "main",
            "Main",
            "DayZServer_x64.exe",
            server_config,
            "mpmissions\\dayzOffline.chernarusplus",
            2302,
            (),
            (),
        ),
    )


class ConfigurationWorkflowTests(unittest.TestCase):
    """Load, preview, and apply contracts of the configuration workflow."""
    def setUp(self) -> None:
        """Create the temporary layout with server and gameplay configuration files."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_configuration_flow_")
        self.root = Path(self.temporary.name)
        self.server = self.root / "serverDZ.cfg"
        self.mission = self.root / "mpmissions" / "dayzOffline.chernarusplus"
        self.mission.mkdir(parents=True)
        self.server.write_text(
            'hostname = "Old";\nmaxPlayers = 60;\nenableCfgGameplayFile = 1;\n',
            encoding="utf-8",
        )
        (self.mission / "cfgGameplay.json").write_text(
            '{"GeneralData":{"disableBaseDamage":false},"unknown":7}\n',
            encoding="utf-8",
        )
        self.service = ConfigurationService(
            FakeProfiles(profile()),  # type: ignore[arg-type]
            FakeSettings(self.root),  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        """Remove the temporary configuration layout."""
        self.temporary.cleanup()

    def test_load_and_preview_are_profile_scoped_and_non_mutating(self) -> None:
        """Loading and previewing must stay profile scoped and leave files untouched."""
        # Load the server target and preview one hostname change
        loaded = self.service.load("main", "server")
        self.assertEqual(loaded["profile_id"], "main")
        preview = self.service.preview(
            "main",
            "server",
            loaded["profile_revision"],
            loaded["settings_revision"],
            loaded["digest"],
            None,
            {"hostname": "Preview"},
        )
        self.assertEqual(preview["changed_fields"], ["hostname"])
        self.assertNotEqual(preview["proposed_digest"], loaded["digest"])
        self.assertIn('hostname = "Old";', self.server.read_text(encoding="utf-8"))
        # The gameplay target resolves inside the mission folder
        gameplay = self.service.load("main", "gameplay")
        self.assertEqual(gameplay["relative_path"], "mpmissions\\dayzOffline.chernarusplus\\cfgGameplay.json")
        self.assertEqual(len(gameplay["server_config_digest"]), 64)

    def test_gameplay_infers_mission_folder_from_server_config(self) -> None:
        """A missing mission root is inferred from the mission template block."""
        # Rewrite the server config with a mission template block
        self.server.write_text(
            'enableCfgGameplayFile = 1;\nclass Missions { class DayZ { template="dayzOffline.chernarusplus"; }; };\n',
            encoding="utf-8",
        )
        without_explicit_mission = profile()
        without_explicit_mission = ProfileRecord(3, ProfileInput(
            **{**without_explicit_mission.values.__dict__, "mission_root": None}
        ))
        service = ConfigurationService(
            FakeProfiles(without_explicit_mission), FakeSettings(self.root),  # type: ignore[arg-type]
        )
        # The inferred mission folder must be used for the gameplay target
        loaded = service.load("main", "gameplay")
        self.assertEqual(
            loaded["relative_path"],
            "mpmissions\\dayzOffline.chernarusplus\\cfgGameplay.json",
        )

    def test_apply_checks_profile_settings_and_content_before_atomic_write(self) -> None:
        """Stale revisions and changed content must block the atomic write."""
        loaded = self.service.load("main", "server")
        with self.assertRaises(RevisionConflict):
            self.service.preview("main", "server", 2, 4, loaded["digest"], None, {"maxPlayers": 80})
        with self.assertRaises(RevisionConflict):
            self.service.preview("main", "server", 3, 2, loaded["digest"], None, {"maxPlayers": 80})
        self.server.write_text('hostname = "Manual";\n', encoding="utf-8")
        # A manual edit after loading must invalidate the captured digest
        with self.assertRaises(ContentChangedError):
            self.service.preview("main", "server", 3, 4, loaded["digest"], None, {"maxPlayers": 80})

    def test_path_escape_and_missing_mission_context_fail_closed(self) -> None:
        """Escaping config paths and missing mission context must fail closed."""
        # A server config outside the DayZ root must be rejected
        escaped = ConfigurationService(
            FakeProfiles(profile("..\\outside.cfg")),  # type: ignore[arg-type]
            FakeSettings(self.root),  # type: ignore[arg-type]
        )
        with self.assertRaises(ConfigurationPathError):
            escaped.load("main", "server")
        # A gameplay target without a mission root must be rejected
        no_mission = profile()
        no_mission = ProfileRecord(3, ProfileInput(**{**no_mission.values.__dict__, "mission_root": None}))
        service = ConfigurationService(
            FakeProfiles(no_mission),  # type: ignore[arg-type]
            FakeSettings(self.root),  # type: ignore[arg-type]
        )
        with self.assertRaises(ConfigurationPathError):
            service.load("main", "gameplay")

    def test_gameplay_requires_explicit_enabled_server_flag(self) -> None:
        """Gameplay access requires the server flag to be present and enabled."""
        gameplay_path = self.mission / "cfgGameplay.json"
        original = gameplay_path.read_bytes()
        # Disabled and absent flags must both block gameplay access
        for server_text in (
            'enableCfgGameplayFile = 0;\n',
            'hostname = "No flag";\n',
        ):
            self.server.write_text(server_text, encoding="utf-8")
            with self.assertRaisesRegex(GameplayPrerequisiteError, "Server settings"):
                self.service.load("main", "gameplay")
            self.assertEqual(gameplay_path.read_bytes(), original)
        # A later disable must block the apply after a successful load
        self.server.write_text("enableCfgGameplayFile = 1;\n", encoding="utf-8")
        loaded = self.service.load("main", "gameplay")
        self.server.write_text("enableCfgGameplayFile = 0;\n", encoding="utf-8")
        with self.assertRaises(GameplayPrerequisiteError):
            self.service.apply(
                "main", "gameplay", 3, 4,
                loaded["digest"], loaded["server_config_digest"],
                {"GeneralData.disableBaseDamage": True},
                lambda _phase, _progress: None,
            )
        self.assertEqual(gameplay_path.read_bytes(), original)

    def test_gameplay_blocks_malformed_or_duplicate_server_flag(self) -> None:
        """Malformed or duplicate enableCfgGameplayFile lines must block gameplay."""
        # Both malformed and duplicate flag lines must fail closed
        for server_text in (
            "enableCfgGameplayFile = broken;\n",
            "enableCfgGameplayFile = 1;\nenableCfgGameplayFile = 1;\n",
        ):
            self.server.write_text(server_text, encoding="utf-8")
            with self.assertRaises(ConfigurationFileError):
                self.service.load("main", "gameplay")

    def test_gameplay_apply_rechecks_server_digest_without_partial_publication(self) -> None:
        """A server edit after loading must block preview and apply without partial writes."""
        loaded = self.service.load("main", "gameplay")
        gameplay_path = self.mission / "cfgGameplay.json"
        original = gameplay_path.read_bytes()
        # Edit the server config so the captured digest goes stale
        self.server.write_text(
            self.server.read_text(encoding="utf-8") + "// manual edit\n",
            encoding="utf-8",
        )
        with self.assertRaises(ContentChangedError):
            self.service.preview(
                "main",
                "gameplay",
                3,
                4,
                loaded["digest"],
                loaded["server_config_digest"],
                {"GeneralData.disableBaseDamage": True},
            )
        with self.assertRaises(ContentChangedError):
            self.service.apply(
                "main",
                "gameplay",
                3,
                4,
                loaded["digest"],
                loaded["server_config_digest"],
                {"GeneralData.disableBaseDamage": True},
                lambda _phase, _progress: None,
            )
        self.assertEqual(gameplay_path.read_bytes(), original)

    def test_gameplay_map_ui_vehicle_fields_load_preview_apply_and_preserve_unknown(self) -> None:
        """Map, UI, and vehicle fields survive a full load, preview, and apply cycle."""
        # Seed gameplay data with unknown vendor entries
        gameplay_path = self.mission / "cfgGameplay.json"
        gameplay_path.write_text(
            '{"MapData":{"ignoreMapOwnership":false,"ignoreNavItemsOwnership":false,'
            '"displayPlayerPosition":false,"displayNavInfo":false,"future":7},'
            '"UIData":{"use3DMap":false},'
            '"VehicleData":{"boatDecayMultiplier":1.0,"vendor":"keep"}}\n',
            encoding="utf-8",
        )
        loaded = self.service.load("main", "gameplay")
        fields = {field["key"]: field for field in loaded["fields"]}
        accepted = {
            "MapData.ignoreMapOwnership": True,
            "MapData.ignoreNavItemsOwnership": True,
            "MapData.displayPlayerPosition": True,
            "MapData.displayNavInfo": True,
            "UIData.use3DMap": True,
            "VehicleData.boatDecayMultiplier": 3.25,
        }
        self.assertTrue(set(accepted) <= set(fields))
        preview = self.service.preview(
            "main", "gameplay", 3, 4, loaded["digest"],
            loaded["server_config_digest"], accepted,
        )
        self.assertEqual(set(preview["changed_fields"]), set(accepted))
        # Apply the same accepted fields through the service
        result = self.service.apply(
            "main", "gameplay", 3, 4, loaded["digest"],
            loaded["server_config_digest"], accepted,
            lambda _phase, _progress: None,
        )
        self.assertEqual(set(result["changed_fields"]), set(accepted))
        # Unknown keys must survive and the reloaded values must match
        text = gameplay_path.read_text(encoding="utf-8")
        self.assertIn('"future": 7', text)
        self.assertIn('"vendor": "keep"', text)
        reloaded = self.service.load("main", "gameplay")
        values = {field["key"]: field["value"] for field in reloaded["fields"]}
        self.assertEqual({key: values[key] for key in accepted}, accepted)

    def test_gameplay_bridge_returns_actionable_stable_disabled_error(self) -> None:
        """The bridge must report a stable disabled-feature error without queueing work."""
        self.server.write_text("enableCfgGameplayFile = 0;\n", encoding="utf-8")
        operations = OperationManager(OperationStore(self.root / "operations-disabled"))
        bridge = BridgeFacade(ConfigurationCoordinator(self.service, operations).handlers())
        # The dispatch must fail without creating an operation
        result = bridge.dispatch(_request("load_configuration", {"profile_id": "main", "target": "gameplay"}))
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "GAMEPLAY_NOT_ENABLED")
        self.assertIn("Server settings", result["error"]["message"])
        self.assertEqual(operations.list_recent(), ())
        operations.shutdown(2)

    def test_cancellation_at_validated_safe_point_leaves_target_unchanged(self) -> None:
        """Cancelling at the validated checkpoint must leave the target file unchanged."""
        loaded = self.service.load("main", "server")
        original = self.server.read_bytes()

        def cancel_at_validated(phase: str, _progress: int) -> None:
            """Cancel the operation as soon as the validated phase is reported."""
            if phase == "validated":
                raise OperationCancelled("synthetic cancellation")

        with self.assertRaises(OperationCancelled):
            self.service.apply(
                "main",
                "server",
                3,
                4,
                loaded["digest"],
                None,
                {"hostname": "Cancelled"},
                cancel_at_validated,
            )
        self.assertEqual(self.server.read_bytes(), original)

    def test_bridge_apply_uses_operation_lane_progress_and_stable_contract(self) -> None:
        """Bridge applies run on the operation lane and report progress and results."""
        # Queue the apply request through the bridge
        operations = OperationManager(OperationStore(self.root / "operations"))
        bridge = BridgeFacade(ConfigurationCoordinator(self.service, operations).handlers())
        loaded = self.service.load("main", "server")
        parameters = {
            "profile_id": "main",
            "target": "server",
            "expected_profile_revision": 3,
            "expected_settings_revision": 4,
            "expected_digest": loaded["digest"],
            "expected_server_digest": None,
            "updates": {"hostname": "Applied"},
        }
        result = bridge.dispatch(_request("apply_configuration", parameters))
        self.assertTrue(result["success"])
        operation_id = result["value"]["operation_id"]
        # Wait for the operation to reach a terminal state
        deadline = time.monotonic() + 2
        while operations.get(operation_id).state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        # The record must report success, progress, and the applied field
        record = operations.get(operation_id)
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(record.progress_phase, "complete")
        self.assertEqual(record.progress_percent, 100)
        self.assertEqual(record.result["changed_fields"], ["hostname"])
        self.assertIn('hostname = "Applied";', self.server.read_text(encoding="utf-8"))
        operations.shutdown(2)

    def test_bridge_rejects_unknown_fields_before_queueing(self) -> None:
        """Unknown fields and missing digests must be rejected before any queueing."""
        operations = OperationManager(OperationStore(self.root / "operations-invalid"))
        bridge = BridgeFacade(ConfigurationCoordinator(self.service, operations).handlers())
        result = bridge.dispatch(
            _request(
                "load_configuration",
                {"profile_id": "main", "target": "server", "path": "arbitrary"},
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        # A missing server digest must also be rejected before queueing
        loaded = self.service.load("main", "server")
        missing_proof = bridge.dispatch(
            _request(
                "apply_configuration",
                {
                    "profile_id": "main",
                    "target": "server",
                    "expected_profile_revision": 3,
                    "expected_settings_revision": 4,
                    "expected_digest": loaded["digest"],
                    "updates": {"hostname": "Blocked"},
                },
            )
        )
        self.assertFalse(missing_proof["success"])
        self.assertEqual(missing_proof["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(operations.list_recent(), ())
        operations.shutdown(2)


def _request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Build a minimal bridge request envelope for the given method."""
    return {
        "contract_version": 1,
        "request_id": f"test-{method}",
        "method": method,
        "parameters": parameters,
    }


if __name__ == "__main__":
    unittest.main()
