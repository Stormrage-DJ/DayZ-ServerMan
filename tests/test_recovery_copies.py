"""A committed restore deletes its recovery copy; a copy that cannot be deleted stays and is reported."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.repositories.recovery_copies import remove_recovery_copy  # noqa: E402


class RecoveryCopyRemovalTests(unittest.TestCase):
    """remove_recovery_copy deletes one operation folder below the recovery root and nothing else."""

    def setUp(self) -> None:
        """Create a recovery root with two operation folders."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_recovery_")
        self.root = Path(self.temporary.name) / "recovery"
        for operation in ("op1", "op2"):
            (self.root / operation / "mpmissions").mkdir(parents=True)
            (self.root / operation / "mpmissions" / "types.xml").write_text("<types/>", encoding="utf-8")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def test_only_the_named_operation_goes(self) -> None:
        """The named folder is deleted, the other stays; a missing folder counts as removed."""
        self.assertTrue(remove_recovery_copy(self.root, "op1"))
        self.assertFalse((self.root / "op1").exists())
        self.assertTrue((self.root / "op2" / "mpmissions" / "types.xml").is_file())
        self.assertTrue(remove_recovery_copy(self.root, "op1"))

    def test_names_outside_one_folder_are_refused(self) -> None:
        """An identifier that leaves the root or names a nested folder deletes nothing."""
        for operation in ("..", "op2/mpmissions", "", "."):
            with self.subTest(operation=operation):
                self.assertFalse(remove_recovery_copy(self.root, operation))
        self.assertTrue((self.root / "op2" / "mpmissions" / "types.xml").is_file())

    def test_a_copy_that_cannot_be_deleted_is_reported(self) -> None:
        """When deletion leaves the folder, the result is False and the folder stays."""
        with patch("dayz_serverman.repositories.recovery_copies.shutil.rmtree"):
            self.assertFalse(remove_recovery_copy(self.root, "op1"))
        self.assertTrue((self.root / "op1").is_dir())


if __name__ == "__main__":
    unittest.main()
