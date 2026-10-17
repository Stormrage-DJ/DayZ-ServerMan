"""Leaves 10.5, 10.32 and 10.36, D13 and AR-4: the INVALID_REQUEST reason table and the kind table of `record_exit`.

Also the proof that every existing command keeps its exit code (PO-9), and the waiter line of the map lane
kinds (PO-8).
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from test_cli_exit_codes import TABLE, failed  # noqa: E402

from dayz_serverman.application.activity_wording import KIND_TEXTS  # noqa: E402
from dayz_serverman.cli.exit_codes import (  # noqa: E402
    FAILED, FIXED_CODES, INVALID_REQUEST_REASONS, KIND_REFUSAL_CLASSES, NOT_CONFIRMED, PRE_CHANGE_PHASES,
    REFUSAL_CODES, REFUSED, dispatch_exit, pre_change, record_exit,
)
from dayz_serverman.cli.waiter import LABEL_ONLY_KINDS, progress_text  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
# The map lane kinds (D13); leaves 10.5, 10.32 and 10.36 add all of them to both tables
MAP_KINDS = frozenset(("APPLY_MISSION_MAP", "RESTORE_MISSION_MAP_ORIGINAL", "RESTORE_MISSION_MAP_PREVIOUS",
                       "ADOPT_MISSION_MAP"))
LEAF_KINDS = frozenset(("APPLY_MISSION_MAP", "RESTORE_MISSION_MAP_ORIGINAL", "RESTORE_MISSION_MAP_PREVIOUS",
                        "ADOPT_MISSION_MAP"))
# The D13 reasons of INVALID_REQUEST with their exit code
D13_REASONS = {**dict.fromkeys(("no_saved_plan", "preview_refused", "candidate_refused", "incorporation_refused",
                                "no_original", "no_previous_state", "target_unprojectable", "no_valid_plan",
                                "plan_limit", "nothing_to_adopt", "adoption_refused"), 3),
               "confirmation_required": 4}


def old_record_exit(record: dict) -> int:
    """Return the exit code by the rule before leaf 10.5: a refusal keeps its REFUSAL_CODES class pre-change."""
    if record.get("state") != "FAILED":
        return record_exit(record)
    code = str(record["terminal_error"]["code"])
    if code in FIXED_CODES:
        return FIXED_CODES[code]
    if code in REFUSAL_CODES:
        return REFUSAL_CODES[code] if pre_change(record) else FAILED
    return FAILED


class ReasonTableTests(unittest.TestCase):
    """dispatch_exit: one table for INVALID_REQUEST; a state reason exits 3, any other reason keeps exit 2."""

    def test_the_d13_reasons(self) -> None:
        """Each reason of the D13 table has its exit code; the code stays INVALID_REQUEST."""
        self.assertEqual(INVALID_REQUEST_REASONS, D13_REASONS)
        for reason, expected in D13_REASONS.items():
            with self.subTest(reason=reason):
                self.assertEqual(dispatch_exit({"code": "INVALID_REQUEST", "details": {"reason": reason}}), expected)
        self.assertEqual((REFUSED, NOT_CONFIRMED), (3, 4))

    def test_other_reasons_and_codes_keep_their_exit(self) -> None:
        """Without a D13 reason every code keeps its 6.4 row, also with any other reason in its details."""
        for code, (expected, _before, _after) in TABLE.items():
            for details in (None, {}, {"reason": "invalid_document"}, {"reason": "invalid_target"}, {"reason": 3}):
                with self.subTest(code=code, details=details):
                    error = {"code": code, "message": "host text", **({} if details is None else {"details": details})}
                    self.assertEqual(dispatch_exit(error), expected)
        # A D13 reason under another code changes nothing
        for code, (expected, _before, _after) in TABLE.items():
            if code != "INVALID_REQUEST":
                self.assertEqual(dispatch_exit({"code": code, "details": {"reason": "no_saved_plan"}}), expected)

    def test_only_map_modules_give_the_d13_reasons(self) -> None:
        """No existing command sees a D13 reason: each one is a literal only in a mission map module."""
        found: dict[str, set[str]] = {}
        for path in PACKAGE.rglob("*.py"):
            if "cli" in path.relative_to(PACKAGE).parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and node.value in D13_REASONS:
                    found.setdefault(str(node.value), set()).add(path.name)
        self.assertTrue(found)
        for reason, files in found.items():
            with self.subTest(reason=reason):
                self.assertTrue(all(name.startswith("mission_map_") for name in files), files)


class KindTableTests(unittest.TestCase):
    """record_exit: the map kinds report no checkpoint; their lane INVALID_REQUEST exits 3 (AR-4)."""

    def test_the_kinds_of_this_leaf(self) -> None:
        """Each map lane kind is pre-change without a phase; only INVALID_REQUEST gets another class."""
        self.assertEqual(set(KIND_REFUSAL_CLASSES), LEAF_KINDS)
        self.assertEqual(LEAF_KINDS & set(PRE_CHANGE_PHASES), LEAF_KINDS)
        for kind in LEAF_KINDS:
            self.assertEqual(PRE_CHANGE_PHASES[kind], frozenset((None,)))
            self.assertEqual(KIND_REFUSAL_CLASSES[kind], {"INVALID_REQUEST": REFUSED})
            self.assertEqual(record_exit(failed(kind, "INVALID_REQUEST", phase=None)), 3)
            self.assertEqual(record_exit(failed(kind, "INVALID_REQUEST", phase="loaded")), 1)
            for code in ("REVISION_CONFLICT", "CONTROL_CONFLICT", "UNSUPPORTED_SNAPSHOT_CONTENT"):
                self.assertEqual(record_exit(failed(kind, code, phase=None)), 3)
            for code in ("NOT_FOUND", "PATH_INVALID"):
                self.assertEqual(record_exit(failed(kind, code, phase=None)), 2)
            self.assertEqual(record_exit(failed(kind, "PUBLICATION_FAILED", phase=None)), 1)
            self.assertEqual(record_exit(failed(kind, "RECOVERY_REQUIRED") | {"state": "RECOVERY_REQUIRED"}), 6)
        self.assertLessEqual(set(KIND_REFUSAL_CLASSES), MAP_KINDS)

    def test_every_existing_kind_keeps_its_exit_code(self) -> None:
        """PO-9: each kind of an existing command, with each code before and after a first change, as before."""
        kinds = (set(KIND_TEXTS) | set(PRE_CHANGE_PHASES) | {"START_SERVER", "RESTART_SERVER", "DELETE_PROFILE",
                                                                 "SAVE_PROFILE", "UNKNOWN_KIND"}) - MAP_KINDS
        self.assertTrue({"APPLY_MEDICAL_FEATURE", "RESTORE_BACKUP", "RESTORE_PROFILE_FROM_BACKUP"} <= kinds)
        for kind in sorted(kinds):
            for code in TABLE:
                for phase, percent in ((None, 0), ("loaded", 50)):
                    record = failed(kind, code, phase=phase, percent=percent)
                    with self.subTest(kind=kind, code=code, phase=phase):
                        self.assertEqual(record_exit(record), old_record_exit(record))


class WaiterLineTests(unittest.TestCase):
    """PO-8: a map lane kind shows its running label only; other kinds keep their phase text."""

    def test_label_only_for_the_map_kinds(self) -> None:
        """Waiting, running and an unknown phase give the label; cancelling keeps its own line."""
        self.assertEqual(LABEL_ONLY_KINDS, MAP_KINDS)
        for kind in LABEL_ONLY_KINDS:
            for state, phase in (("QUEUED", "queued"), ("RUNNING", "running"), ("RUNNING", None)):
                with self.subTest(kind=kind, state=state):
                    record = {"state": state, "progress_phase": phase, "progress_percent": 10}
                    self.assertEqual(progress_text(record, kind), KIND_TEXTS[kind][0])
            self.assertNotEqual(progress_text({"state": "CANCELLING"}, kind), KIND_TEXTS[kind][0])
        backup = {"state": "RUNNING", "progress_phase": "running", "progress_percent": 10}
        self.assertEqual(progress_text(backup, "CREATE_BACKUP"), f"{KIND_TEXTS['CREATE_BACKUP'][0]}: Starting")


if __name__ == "__main__":
    unittest.main()
