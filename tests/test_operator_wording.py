"""Operator wording: every backend kind and phase has a catalogue entry, and no raw identifier is operator text."""
from __future__ import annotations

import re
import unittest

try:
    from tests.ui_harness_support import FRONTEND, ROOT
except ModuleNotFoundError:
    from ui_harness_support import FRONTEND, ROOT


from dayz_serverman.application.log_activity import METHOD_TEXTS, format_manager_record
from dayz_serverman.bridge.contracts import ErrorCode

PACKAGE = ROOT / "runnable" / "src" / "python" / "dayz_serverman"
# Modules that hold the wording catalogue; raw identifiers are keys there
CATALOGUE = ("operation_labels.js", "operation_messages.js", "diagnostic_labels.js", "host_sentences.js")
# Phases that the lane itself sets
WAITING_PHASES = ("accepted", "queued", "running")
TERMINAL_PHASES = ("complete", "cancelled", "failed", "shutdown")
# An upper-case identifier with an underscore, and a snake_case word
UPPER_IDENTIFIER = re.compile(r"[A-Z]{2,}_[A-Z_]+")
SNAKE_WORD = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# String literals of a script: double-quoted, single-quoted, and template literals
LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`')
# Record fields whose raw value must never be put into text
RAW_FIELDS = re.compile(
    r"\b(?:progress_phase|progress_percent|last_working_phase|download_state|start_state|start_error|"
    r"error_code|diagnostic_code|storage_policy)\b"
    r"|\.(?:state|kind|status|code|outcome|action|role)\b(?![\w.?\[(])")
# Interpolations that are no operator text of an identifier, with the reason
ALLOWED_INTERPOLATIONS = {
    "shell_ui.js": ("overall.kind",),           # style class of the status dot
    "configuration_edit.js": ("field.kind",),   # value type of a field in a validation sentence
}


def backend_kinds() -> set[str]:
    """Return every operation kind that a coordinator submits to the lane."""
    kinds: set[str] = set()
    for path in (PACKAGE / "application").glob("*.py"):
        kinds.update(re.findall(r'(?:operations\.submit|_submit)\(\s*"([A-Z][A-Z_]+)"',
                                path.read_text(encoding="utf-8")))
    return kinds


def backend_phases() -> set[str]:
    """Return every phase name that the backend passes to a checkpoint."""
    phases: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        phases.update(re.findall(r'\b_?checkpoint\(\s*"([A-Za-z_]+)"', source))
        # The publication inventory reports through a local name
        if path.name == "mod_publication_inventory.py":
            phases.update(re.findall(r'\bcheck\(\s*"([A-Z_]+)"', source))
    return phases


def backup_service_phases() -> set[str]:
    """Return the phases of the backup service, which a stop or restart reports with a prefix."""
    source = (PACKAGE / "application" / "backup_coordinator.py").read_text(encoding="utf-8")
    declared = re.search(r'frozenset\(\s*\(([^)]*)\)\s*\)', source)
    return set(re.findall(r'"([A-Z_]+)"', declared.group(1))) | {"PUBLISH"}


def operator_text(literal: str) -> str:
    """Return the static text of a literal: quotes removed and interpolations cut out."""
    return re.sub(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", literal[1:-1])


class WordingCatalogueTests(unittest.TestCase):
    """Specification 7.1 to 7.3: the catalogue covers what the backend can report."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the kind table and the phase table of the catalogue."""
        labels = (FRONTEND / "operation_labels.js").read_text(encoding="utf-8")
        kinds = labels.split("const operationKindLabels = Object.freeze({", 1)[1].split("});", 1)[0]
        cls.kinds = dict(re.findall(r'^  ([A-Z_]+): (\[.*\]),$', kinds, re.MULTILINE))
        phases = labels.split("const operationPhaseLabels = Object.freeze({", 1)[1].split("});", 1)[0]
        cls.phases = re.findall(r'^  "([A-Z_*]+)/([A-Za-z_]+)": \["([^"]+)", (true|false)\],$', phases, re.MULTILINE)
        cls.labels = labels

    def test_every_backend_kind_has_the_four_texts(self) -> None:
        """Each submitted kind has a name and three result sentences."""
        kinds = backend_kinds()
        self.assertEqual(len(kinds), 22, sorted(kinds))
        for kind in sorted(kinds):
            self.assertIn(kind, self.kinds, f"operation kind without a catalogue entry: {kind}")
            texts = re.findall(r'"([^"]+)"', self.kinds[kind])
            self.assertEqual(len(texts), 4, kind)
            for text in texts:
                self.assertIsNone(UPPER_IDENTIFIER.search(text), text)
        self.assertEqual(sorted(self.kinds), sorted(kinds), "catalogue kinds that the backend does not submit")

    def test_every_backend_phase_has_an_entry(self) -> None:
        """Each checkpoint phase name is the phase part of at least one catalogue row."""
        phases = backend_phases()
        self.assertGreaterEqual(len(phases), 47, sorted(phases))
        known = {phase for _kind, phase, _text, _mode in self.phases}
        for phase in sorted(phases):
            self.assertIn(phase, known, f"phase without a catalogue entry: {phase}")
        # The backup inside a stop or restart reports the backup phases with a prefix
        lifecycle = {phase for kind, phase, _text, _mode in self.phases if kind == "LIFECYCLE"}
        for phase in sorted(backup_service_phases()):
            self.assertIn(f"BACKUP_{phase}", lifecycle)
        generic = {phase for kind, phase, _text, _mode in self.phases if kind == "*"}
        self.assertEqual(generic, set(WAITING_PHASES))
        for phase in TERMINAL_PHASES:
            self.assertIn(f'"{phase}"', self.labels.split("const operationTerminalPhases", 1)[1].split(";", 1)[0])

    def test_rows_name_known_kinds_and_plain_text(self) -> None:
        """Each phase row belongs to a known kind and its text holds no identifier."""
        for kind, phase, text, _mode in self.phases:
            self.assertIn(kind, {*self.kinds, "*", "LIFECYCLE"}, f"{kind}/{phase}")
            self.assertIsNone(UPPER_IDENTIFIER.search(text), text)
            self.assertIsNone(SNAKE_WORD.search(text), text)
        self.assertEqual(len({(kind, phase) for kind, phase, _text, _mode in self.phases}), len(self.phases))

    def test_fallbacks_exist_for_new_backend_values(self) -> None:
        """An unknown kind, phase, state, code, status, and outcome each have a plain fallback."""
        messages = (FRONTEND / "operation_messages.js").read_text(encoding="utf-8")
        diagnostics = (FRONTEND / "diagnostic_labels.js").read_text(encoding="utf-8")
        for text in ('["Working", "Operation finished.", "The operation did not finish.", "Operation cancelled."]',
                     'Object.freeze(["Working", false])', '"Status unknown"'):
            self.assertIn(text, self.labels)
        self.assertIn("return message ? OPERATION_INTERNAL_TEXT : fallback;", messages)
        self.assertIn("if (!text.includes(\" \") || hostTextLeaks(text)) return null;",
                      (FRONTEND / "host_sentences.js").read_text(encoding="utf-8"))
        for text in ('["Needs attention", "Check the {l}."]', '"Failed"',
                     '"The server state could not be confirmed."'):
            self.assertIn(text, diagnostics)


class OperatorTextLeakTests(unittest.TestCase):
    """Criterion 15: no string that the operator can read holds a raw identifier."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load every frontend script without its comment lines."""
        cls.scripts = {}
        for path in sorted(FRONTEND.glob("*.js")):
            lines = [line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("//")]
            cls.scripts[path.name] = "\n".join(lines)

    def test_prose_literals_hold_no_raw_identifier(self) -> None:
        """A literal with a space is prose; prose holds no upper-case identifier and no snake_case word."""
        for name, source in self.scripts.items():
            for literal in LITERAL.findall(source):
                text = operator_text(literal)
                if " " not in text.strip():
                    continue
                self.assertIsNone(UPPER_IDENTIFIER.search(text), f"{name}: {literal}")
                self.assertIsNone(SNAKE_WORD.search(text), f"{name}: {literal}")

    def test_templates_do_not_interpolate_raw_record_fields(self) -> None:
        """Outside the catalogue and the bar model no text template inserts a raw state, kind, phase, or code."""
        for name, source in self.scripts.items():
            if name in CATALOGUE or name.startswith("operation_"):
                continue
            for literal in re.findall(r"`(?:[^`\\]|\\.)*`", source):
                for expression in re.findall(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", literal):
                    if any(allowed in expression for allowed in ALLOWED_INTERPOLATIONS.get(name, ())):
                        continue
                    if "ServerManDiagnosticLabels" in expression or "ServerManOperationLabels" in expression:
                        continue
                    self.assertIsNone(RAW_FIELDS.search(expression), f"{name}: ${{{expression}}}")

    def test_host_messages_pass_through_the_catalogue(self) -> None:
        """Only the catalogue reads the message of a host error or of a terminal error."""
        pattern = re.compile(r"\b(?:result|response|selected|preview|medical|inventory|profiles|snapshot)\??\.error"
                             r"\??\.message|terminal_error\??\.message|\berror\.message === ")
        for name, source in self.scripts.items():
            if name in CATALOGUE:
                continue
            self.assertIsNone(pattern.search(source), name)
        for name in ("shell_ui.js", "backups.js", "configuration.js", "migration.js", "mods_operations.js",
                     "mod-publication.js", "profile_restore.js", "restore.js", "settings.js", "tweaks.js",
                     "update_status.js"):
            self.assertIn("ServerManOperationMessages.bridgeError(", self.scripts[name], name)

    def test_named_leaks_of_the_specification_are_gone(self) -> None:
        """Each text that specification 7.6 lists is replaced."""
        everything = "\n".join(self.scripts.values())
        for old in (
            '"Manager operation"', "declared safe point", "Manager records are flushed",
            "Waiting for the current safe point", "Waiting for a safe point",
            "Workshop update stopped with state", "Workshop item outcomes recorded",
            "server start failed:", "New mutations are blocked", "Restore journals are uncertain",
            "Policy: ${", "backup_inventory.status", "Restore support pending", "No action is required", "Choose a location to validate it.",
            "Reload the page", "Reload it",
        ):
            self.assertNotIn(old, everything, old)
        for new in (
            "Waiting work is cancelled. Work in progress finishes or stops at a safe moment.",
            "Everything is saved. The window can close.",
            "Cancelling. The update stops at the next safe moment.",
            "Cancelling. The sign-in stops at the next safe moment.", "Server start was not requested.",
            "The archives stay where they are and are only listed.",
            "Cannot be restored", "Recovery is required before the server can be controlled.",
            "Changes are blocked until an unfinished restore is resolved.",
        ):
            self.assertIn(new, everything, new)
        # The placeholder publication state of an update result is never read by the frontend
        self.assertNotIn("publication_state", everything)
        self.assertNotIn("PENDING_PHASE", everything)


class ActivityTextLeakTests(unittest.TestCase):
    """Criterion 15 in Logs, "Manager activity": no line holds a raw kind, state, code, method, or field."""

    def test_activity_lines_hold_no_raw_identifier(self) -> None:
        """Format the records of the review and a sweep of every kind, code, method, and event."""
        raw = "Cannot start while server state is RUNNING_EXTERNAL; phase verify_items of maxPlayers failed."
        records = [
            ("operation.state", {"kind": "START_SERVER", "state": "FAILED", "error_code": "EXTERNAL_PROCESS",
                                 "error_message": "Cannot start while server state is RUNNING_EXTERNAL."}),
            ("operation.state", {"kind": "PUBLISH_MODS_AND_KEYS", "state": "RECOVERY_REQUIRED",
                                 "error_code": "PUBLICATION_FAILED"}),
            ("operation.state", {"kind": "UPDATE_WORKSHOP_ITEMS", "state": "SUCCEEDED"}),
            ("bridge.failure", {"method": "save_steam_settings", "error_code": "INVALID_REQUEST",
                                "message": "steam_account_name must contain only letters, digits, or underscore"}),
            ("schedule.queued", {"action": "RESTART", "operation_id": "abc", "profile_id": "alpha"}),
            ("schedule.saved", {"profile_id": "alpha", "action": "stop", "hour": 4, "minute": 0}),
            ("schedule.skipped", {"profile_id": "alpha", "server_state": "RUNNING_EXTERNAL"}),
            ("operation_lane.recovery_block", {"reason": "journal_state is RECOVERY_REQUIRED"}),
            ("update_check.completed", {"outcome": "FAILED", "code": "NETWORK_UNREACHABLE", "fact_count": 0}),
            ("later.event_name", {"raw_field": "RAW_VALUE"}),
        ]
        codes = [code.value for code in ErrorCode] + ["LATER_CODE", None]
        for kind in [*sorted(backend_kinds()), "LATER_KIND"]:
            for state in ("SUCCEEDED", "CANCELLED", "FAILED", "RECOVERY_REQUIRED", "LATER_STATE"):
                records.extend(("operation.state", {"kind": kind, "state": state, "phase": "verify_items",
                                                    "profile_id": "alpha", "error_code": code,
                                                    "error_message": message})
                               for code in codes for message in (raw, None))
        records.extend(("bridge.failure", {"method": method, "error_code": code, "message": raw})
                       for method in [*METHOD_TEXTS, "later_method", None] for code in ("INVALID_REQUEST", "LATER_CODE"))
        shown = 0
        for event, fields in records:
            for level in ("INFO", "ERROR"):
                line = format_manager_record({"event": event, "level": level, "fields": fields,
                                              "occurred_at": "2026-10-03T10:00:00Z"})
                if line is None:
                    continue
                shown += 1
                self.assertIsNone(UPPER_IDENTIFIER.search(line), line)
                self.assertIsNone(SNAKE_WORD.search(line), line)
                # Neither a raw level nor a name derived from an identifier is printed
                self.assertIsNone(re.search(r"\b(?:ERROR|WARNING|INFO|[A-Z][a-z]+ (?:[A-Z][a-z]+ )+(?:failed|completed))\b",
                                            line), line)
                self.assertNotIn("maxPlayers", line)
        self.assertGreater(shown, 2000)


if __name__ == "__main__":
    unittest.main()
