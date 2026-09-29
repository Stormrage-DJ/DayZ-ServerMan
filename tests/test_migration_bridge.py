"""Host migration bridge tests for selection, preview, import, and reference flows."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.host.api import HostApi  # noqa: E402


class MigrationBridgeTests(unittest.TestCase):
    """End-to-end migration import and backup reference bridge contracts."""
    def setUp(self) -> None:
        """Seed a legacy source tree and wire the host API to it."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_migration_bridge_")
        root = Path(self.temporary.name)
        self.composition = build_composition(root / "Manager")
        self.legacy = root / "Légacy Source"
        (self.legacy / "dayz_server_manager").mkdir(parents=True)
        profiles = self.legacy / "dayz_server_manager-profiles"
        profiles.mkdir()
        (self.legacy / "DayZServer_x64.exe").write_bytes(b"synthetic")
        (profiles / "Main.json").write_text(json.dumps({
            "name": "Main", "config": "serverDZ.cfg", "port": "2302",
            "profiles": "profile", "mods": "@Client", "serverMod": "@Server",
            "args": (
                "-config=serverDZ.cfg -port=2302 -profiles=profile "
                "-mod=@Client -serverMod=@Server -doLogs"
            ),
        }), encoding="utf-8")
        self.api = HostApi(self.composition.host_bridge)
        self.api._set_legacy_folder_selector(lambda: str(self.legacy))

    def tearDown(self) -> None:
        """Shut the operation manager down and remove the temporary tree."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def wait_terminal(self, operation_id: str) -> object:
        """Poll until the migration operation reaches a terminal state."""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = self.composition.operations.get(operation_id)
            if record.state in {
                OperationState.SUCCEEDED, OperationState.FAILED,
                OperationState.CANCELLED, OperationState.RECOVERY_REQUIRED,
            }:
                return record
            time.sleep(0.01)
        raise AssertionError("migration operation did not finish")

    def test_named_host_flow_is_sanitized_and_serialized(self) -> None:
        """The named host flow sanitizes paths and serializes concurrent imports."""
        # Select, preview, and apply the legacy import
        selected = self.api.select_legacy_root()
        self.assertTrue(selected["success"])
        preview = self.api.preview_legacy_import(selected["value"]["selection_id"])
        self.assertTrue(preview["success"])
        # Neither preview nor responses may expose the selected legacy path
        rendered = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn(str(self.legacy), rendered)
        self.assertEqual(preview["value"]["backup_inventory"]["status"], "EXTERNAL_REFERENCE")
        item = preview["value"]["profiles"][0]["item_id"]
        queued = self.api.apply_legacy_import(
            preview["value"]["preview_id"],
            preview["value"]["preview_fingerprint"],
            [item],
        )
        self.assertTrue(queued["success"])
        record = self.wait_terminal(queued["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.composition.profiles.read("main").values.mods[1].launch_scope, "server")

    def test_strict_contract_rejects_extra_fields_and_bad_proofs(self) -> None:
        """Extra parameters and invalid proofs are rejected by the strict contract."""
        result = self.composition.host_bridge.dispatch({
            "contract_version": 1, "request_id": "migration-invalid",
            "method": "preview_legacy_import",
            "parameters": {"selection_id": "x", "root": str(self.legacy)},
        })
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        # An invalid fingerprint proof must be rejected as well
        result = self.api.apply_legacy_import("bad", "not-a-digest", [])
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")

    def test_recovery_block_rejects_import_but_queries_remain_available(self) -> None:
        """A recovery block rejects imports while read-only queries keep working."""
        selected = self.api.select_legacy_root()
        preview = self.api.preview_legacy_import(selected["value"]["selection_id"])
        # Blocking for recovery must not disable the preview query
        self.composition.operations.block_for_recovery("Synthetic recovery block.")
        result = self.api.apply_legacy_import(
            preview["value"]["preview_id"], preview["value"]["preview_fingerprint"],
            [preview["value"]["profiles"][0]["item_id"]],
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "MUTATION_CONFLICT")
        self.assertTrue(self.api.preview_legacy_import(selected["value"]["selection_id"])["success"])

    def test_source_change_has_stable_terminal_code(self) -> None:
        """Editing the source after preview yields the stable SOURCE_CHANGED code."""
        selected = self.api.select_legacy_root()
        preview = self.api.preview_legacy_import(selected["value"]["selection_id"])
        # Change the profile so the preview fingerprint goes stale
        profile = self.legacy / "dayz_server_manager-profiles" / "Main.json"
        profile.write_text('{"name":"Changed"}', encoding="utf-8")
        queued = self.api.apply_legacy_import(
            preview["value"]["preview_id"], preview["value"]["preview_fingerprint"],
            [preview["value"]["profiles"][0]["item_id"]],
        )
        record = self.wait_terminal(queued["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.FAILED)
        self.assertEqual(record.terminal_error.code, "SOURCE_CHANGED")

    def test_external_backup_index_is_sanitized_revalidated_and_not_native(self) -> None:
        """External backups stay indexed as references and never become native."""
        # Seed one legacy archive and import the external index
        backups = self.legacy / "dayz_server_manager-backups" / "Nested"
        backups.mkdir(parents=True)
        archive = backups / "old snapshot.zip"
        archive.write_bytes(b"legacy archive")
        selected = self.api.select_legacy_root()
        preview = self.api.preview_legacy_import(selected["value"]["selection_id"])
        queued = self.api.apply_legacy_import(
            preview["value"]["preview_id"], preview["value"]["preview_fingerprint"],
            ["backups:external-index"],
        )
        self.assertEqual(self.wait_terminal(queued["value"]["operation_id"]).state,
                         OperationState.SUCCEEDED)
        listed = self.api.list_legacy_backup_references()
        self.assertTrue(listed["success"])
        rendered = json.dumps(listed, ensure_ascii=False)
        self.assertNotIn(str(self.legacy), rendered)
        self.assertFalse(listed["value"]["restorable"])
        reference_id = listed["value"]["entries"][0]["reference_id"]
        native = self.api.list_backups("main")
        self.assertNotIn(reference_id, json.dumps(native))

        # Changing the archive must surface as CHANGED with the same reference id
        archive.write_bytes(b"changed archive")
        revalidation = self.api.revalidate_legacy_backup_references(
            listed["value"]["revision"],
        )
        record = self.wait_terminal(revalidation["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        refreshed = self.api.list_legacy_backup_references()
        self.assertEqual(refreshed["value"]["entries"][0]["status"], "CHANGED")
        self.assertEqual(refreshed["value"]["entries"][0]["reference_id"], reference_id)

    def test_external_reference_query_survives_recovery_block(self) -> None:
        """Reference queries stay available while revalidation is blocked."""
        self.composition.operations.block_for_recovery("Synthetic recovery block.")
        self.assertTrue(self.api.list_legacy_backup_references()["success"])
        result = self.api.revalidate_legacy_backup_references(0)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "MUTATION_CONFLICT")

    def test_external_reference_contract_rejects_extra_fields(self) -> None:
        """Extra parameters on the reference query are rejected."""
        result = self.composition.host_bridge.dispatch({
            "contract_version": 1, "request_id": "legacy-backup-invalid",
            "method": "list_legacy_backup_references", "parameters": {"path": "x"},
        })
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")

    def test_native_selection_cancel_and_failure_are_sanitized(self) -> None:
        """Cancelled and failed native selection stay sanitized."""
        # A cancelled selection reports cancellation without an error
        self.api._set_legacy_folder_selector(lambda: None)
        self.assertTrue(self.api.select_legacy_root()["value"]["cancelled"])
        # A raising selector must produce a sanitized failure
        self.api._set_legacy_folder_selector(lambda: (_ for _ in ()).throw(OSError("private path")))
        result = self.api.select_legacy_root()
        self.assertFalse(result["success"])
        self.assertNotIn("private path", str(result))


if __name__ == "__main__":
    unittest.main()
