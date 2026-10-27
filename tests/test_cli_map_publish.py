"""Leaf 10.5: `map preview` and `map apply` over the real preview and Apply lane: text, JSON, choices, question.

Each test uses the temporary synthetic missions of `ApplyFixture`; no real mission is read or written.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from map_publish_fixtures import EXCLUDE_U, INFECTED, OBJECTS, SPECS, PublishCliFixture  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from test_cli_input_names import InputNameChecker, flags as parser_flags  # noqa: E402
from test_cli_map_publish_wording import CLI_TEXTS  # noqa: E402

from dayz_serverman.application.mission_map_apply_choices import CHOICE_ENCOUNTERS_TEXT, CHOICE_FILES_TEXT  # noqa: E402
from dayz_serverman.cli import help_wording  # noqa: E402
from dayz_serverman.cli import map_publish_wording as words  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.domain.mission_map_preview_texts import (  # noqa: E402
    ZOMBIE_MAX_COUNT_ZERO_TEXT, ZONE_SPAWN_DIST_ZERO_TEXT,
)
from dayz_serverman.cli import map_review_render  # noqa: E402
from dayz_serverman.cli.output import line_text  # noqa: E402
from tests.mission_map_mapgroup_support import zone  # noqa: E402
from tests.mission_map_preview_support import GLOBALS  # noqa: E402

# The heading of the infected values (Product Owner 2026-10-16 17:17:52 in evidence 10.5)
VALUES_HEADING = "Values that limit infected"

# Globals with both shown values 0, and globals that give no value ("not read")
ZERO_GLOBALS = GLOBALS.replace(b'value="1000"', b'value="0"').replace(b'value="300"', b'value="0"')
UNREADABLE_GLOBALS = b"<variables><var name=\"ZombieMaxCount\" value=\"-1\"/></variables>"


class SpecTests(unittest.TestCase):
    """D11, D14 and D20: both are owner commands of phase 10; Apply reviews and asks, the preview asks nothing."""

    def test_specs(self) -> None:
        """Session, confirmation, profile rule, methods, options and help texts of the Product Owner."""
        preview, apply = SPECS["map preview"], SPECS["map apply"]
        self.assertEqual((preview.kind, preview.confirm, preview.profile, preview.phase),
                         (Kind.WRITE, Confirm.NONE, ProfileRule.READ_DEFAULT, 10))
        self.assertEqual(preview.bridge_methods, {"load_mission_map", "preview_mission_map"})
        self.assertEqual((apply.kind, apply.confirm, apply.profile, apply.phase),
                         (Kind.WRITE, Confirm.REVIEW, ProfileRule.WRITE_REQUIRED, 10))
        self.assertEqual(apply.bridge_methods, {"get_application_snapshot", "load_mission_map",
                                                "preview_mission_map", "apply_mission_map"})
        self.assertEqual([(option.dest, option.flag, option.metavar) for option in apply.options[1:]],
                         [("keep_change", "--keep-change", "PATH"), ("replace_file", "--replace-file", "PATH"),
                          ("exclude_encounter", "--exclude-encounter", "ID")])
        self.assertEqual(help_wording.COMMAND_TEXTS["map preview"],
                         "Show what Apply would change in the mission files. Nothing is changed.")
        self.assertEqual(help_wording.COMMAND_TEXTS["map apply"],
                         "Apply the saved map plan to the mission files, after a review.")
        self.assertEqual(help_wording.OPTION_TEXTS["exclude_encounter"],
                         "Leave this encounter out of the apply, by its ID as the review prints it. Repeat it for "
                         "more encounters.")


class PreviewTests(PublishCliFixture):
    """`map preview`: the review blocks in the window order, and the 8.6 answer as JSON."""

    def test_text_review_and_json_answer(self) -> None:
        """The sections, the CLI choice line, the 7.3 reason, the stop line; JSON is the answer unchanged."""
        self.save(OBJECTS, 25)
        before = self.snapshot()
        run = self.command("preview")
        self.assertEqual((run.code, run.calls), (0, ["load_mission_map", "preview_mission_map"]), run.stderr)
        lines = run.stdout.splitlines()
        self.assertEqual(lines[0], "Profile: Main")
        headings = [line for line in lines if not line.startswith(" ")][1:]
        self.assertEqual(headings, [words.NOTICES_HEADING, words.ENCOUNTER_CHOICES_HEADING, words.CHANGES_HEADING,
                                    words.ENCOUNTERS_HEADING, words.LOOT_HEADING, words.FILES_HEADING,
                                    words.STOP_SERVER])
        self.assertIn("  " + CLI_TEXTS["ENCOUNTER_CHOICE"].format(id="u"), lines)
        self.assertIn("  This mission does not offer the infected event Unzoned.", lines)
        self.assertIn("  " + words.LINKED_LOOT, lines)
        self.assertIn("  Global container capacity: +25%", lines)
        answer = self.command("preview", json_mode=True).json()["value"]
        self.assertEqual(answer, self.preview.preview("main"))
        self.assertRegex(answer["fingerprint"], "^[0-9a-f]{64}$")
        self.assertEqual(self.snapshot(), before)

    def test_both_notices_in_their_order_in_text_and_json(self) -> None:
        """Both infected notices print unchanged and in their order; JSON carries the infected object."""
        self.save([INFECTED])
        self.write("db/globals.xml", ZERO_GLOBALS)
        lines = self.command("preview").stdout.splitlines()
        notices = [lines.index("  " + text) for text in (ZOMBIE_MAX_COUNT_ZERO_TEXT, ZONE_SPAWN_DIST_ZERO_TEXT)]
        self.assertEqual(notices, sorted(notices))
        self.assertLess(lines.index(words.ENCOUNTERS_HEADING), notices[0])
        infected = self.command("preview", json_mode=True).json()["value"]["infected"]
        self.assertEqual(infected, self.preview.preview("main")["infected"])
        self.assertEqual((infected["zombie_max_count"], infected["zone_spawn_dist"], len(infected["notices"])),
                         (0, 0, 2))

    def test_a_value_that_is_not_read_is_null_and_gives_no_notice(self) -> None:
        """An unreadable value is null in the JSON and gives no notice in the text."""
        self.save([INFECTED])
        self.write("db/globals.xml", UNREADABLE_GLOBALS)
        infected = self.command("preview", json_mode=True).json()["value"]["infected"]
        self.assertEqual((infected["zombie_max_count"], infected["zone_spawn_dist"], infected["notices"]),
                         (None, None, []))
        self.assertNotIn(ZOMBIE_MAX_COUNT_ZERO_TEXT, self.command("preview").stdout)

    def test_infected_values_with_events_both_caps_and_a_null_value(self) -> None:
        """Evidence 10.5: the value lines after both lists of Encounters; a null value is "not read"."""
        self.save([INFECTED, dict(INFECTED, id="e2", excluded=True)])
        lines = self.command("preview").stdout.splitlines()
        heading = lines.index("  " + VALUES_HEADING)
        self.assertLess(lines.index("  " + words.EXCLUDED), heading)
        self.assertEqual(lines[heading + 1:heading + 4], ["    InfectedVillage: nominal 50, max 100",
                                                          "    ZombieMaxCount in db/globals.xml: 1000",
                                                          "    ZoneSpawnDist in db/globals.xml: 300"])
        self.write("db/globals.xml", UNREADABLE_GLOBALS)
        lines = self.command("preview").stdout.splitlines()
        heading = lines.index("  " + VALUES_HEADING)
        self.assertEqual(lines[heading + 2:heading + 4], ["    ZombieMaxCount in db/globals.xml: not read",
                                                          "    ZoneSpawnDist in db/globals.xml: not read"])
        render = map_review_render._infected_values({"events": {"InfectedArmy": {"nominal": None, "max": 4}},
                                                    "zombie_max_count": 0, "zone_spawn_dist": None})
        self.assertEqual([line_text(line) for line in render],
                         ["  " + VALUES_HEADING, "    InfectedArmy: nominal not read, max 4",
                          "    ZombieMaxCount in db/globals.xml: 0", "    ZoneSpawnDist in db/globals.xml: not read"])

    def test_no_value_line_without_a_written_infected_encounter(self) -> None:
        """Without an included infected encounter the value lines are not printed; JSON keeps the object."""
        for objects in ([zone("rich")], [dict(INFECTED, excluded=True)]):
            with self.subTest(objects=[item["id"] for item in objects]):
                self.save(objects, expected=self.plans.load("main")["revision"])
                self.assertNotIn(VALUES_HEADING, self.command("preview").stdout)
                self.assertIn("infected", self.command("preview", json_mode=True).json()["value"])

    def test_plan_excluded_encounter_in_text_and_json(self) -> None:
        """An available encounter that the plan excludes is listed under the PO-A1 label, and in the JSON."""
        self.save([dict(INFECTED, excluded=True), dict(INFECTED, id="e2", name="Second encounter")])
        lines = self.command("preview").stdout.splitlines()
        label = lines.index("  " + CLI_TEXTS["EXCLUDED"])
        self.assertEqual(lines[label + 1], "    Encounter e1")
        written = lines.index("  " + words.WRITTEN)
        self.assertEqual(lines[written + 1], "    Second encounter")
        excluded = self.command("preview", json_mode=True).json()["value"]["encounters"]["excluded"]
        self.assertEqual(excluded, self.preview.preview("main")["encounters"]["excluded"])
        self.assertEqual([item["id"] for item in excluded], ["e1"])

    def test_written_encounters_are_named_by_id_when_the_revisions_differ(self) -> None:
        """AR-9: the saved plan names a written encounter only when its revision is the revision of the answer."""
        self.save([dict(INFECTED, name="Named")])
        original = self.plans.load
        # The load of the command sees another revision, as when the plan changed between the two calls
        self.load = lambda profile_id: dict(original(profile_id), revision=99)  # type: ignore[method-assign]
        lines = self.command("preview").stdout.splitlines()
        self.assertEqual(lines[lines.index("  " + words.WRITTEN) + 1], "    e1")
        self.load = original  # type: ignore[method-assign]
        lines = self.command("preview").stdout.splitlines()
        self.assertEqual(lines[lines.index("  " + words.WRITTEN) + 1], "    Named")


class ApplyTests(PublishCliFixture):
    """`map apply`: the choices of the three options, the question, `--yes` and the JSON value of D12."""

    def test_apply_with_yes_and_the_json_value(self) -> None:
        """The review, then the end line; JSON gives the record, the review and the result of the record."""
        self.save(OBJECTS, 25)
        run = self.command("apply", *EXCLUDE_U, json_mode=True)
        self.assertEqual(run.code, 0, run.stdout)
        value = run.json()["value"]
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("APPLY_MISSION_MAP", "SUCCEEDED"))
        self.assertEqual(value["result"], value["operations"][0]["result"])
        self.assertEqual((value["result"]["result"], value["result"]["excluded_encounters"]), ("COMMITTED", ["u"]))
        self.assertEqual(value["review"]["choices"]["unavailable_encounters"], ["u"])
        self.assertEqual(run.calls[:4], ["get_application_snapshot", "load_mission_map", "preview_mission_map",
                                         "apply_mission_map"])

    def test_keep_and_replace_send_the_reviewed_key_sets(self) -> None:
        """Keep the change incorporates the edit, Replace the file keeps a backup; the text ends with the kind line."""
        self.applied()
        for flag, field in (("--keep-change", "incorporated"), ("--replace-file", "replaced")):
            with self.subTest(flag=flag):
                self.edit_positions()
                run = self.command("apply", flag, "mapgrouppos.xml", *EXCLUDE_U, json_mode=True)
                self.assertEqual(run.code, 0, run.stdout)
                result = run.json()["value"]["result"]
                found = [item if isinstance(item, str) else item["path"] for item in result[field]]
                self.assertEqual(found, ["mapgrouppos.xml"])
        self.edit_positions()
        run = self.command("apply", "--keep-change", "mapgrouppos.xml", *EXCLUDE_U)
        self.assertEqual((run.code, run.stdout.splitlines()[-1]), (0, "Mission map applied."), run.stderr)
        self.assertIn("  " + CLI_TEXTS["FILE_CHOICE"].format(path="mapgrouppos.xml"), run.stdout.splitlines())

    def test_question_without_yes_and_without_a_terminal(self) -> None:
        """A terminal asks after the review; "n" and no terminal exit 4 and change nothing."""
        self.save(OBJECTS, 25)
        before = self.snapshot()
        stdin = FakeTerminal("n\n")
        run = self.command("apply", *EXCLUDE_U, yes=False, stdin=stdin)
        self.assertEqual((run.code, stdin.reads), (4, 1))
        self.assertIn("Continue? [y/N]", run.stderr)
        self.assertEqual(run.stdout.splitlines()[-1], words.STOP_SERVER)
        stdin = FakeTerminal("y\n", terminal=False)
        run = self.command("apply", *EXCLUDE_U, yes=False, stdin=stdin)
        self.assertEqual((run.code, stdin.reads), (4, 0))
        run = self.command("apply", *EXCLUDE_U, yes=False, json_mode=True, stdin=FakeTerminal("y\n"))
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["review"]["choices"]["unavailable_encounters"]),
                         (4, "CONFIRMATION_REQUIRED", ["u"]))
        self.assertEqual(self.snapshot(), before)
        stdin = FakeTerminal("y\n")
        self.assertEqual((self.command("apply", *EXCLUDE_U, yes=False, stdin=stdin).code, stdin.reads), (0, 1))

    def test_a_missing_choice_exits_4_also_with_yes_and_in_a_terminal(self) -> None:
        """PO-2: the 8.7 text, then the CLI line; no question, and `--yes` never makes a choice."""
        self.applied()
        self.edit_positions()
        before = self.snapshot()
        for yes, stdin in ((True, None), (False, FakeTerminal("y\n")), (True, FakeTerminal("y\n"))):
            with self.subTest(yes=yes, terminal=stdin is not None):
                run = self.command("apply", *EXCLUDE_U, yes=yes, stdin=stdin)
                self.assertEqual(run.code, 4)
                self.assertEqual(run.stderr.strip(), f"{CHOICE_FILES_TEXT} {CLI_TEXTS['MISSING_CHOICE']}")
                self.assertEqual(run.stdout, "")
                self.assertEqual(getattr(stdin, "reads", 0), 0)
        run = self.command("apply", "--replace-file", "mapgrouppos.xml")
        self.assertEqual((run.code, run.stderr.strip()), (4, f"{CHOICE_ENCOUNTERS_TEXT} {CLI_TEXTS['MISSING_CHOICE']}"))
        error = self.command("apply", json_mode=True).json()["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("CONFIRMATION_REQUIRED", "choice_required"))
        self.assertEqual(self.snapshot(), before)

    def test_a_path_or_id_that_the_review_lacks_and_a_path_in_both_options_exit_2(self) -> None:
        """Both refusals come before the question and name the value as typed."""
        self.applied()
        self.edit_positions()
        before = self.snapshot()
        for flags, value in ((("--keep-change", "db/types.xml"), "db/types.xml"),
                             (("--replace-file", "other.xml"), "other.xml"),
                             (("--exclude-encounter", "e1"), "e1")):
            with self.subTest(value=value):
                run = self.command("apply", *flags, *EXCLUDE_U)
                self.assertEqual((run.code, run.stderr.strip()), (2, CLI_TEXTS["NOT_IN_REVIEW"].format(value=value)))
        typed = ["--keep-change", "mapgrouppos.xml", "--replace-file", "mapgrouppos.xml", *EXCLUDE_U]
        run = self.command("apply", *typed)
        self.assertEqual((run.code, run.stderr.strip()), (2, CLI_TEXTS["IN_BOTH"].format(path="mapgrouppos.xml")))
        self.assertEqual(self.snapshot(), before)
        # 11.3: the typed value is an echo, the options are text to type, and the review data are values
        checker = InputNameChecker(self, parser_flags() | {"mapgrouppos.xml", "u"}, typed)
        checker.blocks(run.output.written)
        checker.blocks(self.command("preview").output.written)

    def test_keep_the_change_refused_by_the_review_exits_3_with_its_text(self) -> None:
        """A file whose editor-owned values changed: Keep the change exits 3 with the review text unchanged."""
        self.applied()
        # Removing a detached placement that the editor wrote changes an editor-owned value (8.7)
        path = self.mission / "mapgroupdirt.xml"
        dirt = path.read_bytes()
        path.write_bytes(dirt.replace(re.search(rb"[^\n]*ServerManMap_G_[^\n]*\n", dirt).group(0), b"", 1))
        answer = self.preview.preview("main")
        message = next(item["incorporate"]["message"] for item in answer["external_changes"]
                       if item["path"] == "mapgroupdirt.xml")
        self.assertTrue(message)
        flags = [flag for path_name in answer["choices"]["external_changes"]
                 for flag in ("--keep-change", path_name)]
        run = self.command("apply", *flags, *EXCLUDE_U)
        self.assertEqual((run.code, run.stderr.strip()), (3, message))
        lines = self.command("preview").stdout.splitlines()
        self.assertIn("  " + message, lines)


if __name__ == "__main__":
    unittest.main()
