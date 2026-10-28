"""Criterion 3: every GUI bridge method is in a CLI command, internal, excluded or pending, as 01.04 says (task 2.5)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.registry import (  # noqa: E402
    EXCLUDED_METHODS, EXCLUSION_REASONS, INTERNAL_METHODS, PENDING_PREFIX, PENDING_TASK_10, Confirm, Kind,
    ProfileRule,
)
from dayz_serverman.composition import build_composition  # noqa: E402

# The command grammar of 01.04 with the phase of the plan that builds each command (`run` of phase 8 is deferred)
MATRIX: dict[str, int] = {
    **dict.fromkeys(("status", "server status", "server players", "schedule show", "backup list", "updates status",
                     "mods list", "steam show", "profile list", "profile show", "profile command", "profile missions",
                     "config show", "tweaks show", "tweaks medical show", "logs", "settings show"), 2),
    **dict.fromkeys(("server start", "server stop", "server restart", "schedule set", "schedule clear",
                     "profile backup-after-stop"), 3),
    **dict.fromkeys(("backup create", "backup restore", "backup recover", "profile restore"), 4),
    **dict.fromkeys(("updates check", "updates auto", "mods verify", "mods update", "steam set", "steam login"), 5),
    **dict.fromkeys(("config set", "tweaks set", "tweaks medical set", "profile create",
                     "profile edit", "profile delete"), 6),
    **dict.fromkeys(("settings check-path", "settings set", "migrate preview", "migrate apply"), 7),
    # Task 10 of the mission map plan (D20): each map leaf adds the commands that it builds
    **dict.fromkeys(("map show", "map inspect", "map save", "map set-aside", "map preview", "map apply",
                     "map restore", "map recover", "map export", "map import", "map copy", "map adopt"), 10),
}
# Commands built so far, by task
BUILT = {
    # Task 2.6: the reads of phase 2
    "status", "server status", "server players", "schedule show", "backup list", "updates status", "mods list",
    "steam show", "profile list", "profile show", "profile command", "profile missions", "config show",
    "tweaks show", "tweaks medical show", "logs", "settings show",
    # Task 3.3: the lifecycle commands of phase 3
    "server start", "server stop", "server restart", "schedule set", "schedule clear", "profile backup-after-stop",
    # Task 4.1: backup create and recover; task 4.2: backup restore; task 4.3: profile restore
    "backup create", "backup recover", "backup restore", "profile restore",
    # Task 5.1: updates and Steam settings
    "updates check", "updates auto", "steam set", "steam login",
    # Task 5.2: mods verify; task 5.3: mods update
    "mods verify", "mods update",
    # Task 6.1: configuration and tweaks edits
    "config set", "tweaks set", "tweaks medical set",
    # Task 6.2: profile create, edit and delete
    "profile create", "profile edit", "profile delete",
    # Task 7.1: the folder check and the folder save
    "settings check-path", "settings set",
    # Task 7.2: the legacy import
    "migrate preview", "migrate apply",
    # Task 10.4: the map reads; task 10.15: the save and the set aside of the saved plan; task 10.5: preview, apply
    "map show", "map inspect", "map save", "map set-aside", "map preview", "map apply",
    # Task 10.32: the restore of both modes and the editor recovery; task 10.6: the export; task 10.33: the import
    "map restore", "map recover", "map export", "map import",
    # Task 10.7: the copy between profiles; task 10.36: the adoption
    "map copy", "map adopt",
}
# Commands that a later task still builds; each task removes its commands, and the table is empty at phase 9
PENDING = set(MATRIX) - BUILT
# Commands that confirm before they change something: the "Confirm" column of 01.04 as corrected by the
# Product Owner's sweep of 2026-10-07 22:02:06 (criterion 30), with the lifecycle commands of phase 3
CONFIRMING = {
    "server start", "server stop", "server restart", "backup create", "backup restore", "profile restore",
    "mods update", "profile delete", "config set", "tweaks set", "tweaks medical set",
    "migrate apply",
    # Task 10 of the mission map plan (D14): the commands whose window action confirms
    "map set-aside", "map apply", "map restore", "map import", "map copy", "map adopt",
}
# Bridge methods that wait for a command of task 10 of the mission map plan. The CLI leaves removed them all;
# leaf 10.36 removed the two adoption methods, which `map adopt` calls
EXPECTED_PENDING_TASK_10: dict[str, str] = {}
# The three methods of leaf 8.9 that `map restore` and `map recover` call since leaf 10.32, by command
LEAF_10_32_METHODS = {"map restore": frozenset(("preview_mission_map_restore", "restore_mission_map")),
                      "map recover": frozenset(("inspect_mission_map_recovery",))}
# The two methods of leaf 8.9 that `map preview` and `map apply` call since leaf 10.5
LEAF_10_5_METHODS = frozenset(("preview_mission_map", "apply_mission_map"))
# Display reads of the map editor (3.17): permanent exclusions with the Product Owner reason of RQ1
DISPLAY_REASON = "display-only rendering data; Inspect and the capability read give the same facts as text"
DISPLAY_READS = frozenset(("read_mission_map_tier_layer", "read_mission_map_background"))


class ParityMatrixTests(unittest.TestCase):
    """The registry covers the owner allowlist exactly as the parity matrix divides it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the owner allowlist of a real composition on an empty root."""
        with tempfile.TemporaryDirectory(prefix="serverman_parity_") as temporary:
            composition = build_composition(Path(temporary))
            try:
                cls.allowlist = composition.bridge.allowed_methods
            finally:
                composition.shutdown.request_shutdown()
                composition.shutdown.wait_for_close(5)

    def test_every_owner_method_is_used_internal_excluded_or_pending(self) -> None:
        """Each of the 81 methods is in a command, internal or excluded (70 + 4 + 7); none is pending (10.36)."""
        self.assertEqual(len(self.allowlist), 81)
        used = set().union(*(command.bridge_methods for command in COMMANDS))
        pending = set(PENDING_TASK_10)
        self.assertEqual(self.allowlist - used - INTERNAL_METHODS - EXCLUDED_METHODS - pending, set())
        self.assertEqual(len(self.allowlist - INTERNAL_METHODS - EXCLUDED_METHODS - pending), 70)
        # A pending method never counts as an exclusion
        self.assertEqual((len(INTERNAL_METHODS), len(EXCLUDED_METHODS)), (4, 7))
        # Internal, excluded and pending methods are never named by a command, and every named method exists
        self.assertEqual(used & (INTERNAL_METHODS | EXCLUDED_METHODS | pending), set())
        self.assertEqual(used - self.allowlist, set())
        self.assertTrue(INTERNAL_METHODS | EXCLUDED_METHODS | pending <= self.allowlist)

    def test_dispositions_are_disjoint_with_reasons(self) -> None:
        """Each method has one disposition; an exclusion has a reason and a pending entry names its command."""
        self.assertEqual(EXCLUDED_METHODS, frozenset(EXCLUSION_REASONS))
        self.assertEqual(INTERNAL_METHODS & EXCLUDED_METHODS, set())
        self.assertEqual(set(PENDING_TASK_10) & (INTERNAL_METHODS | EXCLUDED_METHODS), set())
        for method, reason in EXCLUSION_REASONS.items():
            with self.subTest(excluded=method):
                self.assertTrue(reason.strip())
        for method, planned in PENDING_TASK_10.items():
            with self.subTest(pending=method):
                self.assertTrue(planned.startswith(PENDING_PREFIX))
                self.assertTrue(planned[len(PENDING_PREFIX):].strip())

    def test_pending_methods_are_pinned(self) -> None:
        """The pending methods and their planned commands are exactly the expected ones."""
        self.assertEqual(PENDING_PREFIX, "pending task 10: ")
        self.assertEqual(dict(PENDING_TASK_10), EXPECTED_PENDING_TASK_10)

    def test_the_methods_of_leaf_8_9_are_used_by_their_commands(self) -> None:
        """Preview and Apply are used since 10.5; both restore methods and recovery since 10.32, never pending."""
        specs = {command.name: command for command in COMMANDS}
        used = set().union(*(command.bridge_methods for command in COMMANDS))
        self.assertLessEqual(LEAF_10_5_METHODS, used & self.allowlist)
        for name, methods in LEAF_10_32_METHODS.items():
            with self.subTest(command=name):
                self.assertLessEqual(methods, specs[name].bridge_methods & self.allowlist)
        leaf_8_9 = LEAF_10_5_METHODS.union(*LEAF_10_32_METHODS.values())
        self.assertEqual(leaf_8_9 & (set(PENDING_TASK_10) | INTERNAL_METHODS | EXCLUDED_METHODS), set())

    def test_parity_gap_list_is_closed(self) -> None:
        """Leaf 10.14: no pending table entry, no pending command and no map command without a handler stay."""
        self.assertEqual(dict(PENDING_TASK_10), {})
        self.assertEqual(EXPECTED_PENDING_TASK_10, {})
        self.assertEqual(PENDING, set())
        map_commands = [command for command in COMMANDS if command.path[0] == "map"]
        self.assertEqual(len(map_commands), 12)
        for command in map_commands:
            with self.subTest(command=command.name):
                self.assertFalse(command.pending)
                self.assertTrue(command.bridge_methods)

    def test_display_reads_are_permanent_exclusions(self) -> None:
        """Both display reads are registered and excluded with the RQ1 reason; neither is ever pending (3.17)."""
        self.assertLessEqual(DISPLAY_READS, self.allowlist)
        self.assertEqual({method: EXCLUSION_REASONS[method] for method in DISPLAY_READS},
                         dict.fromkeys(DISPLAY_READS, DISPLAY_REASON))
        self.assertEqual(DISPLAY_READS & set(PENDING_TASK_10), set())

    def test_registry_names_the_commands_of_the_matrix(self) -> None:
        """The registry holds exactly the commands of 01.04, each once."""
        names = [command.name for command in COMMANDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(MATRIX))
        for command in COMMANDS:
            with self.subTest(command=command.name):
                self.assertEqual(command.phase, MATRIX[command.name])

    def test_pending_table_matches_the_registry(self) -> None:
        """A command without a handler is listed as PENDING, and a listed one has no handler yet."""
        self.assertEqual({command.name for command in COMMANDS if command.pending}, PENDING)

    def test_confirm_column_matches_the_sweep(self) -> None:
        """Criterion 30: exactly the commands whose window action confirms ask before they change something."""
        self.assertEqual({command.name for command in COMMANDS if command.confirm is not Confirm.NONE}, CONFIRMING)

    def test_read_commands_use_only_observer_methods(self) -> None:
        """A read runs in an observer session, which holds only the read handlers (4.3)."""
        for command in COMMANDS:
            if command.kind is Kind.READ:
                with self.subTest(command=command.name):
                    self.assertEqual(command.bridge_methods - OBSERVER_READ_METHODS, set())

    def test_profile_commands_take_the_profile_option(self) -> None:
        """Every command with a profile rule takes `--profile` (6.2)."""
        for command in COMMANDS:
            flags = {option.flag for option in command.options}
            with self.subTest(command=command.name):
                self.assertEqual("--profile" in flags, command.profile is not ProfileRule.NONE)


if __name__ == "__main__":
    unittest.main()
