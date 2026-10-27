"""Choose the tests that CI runs: the riskiest tests that fit a time budget.

Every test stays in the repository and runs locally; CI runs only the tests in the
selection list ``tests/ci_unit.txt``, which this module writes. CI runs no Edge test.

Risk is impact x likelihood, each from 1 to 5:

- Impact: what an operator loses when the tested behaviour is wrong, from the words in
  the module, class, and test names. 5 guards files against loss (restore, backup,
  recovery, crash, commit, lock, delete); 4 guards what is written or whether the server
  runs (apply, publish, profile, configuration, mods update, loot); 3 covers the
  command line, the bridge, status, and Steam; 2 is the rest. A test of how something
  looks or reads (colour, layout, wording, label) is 1, whatever its module.
- Likelihood: how often the tested code changed. It is the mean of two git quintiles:
  the commits to the source files that the test module imports, and the commits to the
  test module itself. A commit whose subject says "fix" counts twice.

The selection keeps whole risk levels from the top while they fit the budget. In the
first level that does not fit, it keeps the cheapest tests that still fit.

Measure the durations with the test source path set, then write a list::

    python -m tests.ci_risk time tests.test_a tests.test_b > unit.times
    python -m tests.ci_risk select unit.times 200 tests/ci_unit.txt [report.tsv]

The budget is in seconds of one local serial run. Before a push, run the tests that
the change touches, in or out of the lists, and write the lists again when a test is
added or its names change::

    python -m unittest $(python -m tests.ci_risk changed [base])
"""

from __future__ import annotations

import ast
import collections
import io
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
SOURCE = REPO / "runnable" / "src" / "python"

# A keyword matches a name word that starts with it; the highest level wins
IMPACT = [
    (5, "restore backup rollback recover crash fault interrupt commit journal atomic lock delet purge "
        "corrupt damage integrity migrat swap remnant torn partial"),
    (4, "apply publish publication adopt transfer provision retain incorporate staging guard ownership "
        "types economy configuration config mods update profile cfg pars serial xml edit valid plan loot "
        "abundance merge ledger record store hash verify refus prestart readiness starter mapgroup "
        "evaluation capacity medical steamcmd workshop build shared user operation instance session "
        "lifecycle stop start shutdown"),
    (3, "cli bridge status steam a2s players query log schedul settings catalog inspect preview composition "
        "coordinator service domain geometry terrain image png providers families exit"),
]
# Words of a test that checks how something looks or reads, not what happens to the files
COSMETIC = ("wording colour color contrast style css font footer text texts label tooltip icon sentence "
            "sentences pins palette theme looks muted wrap spacing width height aria caption hint title "
            "says phrase readout sizes geometry layout fit")
# Whole-codebase static checks: one test covers every source file, so they always run
PINNED = {"test_undefined_names"}


def _words(name: str) -> list[str]:
    """Split a dotted or snake-case name into lower-case words."""
    return [word for word in re.split(r"[_.]+", name.lower()) if word]


def _hits(words: list[str], keys: str) -> bool:
    """Tell whether a word starts with one of the space-separated keywords."""
    return any(word.startswith(key) for word in words for key in keys.split())


def impact(test_id: str) -> int:
    """Return the impact of one test from its module, class, and method names."""
    _, module, *rest = test_id.split(".")
    if module in PINNED:
        return 5
    if _hits(_words(module), COSMETIC) or _hits(_words(rest[-1]), COSMETIC):
        return 1
    words = _words(module.removeprefix("test_")) + _words(".".join(rest).removeprefix("test_"))
    return next((level for level, keys in IMPACT if _hits(words, keys)), 2)


def churn() -> dict[str, float]:
    """Return the commits per source and test file; a fix commit counts twice."""
    log = subprocess.run(["git", "-C", str(REPO), "log", "--name-only", "--format=@@%s"],
                         capture_output=True, text=True, check=True).stdout
    weight, score = 1.0, collections.Counter()
    for line in log.splitlines():
        if line.startswith("@@"):
            weight = 2.0 if re.search(r"\bfix", line, re.IGNORECASE) else 1.0
        elif line.startswith(("runnable/src/", "tests/")):
            score[line] += weight
    return score


def quintiles(values: dict[str, float]) -> dict[str, int]:
    """Map each value to its quintile, 1 for the lowest fifth and 5 for the highest."""
    ordered = sorted(values.values())
    return {key: min(5, 1 + int(sum(v <= value for v in ordered) / len(ordered) * 5))
            for key, value in values.items()}


