"""Reader-exempt observer calls (3.4, QF-2): each opens, lists and stats nothing inside a swapped folder."""

from __future__ import annotations

import builtins
import os
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import PROFILE_ID, populate, protected_folders, read_calls  # noqa: E402
from dayz_serverman.adapters.windows import shared_files  # noqa: E402
from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS, READER_EXEMPT_METHODS  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.composition_model import SessionMode  # noqa: E402

# The functions through which a call can open, list or stat a path
RECORDED = (
    (os, "stat"), (os, "lstat"), (os, "scandir"), (os, "listdir"), (os.path, "isdir"), (os.path, "isfile"),
    (os.path, "exists"), (os.path, "realpath"), (builtins, "open"), (shared_files, "_create_file"),
)


@contextmanager
def recorded_paths() -> Iterator[list[str]]:
    """Record the path argument of every open, listing and stat while the block runs."""
    seen: list[str] = []

    def recorder(real: Callable) -> Callable:
        """Wrap one function so that its first argument is recorded when it names a path."""
        def wrapped(*arguments, **keywords):
            """Record, then call the real function."""
            if arguments and isinstance(arguments[0], (str, os.PathLike)):
                seen.append(os.fspath(arguments[0]))
            return real(*arguments, **keywords)
        return wrapped
    with ExitStack() as stack:
        for owner, name in RECORDED:
            stack.enter_context(patch.object(owner, name, recorder(getattr(owner, name))))
        yield seen


def inside(path: str, folder: str) -> bool:
    """Report whether a canonical path is the folder or below it."""
    return path == folder or path.startswith(folder + os.sep)


class ObserverExemptionTests(unittest.TestCase):
    """Every method of READER_EXEMPT_METHODS on a fixture with mods, keys, a generated folder and a mission."""

    @classmethod
    def setUpClass(cls) -> None:
        """Populate one manager root and one DayZ root."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_exempt_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))
        cls.protected = [os.path.normcase(os.path.realpath(folder)) for folder in protected_folders(cls.dayz)]

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove both roots."""
        cls.temporary.cleanup()

    def test_exempt_methods_are_observer_reads(self) -> None:
        """Each exemption names an observer read method."""
        self.assertLessEqual(READER_EXEMPT_METHODS, OBSERVER_READ_METHODS)

    def test_each_exempt_call_stays_outside_the_swapped_folders(self) -> None:
        """No recorded path lies inside an @Mod folder, keys, serverman\\<id> or the mission folder."""
        composition = build_composition(self.manager, mode=SessionMode.OBSERVER)
        calls = [(method, parameters) for method, parameters in read_calls(self.dayz, None, self.dayz)
                 if method in READER_EXEMPT_METHODS]
        self.assertEqual({method for method, _ in calls}, READER_EXEMPT_METHODS)
        for method, parameters in calls:
            with self.subTest(method=method, parameters=parameters):
                with recorded_paths() as seen:
                    answer = composition.bridge.dispatch(
                        {"contract_version": 1, "request_id": "exempt", "method": method, "parameters": parameters})
                self.assertTrue(answer["success"], answer)
                canonical = {os.path.normcase(os.path.realpath(path)) for path in seen}
                offending = sorted(path for path in canonical for folder in self.protected if inside(path, folder))
                self.assertEqual(offending, [])

    def test_the_recorder_sees_a_read_under_the_reader_side(self) -> None:
        """Control: a reader-side call (the mission configuration) is recorded inside the mission folder."""
        composition = build_composition(self.manager, mode=SessionMode.OBSERVER)
        with recorded_paths() as seen:
            composition.bridge.dispatch({"contract_version": 1, "request_id": "control",
                                         "method": "load_mission_configuration",
                                         "parameters": {"profile_id": PROFILE_ID, "target": "economy"}})
        canonical = {os.path.normcase(os.path.realpath(path)) for path in seen}
        self.assertTrue(any(inside(path, self.protected[-1]) for path in canonical))


if __name__ == "__main__":
    unittest.main()
