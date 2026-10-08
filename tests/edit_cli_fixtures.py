"""Fixtures of the phase 6 editing commands: the lifecycle root with mission files and a recovery block.

The root is the one of `lifecycle_cli_fixtures` (real compositions, two profiles, a fake process
table); these helpers add the medical files, a legacy starter loadout and an unreadable restore
journal, and give the paths that the edit commands write.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import LifecycleRoot  # noqa: E402
from session_fixtures import PROFILE_ID  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402

PROFILE = ["--profile", PROFILE_ID]
# A legacy starter loadout block that the Tweaks page offers to convert (two items on lines 5 and 6)
LEGACY_INIT = ('class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n'
               '\t{\n\t\tEntityAI itemEnt;\n'
               '\t\titemEnt = player.GetInventory().CreateInInventory( "WaterBottle" );\n'
               '\t\titemEnt = player.GetInventory().CreateAttachment( "HipPack_Medical" );\n\t}\n};\n')
# The medical item spawns file of the fixture mission: one medical type
TYPES_XML = ('<?xml version="1.0"?><types><type name="BandageDressing">'
             '<nominal>1</nominal><min>1</min></type></types>')


class EditRoot(LifecycleRoot):
    """The lifecycle root with the files of the configuration and tweak edits."""

    @property
    def server_config(self) -> Path:
        """Return the fixture profile's generated server configuration."""
        return self.dayz / "serverman" / PROFILE_ID / "serverDZ.cfg"

    @property
    def mission(self) -> Path:
        """Return the fixture profile's mission folder."""
        return self.dayz / "mpmissions" / "dayzOffline.enoch"

    def add_medical_files(self) -> None:
        """Write the two medical files that `tweaks medical` reads."""
        (self.mission / "db" / "types.xml").write_text(TYPES_XML, encoding="utf-8")
        (self.mission / "mapgroupproto.xml").write_text("<groups/>", encoding="utf-8")

    def add_legacy_starter(self) -> None:
        """Write an init.c with a legacy starter loadout block."""
        (self.mission / "init.c").write_text(LEGACY_INIT, encoding="utf-8")

    def broken_journal(self) -> None:
        """Leave an unreadable restore journal, so the next owner session blocks changes for recovery."""
        journals = self.manager / "data" / "operations" / "restore-journals"
        journals.mkdir(parents=True, exist_ok=True)
        (journals / "broken.json").write_text("{}", encoding="utf-8")


class ChangingTerminal(FakeTerminal):
    """A terminal whose "y" answer comes after a change by someone else, so the reviewed request is stale."""

    def __init__(self, change) -> None:
        """Keep the change to make while the question waits."""
        super().__init__("y\n")
        self.change = change

    def readline(self, *args: object) -> str:
        """Change the file, then answer yes."""
        self.change()
        return super().readline(*args)


__all__ = ["ChangingTerminal", "EditRoot", "FakeTerminal", "LEGACY_INIT", "PROFILE", "PROFILE_ID"]
