"""Criterion 3: every GUI bridge method is in a CLI command, internal or excluded, as 01.04 says (task 2.5)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.registry import EXCLUDED_METHODS, INTERNAL_METHODS, Kind, ProfileRule  # noqa: E402
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
    **dict.fromkeys(("config set", "tweaks set", "tweaks convert-loadout", "tweaks medical set", "profile create",
                     "profile edit", "profile delete"), 6),
    **dict.fromkeys(("settings check-path", "settings set", "migrate preview", "migrate apply"), 7),
}
# Commands built so far, by task
BUILT = {
    # Task 2.6: the reads of phase 2
    "status", "server status", "server players", "schedule show", "backup list", "updates status", "mods list",
    "steam show", "profile list", "profile show", "profile command", "profile missions", "config show",
    "tweaks show", "tweaks medical show", "logs", "settings show",
    # Task 3.3: the lifecycle commands of phase 3
    "server start", "server stop", "server restart", "schedule set", "schedule clear", "profile backup-after-stop",
}
# Commands that a later task still builds; each task removes its commands, and the table is empty at phase 9
PENDING = set(MATRIX) - BUILT


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

    def test_every_owner_method_is_used_internal_or_excluded(self) -> None:
        """Each of the 60 methods is in a command's bridge methods, or internal, or excluded (53 + 4 + 3)."""
        self.assertEqual(len(self.allowlist), 60)
        used = set().union(*(command.bridge_methods for command in COMMANDS))
        self.assertEqual(self.allowlist - used - INTERNAL_METHODS - EXCLUDED_METHODS, set())
        self.assertEqual(len(self.allowlist - INTERNAL_METHODS - EXCLUDED_METHODS), 53)
        # Internal and excluded methods are never named by a command, and every named method exists
        self.assertEqual(used & (INTERNAL_METHODS | EXCLUDED_METHODS), set())
        self.assertEqual(used - self.allowlist, set())
        self.assertTrue(INTERNAL_METHODS | EXCLUDED_METHODS <= self.allowlist)

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