def imported_sources(module_path: Path, seen: set[str] | None = None) -> set[str]:
    """Return the application files that a test module imports, also through tests.* support modules."""
    seen = set() if seen is None else seen
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names += [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
    found: set[str] = set()
    for name in names:
        if name.startswith("dayz_serverman"):
            path = (SOURCE / Path(*name.split("."))).with_suffix(".py")
            if path.exists():
                found.add(path.relative_to(REPO).as_posix())
        elif name.startswith("tests.") and name not in seen:
            seen.add(name)
            support = TESTS / f"{name.split('.')[1]}.py"
            if support.exists() and support != module_path:
                found |= imported_sources(support, seen)
    return found


def frontend_sources(module: str, files: list[str]) -> set[str]:
    """Return the frontend files that share a name word with an interface test module."""
    words = set(_words(module.removeprefix("test_"))) - {"mission", "map", "editor", "msedge", "dynamic", "static"}
    return {name for name in files
            if name.startswith("runnable/src/frontend/") and words & set(_words(Path(name).stem))}


def read_times(path: str) -> dict[str, float]:
    """Read the lines '  1.2s tests.test_x.Class.test_y' that the time command prints."""
    times = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*([\d.]+)s (tests\.\S+)$", line)
        if match:
            times[match.group(2)] = float(match.group(1))
    return times


def rank(times: dict[str, float]) -> list[dict]:
    """Score each timed test and return the rows from the highest risk to the lowest."""
    files = churn()
    likely = quintiles(files)
    per_module: dict[str, int] = {}
    rows = []
    for test_id, seconds in times.items():
        module = test_id.split(".")[1]
        if module not in per_module:
            sources = imported_sources(TESTS / f"{module}.py") | frontend_sources(module, list(files))
            source = sum(likely.get(name, 1) for name in sources) / len(sources) if sources else 3
            per_module[module] = 5 if module in PINNED else round((source + likely.get(f"tests/{module}.py", 1)) / 2)
        level = impact(test_id)
        rows.append({"id": test_id, "seconds": seconds, "impact": level,
                     "likelihood": per_module[module], "risk": level * per_module[module]})
    return sorted(rows, key=lambda row: (-row["risk"], row["seconds"]))


def select(rows: list[dict], budget: float) -> set[str]:
    """Keep whole risk levels from the top; in the first level that does not fit, keep its cheapest tests."""
    used, keep = 0.0, set()
    for level in sorted({row["risk"] for row in rows}, reverse=True):
        group = sorted((row for row in rows if row["risk"] == level), key=lambda row: row["seconds"])
        if used + sum(row["seconds"] for row in group) <= budget:
            used += sum(row["seconds"] for row in group)
            keep |= {row["id"] for row in group}
            continue
        for row in group:
            if used + row["seconds"] <= budget:
                used += row["seconds"]
                keep.add(row["id"])
        break
    return keep


def changed_modules(base: str) -> list[str]:
    """Return the test modules that a change since base touches: changed tests and tests of changed files."""
    names = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", base],
                           capture_output=True, text=True, check=True).stdout.split()
    names += subprocess.run(["git", "-C", str(REPO), "ls-files", "--others", "--exclude-standard"],
                            capture_output=True, text=True, check=True).stdout.split()
    changed = set(names)
    modules = []
    for path in sorted(TESTS.glob("test_*.py")):
        relative = path.relative_to(REPO).as_posix()
        if (relative in changed or imported_sources(path) & changed
                or frontend_sources(path.stem, sorted(changed))):
            modules.append(f"tests.{path.stem}")
    return modules


class _TimedResult(unittest.TextTestResult):
    """Print the duration of each test when it ends."""

    def startTest(self, test: unittest.TestCase) -> None:
        """Note the start time."""
        self._started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        """Print the seconds and the test ID."""
        print(f"{time.perf_counter() - self._started:6.1f}s {test.id()}", flush=True)
        super().stopTest(test)


def main(argv: list[str]) -> int:
    """Run the time or the select command."""
    if argv[:1] == ["time"]:
        for name in argv[1:]:
            suite = unittest.defaultTestLoader.loadTestsFromName(name)
            unittest.TextTestRunner(stream=io.StringIO(), resultclass=_TimedResult).run(suite)
        return 0
    if argv[:1] == ["select"] and len(argv) in (4, 5):
        times, budget, output = read_times(argv[1]), float(argv[2]), Path(argv[3])
        rows = rank(times)
        keep = select(rows, budget)
        used = sum(row["seconds"] for row in rows if row["id"] in keep)
        lowest = min((row["risk"] for row in rows if row["id"] in keep), default=0)
        header = (f"# Written by python -m tests.ci_risk select; see tests/ci_risk.py for the rule.\n"
                  f"# {len(keep)} of {len(rows)} tests, risk {lowest} and above, "
                  f"{used:.0f} of {sum(times.values()):.0f} seconds of a local serial run.\n")
        output.write_text(header + "".join(f"{row['id']}\n" for row in sorted(rows, key=lambda r: r["id"])
                                           if row["id"] in keep), encoding="utf-8")
        if len(argv) == 5:
            with open(argv[4], "w", encoding="utf-8") as report:
                report.write("ci\trisk\timpact\tlikelihood\tseconds\ttest\n")
                for row in rows:
                    report.write(f"{'run' if row['id'] in keep else 'skip'}\t{row['risk']}\t{row['impact']}\t"
                                 f"{row['likelihood']}\t{row['seconds']}\t{row['id']}\n")
        print(header, end="")
        return 0
    if argv[:1] == ["changed"] and len(argv) <= 2:
        print("\n".join(changed_modules(argv[1] if len(argv) == 2 else "main")))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
