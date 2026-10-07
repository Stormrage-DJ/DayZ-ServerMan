"""Update-check cache: round trip, whole-file rejection, caps and atomic write."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.update_check import (  # noqa: E402
    AttemptOutcome,
    CheckAttempt,
    RemoteFact,
    RemoteItemResult,
    UpdateCheckRecord,
)
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402
from dayz_serverman.repositories import update_check_cache as cache  # noqa: E402
from dayz_serverman.repositories.paths import PortablePaths  # noqa: E402

CHECKED = "2026-10-03T12:00:00.000+00:00"


def fact(result: RemoteItemResult = RemoteItemResult.OK) -> RemoteFact:
    """Return one fact; only an OK fact carries a time and a size."""
    if result is RemoteItemResult.OK:
        return RemoteFact(result, 1_700_000_000, 4096, CHECKED)
    return RemoteFact(result, None, None, CHECKED)


def document() -> dict[str, object]:
    """Return one well-formed persisted schema 1 document."""
    return {
        "schema_version": 1,
        "last_attempt": {"finished_at": CHECKED, "outcome": "FAILED", "error_code": "TIMEOUT"},
        "last_success_at": CHECKED,
        "items": {"111": fact().to_dict()},
    }


class UpdateCheckCacheTests(unittest.TestCase):
    """Persistence rules of the disposable remote-fact cache."""

    def setUp(self) -> None:
        """Create a temporary data directory, a log file and the repository."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = PortablePaths.from_root(self.root).update_check_cache
        self.log_path = self.root / "manager.jsonl"
        self.repository = cache.UpdateCheckCacheRepository(
            self.path, StructuredLogger(self.log_path),
        )

    def write_raw(self, value: object) -> None:
        """Store a raw JSON value as the cache file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_path_is_the_data_update_check_file(self) -> None:
        """The cache lives in the manager data directory."""
        self.assertEqual(self.path, self.root.resolve() / "data" / "update-check.json")

    def test_round_trip_keeps_attempt_success_time_and_every_result(self) -> None:
        """A saved record loads back equal, for each item result."""
        record = UpdateCheckRecord(
            CheckAttempt(CHECKED, AttemptOutcome.OK, None), CHECKED,
            {str(100 + index): fact(result) for index, result in enumerate(RemoteItemResult)},
        )
        self.assertIsNone(self.repository.load())
        self.assertTrue(self.repository.save(record, set(record.items)))
        self.assertEqual(self.repository.load(), record)
        # A failed attempt and an empty record survive as well
        failed = UpdateCheckRecord(CheckAttempt(CHECKED, AttemptOutcome.FAILED, "TLS_FAILURE"))
        self.assertTrue(self.repository.save(failed, ()))
        self.assertEqual(self.repository.load(), failed)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertEqual([entry.name for entry in self.path.parent.iterdir()], [self.path.name])

    def test_corrupt_file_is_ignored_and_replaced_by_the_next_write(self) -> None:
        """Unreadable content means never checked; the next write replaces it."""
        self.path.parent.mkdir(parents=True)
        for content in (b"not-json", b"\xff\xfe", b"", b"[]", b'{"schema_version": NaN}'):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                self.assertIsNone(self.repository.load())
        record = UpdateCheckRecord(None, None, {"111": fact()})
        self.assertTrue(self.repository.save(record, {"111"}))
        self.assertEqual(self.repository.load(), record)

    def test_wrong_version_is_ignored(self) -> None:
        """Any schema version other than integer 1 rejects the file."""
        for version in (0, 2, "1", True, 1.0, None):
            with self.subTest(version=version):
                raw = document()
                raw["schema_version"] = version
                self.write_raw(raw)
                self.assertIsNone(self.repository.load())

    def test_oversized_file_is_ignored(self) -> None:
        """A file above 1 MiB is rejected; a file at the cap is read."""
        text = json.dumps(document())
        self.path.parent.mkdir(parents=True)
        self.path.write_text(text + " " * (cache.MAX_FILE_BYTES - len(text)), encoding="utf-8")
        self.assertIsNotNone(self.repository.load())
        self.path.write_text(text + " " * (cache.MAX_FILE_BYTES - len(text) + 1), encoding="utf-8")
        self.assertIsNone(self.repository.load())

    def test_one_malformed_part_rejects_the_whole_file(self) -> None:
        """A malformed root, attempt or item makes the complete file unusable."""
        self.write_raw(document())
        self.assertIsNotNone(self.repository.load())
        item = ("items", "111")
        changes = {
            "extra root field": ((), "extra", 1),
            "items not a map": ((), "items", []),
            "success time not UTC": ((), "last_success_at", "2026-10-03T12:00:00.000+02:00"),
            "success time impossible": ((), "last_success_at", "2026-13-03T12:00:00.000+00:00"),
            "attempt not an object": ((), "last_attempt", "OK"),
            "attempt outcome": (("last_attempt",), "outcome", "MAYBE"),
            "attempt time": (("last_attempt",), "finished_at", "2026-10-03T12:00:00Z"),
            "attempt code": (("last_attempt",), "error_code", 7),
            "attempt extra field": (("last_attempt",), "body", "text"),
            "item id": (("items",), "0x1", fact().to_dict()),
            "item not an object": (("items",), "111", 5),
            "item result": (item, "result", "CURRENT"),
            "item time text": (item, "time_updated", "1700000000"),
            "item time zero": (item, "time_updated", 0),
            "item time boolean": (item, "time_updated", True),
            "item size negative": (item, "file_size", -1),
            "item size text": (item, "file_size", "4096"),
            "item checked_at": (item, "checked_at", None),
            "item extra field": (item, "title", "text"),
            "data without OK": (item, "result", "NOT_FOUND"),
        }
        for name, (parents, key, value) in changes.items():
            with self.subTest(name=name):
                raw = copy.deepcopy(document())
                target = raw
                for parent in parents:
                    target = target[parent]
                target[key] = value
                self.write_raw(raw)
                self.assertIsNone(self.repository.load())

    def test_item_cap_applies_to_write_and_read(self) -> None:
        """A write keeps the 1,000 numerically first ids; a larger file is rejected."""
        ids = [str(value) for value in range(1, 1_003)]
        record = UpdateCheckRecord(None, None, {key: fact() for key in reversed(ids)})
        self.assertTrue(self.repository.save(record, set(ids)))
        loaded = self.repository.load()
        self.assertEqual(sorted(loaded.items, key=int), ids[:1_000])
        raw = document()
        raw["items"] = {key: fact().to_dict() for key in ids[:1_001]}
        self.write_raw(raw)
        self.assertIsNone(self.repository.load())

    def test_write_drops_ids_that_no_profile_configures(self) -> None:
        """Only configured ids are persisted."""
        record = UpdateCheckRecord(None, CHECKED, {key: fact() for key in ("111", "222", "333")})
        self.assertTrue(self.repository.save(record, {"222", "333", "444"}))
        self.assertEqual(set(self.repository.load().items), {"222", "333"})

    def test_failed_write_is_logged_and_keeps_the_previous_file(self) -> None:
        """A failed swap returns False, logs one event and leaves no staged file."""
        first = UpdateCheckRecord(None, CHECKED, {"111": fact()})
        self.assertTrue(self.repository.save(first, {"111"}))
        with patch.object(cache, "replace_file", side_effect=PermissionError("locked")):
            saved = self.repository.save(UpdateCheckRecord(None, None, {}), set())
        self.assertFalse(saved)
        self.assertEqual(self.repository.load(), first)
        self.assertEqual([entry.name for entry in self.path.parent.iterdir()], [self.path.name])
        records = [
            json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [(entry["event"], entry["level"], entry["fields"]) for entry in records],
            [("update_check.cache_write_failed", "WARNING", {"error": "PermissionError"})],
        )


if __name__ == "__main__":
    unittest.main()
