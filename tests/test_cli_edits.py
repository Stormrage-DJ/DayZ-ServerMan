"""Task 6.1: `--set` typing and `--from-file` shapes (10.6), the edit reviews (8.2) and their wording (11.3).

Unit tests without a composition: the typing rules per source of the type, the pre-steps of
`config set` and `tweaks set` over a scripted load, the review rows, the copies of the window's
dialogs and the input-name rules of criterion 18.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import review_wording  # noqa: E402
from dayz_serverman.cli import edit_review, edit_wording, presteps  # noqa: E402
from dayz_serverman.cli.edits import kind_value, like_current  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Line, line_text, render_blocks  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
FIELDS = [{"key": "hostname", "kind": "string", "secret": False, "present": True, "value": "Old"},
          {"key": "maxPlayers", "kind": "integer", "secret": False, "present": True, "value": 60},
          {"key": "disableVoN", "kind": "boolean", "secret": False, "present": False, "value": None},
          {"key": "serverTimeAcceleration", "kind": "number", "secret": False, "present": True, "value": 1},
          {"key": "password", "kind": "string", "secret": True, "present": True, "value": "secret"}]


class TypingTests(unittest.TestCase):
    """Each typing source of 10.6, and its refusals (exit 2)."""

    def refused(self, function, *arguments) -> str:
        """Return the text of the exit-2 refusal of one typing call."""
        with self.assertRaises(CliFailure) as raised:
            function(*arguments)
        self.assertEqual((raised.exception.exit_code, raised.exception.code), (2, "USAGE"))
        return line_text(raised.exception.message)

    def test_configuration_field_kinds(self) -> None:
        """boolean words in any case, base-10 integers, JSON numbers; strings stay text."""
        for text, value in (("on", True), ("TRUE", True), ("yes", True), ("1", True), ("Off", False),
                            ("false", False), ("no", False), ("0", False)):
            self.assertIs(kind_value("boolean", "k", text), value)
        self.assertEqual((kind_value("integer", "k", " 42 "), kind_value("integer", "k", "-3")), (42, -3))
        self.assertEqual((kind_value("number", "k", "2"), kind_value("number", "k", "0.5")), (2, 0.5))
        self.assertIsInstance(kind_value("number", "k", "2"), int)
        self.assertEqual(kind_value("string", "k", "  spaced = text "), "  spaced = text ")
        self.assertEqual(self.refused(kind_value, "boolean", "k", "maybe"),
                         f"The value maybe of k must be {edit_wording.EXPECTED_BOOLEAN}.")
        for kind, text in (("integer", "1.5"), ("integer", "0x10"), ("integer", ""), ("number", "NaN"),
                           ("number", "1e999"), ("number", "true"), ("number", "abc")):
            with self.subTest(kind=kind, text=text):
                self.refused(kind_value, kind, "k", text)

    def test_like_the_current_value(self) -> None:
        """The JSON type of the current value decides; without one a JSON literal, else text."""
        self.assertIs(like_current(False, "k", "on"), True)
        self.assertEqual(like_current(5, "k", "7"), 7)
        self.assertEqual(like_current(0.25, "k", "1"), 1)
        self.assertEqual(like_current(["A"], "k", '["B", "C"]'), ["B", "C"])
        self.assertEqual(like_current({"a": 1}, "k", '{"b": 2}'), {"b": 2})
        self.assertEqual(like_current("text", "k", "12"), "12")
        self.assertEqual((like_current(None, "k", "12"), like_current(None, "k", "word")), (12, "word"))
        self.assertIn("a JSON array", self.refused(like_current, ["A"], "k", "B"))
        self.assertIn("a JSON object", self.refused(like_current, {}, "k", "[]"))
        self.assertIn("a whole number", self.refused(like_current, 5, "k", "five"))


class PrestepTests(unittest.TestCase):
    """`config set` and `tweaks set` pre-steps: the merged bridge `updates` (10.6)."""

    def options(self, sets: list[str], from_file: str | None = None, target: str = "server") -> SimpleNamespace:
        """Return the parsed options of one command line."""
        return SimpleNamespace(set=sets, from_file=from_file, target=target)

    def test_configuration_updates_are_typed_by_field_kind(self) -> None:
        """Each --set value gets its field's type; the file comes first and --set replaces its key."""
        def call(method: str, **_parameters: object) -> dict:
            """Answer the load of the configuration."""
            self.assertEqual(method, "load_configuration")
            return {"fields": FIELDS}

        resolved = presteps.configuration_keys(call, self.options(
            ["maxPlayers=10", "disableVoN=on", "serverTimeAcceleration=2.5", "hostname=New"]), "alpha")
        self.assertEqual(resolved, {"updates": {"maxPlayers": 10, "disableVoN": True, "serverTimeAcceleration": 2.5,
                                                "hostname": "New"}})
        with self.assertRaises(CliFailure) as raised:
            presteps.configuration_keys(call, self.options([]), "alpha")
        self.assertIn("Name at least one change", line_text(raised.exception.message))

    def test_tweak_updates_are_typed_like_the_current_value(self) -> None:
        """Each --set value of a tweak gets the JSON type of the value that the load returned."""
        def call(method: str, **parameters: object) -> dict:
            """Answer the load of the mission target."""
            self.assertEqual((method, parameters["target"]), ("load_mission_configuration", "starter_loadout"))
            return {"values": {"items": ["Apple"]}}

        resolved = presteps.tweak_keys(call, self.options(['items=["Apple", "Pear"]'], target="starter-loadout"),
                                       "alpha")
        self.assertEqual(resolved, {"updates": {"items": ["Apple", "Pear"]}})


