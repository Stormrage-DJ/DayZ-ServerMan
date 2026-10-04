"""Download observer and item phases: bounded, read-only, advisory, and stopped with the run."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import download_progress  # noqa: E402
from dayz_serverman.application.download_progress import (  # noqa: E402
    ABSENT, UNKNOWN, DownloadObserver, ItemProgress, SizeRecordingCheck, measure_folder,
)
from dayz_serverman.domain.update_check import RemoteFact, RemoteItemResult  # noqa: E402


def entry(workshop_id: str, phase: str, done=None, total=None) -> dict[str, object]:
    """Return one expected progress entry."""
    return {"workshop_id": workshop_id, "phase": phase, "done_bytes": done, "total_bytes": total}


class DownloadProgressTests(unittest.TestCase):
    """Design sections 11 and 13.6: item phases, folder samples, bounds and the stop."""

    def setUp(self) -> None:
        """Create a Workshop library with an empty downloads folder beside the content folder."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        library = Path(self.temporary.name) / "steamapps" / "workshop"
        self.root = library / "content" / "221100"
        self.downloads = library / "downloads" / "221100"
        self.root.mkdir(parents=True)
        self.downloads.mkdir(parents=True)
        self.published: list[list[dict[str, object]]] = []
        self.progress = ItemProgress(self.published.append)

    def grow(self, workshop_id: str, name: str, size: int) -> None:
        """Write one file of the given size into the download folder of an item."""
        path = self.downloads / workshop_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    def test_measure_sums_file_sizes_and_reports_an_absent_folder(self) -> None:
        """A growing folder gives a growing sum over nested files; no folder is ABSENT."""
        self.assertEqual(measure_folder(self.downloads / "111"), ABSENT)
        self.grow("111", "a.bin", 10)
        self.assertEqual(measure_folder(self.downloads / "111"), 10)
        self.grow("111", "nested/b.bin", 32)
        self.assertEqual(measure_folder(self.downloads / "111"), 42)

    def test_measure_is_unknown_above_a_bound_or_after_an_error(self) -> None:
        """Too many entries, too much time and a read error give UNKNOWN, never an exception."""
        for index in range(4):
            self.grow("111", f"{index}.bin", 1)
        with patch.object(download_progress, "MAX_ENTRIES", 3):
            self.assertEqual(measure_folder(self.downloads / "111"), UNKNOWN)
        ticks = iter(range(0, 100))
        self.assertEqual(measure_folder(self.downloads / "111", lambda: float(next(ticks))), UNKNOWN)
        with patch.object(download_progress.os, "scandir", side_effect=PermissionError("denied")):
            self.assertEqual(measure_folder(self.downloads / "111"), UNKNOWN)

    def junction(self, link: Path, target: Path) -> None:
        """Create a directory junction, or skip the test when this machine cannot."""
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, check=False) if os.name == "nt" else None
        if made is None or made.returncode != 0 or not link.is_dir():
            self.skipTest("a directory junction cannot be created here")
        # Remove the junction itself before the temporary folder is cleaned
        self.addCleanup(lambda: os.rmdir(link) if os.path.lexists(link) else None)

    def test_measure_follows_no_junction(self) -> None:
        """QF-019: a junction inside an item folder and an item folder that is a junction are not measured."""
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "big.bin").write_bytes(b"x" * 5000)
        self.grow("111", "a.bin", 10)
        self.junction(self.downloads / "111" / "link", outside)
        self.assertEqual(measure_folder(self.downloads / "111"), 10)
        self.junction(self.downloads / "222", outside)
        self.assertEqual(measure_folder(self.downloads / "222"), ABSENT)
        # The observer publishes nothing for the item whose folder is a junction
        self.progress.queued(("111", "222"), {})
        DownloadObserver(self.root, self.progress, ("111", "222")).sample_once()
        self.assertEqual(self.published[-1], [entry("111", "downloading", 10), entry("222", "queued")])
        self.assertEqual((outside / "big.bin").stat().st_size, 5000)

    def test_item_phases_follow_the_rows_of_the_design(self) -> None:
        """Queued, downloading, folder gone, verifying, done and failed, in publication order."""
        progress = self.progress
        progress.queued(("111", "222"), {"111": 100})
        self.assertEqual(self.published[-1], [entry("111", "queued", None, 100), entry("222", "queued")])
        # A folder that was never seen changes nothing, and neither does an unknown first sample
        count = len(self.published)
        progress.sample("111", ABSENT)
        progress.sample("111", UNKNOWN)
        progress.sample("999", 5)
        self.assertEqual(len(self.published), count)
        progress.sample("111", 40)
        self.assertEqual(self.published[-1][0], entry("111", "downloading", 40, 100))
        # The count is limited to the size; without a size the sample stands
        progress.sample("111", 150)
        progress.sample("222", 7)
        self.assertEqual(self.published[-1], [entry("111", "downloading", 100, 100),
                                              entry("222", "downloading", 7, None)])
        progress.sample("222", UNKNOWN)
        self.assertEqual(self.published[-1][1], entry("222", "downloading", None, None))
        # The folder was seen and is gone: the item is complete
        progress.sample("111", 30)
        progress.sample("111", ABSENT)
        self.assertEqual(self.published[-1][0], entry("111", "downloading", 100, 100))
        # The proof step: an unsent item gets its entry here; later samples are ignored
        progress.verifying("111")
        progress.verifying("333")
        progress.sample("111", 10)
        self.assertEqual(self.published[-1], [entry("111", "verifying"), entry("222", "downloading"),
                                              entry("333", "verifying")])
        progress.finished("111", True)
        progress.finished("333", False)
        progress.fail_unfinished()
        self.assertEqual(self.published[-1], [entry("111", "done"), entry("222", "failed"),
                                              entry("333", "failed")])

    def test_publication_failure_is_swallowed(self) -> None:
        """Progress is advisory: a failing publication never reaches the operation."""
        def failing(_items) -> None:
            """Stand in for a publication that fails."""
            raise ValueError("synthetic publication failure")

        progress = ItemProgress(failing)
        progress.queued(("111",), {})
        progress.sample("111", 5)
        progress.finished("111", True)

    def test_size_recording_check_keeps_the_sizes_of_answered_items(self) -> None:
        """The wrapper passes the facts through and remembers the file size of each OK fact."""
        facts = {"111": RemoteFact(RemoteItemResult.OK, 1, 50, "t"),
                 "222": RemoteFact(RemoteItemResult.OK, 1, None, "t"),
                 "333": RemoteFact(RemoteItemResult.NOT_FOUND, None, 9, "t")}
        check = SizeRecordingCheck(SimpleNamespace(check_now=lambda ids: facts))
        self.assertIs(check.check_now(("111", "222", "333")), facts)
        self.assertEqual(check.sizes, {"111": 50, "222": None})

    def test_observer_follows_a_growing_folder_and_stops_with_the_run(self) -> None:
        """The worker samples the folder beside `content` and ends within two seconds."""
        self.progress.queued(("111", "222"), {"111": 1000})
        with DownloadObserver(self.root, self.progress, ("111", "222"), interval=0.02) as observer:
            self.grow("111", "a.bin", 100)
            self.wait_for(entry("111", "downloading", 100, 1000))
            self.grow("111", "b.bin", 300)
            self.wait_for(entry("111", "downloading", 400, 1000))
            # The item without a folder keeps waiting, without byte counts
            self.assertEqual(self.published[-1][1], entry("222", "queued"))
            started = time.monotonic()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(observer._worker.is_alive())
        # The observer reads only: the folder holds what the test wrote
        self.assertEqual(sorted(path.name for path in (self.downloads / "111").iterdir()),
                         ["a.bin", "b.bin"])

    def test_observer_bounds_the_items_and_survives_errors(self) -> None:
        """At most 200 items are sampled; an error in a sample never leaves the worker."""
        ids = tuple(str(1000 + index) for index in range(205))
        sampled: list[str] = []

        def failing_sample(workshop_id: str, _value: object) -> None:
            """Record the item, then fail."""
            sampled.append(workshop_id)
            raise RuntimeError("synthetic sample failure")

        with DownloadObserver(self.root, SimpleNamespace(sample=failing_sample), ids, interval=0.01) as observer:
            self.assertEqual(len(observer._ids), 200)
            for _ in range(200):
                if len(sampled) >= 2:
                    break
                time.sleep(0.01)
        self.assertGreaterEqual(len(sampled), 2)
        self.assertFalse(observer._worker.is_alive())
        # A stopped observer takes no further sample
        observer.sample_once()
        self.assertEqual(set(sampled), {"1000"})

    def wait_for(self, expected: dict[str, object]) -> None:
        """Wait until the first entry of the last publication is the expected one."""
        for _ in range(300):
            if self.published and self.published[-1][0] == expected:
                return
            time.sleep(0.01)
        self.fail(f"not published: {expected}; last: {self.published[-1]}")


if __name__ == "__main__":
    unittest.main()
