"""Run test modules in parallel worker processes and merge their results into one unittest result.

``tests.ci_run`` uses it for ``--jobs=N``. The names are modules or test IDs; the IDs of one module
form one unit of work, so the tests of one module never run at the same time. The slowest modules start first; their
durations come from ``ci_durations.json`` beside this file, and an unknown module counts as one second.
"""

from __future__ import annotations

import io
import json
import sys
import time
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Seconds that each slow module took in a local run; it orders the work, it does not limit it
DURATIONS = Path(__file__).with_name("ci_durations.json")
# Seconds assumed for a module without a recorded duration
UNKNOWN_SECONDS = 1.0


class TestRef:
    """A picklable stand-in for a finished test: the merged result needs only its identifier."""

    def __init__(self, identifier: str) -> None:
        """Keep the dotted test identifier."""
        self._identifier = identifier

    def id(self) -> str:  # noqa: A003 - the name of unittest.TestCase.id
        """Return the dotted test identifier."""
        return self._identifier

    def __str__(self) -> str:
        """Show the identifier as unittest does in its report."""
        return self._identifier


class MergedResult:
    """The parts of a unittest result that the annotations and the exit code read."""

    def __init__(self) -> None:
        """Start with no tests."""
        self.testsRun = 0
        self.errors: list[tuple[TestRef, str]] = []
        self.failures: list[tuple[TestRef, str]] = []
        self.skipped: list[tuple[TestRef, str]] = []
        self.unexpectedSuccesses: list[TestRef] = []

    def add(self, part: dict) -> None:
        """Add the result of one module."""
        self.testsRun += part["run"]
        for key in ("errors", "failures", "skipped"):
            getattr(self, key).extend((TestRef(test), text) for test, text in part[key])
        self.unexpectedSuccesses.extend(TestRef(test) for test in part["unexpected"])

    def wasSuccessful(self) -> bool:
        """Mirror unittest: no error, no failure and no unexpected success."""
        return not (self.errors or self.failures or self.unexpectedSuccesses)


def run_module(name: str, tests: list[str]) -> dict:
    """Run the named tests of one module in this worker and return the picklable result and report."""
    stream = io.StringIO()
    started = time.perf_counter()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames(tests)
    except Exception as error:  # noqa: BLE001 - a module that cannot load is one error of the run
        return {"name": name, "run": 0, "errors": [(name, f"{type(error).__name__}: {error}")], "failures": [],
                "skipped": [], "unexpected": [], "report": "", "seconds": 0.0}
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return {
        "name": name, "run": result.testsRun,
        "errors": [(test.id(), trace) for test, trace in result.errors],
        "failures": [(test.id(), trace) for test, trace in result.failures],
        "skipped": [(test.id(), reason) for test, reason in result.skipped],
        "unexpected": [test.id() for test in result.unexpectedSuccesses],
        "report": stream.getvalue(), "seconds": time.perf_counter() - started,
    }


def ordered(modules: list[str]) -> list[str]:
    """Return the modules with the slowest recorded ones first, so no long module starts last."""
    try:
        known = json.loads(DURATIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    return sorted(modules, key=lambda name: -float(known.get(name, UNKNOWN_SECONDS)))


def by_module(names: list[str]) -> dict[str, list[str]]:
    """Group modules and test IDs by their module, tests.test_x, keeping the given order."""
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(".".join(name.split(".")[:2]), []).append(name)
    return groups


def run(names: list[str], jobs: int) -> MergedResult:
    """Run the modules or test IDs on the given number of worker processes; print each report when its module ends."""
    merged = MergedResult()
    started = time.perf_counter()
    groups = by_module(names)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_module, module, groups[module]) for module in ordered(list(groups))]
        for future in as_completed(futures):
            part = future.result()
            merged.add(part)
            # A failing module prints its whole report; a passing one prints a single line
            if part["errors"] or part["failures"] or part["unexpected"]:
                print(f"=== {part['name']} ({part['seconds']:.1f}s)\n{part['report']}", flush=True)
            else:
                print(f"ok {part['name']}: {part['run']} tests, {part['seconds']:.1f}s", flush=True)
    print(f"Ran {merged.testsRun} tests in {time.perf_counter() - started:.1f}s on {jobs} workers", file=sys.stderr)
    return merged
