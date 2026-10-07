"""AST scanner of the A12 choke point: file reads, replaces, renames and copies outside shared_files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


# The package folder that the scan walks, host/ included
PACKAGE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
# The A12 reason classes of the R-1 ruling
REASON_CLASSES = frozenset({1, 2, 3, 4, 5})
# Text of an allowlist entry that covers every finding of its file
WHOLE_FILE = "whole file"
# Module calls that replace, rename or move a file
MOVE_CALLS = frozenset({("os", "replace"), ("os", "rename"), ("os", "renames"), ("shutil", "move")})
# Module calls that copy a file, which opens the source for read
COPY_CALLS = frozenset({("shutil", "copyfile"), ("shutil", "copy"), ("shutil", "copy2"), ("shutil", "copytree")})
# Modules whose move and copy calls the scan looks for; an alias of one hides them
MOVING_MODULES = frozenset(module for module, _name in MOVE_CALLS | COPY_CALLS)
# The bundled-asset module of the host exclusion
HOST_ASSETS = "host/assets.py"


@dataclass(frozen=True)
class Finding:
    """One read, replace, rename or copy outside the choke point."""

    path: str
    line: int
    pattern: str


@dataclass(frozen=True)
class AllowEntry:
    """One reviewed allowlist row: file, line text and A12 reason class."""

    path: str
    text: str
    reason_class: int | None
    reason: str


def _module_attribute(node: ast.AST) -> tuple[str, str] | None:
    """Return (module, name) for an expression of the form module.name."""
    # Only a plain name followed by one attribute counts
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _string_literal(node: ast.AST | None) -> str | None:
    """Return the value of a string constant, or None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _mode_argument(call: ast.Call, position: int) -> ast.AST | None:
    """Return the mode argument from its keyword or its position."""
    # A keyword wins over a positional argument
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _is_read_mode(mode: ast.AST | None) -> bool:
    """Say whether an open mode is absent, not a literal, or reads."""
    text = _string_literal(mode)
    return mode is None or text is None or "r" in text or "+" in text


