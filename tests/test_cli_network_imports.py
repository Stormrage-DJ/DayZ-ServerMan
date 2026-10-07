"""Criterion 17: the CLI and its session and lock modules add no network code and never import the host (task 2.5)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
# Modules of the CLI work that the rule covers (design 6.1, 12.1 row 2.5)
SCANNED = (
    *sorted((PACKAGE / "cli").rglob("*.py")),
    PACKAGE / "session.py",
    PACKAGE / "session_observer.py",
    PACKAGE / "adapters" / "windows" / "shared_files.py",
    PACKAGE / "adapters" / "windows" / "instance_lock.py",
    PACKAGE / "adapters" / "windows" / "instance_holder.py",
    PACKAGE / "adapters" / "windows" / "server_folder_lock.py",
)
# Standard and common third-party modules that open network connections
NETWORK_MODULES = frozenset((
    "socket", "socketserver", "ssl", "http", "urllib", "urllib3", "asyncio", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "websocket", "websockets", "select",
    "selectors",
))


def imported_modules(path: Path) -> set[str]:
    """Return every absolute and relative module name that a file imports, with its top-level name."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add("." * node.level + module)
            # `from .. import host` names the module in the alias list
            names.update("." * node.level + (f"{module}." if module else "") + alias.name for alias in node.names)
    return names


class NetworkImportTests(unittest.TestCase):
    """The scanned modules import no network module; `cli/` imports neither `host` nor pywebview."""

    def test_the_scanned_files_exist(self) -> None:
        """Every file of the rule is present, so the scan cannot pass on a renamed file."""
        self.assertGreaterEqual(len(SCANNED), 16)
        for path in SCANNED:
            self.assertTrue(path.is_file(), path)

    def test_no_network_module_is_imported(self) -> None:
        """No import names a network module, also not as a dotted submodule."""
        for path in SCANNED:
            with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                tops = {name.lstrip(".").split(".")[0] for name in imported_modules(path) if not name.startswith(".")}
                self.assertEqual(tops & NETWORK_MODULES, set())

    def test_cli_imports_neither_host_nor_webview(self) -> None:
        """`cli/` never imports `host`, pywebview or webview (A1, design 6.1)."""
        for path in sorted((PACKAGE / "cli").rglob("*.py")):
            with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                for name in imported_modules(path):
                    parts = name.lstrip(".").split(".")
                    self.assertNotIn("host", parts, name)
                    self.assertFalse({"webview", "pywebview"} & set(parts), name)


if __name__ == "__main__":
    unittest.main()
