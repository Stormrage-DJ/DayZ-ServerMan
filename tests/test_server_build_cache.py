"""The disposable cache of the server build check: round trip, strict reading and a failed write."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.domain.server_build import BranchFact, BuildCheckRecord
from dayz_serverman.domain.update_check import AttemptOutcome, CheckAttempt
from dayz_serverman.repositories.server_build_cache import MAX_FILE_BYTES, ServerBuildCacheRepository

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FINISHED = "2026-10-05T11:00:00.000+00:00"


class Recorder:
    """Logger stand-in."""

    def __init__(self) -> None:
        """Start without events."""
        self.events: list[tuple[str, dict]] = []

    def emit(self, event, level="INFO", fields=None):
        """Keep one event."""
        self.events.append((event, dict(fields or {})))


class CacheTests(unittest.TestCase):
    """Detailed design 14.6, cache paragraph."""

    def setUp(self) -> None:
        """Create the repository in a temporary data folder."""
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "data" / "server-build-check.json"
        self.logger = Recorder()
        self.cache = ServerBuildCacheRepository(self.path, self.logger, clock=lambda: NOW)

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def record(self) -> BuildCheckRecord:
        """Return a success record with two branches."""
        return BuildCheckRecord(CheckAttempt(FINISHED, AttemptOutcome.OK, None), FINISHED,
                                {"public": BranchFact(24570360, 1786528820), "experimental_public": BranchFact(3889697, None)})

    def document(self) -> dict:
        """Return a valid stored document."""
        self.assertTrue(self.cache.save(self.record()))
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, value) -> None:
        """Store a raw document."""
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_round_trip_holds_only_the_schema_fields(self) -> None:
        """The file holds attempt, last success and branch facts only."""
        stored = self.document()
        self.assertEqual(set(stored), {"schema_version", "last_attempt", "last_success_at", "branches"})
        self.assertEqual(self.cache.load(), self.record())

    def test_unusable_files_are_never_checked(self) -> None:
        """Missing, oversized, wrong version, malformed or future data is ignored as a whole."""
        self.assertIsNone(self.cache.load())
        good = self.document()
        changes = [
            lambda d: d.update(schema_version=2), lambda d: d.update(extra=1),
            lambda d: d["branches"].update({"bad name": {"build_id": 1, "time_updated": None}}),
            lambda d: d["branches"]["public"].update(build_id=0),
            lambda d: d["branches"]["public"].update(build_id=4294967296),
            lambda d: d["branches"]["public"].update(time_updated=-1),
            lambda d: d["branches"]["public"].update(build_id=True),
            lambda d: d.update(last_success_at="2026-10-05T12:02:00.000+00:00"),
            lambda d: d["last_attempt"].update(outcome="MAYBE"),
            lambda d: d.update(branches={f"b{index}": {"build_id": 1, "time_updated": None} for index in range(65)}),
        ]
        for change in changes:
            with self.subTest(change=change):
                value = json.loads(json.dumps(good))
                change(value)
                self.write(value)
                self.assertIsNone(self.cache.load())
        self.path.write_bytes(b"{" + b" " * MAX_FILE_BYTES + b"}")
        self.assertIsNone(self.cache.load())
        self.path.write_text('{"schema_version": NaN}', encoding="utf-8")
        self.assertIsNone(self.cache.load())

    def test_a_failed_write_is_logged_and_keeps_the_file(self) -> None:
        """The cache is disposable: a failed write returns False and logs the error type only."""
        self.document()
        before = self.path.read_bytes()
        with patch("dayz_serverman.repositories.update_check_cache.replace_file", side_effect=OSError("disk")):
            self.assertFalse(self.cache.save(self.record()))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.logger.events, [("server_build.cache_write_failed", {"error_type": "OSError"})])


if __name__ == "__main__":
    unittest.main()
