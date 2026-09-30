"""Tests for manager and server log querying, limits, and encoding safety."""
from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from dayz_serverman.application.logs import LogQueryService, TAIL_BYTES
from dayz_serverman.bridge.facade import ApplicationCallError


class LogQueryTests(unittest.TestCase):
    """Log query contracts for sources, line limits, and UTF-8 safety."""
    def setUp(self) -> None:
        """Create the empty manager and server log files."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_logs_")
        self.root = Path(self.temporary.name)
        self.manager = self.root / "manager.jsonl"
        self.server = self.root / "dayz-server.log"
        self.service = LogQueryService(self.manager, self.server)

    def tearDown(self) -> None:
        """Remove the temporary log directory."""
        self.temporary.cleanup()

    def test_missing_log_is_a_safe_empty_result(self) -> None:
        """A missing log file returns an empty non-truncated result."""
        result = self.service.read_log({"source": "manager", "maximum_lines": 20})
        self.assertEqual(result, {
            "source": "manager", "lines": [], "truncated": False,
            "revision": "missing",
        })

    def test_query_returns_only_requested_newest_lines(self) -> None:
        """The query returns only the requested newest lines and flags truncation."""
        self.server.write_text("one\ntwo\nthree\n", encoding="utf-8")
        result = self.service.read_log({"source": "server", "maximum_lines": 2})
        self.assertEqual(result["lines"], ["two", "three"])
        self.assertTrue(result["truncated"])

    def test_query_rejects_arbitrary_source_and_oversized_limit(self) -> None:
        """Arbitrary sources and oversized limits are rejected."""
        for parameters in (
            {"source": "../../secret", "maximum_lines": 20},
            {"source": "manager", "maximum_lines": 1001},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ApplicationCallError):
                self.service.read_log(parameters)

    def test_large_log_is_byte_bounded_and_utf8_safe(self) -> None:
        """A large log is trimmed by bytes and keeps UTF-8 characters intact."""
        self.server.write_bytes(b"discard\n" + b"x" * TAIL_BYTES + "\nárvíz\n".encode())
        result = self.service.read_log({"source": "server", "maximum_lines": 10})
        self.assertTrue(result["truncated"])
        self.assertEqual(result["lines"][-1], "árvíz")

    def test_manager_activity_hides_polling_and_formats_terminal_results(self) -> None:
        """The default manager view contains decisions, not transport polling."""
        records = [
            self.record("bridge.request", fields={"method": "read_log"}),
            self.record("bridge.success", fields={"method": "read_log"}),
            self.record("operation.progress", fields={"phase": "copy", "progress_percent": 50}),
            self.record("operation.state", fields={"kind": "CREATE_PROFILE", "state": "RUNNING"}),
            self.record("operation.state", fields={"kind": "CREATE_PROFILE", "state": "SUCCEEDED"}),
            self.record(
                "operation.state", level="ERROR",
                fields={"kind": "BACKUP", "state": "FAILED", "error_message": "Disk is full."},
            ),
            self.record(
                "bridge.failure", level="WARNING",
                fields={"method": "save_settings", "message": "The settings are invalid."},
            ),
        ]
        self.manager.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
        )

        result = self.service.read_log({"source": "manager", "maximum_lines": 20})

        self.assertEqual(len(result["lines"]), 3)
        self.assertIn("Create Profile completed.", result["lines"][0])
        self.assertIn("Backup failed: Disk is full.", result["lines"][1])
        self.assertIn("Save Settings failed: The settings are invalid.", result["lines"][2])

    def test_manager_diagnostics_returns_raw_structured_records(self) -> None:
        """Technical diagnostics remain available as an explicit source."""
        line = json.dumps(self.record("bridge.request", fields={"method": "read_log"}))
        self.manager.write_text(f"{line}\n", encoding="utf-8")

        result = self.service.read_log({
            "source": "manager_diagnostics", "maximum_lines": 20,
        })

        self.assertEqual(result["lines"], [line])
        self.assertNotEqual(result["revision"], "missing")

    @staticmethod
    def record(event: str, *, level: str = "INFO", fields: dict | None = None) -> dict:
        """Build one structured manager-log fixture."""
        return {
            "occurred_at": "2026-09-30T10:11:12.000+00:00",
            "level": level,
            "event": event,
            "fields": fields or {},
        }


if __name__ == "__main__":
    unittest.main()
