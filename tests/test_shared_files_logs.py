"""A12 log rules: a rotation never fails emit, and log reads keep whole records."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_serverman.adapters.windows import shared_files
from dayz_serverman.adapters.windows.shared_files import open_shared
from dayz_serverman.application import logs
from dayz_serverman.application.logs import LogQueryService
from dayz_serverman.observability.structured_log import StructuredLogger
from tests.test_shared_files import WINDOWS, SharedFilesTestCase


class LogTests(SharedFilesTestCase):
    """Log rotation never fails emit, and log reads keep whole records."""

    def logger(self) -> tuple[Path, StructuredLogger]:
        """Return a logger whose active file holds two records and rotates on the third."""
        probe = StructuredLogger(self.root / "probe.jsonl")
        probe.emit("test.record", fields={"n": 0, "text": "x" * 150})
        size = (self.root / "probe.jsonl").stat().st_size
        path = self.root / "manager.jsonl"
        return path, StructuredLogger(path, maximum_bytes=2 * size + size // 2)

    def emit(self, logger: StructuredLogger, number: int) -> None:
        """Append one record of the same size as every other."""
        logger.emit("test.record", fields={"n": number, "text": "x" * 150})

    @staticmethod
    def numbers(data: bytes) -> list[int]:
        """Return the record numbers of a log file's bytes."""
        return [json.loads(line)["fields"]["n"] for line in data.splitlines()]

    def test_rotation_with_shared_readers_on_both_files(self) -> None:
        """Readers on manager.jsonl and .1 do not stop a rotation, and emit raises nothing."""
        path, logger = self.logger()
        previous = path.with_name("manager.jsonl.1")
        for number in (1, 2, 3, 4):
            self.emit(logger, number)
        self.assertEqual(self.numbers(previous.read_bytes()), [1, 2])
        with open_shared(path) as active_reader, open_shared(previous) as previous_reader:
            self.emit(logger, 5)
            # The open readers still see the bytes they opened
            self.assertEqual(self.numbers(active_reader.read()), [3, 4])
            self.assertEqual(self.numbers(previous_reader.read()), [1, 2])
        self.assertEqual(self.numbers(previous.read_bytes()), [3, 4])
        self.assertEqual(self.numbers(path.read_bytes()), [5])

    @unittest.skipUnless(WINDOWS, "a plain reader blocks the replace only on Windows")
    def test_a_refused_rotation_keeps_the_active_file_and_retries_later(self) -> None:
        """A plain reader refuses the rotation: the record is appended to the active file, nothing is raised."""
        path, logger = self.logger()
        previous = path.with_name("manager.jsonl.1")
        self.emit(logger, 1)
        self.emit(logger, 2)
        with open(path, "rb"), patch.object(shared_files, "sleep"):
            self.emit(logger, 3)
        self.assertFalse(previous.exists())
        self.assertEqual(self.numbers(path.read_bytes()), [1, 2, 3])
        # The next record rotates once the reader is gone
        self.emit(logger, 4)
        self.assertEqual(self.numbers(previous.read_bytes()), [1, 2, 3])
        self.assertEqual(self.numbers(path.read_bytes()), [4])

    def test_tail_drops_a_partial_last_record_of_the_manager_log(self) -> None:
        """manager and manager_diagnostics drop an unfinished last line; the server log keeps it."""
        manager = self.root / "manager.jsonl"
        server = self.root / "server.log"
        record = json.dumps({"occurred_at": "2026-10-07T10:00:00.000+00:00", "level": "INFO",
                             "event": "bridge.request", "fields": {}})
        manager.write_bytes(f"{record}\n{{\"occurred_at\": \"2026".encode("utf-8"))
        server.write_bytes(b"one\ntwo\npartial")
        service = LogQueryService(manager, server)
        self.assertEqual(logs._tail_text(manager, whole_records=True), (f"{record}\n", False))
        diagnostics = service.read_log({"source": "manager_diagnostics", "maximum_lines": 20})
        self.assertEqual(diagnostics["lines"], [record])
        self.assertEqual(service.read_log({"source": "server", "maximum_lines": 20})["lines"],
                         ["one", "two", "partial"])

    def test_tail_reads_a_file_removed_after_the_size_check_as_empty(self) -> None:
        """A rotation between the size check and the open reads as empty and not truncated."""
        path = self.root / "manager.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        with patch.object(logs, "open_shared", side_effect=FileNotFoundError(2, "gone")):
            self.assertEqual(logs._tail_text(path, whole_records=True), ("", False))


if __name__ == "__main__":
    unittest.main()
