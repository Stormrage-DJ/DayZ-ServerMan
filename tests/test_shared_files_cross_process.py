"""S4 as a test: a reader in another process never fails replace_file and never sees a mixed file (A12)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dayz_serverman.adapters.windows.shared_files import replace_file
from dayz_serverman.observability.structured_log import StructuredLogger


# The source folder that the child process imports the package from
SOURCE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"
# Replaces per run (design 12.1, row 2.2)
REPLACES = 500
# Two payloads of different bytes; a whole read equals one of them
PAYLOADS = (b"A" * 65536, b"B" * 65536)
# The child reader: "held" keeps one handle and re-reads it; "loop" opens, reads and closes again and again
CHILD = r"""
import json, sys
from dayz_serverman.adapters.windows.shared_files import open_shared
mode, payloads = sys.argv[1], {b"A" * 65536, b"B" * 65536}
paths = sys.argv[2:]
reads = bad = 0
handles = [open_shared(path) for path in paths] if mode in ("held", "hold") else []
print("ready", flush=True)
# Read until the parent closes stdin; "hold" only keeps the handles open
import threading
done = threading.Event()
threading.Thread(target=lambda: (sys.stdin.read(), done.set()), daemon=True).start()
while not done.is_set():
    if mode == "hold":
        done.wait(0.05)
        continue
    if mode == "held":
        handles[0].seek(0)
        data = handles[0].read()
    else:
        with open_shared(paths[0]) as stream:
            data = stream.read()
    reads += 1
    bad += data not in payloads
print(json.dumps({"reads": reads, "bad": bad}), flush=True)
"""


@unittest.skipUnless(os.name == "nt", "the sharing modes of A12 are Windows behaviour")
class CrossProcessReplaceTests(unittest.TestCase):
    """A child process reads while the parent replaces (S4 on NTFS)."""

    def setUp(self) -> None:
        """Create the temporary folder of the shared file."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_s4_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def start_child(self, mode: str, *paths: Path) -> subprocess.Popen[str]:
        """Start the reader child and wait until its handles are open."""
        environment = dict(os.environ, PYTHONPATH=str(SOURCE))
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, mode, *map(str, paths)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True, env=environment,
        )
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        return child

    @staticmethod
    def stop_child(child: subprocess.Popen[str]) -> dict[str, int]:
        """Close the child's stdin and return its read counts."""
        output, _ = child.communicate("", timeout=30)
        return json.loads(output.strip().splitlines()[-1])

    def replace_many(self, target: Path) -> tuple[int, float]:
        """Call replace_file REPLACES times with alternating payloads; return failures and seconds."""
        failures = 0
        started = time.perf_counter()
        for number in range(REPLACES):
            staging = self.root / f".record.{number}.tmp"
            staging.write_bytes(PAYLOADS[(number + 1) % 2])
            try:
                replace_file(staging, target)
            except OSError:
                failures += 1
                staging.unlink(missing_ok=True)
        return failures, time.perf_counter() - started

    def run_mode(self, mode: str) -> None:
        """Run one reader mode against REPLACES replaces and check the S4 outcome."""
        target = self.root / "record.json"
        target.write_bytes(PAYLOADS[0])
        child = self.start_child(mode, target)
        failures, seconds = self.replace_many(target)
        counts = self.stop_child(child)
        print(f"\nS4 {mode}: {REPLACES} replaces in {seconds:.2f} s, {failures} failed; "
              f"reader {counts['reads']} reads, {counts['bad']} mixed", file=sys.stderr)
        self.assertEqual(failures, 0)
        self.assertEqual(counts["bad"], 0)
        self.assertGreater(counts["reads"], 0)
        self.assertIn(target.read_bytes(), PAYLOADS)

    def test_held_reader_never_fails_a_replace(self) -> None:
        """One handle held for the whole run: 0 failures, whole content only."""
        self.run_mode("held")

    def test_looping_reader_never_fails_a_replace(self) -> None:
        """Open, read and close in a loop: 0 failures, whole content only."""
        self.run_mode("loop")

    def test_log_rotation_with_a_reader_on_both_files(self) -> None:
        """A child holding manager.jsonl and .1 does not make emit raise, and the log still rotates."""
        path = self.root / "manager.jsonl"
        logger = StructuredLogger(path, maximum_bytes=1024)
        # Create both files, then let the child hold them while the log rotates many times
        while not path.with_name("manager.jsonl.1").exists():
            logger.emit("test.record", fields={"text": "x" * 200})
        child = self.start_child("hold", path, path.with_name("manager.jsonl.1"))
        for number in range(50):
            logger.emit("test.record", fields={"n": number, "text": "x" * 200})
        self.stop_child(child)
        last = path.with_name("manager.jsonl.1").read_bytes().splitlines()[-1]
        self.assertEqual(json.loads(last)["fields"]["n"] + 1, json.loads(path.read_bytes().splitlines()[0])["fields"]["n"])


if __name__ == "__main__":
    unittest.main()
