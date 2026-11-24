"""Crash restart static checks (plan 3.2): who starts the monitor, what was not added, what is not saved."""

from __future__ import annotations

import ast
import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PACKAGE = PROJECT_ROOT / "runnable" / "src" / "python" / "dayz_serverman"
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"


class CrashStaticTests(unittest.TestCase):
    """The expected values are typed by hand from the criteria."""

    def setUp(self) -> None:
        """Make a disposable manager folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_static_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Manager"

    def test_only_the_window_runtime_starts_the_monitor(self) -> None:
        """Criterion 7: one call site, in the runtime, behind the owner check."""
        hits = []
        for path in sorted(PACKAGE.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if ".crash_restart.start(" in line:
                    hits.append((path.relative_to(PACKAGE).as_posix(), lines[index - 1].strip()))
        self.assertEqual(hits, [("host/runtime.py", "if composition.mode is SessionMode.OWNER:")])

    def test_a_command_session_holds_a_stopped_monitor(self) -> None:
        """Criterion 7: a command session builds the monitor and never starts it."""
        session = open_owner_session(self.root, "command", "status", require_byte_range_lock=False)
        self.addCleanup(session.close, 5)
        self.assertFalse(session.composition.crash_restart.is_running())

    def test_nothing_popup_like_or_configurable_was_added(self) -> None:
        """Criterion 3: no popup or sound in the notice, one owner method, no setting."""
        text = (FRONTEND / "overview_crash_notice.js").read_text(encoding="utf-8")
        for token in ("alert(", "confirm(", "prompt(", "window.open", "new Audio", "showModal"):
            self.assertNotIn(token, text)
        composition = build_composition(self.root)
        self.addCleanup(composition.operations.shutdown, 2)
        names = sorted(name for name in composition.bridge.allowed_methods if "crash" in name)
        self.assertEqual(names, ["cancel_crash_restart"])
        self.assertEqual([item.name for item in dataclasses.fields(ManagerSettings) if "crash" in item.name], [])

    def test_the_new_modules_persist_nothing(self) -> None:
        """Criterion 9: the new modules import no repository, json, pickle or sqlite3 and never call open."""
        files = sorted((PACKAGE / "application").glob("crash_restart*.py"))
        self.assertEqual(len(files), 5)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif isinstance(node, ast.Import):
                    modules = [item.name for item in node.names]
                else:
                    modules = []
                for module in modules:
                    self.assertNotIn(module.split(".")[0], {"repositories", "json", "pickle", "sqlite3"}, path.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "open", path.name)

    def test_the_contract_version_is_still_one(self) -> None:
        """Criterion 9: the contract version is 1, also in a real owner reply."""
        self.assertEqual(CONTRACT_VERSION, 1)
        composition = build_composition(self.root)
        self.addCleanup(composition.operations.shutdown, 2)
        answer = composition.bridge.dispatch(
            {"contract_version": 1, "request_id": "x", "method": "get_server_status", "parameters": {}})
        self.assertTrue(answer["success"])
        self.assertEqual(answer["contract_version"], 1)


if __name__ == "__main__":
    unittest.main()
