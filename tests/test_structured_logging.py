"""Structured logging redaction, correlation, and operation event tests."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402


class StructuredLoggingTests(unittest.TestCase):
    """Structured logging redaction and bridge correlation contracts."""
    def setUp(self) -> None:
        """Create a temporary log directory and the logger under test."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_logs_")
        self.root = Path(self.temporary.name)
        self.path = self.root / "data" / "logs" / "manager.jsonl"
        self.logger = StructuredLogger(self.path)

    def tearDown(self) -> None:
        """Remove the temporary log directory."""
        self.temporary.cleanup()

    def entries(self) -> list[dict]:
        """Return the parsed log entries in file order."""
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_nested_sensitive_fields_and_inline_credentials_are_redacted(self) -> None:
        """Nested and inline credentials never reach the log file."""
        # Emit a fixture event carrying secrets at several nesting levels
        self.logger.emit(
            "fixture.event",
            correlation_id="request-1",
            fields={
                "password": "do-not-write",
                "nested": {"access_token": "also-secret"},
                "message": "authorization=BearerValue safe text",
            },
        )
        # Confirm no secret text survives anywhere in the file
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("do-not-write", text)
        self.assertNotIn("also-secret", text)
        self.assertNotIn("BearerValue", text)
        # Confirm correlation is kept and marked fields are redacted
        entry = self.entries()[0]
        self.assertEqual(entry["schema_version"], 1)
        self.assertEqual(entry["correlation_id"], "request-1")
        self.assertEqual(entry["fields"]["password"], "[REDACTED]")

    def test_inline_redaction_covers_query_bearer_quoted_and_api_key_values(self) -> None:
        """Query, bearer, quoted, and API key shapes are all redacted inline."""
        # Emit one event per credential shape seen in real command lines
        self.logger.emit(
            "probe.event",
            fields={
                "message": (
                    "url=https://x.invalid/?access_token=SUPERSECRET&api_key=ALSOSECRET"
                ),
                "header": "authorization: Bearer qa-secret-token",
                "quoted": 'password="qa secret phrase"; API key = FINAL_SECRET',
                "nested": {"api_key": "NESTED_SECRET"},
                "ordinary": "API key rotation uses a token bucket and secret sharing.",
            },
        )

        # Confirm every planted secret is removed from the written bytes
        text = self.path.read_text(encoding="utf-8")
        for secret in (
            "SUPERSECRET",
            "ALSOSECRET",
            "qa-secret-token",
            "qa secret phrase",
            "FINAL_SECRET",
            "NESTED_SECRET",
        ):
            self.assertNotIn(secret, text)
        # Confirm each field was rewritten to its redacted form
        fields = self.entries()[0]["fields"]
        self.assertEqual(
            fields["message"],
            "url=https://x.invalid/?access_token=[REDACTED]&api_key=[REDACTED]",
        )
        self.assertEqual(fields["header"], "authorization: [REDACTED]")
        self.assertEqual(
            fields["quoted"],
            "password=[REDACTED]; API key = [REDACTED]",
        )
        self.assertEqual(fields["nested"]["api_key"], "[REDACTED]")
        self.assertEqual(
            fields["ordinary"],
            "API key rotation uses a token bucket and secret sharing.",
        )

    def test_bridge_request_correlates_with_durable_operation_events(self) -> None:
        """One bridge request correlates with its durable operation events."""
        # Wire an operation manager and a bridge method that submits one operation
        manager = OperationManager(OperationStore(self.root / "operations"), logger=self.logger)

        def submit(_parameters):
            """Submit the fixture operation and return its identifier."""
            operation = manager.submit(
                "FIXTURE",
                lambda context: _completed(context),
                log_fields={"profile_id": "livonia-main", "target_role": "dayz_server"},
            )
            return {"operation_id": operation.operation_id}

        # Dispatch one correlated request through the bridge facade
        facade = BridgeFacade({"run_fixture": submit}, self.logger)
        result = facade.dispatch(
            {
                "contract_version": 1,
                "request_id": "correlation-7",
                "method": "run_fixture",
                "parameters": {},
            }
        )
        # Wait for the operation to reach a terminal state before reading the log
        operation_id = result["value"]["operation_id"]
        deadline = time.monotonic() + 2
        while manager.get(operation_id).state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        manager.shutdown(2)

        # Confirm bridge and operation events share the request correlation
        related = [entry for entry in self.entries() if entry["correlation_id"] == "correlation-7"]
        self.assertTrue(any(entry["event"] == "bridge.request" for entry in related))
        operation_entries = [entry for entry in related if entry["operation_id"] == operation_id]
        self.assertTrue(any(entry["event"] == "operation.progress" for entry in operation_entries))
        self.assertTrue(any(entry["fields"].get("state") == "SUCCEEDED" for entry in operation_entries))
        # Confirm the completion event carries the submitted log fields
        completed = next(entry for entry in operation_entries if entry["fields"].get("state") == "SUCCEEDED")
        self.assertEqual(completed["fields"]["profile_id"], "livonia-main")
        self.assertEqual(completed["fields"]["target_role"], "dayz_server")
        self.assertEqual(completed["fields"]["child_process_id"], 700)


def _completed(context) -> dict[str, object]:
    """Complete the fixture operation through one checkpoint."""
    context.checkpoint("fixture-safe-point", 50)
    return {"completed": True, "process_id": 700}


if __name__ == "__main__":
    unittest.main()