class _Scanner(ast.NodeVisitor):
    """Collect findings of one module and keep the enclosing function and expression stack."""

    def __init__(self, path: str) -> None:
        """Start an empty scan of one module."""
        self.path = path
        self.findings: list[Finding] = []
        self.functions: list[ast.AST] = []
        self.stack: list[ast.AST] = []

    def visit(self, node: ast.AST) -> None:
        """Visit a node with its ancestors on the stack."""
        # Track functions for the ZipFile and host rules, and every ancestor for comprehensions
        is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_function:
            self.functions.append(node)
        self.stack.append(node)
        try:
            super().visit(node)
        finally:
            self.stack.pop()
            if is_function:
                self.functions.pop()

    def _add(self, node: ast.AST, pattern: str) -> None:
        """Record one finding at the line of a node."""
        self.findings.append(Finding(self.path, node.lineno, pattern))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Report `from os import replace` and the like: the bare name would hide the call (QF-23)."""
        for alias in node.names:
            if (node.module, alias.name) in MOVE_CALLS or (node.module, alias.name) in COPY_CALLS:
                self._add(node, f"from {node.module} import {alias.name}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Report `import os as x` and `import shutil as x`: an alias would hide every call through it (QF-23)."""
        for alias in node.names:
            if alias.name in MOVING_MODULES and alias.asname is not None and alias.asname != alias.name:
                self._add(node, f"import {alias.name} as {alias.asname}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Report every reference to os.replace, os.rename, shutil.move or a shutil copy."""
        # A reference counts, not only a call, so a default argument is caught too
        pair = _module_attribute(node)
        if pair in MOVE_CALLS or pair in COPY_CALLS:
            self._add(node, f"{pair[0]}.{pair[1]}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Report reads, path replaces and renames, and read-mode archives."""
        function = node.func
        # Path.replace(x) and Path.rename(x): one positional argument and no keyword
        if isinstance(function, ast.Attribute) and function.attr in {"replace", "rename"}:
            if len(node.args) == 1 and not node.keywords and _module_attribute(function) not in MOVE_CALLS:
                self._add(node, f".{function.attr}(x)")
        # Builtin open and any <expr>.open with a read, update or unknown mode
        if isinstance(function, ast.Name) and function.id == "open":
            if _is_read_mode(_mode_argument(node, 1)):
                self._add(node, "open")
        elif isinstance(function, ast.Attribute) and function.attr == "open":
            if _is_read_mode(_mode_argument(node, 0)):
                self._add(node, ".open")
        # Path.read_text and Path.read_bytes, except the bundled host assets
        if isinstance(function, ast.Attribute) and function.attr in {"read_text", "read_bytes"}:
            if not self._is_host_asset_read(function.value):
                self._add(node, f".{function.attr}")
        # ZipFile in read mode on anything other than an open_shared file object
        is_zip = (isinstance(function, ast.Name) and function.id == "ZipFile") or (
            _module_attribute(function) == ("zipfile", "ZipFile")
        )
        if is_zip and _string_literal(_mode_argument(node, 1)) in {None, "r"}:
            if not self._is_shared_file_object(node.args[0] if node.args else None):
                self._add(node, "ZipFile")
        self.generic_visit(node)

    def _is_shared_file_object(self, argument: ast.AST | None) -> bool:
        """Say whether a name is bound by `with open_shared(...) as <name>` in the same function."""
        if not isinstance(argument, ast.Name) or not self.functions:
            return False
        # Look for the with-item in the enclosing function only
        for node in ast.walk(self.functions[-1]):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    call = item.context_expr
                    target = item.optional_vars
                    named = isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    if named and call.func.id == "open_shared" and isinstance(target, ast.Name):
                        if target.id == argument.id:
                            return True
        return False

    def _is_host_asset_read(self, receiver: ast.AST) -> bool:
        """Apply the A12 host exclusion: `root / <name>` with a resolved parameter and a literal name."""
        if self.path != HOST_ASSETS or not self.functions:
            return False
        # The receiver must be `root / <name>` with a plain local name on the left
        if not (isinstance(receiver, ast.BinOp) and isinstance(receiver.op, ast.Div)):
            return False
        if not isinstance(receiver.left, ast.Name):
            return False
        function = self.functions[-1]
        if not self._is_resolved_parameter(function, receiver.left.id):
            return False
        # The name is a string literal or a comprehension target over string literals
        if _string_literal(receiver.right) is not None:
            return True
        return isinstance(receiver.right, ast.Name) and self._is_literal_loop_target(receiver.right.id)

    @staticmethod
    def _is_resolved_parameter(function: ast.AST, name: str) -> bool:
        """Say whether `name` is bound exactly once, by `name = <parameter>.resolve(...)`."""
        parameters = {argument.arg for argument in function.args.args + function.args.kwonlyargs}
        bindings: list[ast.AST] = []
        # Count every binding of the name inside the function
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store):
                bindings.append(node)
        if len(bindings) != 1 or name in parameters:
            return False
        # The single binding must be a plain assignment of <parameter>.resolve(...)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and node.targets[0] is bindings[0]:
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
                    receiver = value.func.value
                    return value.func.attr == "resolve" and isinstance(receiver, ast.Name) and receiver.id in parameters
        return False

    def _is_literal_loop_target(self, name: str) -> bool:
        """Say whether an enclosing comprehension binds `name` over a tuple or list of string literals."""
        comprehensions = (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)
        # Only comprehensions inside the same expression are ancestors on the stack
        for node in reversed(self.stack):
            if isinstance(node, ast.stmt):
                return False
            if isinstance(node, comprehensions):
                for generator in node.generators:
                    target = generator.target
                    values = generator.iter
                    if isinstance(target, ast.Name) and target.id == name and isinstance(values, (ast.Tuple, ast.List)):
                        return all(_string_literal(item) is not None for item in values.elts)
        return False


def scan_source(path: str, source: str) -> list[Finding]:
    """Return the findings of one module's source text."""
    scanner = _Scanner(path)
    scanner.visit(ast.parse(source))
    return sorted(set(scanner.findings), key=lambda finding: (finding.line, finding.pattern))


def scan_package(package: Path = PACKAGE) -> tuple[list[Finding], dict[str, list[str]]]:
    """Scan every module of the package and return the findings with each module's lines."""
    findings: list[Finding] = []
    lines: dict[str, list[str]] = {}
    # Walk the package in a stable order, host/ included
    for module in sorted(package.rglob("*.py")):
        relative = module.relative_to(package).as_posix()
        source = module.read_text(encoding="utf-8")
        lines[relative] = source.splitlines()
        findings.extend(scan_source(relative, source))
    return findings, lines


def allowlist_problems(
    findings: list[Finding], allowlist: tuple[AllowEntry, ...], lines: dict[str, list[str]]
) -> list[str]:
    """Return every unlisted finding, every entry without a reason class and every stale entry."""
    problems: list[str] = []
    # Each entry needs a reason class of A12 and a file and line text that still exist
    for entry in allowlist:
        if entry.reason_class not in REASON_CLASSES or not entry.reason.strip():
            problems.append(f"allowlist entry without an A12 reason class: {entry.path} {entry.text!r}")
        module = lines.get(entry.path)
        if module is None or (entry.text != WHOLE_FILE and not any(entry.text in line for line in module)):
            problems.append(f"stale allowlist entry: {entry.path} {entry.text!r}")
    # Each finding needs a matching entry for its file and line
    for finding in findings:
        text = lines.get(finding.path, [])[finding.line - 1] if finding.path in lines else ""
        covered = any(
            entry.path == finding.path and (entry.text == WHOLE_FILE or entry.text in text) for entry in allowlist
        )
        if not covered:
            problems.append(f"{finding.path}:{finding.line} {finding.pattern}: {text.strip()}")
    return problems
