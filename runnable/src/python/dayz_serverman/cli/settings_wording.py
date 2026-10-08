"""CLI sentences of the settings commands (phase 7): folder arguments, `settings set` and its restart notice.

Every sentence is built from typed parts (`cli/output.py`): a folder that the operator typed is an
`Echo`, an option to type is `TypeText` (criterion 18, design 11.3).
"""

from __future__ import annotations

from pathlib import Path

from .exit_codes import USAGE
from .output import CliFailure, Echo, Line, SentencePart, TypeText, sentence

# Which backup folder is in use, as `settings show` words it
BACKUP_CUSTOM = "Backups go to the custom backup folder."
BACKUP_PORTABLE = "Backups go to the portable default folder, which moves with the DayZ-ServerMan folder."
# `frontend/settings.js` SETTINGS_RESTART_TEXT: the notice after a save that set the DayZ server folder while
# changes were blocked for interrupted work (QF-069)
SETTINGS_RESTART = "Restart DayZ-ServerMan. It then checks the interrupted work in this DayZ server folder."
# What that restart means for a command line: every command that changes something starts DayZ-ServerMan anew
RESTART_FOR_COMMANDS = ("Each command that changes something starts DayZ-ServerMan anew and does this check "
                        "first; opening the window does it as well.")
# The options of `settings set`, in the order of its help
SET_OPTIONS = ("--dayz-root", "--steamcmd-root", "--backup-root", "--default-backup-root")


def folder_path(text: str) -> str:
    """Return a folder argument as an absolute path (the folder dialog gives one); empty text exits 2."""
    if not text.strip():
        raise CliFailure("USAGE", sentence("Give a folder. An empty text names no folder."), USAGE)
    return str(Path(text).absolute())


def no_location_named() -> Line:
    """Word `settings set` without any folder to change."""
    pieces: list[SentencePart] = []
    for index, option in enumerate(SET_OPTIONS):
        pieces.extend(("" if not index else " or " if index == len(SET_OPTIONS) - 1 else ", ", TypeText(option)))
    return sentence("Name at least one folder to change with ", *pieces, ".")


def folder_unusable(text: str, explanation: str) -> Line:
    """Word a folder that the folder dialog could not have given: none exists at the path, or it is a file."""
    return sentence("The folder ", Echo(text), " cannot be used. ", explanation)


def restart_needed() -> list[Line]:
    """Word the restart that a save through the "no DayZ server folder" block asks for (QF-069)."""
    return [sentence(SETTINGS_RESTART), sentence(RESTART_FOR_COMMANDS)]
