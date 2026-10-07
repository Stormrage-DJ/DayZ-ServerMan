"""S6 regression: a reader inside the swapped folders never fails a swap or its rollback under the writer side."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folder_lock_fixtures import finish, kill, spawn_reader, writer_for  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile  # noqa: E402
from dayz_serverman.adapters.windows.shared_files import rename_directory  # noqa: E402

# Swap rounds of the regression; each round publishes and rolls back both folders
ROUNDS = 40


@unittest.skipUnless(os.name == "nt", "the folder rename conflict exists on Windows")
class FolderSwapUnderReaderTests(unittest.TestCase):
    """A child reads in loops inside an @Mod folder and a generated profile folder while the parent swaps."""

    def setUp(self) -> None:
        """Create the lock file, a DayZ root with both folders and a staged copy of each."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_swaps_")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.lock_path = base / "data" / "server-folders.lock"
        self.lock_path.parent.mkdir()
        FolderLockFile.create(self.lock_path).close()
        dayz = base / "DayZ"
        self.targets = (dayz / "@Mod", dayz / "serverman" / "main")
        for target in self.targets:
            (target / "addons").mkdir(parents=True)
            (target / "addons" / "data.pbo").write_bytes(b"old" * 1000)
            (target / "config.cfg").write_text("value = 1;\n", encoding="utf-8")
            stage = target.with_name(f".{target.name}.stage")
            shutil.copytree(target, stage)
            (stage / "config.cfg").write_text("value = 2;\n", encoding="utf-8")

    def swap_round(self) -> None:
        """Publish each staged folder over its target, then roll both back, as publication and compensation do."""
        for target in self.targets:
            stage, recovery = target.with_name(f".{target.name}.stage"), target.with_name(f".{target.name}.recovery")
            # Publication: prior target aside, stage into place
            rename_directory(target, recovery)
            rename_directory(stage, target)
        for target in reversed(self.targets):
            stage, recovery = target.with_name(f".{target.name}.stage"), target.with_name(f".{target.name}.recovery")
            # Rollback: the output back to its stage, the prior target back into place
            rename_directory(target, stage)
            rename_directory(recovery, target)

    def test_swaps_and_rollbacks_never_fail_while_a_reader_loops(self) -> None:
        """0 failures on both sides; the reader really read during the rounds."""
        child = spawn_reader(self.lock_path, "folders", *map(str, self.targets))
        self.addCleanup(kill, child)
        writer = writer_for(self.lock_path)
        self.addCleanup(writer.close)
        failures = 0
        for _ in range(ROUNDS):
            try:
                with writer.exclusive(5):
                    self.swap_round()
            except OSError:
                failures += 1
            # Give the reader a moment between rounds, as live operations do
            time.sleep(0.01)
        reads, child_failures = map(int, finish(child).split())
        self.assertEqual((failures, child_failures), (0, 0))
        self.assertGreater(reads, 0)
        # The rollbacks restored the prior content
        for target in self.targets:
            self.assertEqual((target / "config.cfg").read_text(encoding="utf-8"), "value = 1;\n")


if __name__ == "__main__":
    unittest.main()
