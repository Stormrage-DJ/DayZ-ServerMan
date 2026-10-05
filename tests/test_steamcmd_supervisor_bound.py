"""The SteamCMD supervisor ends within its bound when a tree member keeps the output pipe open."""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows import steamcmd  # noqa: E402

# Longest time one supervision may take in these tests, in seconds
BOUND_SECONDS = 5.0


class EndlessOutput:
    """A console pipe that a leftover process holds open: it yields nothing until released."""

    def __init__(self) -> None:
        """Prepare the release signal."""
        self.released = threading.Event()

    def __iter__(self) -> EndlessOutput:
        """Return the iterator itself."""
        return self

    def __next__(self) -> str:
        """Block until the test releases the pipe, then end the stream."""
        self.released.wait(30.0)
        raise StopIteration


class SupervisorBoundTests(unittest.TestCase):
    """Cancellation escalates while the tree or the reader is still busy (Architect change 3)."""

    def setUp(self) -> None:
        """Build a root process that has exited while its pipe stays open."""
        self.output = EndlessOutput()
        self.process = Mock(pid=42, returncode=0, stdout=self.output)
        # The root process has already exited
        self.process.poll.return_value = 0
        self.tree = Mock()
        self.tree.evidence.process_id = 42

    def tearDown(self) -> None:
        """Release the pipe so the reader thread ends."""
        self.output.released.set()

    def supervise(self, cancellation) -> tuple[threading.Thread, list]:
        """Run the supervisor on a thread; return the thread and its result holder."""
        result: list = []

        def work() -> None:
            """Supervise the fake process with the patched tree and a short reader grace."""
            with patch.object(steamcmd, "OwnedProcessTree", return_value=self.tree), \
                    patch.object(steamcmd, "READER_GRACE_SECONDS", 0.2):
                result.append(steamcmd.supervise_owned(self.process, cancellation, lambda _e: None, []))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(BOUND_SECONDS)
        return thread, result

    def test_escalation_runs_after_the_root_exited(self) -> None:
        """A cancelled run whose root exited but whose pipe stays open ends, unproven."""
        self.tree.wait_absent.return_value = True
        thread, result = self.supervise(lambda: True)
        self.assertFalse(thread.is_alive(), "the supervisor did not end within its bound")
        self.tree.request_close.assert_called_once_with()
        # The tree is gone, but an open pipe has an unknown holder
        self.assertTrue(result[0].cancelled)
        self.assertFalse(result[0].termination_confirmed)

    def test_failed_escalation_after_root_exit_reports_unproven(self) -> None:
        """When the tree does not go away, close, terminate and kill run and the result is unproven."""
        self.tree.wait_absent.return_value = False
        thread, result = self.supervise(lambda: True)
        self.assertFalse(thread.is_alive(), "the supervisor did not end within its bound")
        self.tree.request_close.assert_called_once_with()
        self.tree.terminate_tree.assert_called_once_with()
        self.tree.kill_tree.assert_called_once_with()
        self.assertFalse(result[0].termination_confirmed)

    def test_late_cancellation_still_escalates(self) -> None:
        """A deadline that passes while only the pipe is open still ends the supervision."""
        calls = {"count": 0}

        def cancellation() -> bool:
            """Report the cancellation from the third poll on."""
            calls["count"] += 1
            return calls["count"] >= 3

        self.tree.wait_absent.return_value = True
        thread, result = self.supervise(cancellation)
        self.assertFalse(thread.is_alive(), "the supervisor did not end within its bound")
        self.assertTrue(result[0].cancelled)
        self.assertFalse(result[0].termination_confirmed)

    def test_an_exited_tree_with_a_finished_reader_is_confirmed(self) -> None:
        """The ordinary end is unchanged: output ends, the tree is gone, the result is confirmed."""
        self.process.stdout = iter(("line\n",))
        self.tree.wait_absent.return_value = True
        thread, result = self.supervise(lambda: False)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0].termination_confirmed)
        self.assertEqual(result[0].lines, ("line\n",))
        self.tree.request_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
