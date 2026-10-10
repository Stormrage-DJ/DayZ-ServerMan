"""Operator wording for host sentences: the path roles inside a sentence and the reasons of a recovery block.

Python sibling of `activity_wording.py`. The frontend file `host_sentences.js` holds the same block reasons;
tests keep the two catalogues equal. A raw role is a key here and is never printed.
"""

from __future__ import annotations

import re

# Reason shown when a block reason is neither known nor a plain sentence
BLOCK_FALLBACK = "An earlier operation did not finish cleanly."
# The way out of a block that has no catalogue sentence: a restart checks again (QF-045)
BLOCK_RESTART_ACTION = "Restart DayZ-ServerMan to check again."
# The way out of such a block when a restore owns it: the Backups page finishes the restore
BLOCK_RESTORE_ACTION = "Open Backups to finish the restore."
# Operation kind of a restore apply; the blocks it owns are lifted on the Backups page
BLOCK_RESTORE_OWNER = "RESTORE_BACKUP"

# Reason sentence of an unfinished legacy import; the host words this block in two ways
_IMPORT_BLOCK = ("A legacy import was interrupted and could not be undone safely. "
                 "Restart DayZ-ServerMan; it checks the unfinished import again when it starts.")
# Known reasons of a recovery block, by a fragment of the host reason; the first match wins
BLOCK_REASONS: tuple[tuple[str, str], ...] = (
    # A direct profile restore is worded by cause (QF-075); these rows come before "restore recovery"
    ("Direct profile restore journals", "A profile restore from a backup archive did not finish, and its restore records cannot be read, so DayZ-ServerMan cannot finish or undo it. Restart DayZ-ServerMan to read them again."),
    ("Direct profile restore recovery requires a configured DayZ root", "A profile restore from a backup archive did not finish, and it cannot be checked because no DayZ server folder is set. In Settings, set the DayZ server folder that the restore used and change nothing else. Then save and restart DayZ-ServerMan."),
    ("Direct profile restore recovery requires attention", "A profile restore from a backup archive did not finish, and DayZ-ServerMan cannot finish or undo it safely because the DayZ server folder or the restored files changed or cannot be opened. If a drive or folder was unavailable, make it available again, then restart DayZ-ServerMan."),
    ("Direct profile restore requires recovery", "A profile restore from a backup archive did not finish. Stop the DayZ server, then restart DayZ-ServerMan; it checks the unfinished restore when it starts."),
    # A backup restore without a DayZ server folder (QF-069); it contains "restore recovery", so it comes first
    ("Backup restore recovery requires a configured DayZ root", "A backup restore did not finish, and it cannot be checked because no DayZ server folder is set. In Settings, set the DayZ server folder that the restore used and change nothing else. Then save, and open Backups or restart DayZ-ServerMan."),
    ("restore recovery", "A backup restore did not finish cleanly. Open Backups; DayZ-ServerMan checks the unfinished restore again there."),
    ("mod publication recovery", "Applying mods to the server folder was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the server folder again when it starts."),
    ("unresolved mod publication", "Applying mods to the server folder was interrupted, and it cannot be checked because no DayZ server folder is set. In Settings, set the DayZ server folder that the apply used and change nothing else. Then save and restart DayZ-ServerMan."),
    ("interrupted mod publication", "Applying mods to the server folder was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted backup restore", "A backup restore was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then open Backups again or restart DayZ-ServerMan."),
    ("interrupted direct profile restore", "A profile restore from a backup archive was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted profile creation", "Creating a profile was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted SteamCMD update", "A mod update was interrupted, so its result is not known. Restart DayZ-ServerMan, then update the mods again."),
    ("process-tree exit after the sign-in", "SteamCMD did not close cleanly after the Steam sign-in, so the sign-in cannot be confirmed. Close SteamCMD, restart DayZ-ServerMan, then sign in again."),
    ("process-tree exit", "SteamCMD did not close cleanly, so the mod update cannot be confirmed. Close SteamCMD, restart DayZ-ServerMan, then update the mods again."),
    ("Profile provisioning recovery", "Creating a profile was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the unfinished profile again when it starts."),
    ("migration recovery", _IMPORT_BLOCK),
    ("Migration publication", _IMPORT_BLOCK),
)
# Label of each path role and settings field inside a sentence
ROLE_LABELS: dict[str, str] = {
    "dayz_root": "DayZ server folder", "dayz_executable": "DayZ server program",
    "steamcmd_root": "SteamCMD folder", "steamcmd_executable": "SteamCMD program",
    "workshop_content_root": "Workshop download folder",
    "backup_root": "backup folder", "custom_backup_root": "backup folder",
}

# An upper-case identifier, a snake_case word, a camelCase word, or a bracketed list of names
_IDENTIFIER = re.compile(r"[A-Z]{2,}_[A-Z_]+|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]+[A-Z]\w*|[\[\]{}]")
# A path role as a whole word, the longest role first
_ROLE_NAME = re.compile(r"\b(" + "|".join(sorted(ROLE_LABELS, key=len, reverse=True)) + r")\b")


def leaks_identifier(text: str) -> bool:
    """Report whether a text still holds something that is not operator wording."""
    return _IDENTIFIER.search(text) is not None


def plain_sentence(message: object) -> str | None:
    """Return a host message as one operator sentence, or None when it cannot be shown.

    Path roles become their labels. A bare code, an empty text, and a text that
    still holds an identifier are refused.
    """
    text = _ROLE_NAME.sub(lambda match: f"the {ROLE_LABELS[match.group(1)]}", str(message or "")).strip()
    text = text.replace("DayZ root", "DayZ server folder")
    if " " not in text or leaks_identifier(text):
        return None
    # Start with a capital letter and end with a full stop
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else f"{text}."


def block_reason_text(reason: object, owner: object = None) -> str:
    """Word why changes are blocked: the known sentence, else the host sentence or the fallback with its way out."""
    text = str(reason or "")
    # A known reason has its own sentence, which already names the way out
    for fragment, sentence in BLOCK_REASONS:
        if fragment in text:
            return sentence
    # The owner of the block chooses the way out of any other reason
    action = BLOCK_RESTORE_ACTION if owner == BLOCK_RESTORE_OWNER else BLOCK_RESTART_ACTION
    return f"{plain_sentence(text) or BLOCK_FALLBACK} {action}"
