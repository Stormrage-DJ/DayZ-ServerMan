"""Run unittest modules for CI and report the outcome as GitHub workflow annotations.

Use it exactly like ``python -m unittest``: ``python -m tests.ci_run tests.test_a tests.test_b``.
The console output and the exit code are those of unittest. After the run, the
module prints workflow commands: one error per failed or erroring test (with its
last exception line) and one notice with the counts and the skipped tests.
GitHub shows annotations on the run page and serves them from the public
check-run annotations API, so a failure can be read without the job log.
"""

from __future__ import annotations

import re
import sys
import unittest

# An unindented exception line, for example "AssertionError: ..." or "OSError"
EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Failure|Warning)\b")

# GitHub keeps at most 10 error annotations per step; the last slot reports the rest
ERROR_SLOTS = 10
# Skipped tests listed in the notice before the list is shortened
SKIP_LIST_LIMIT = 30


def _escape_data(text: str) -> str:
    """Escape a workflow-command message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    """Escape a workflow-command property value such as the title."""
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def _test_id(test: unittest.TestCase) -> str:
    """Return the dotted identifier of a test, or its description for module-level errors."""
    identifier = getattr(test, "id", None)
    return identifier() if callable(identifier) else str(test)


def _exception_text(trace: str) -> str:
    """Return the final exception line of a traceback and up to four lines that follow it."""
    lines = [line.rstrip() for line in trace.splitlines() if line.strip()]
    if not lines:
        return "no traceback"
    # The exception line starts unindented with a class name such as AssertionError:
    start = max((index for index, line in enumerate(lines)
                 if EXCEPTION_LINE.match(line)), default=len(lines) - 1)
    return "\n".join(lines[start:start + 5])[:800]


def annotations(label: str, result: unittest.TestResult) -> list[str]:
    """Return the workflow commands that describe one finished unittest result."""
    commands: list[str] = []
    # One error per problem, in the order unittest reports them
    problems = [("ERROR", test, trace) for test, trace in result.errors]
    problems += [("FAIL", test, trace) for test, trace in result.failures]
    problems += [("UNEXPECTED SUCCESS", test, "expected failure passed")
                 for test in result.unexpectedSuccesses]
    shown = problems if len(problems) <= ERROR_SLOTS else problems[:ERROR_SLOTS - 1]
    for kind, test, trace in shown:
        title = _escape_property(f"{label} {kind}: {_test_id(test)}")
        commands.append(f"::error title={title}::{_escape_data(_exception_text(trace))}")
    if len(problems) > len(shown):
        hidden = len(problems) - len(shown)
        commands.append(f"::error title={_escape_property(label)}::"
                        f"{hidden} more failed or erroring tests; see the job log.")
    # One notice with the counts and the skipped tests, so a green run is checkable too
    skipped = sorted(f"{_test_id(test)} ({reason})" for test, reason in result.skipped)
    status = "OK" if result.wasSuccessful() else "FAILED"
    summary = (f"{status}: ran {result.testsRun}, failures {len(result.failures)}, "
               f"errors {len(result.errors)}, skipped {len(skipped)}")
    listed = skipped[:SKIP_LIST_LIMIT]
    if len(skipped) > len(listed):
        listed.append(f"... and {len(skipped) - len(listed)} more")
    body = summary + ("\nSkipped:\n" + "\n".join(listed) if listed else "")
    commands.append(f"::notice title={_escape_property(label + ' summary')}::{_escape_data(body)}")
    return commands


def main(argv: list[str]) -> int:
    """Run the named test modules, print the annotations and return unittest's exit code."""
    label = "Unit tests"
    # An optional leading --label=<text> names the job in the annotation titles
    if argv and argv[0].startswith("--label="):
        label, argv = argv[0].split("=", 1)[1] or label, argv[1:]
    # A module that cannot even be loaded (for example a syntax error) stops unittest itself
    try:
        program = unittest.main(module=None, argv=["python -m unittest", *argv], exit=False)
    except Exception as error:
        print(f"::error title={_escape_property(label + ' could not load the tests')}::"
              f"{_escape_data(f'{type(error).__name__}: {error}')}", flush=True)
        raise
    result = program.result
    # Flush unittest's stream first so the commands follow the report in the log
    sys.stderr.flush()
    for command in annotations(label, result):
        print(command, flush=True)
    # Mirror unittest: 0 on success, 5 when nothing ran, 1 otherwise
    if result.testsRun == 0 and not result.skipped:
        return 5
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