class ReviewTests(unittest.TestCase):
    """The review rows of 8.2 and the copies of the window's dialogs."""

    def test_configuration_review_hides_a_secret(self) -> None:
        """One row per changed field; a secret shows (hidden) and (changed), never its value."""
        preview = {"relative_path": "cfg\\serverDZ.cfg", "changed_fields": ["maxPlayers", "password"],
                   "fields": [{**FIELDS[1], "value": 10}, {**FIELDS[4], "value": "new secret"}]}
        text = render_blocks(edit_review.config_review("server", {"fields": FIELDS}, preview))
        self.assertIn("2 validated change(s).", text)
        self.assertRegex(text, r"Maximum players\s+maxPlayers\s+60\s+10")
        self.assertRegex(text, r"Server password\s+password\s+\(hidden\)\s+\(changed\)")
        self.assertNotIn("secret", text)

    def test_event_review_names_the_event_and_its_field(self) -> None:
        """An event change "<event>.<field>" is one row of the key `events` with the window's words."""
        loaded = {"values": {"events": {"StaticHeliCrash": {"nominal": 3}}}}
        preview = {"relative_path": "db\\events.xml", "changed_fields": ["StaticHeliCrash.nominal"],
                   "adopts_marker_region": True}
        updates = {"events": {"StaticHeliCrash": {"nominal": 5}}}
        text = render_blocks(edit_review.tweaks_review("events", loaded, preview, updates))
        self.assertRegex(text, r"HeliCrash: nominal\s+events\s+3\s+5")
        self.assertIn(edit_wording.MARKER_TAKEN_OVER, text)

    def test_dialog_copies_match_the_frontend(self) -> None:
        """The delete and conversion dialog texts appear unchanged in their frontend files."""
        delete = (FRONTEND / "profile_delete.js").read_text(encoding="utf-8")
        self.assertIn(f'"{review_wording.PROFILE_DELETE_TITLE}"', delete)
        self.assertIn("`" + review_wording.PROFILE_DELETE_LEAD + "${profileState.selected.display_name}"
                      + review_wording.PROFILE_DELETE_BODY + "`", delete)
        dialog = (FRONTEND / "tweaks_dialog.js").read_text(encoding="utf-8")
        lead, dash, middle, tail = review_wording.STARTER_CONVERSION_RANGE
        self.assertIn(f'"{review_wording.STARTER_CONVERSION_TITLE}"', dialog)
        self.assertIn(f"`{lead}${{range.start_line}}{dash}${{range.end_line}}{middle}${{range.items.length}}{tail}`",
                      dialog)
        self.assertIn(f'"{review_wording.STARTER_CONVERSION_BODY}"', dialog)

    def test_sentences_and_reviews_follow_the_input_name_rules(self) -> None:
        """Criterion 18: keys only in the headed Key column, as text to type, or as an echo of the command line."""
        from test_cli_input_names import InputNameChecker, flags
        checker = InputNameChecker(self, flags() | {"maxPlayers", "hostname", "livonia-main"},
                                   ["maxPlayers", "many"])
        preview = {"relative_path": "cfg", "changed_fields": ["maxPlayers"], "fields": [{**FIELDS[1], "value": 10}]}
        blocks: list = [edit_wording.invalid_value("maxPlayers", "many", edit_wording.EXPECTED_INTEGER),
                        edit_wording.no_changes(), edit_wording.profile_id_fixed(),
                        edit_wording.create_values_missing(["profile_id", "game_port"]),
                        edit_wording.changes_validated(2), edit_wording.nothing_to_convert(),
                        edit_wording.delete_needs_stop(),
                        edit_wording.profile_created({"readiness": {"ready": True}}),
                        edit_wording.profile_created({"readiness": {"ready": True}, "reused_files": True}),
                        edit_wording.profile_created({"readiness": {"ready": False, "reasons": [
                            "Server executable is missing.", "bad_identifier here"]}}),
                        *edit_review.config_review("server", {"fields": FIELDS}, preview),
                        *edit_review.conversion_review({"relative_path": "init.c", "conversion": {
                            "start_line": 5, "end_line": 7, "items": ["A"]}}),
                        *edit_review.medical_review("medical_item_spawns", True, {"relative_path": "db"}),
                        *edit_review.profile_delete_review({"profile_id": "livonia-main", "display_name": "Livonia"})]
        checker.blocks(blocks)
        # A host reason that holds an identifier is left out; a readiness without reasons gets the fallback
        self.assertEqual(line_text(edit_wording.profile_created({"readiness": {"ready": False, "reasons": [
            "bad_identifier here"]}})), "Profile created, but needs attention: Check its paths.")
        self.assertTrue(all(isinstance(block, Line) or hasattr(block, "headings") for block in blocks))


if __name__ == "__main__":
    unittest.main()
