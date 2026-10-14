"""A181 test 13: Inspect slices under the real folder lock let a waiting owner writer in within one slice."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.adapters.windows.instance_lock import InstanceLockUnsupported
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderReader
from dayz_serverman.adapters.windows.shared_files import open_shared, rename_directory
from dayz_serverman.application.mission_map_inspection import MissionMapInspection
from dayz_serverman.application.mission_map_inspection_jobs import InspectionJobs
from dayz_serverman.application.mission_map_reader_port import DelegatingReaderPort
from dayz_serverman.repositories import mission_map_read_pass
from dayz_serverman.session_observer import BridgeCallFailed, open_observer_session
from tests.folder_lock_fixtures import writer_for
from tests.mission_map_inspection_support import InspectionFixture, mission_bytes, write_mission

# Pause of each slowed block read, so one capture needs several slices
READ_PAUSE_SECONDS = 0.01
# Longest wait of the test threads
JOIN_SECONDS = 10.0


class SlowStream:
    """A shared-read stream that reads at most 4 KiB per call and pauses, and reports that a slice reads."""

    def __init__(self, stream, reading: threading.Event) -> None:
        """Wrap one open stream."""
        self._stream, self._reading = stream, reading

    def __enter__(self):
        """Enter the wrapped stream."""
        self._stream.__enter__()
        return self

    def __exit__(self, *details):
        """Close the wrapped stream."""
        return self._stream.__exit__(*details)

    def fileno(self) -> int:
        """Return the descriptor of the wrapped stream."""
        return self._stream.fileno()

    def seek(self, offset: int) -> int:
        """Seek the wrapped stream."""
        return self._stream.seek(offset)

    def read(self, size: int) -> bytes:
        """Report the read, pause, then read one small block."""
        self._reading.set()
        time.sleep(READ_PAUSE_SECONDS)
        return self._stream.read(min(size, 4096))


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class RealLockTests(InspectionFixture):
    """An observer-style reader port over the real lock file, and an owner writer on its own handle."""

    def setUp(self) -> None:
        """Write a 655 KB mission, create the lock file and bind a real reader to the Inspect port."""
        super().setUp()
        write_mission(self.mission, mission_bytes(512, 512))
        lock_path = self.root.parent / "server-folders.lock"
        FolderLockFile.create(lock_path).close()
        reader = FolderReader(FolderLockFile.open_existing(lock_path))
        self.addCleanup(reader.close)
        self.writer = writer_for(lock_path)
        self.addCleanup(self.writer.close)
        port = DelegatingReaderPort()
        port.bind(reader)
        self.inspection = MissionMapInspection(self.service, port, jobs=InspectionJobs())
        self.reading = threading.Event()
        slowed = mock.patch.object(mission_map_read_pass, "open_shared",
                                   lambda path: SlowStream(open_shared(path), self.reading))
        slowed.start()
        self.addCleanup(slowed.stop)

    def test_13_a_waiting_writer_enters_within_one_slice_and_renames_the_mission(self) -> None:
        """The writer waits at most one slice; while it holds, calls return unchanged progress."""
        self.saved([])
        answer = self.start({"point": {"x": 500.0, "z": 500.0}})
        self.assertEqual(answer["status"], "running")
        job_id = answer["job"]["id"]
        job = self.jobs_of()[job_id]
        self.reading.clear()
        result: dict = {}
        holding = threading.Event()

        def write() -> None:
            """Wait for a reading slice, then take the writer side and rename the mission folder twice."""
            self.reading.wait(JOIN_SECONDS)
            started = time.monotonic()
            with self.writer.exclusive(5.0):
                result["waited"] = time.monotonic() - started
                moved = self.mission.with_name("dayzOffline.restoring")
                rename_directory(self.mission, moved)
                rename_directory(moved, self.mission)
                result["renamed"] = True
                holding.set()
                time.sleep(0.6)
        thread = threading.Thread(target=write)
        thread.start()
        calls = []
        while answer["status"] == "running":
            before = job.run._pass.position if job.run._pass is not None else None
            started = time.monotonic()
            answer = self.proceed(job_id)
            calls.append((holding.is_set() and thread.is_alive(), before,
                          job.run._pass.position if job.run._pass is not None else None,
                          time.monotonic() - started))
        thread.join(JOIN_SECONDS)
        self.assertEqual(answer["status"], "done")
        self.assertTrue(result.get("renamed"))
        self.assertLess(result["waited"], 1.0)
        self.assertTrue(all(duration < 1.0 for _held, _before, _after, duration in calls), calls)
        blocked = [call for call in calls if call[0]]
        self.assertTrue(blocked, calls)
        self.assertTrue(all(before == after for _held, before, after, _duration in blocked), blocked)

    def jobs_of(self) -> dict:
        """Return the live jobs of the service."""
        return self.inspection._jobs._jobs



class ObserverSessionTests(unittest.TestCase):
    """The observer session binds its reader to the Inspect port and keeps the unsupported-lock refusal."""

    def test_sliced_reads_use_the_session_reader_and_refuse_an_unusable_lock(self) -> None:
        """A lock file that cannot be opened refuses Inspect as unsupported; cancel stays reader-exempt."""
        with tempfile.TemporaryDirectory(prefix="serverman_inspect_observer_") as temporary:
            root = Path(temporary) / "Manager"
            (root / "data" / "server-folders.lock").mkdir(parents=True)
            session = open_observer_session(root)
            try:
                self.assertIs(session.composition.mission_map.reader._reader, session.reader)
                with self.assertRaises(InstanceLockUnsupported):
                    session.call("inspect_mission_map", {"profile_id": "main", "plan": None,
                                                         "target": {"point": {"x": 1, "z": 1}}})
                with self.assertRaises(BridgeCallFailed) as caught:
                    session.call("cancel_mission_map_inspection", {"profile_id": "main", "job_id": "lost"})
                self.assertEqual(caught.exception.error["details"]["reason"], "inspection_unknown")
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
