"""Recovery tests for active journal plan validation and blocked tampering."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.repositories.migration_journal import MigrationJournalRepository  # noqa: E402
from dayz_serverman.repositories.migration_publication import MigrationPublication  # noqa: E402
from dayz_serverman.repositories.migrations import MigrationStorage  # noqa: E402


SHA = hashlib.sha256(b"source").hexdigest()
FINGERPRINT = hashlib.sha256(b"preview").hexdigest()


class ActiveMigrationPlanValidationTests(unittest.TestCase):
    """Validation contracts for tampered active migration journals."""
    def setUp(self) -> None:
        """Build the publication over a temporary migration storage."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_active_plan_")
        self.root = Path(self.temporary.name) / "Manager"
        self.storage = MigrationStorage(self.root / "data" / "migrations")
        self.journals = MigrationJournalRepository(self.storage.root / "publication-journals")
        self.blocked: list[str] = []
        self.publisher = MigrationPublication(
            self.root, self.storage, self.journals,
            block_recovery=self.blocked.append,
        )

    def tearDown(self) -> None:
        """Remove the temporary migration storage."""
        self.temporary.cleanup()

    def test_plan_tamper_blocks_before_any_target_access(self) -> None:
        """Every plan tamper blocks recovery before any destination is touched."""
        # Each case tampers with one plan field for its recorded state
        cases = (
            ("PREPARED", "target_relative", 0, "data/arbitrary.cfg"),
            ("PUBLISHING", "target_relative", 1, "data/profiles/main-copy.json"),
            ("COMMITTED", "role", 0, "PROFILE"),
            ("ROLLED_BACK", "target_relative", 1, "config/manager.json"),
            ("PREPARED", "label", 1, "other-profile"),
            ("PUBLISHING", "label", 2, "Other index"),
            ("COMMITTED", "label", 3, "f" * 32),
            ("ROLLED_BACK", "migration_id", None, "e" * 32),
        )
        # A sentinel file proves that no recovery wrote to the target tree
        sentinel = self.root / "data" / "arbitrary.cfg"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"must remain untouched")
        for number, (state, field, item_index, value) in enumerate(cases, 40):
            migration_id = f"{number:032x}"
            self._create_journal(migration_id, state)
            path = self.journals.path(migration_id)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if item_index is None:
                raw[field] = value
            else:
                raw["destinations"][item_index][field] = value
            path.write_text(json.dumps(raw), encoding="utf-8")
            # Recovery must request remediation and leave the sentinel intact
            with self.subTest(state=state, field=field, value=value):
                before = sentinel.read_bytes()
                result = self.publisher.inspect_recovery()
                self.assertEqual(result["state"], "RECOVERY_REQUIRED")
                self.assertEqual(sentinel.read_bytes(), before)
                self.assertTrue(path.exists())
            path.unlink(missing_ok=True)

    def _create_journal(self, migration_id: str, state: str) -> None:
        """Write a journal for the given migration id and publication state."""
        destination_state = (
            "PUBLISHED" if state == "COMMITTED"
            else "RESTORED" if state == "ROLLED_BACK" else "PLANNED"
        )
        flags = {
            "PREPARED": (False, False, False, None),
            "PUBLISHING": (True, False, False, None),
            "COMMITTED": (True, True, True, "COMMITTED"),
            "ROLLED_BACK": (True, False, True, "ROLLED_BACK"),
        }[state]
        self.journals.create(migration_id, {
            "source_digest": SHA, "preview_fingerprint": FINGERPRINT,
            "state": state, "publication_started": flags[0],
            "committed": flags[1], "resolved": flags[2], "result": flags[3],
            "destinations": _destinations(migration_id, destination_state),
        })


def _destinations(migration_id: str, state: str) -> list[dict[str, object]]:
    """Build the canonical four-destination plan for a migration journal."""
    identities = (
        ("SETTINGS", "Manager settings", "manager-settings", "config/manager.json"),
        ("PROFILE", "main", "main", "data/profiles/main.json"),
        ("LEGACY_BACKUP_INDEX", "Legacy backup index",
         "0" * 64, "data/migrations/legacy-backup-index.json"),
        ("REPORT", migration_id, migration_id,
         f"data/migrations/reports/{migration_id}.json"),
    )
    return [{
        "role": role, "label": label, "payload_identity": payload_identity,
        "prior_payload_identity": None, "target_relative": target,
        "staged_relative": f"output/{index:04d}.bin", "recovery_relative": None,
        "prior_exists": False, "prior_sha256": None,
        "staged_sha256": hashlib.sha256(f"output-{index}".encode()).hexdigest(),
        "state": state,
    } for index, (role, label, payload_identity, target) in enumerate(identities)]


if __name__ == "__main__":
    unittest.main()
