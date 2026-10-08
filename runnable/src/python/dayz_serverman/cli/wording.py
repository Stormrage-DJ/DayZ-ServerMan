"""CLI-only sentences: refusals, profile and argument errors, and the way-out table (design 11.3).

Every sentence is built from typed parts (`cli/output.py`), so an input name appears only
as text to type (`TypeText`) or as an echo of what the operator typed (`Echo`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..application.activity_wording import error_text
from .output import Echo, Line, SentencePart, TypeText, Value, sentence

# D2: another holder of the instance lock (3.1); the holder part is filled by `instance_active`
INSTANCE_ACTIVE = "Another DayZ-ServerMan is active for this manager folder{holder}. Nothing was changed. Try again when it has finished."
# A5: the CLI cannot take the byte-range lock in this folder (3.1, 3.2)
INSTANCE_LOCK_UNSUPPORTED = ("DayZ-ServerMan cannot protect this manager folder against a second copy. "
                             "Move the folder to a local drive.")
# A13: an observer read waited 30 s for an owner's swap step (3.4)
FOLDER_BUSY = "The server files are being changed by another DayZ-ServerMan. Try again in a minute."
# 4.5: a read that cannot answer on a root without a data folder
NOT_SET_UP = ("This manager folder is not set up yet. Open DayZ-ServerMan once, "
              "or run a command that changes something.")
# A registered command whose implementation comes with a later phase of the plan
NOT_AVAILABLE = "This command is not available in this version yet."
# Unexpected failure inside the CLI; the details stay in the diagnostics log
INTERNAL = "Something went wrong inside DayZ-ServerMan."

# Waiter notes (design 7)
CANCEL_REQUESTED = "Cancelling at the next safe point."
NOT_CANCELLABLE = "This step cannot be cancelled. DayZ-ServerMan waits until it is finished or undone."
ALREADY_CANCELLING = "Already cancelling. Please wait."
SIGN_IN_PROMPT = "Steam sign-in: answer the SteamCMD questions below."
# Ctrl+C during a read, or before the next step of a flow (design 7, "Ctrl+C at other points")
READ_CANCELLED = "Cancelled. Nothing was changed."
STEP_CANCELLED = "Cancelled. The next step was not started."
# Confirmation (design 8.1)
NOTHING_CHANGED = "Nothing was changed."
# The last line of a declined or refused `mods update` after its download (criterion 24)
MODS_DOWNLOADED = "The mods are downloaded; nothing was applied."

# Way out of a catalogue text: GUI page sentences become CLI commands, by exact substring (11.3)
WAY_OUT: tuple[tuple[str, tuple[SentencePart, ...]], ...] = (
    ("Details are in Logs, Manager diagnostics.",
     ("Details: run ", TypeText("logs --source diagnostics"), ".")),
    ("Open Backups to finish the restore.", ("Run ", TypeText("backup recover"), " to finish the restore.")),
    ("Check the folders in Settings and try again.",
     ("Check the folders with ", TypeText("settings show"), ", then try again.")),
    ("Set a runtime profile directory in Profiles first.",
     ("Set a runtime profile directory with ", TypeText("profile edit"), " first.")),
    ("Turn on “Use gameplay configuration” in Configuration first.",
     ("Turn on the gameplay configuration with ", TypeText("config set"), " first.")),
    ("Open another page, return to this one, and try again.", ("Run the command again.",)),
    ("Review and apply the mods before the server starts.",
     ("Run ", TypeText("mods update"), " before the server starts.")),
    # Rows added in 2.5: further catalogue texts that name a GUI page (Realization of 2.5)
    ("Open Backups; DayZ-ServerMan checks the unfinished restore again there.",
     ("Run ", TypeText("backup recover"), "; DayZ-ServerMan checks the unfinished restore again.")),
    ("Then save, and open Backups or restart DayZ-ServerMan.",
     ("Then run ", TypeText("backup recover"), ", or restart DayZ-ServerMan.")),
    ("then open Backups again or restart DayZ-ServerMan.",
     ("then run ", TypeText("backup recover"), " or restart DayZ-ServerMan.")),
    ("In Settings, set the DayZ server folder", ("With ", TypeText("settings set"), ", set the DayZ server folder")),
    ("The result is on the Mods page.", ("Run ", TypeText("mods list"), " for the result.")),
    ("Check the server state on Overview.", ("Check the server state with ", TypeText("server status"), ".")),
    # Rows added in 2.6: the server build texts that the read commands print
    ("Check its folder in Settings", ("Check its folder with ", TypeText("settings show"))),
    ("no DayZ server folder is set in Settings", ("no DayZ server folder is set; set it with ", TypeText("settings set"))),
    ("Set its folder in Settings", ("Set its folder with ", TypeText("settings set"))),
)


def way_out(text: str) -> Line:
    """Return a catalogue text as one line, with each GUI way out replaced by its CLI command."""
    pieces: list[SentencePart] = [text]
    for needle, replacement in WAY_OUT:
        expanded: list[SentencePart] = []
        for piece in pieces:
            if not isinstance(piece, str) or needle not in piece:
                expanded.append(piece)
                continue
            # Split around each occurrence and put the CLI parts in place of the page sentence
            chunks = piece.split(needle)
            for index, chunk in enumerate(chunks):
                expanded.append(chunk)
                if index < len(chunks) - 1:
                    expanded.extend(replacement)
        pieces = expanded
    return sentence(*pieces)


def bridge_error_line(error: Mapping[str, Any], kind: object = None) -> Line:
    """Word a bridge or operation error with the GUI catalogue and the CLI way out; a code is never printed."""
    details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
    text = error_text(error.get("code"), error.get("message"), owner=details.get("owner"), kind=kind)
    return way_out(text)


def instance_active(holder: Any) -> Line:
    """Word the D2 refusal, naming a live holder when its details are known."""
    if holder is None:
        named = ""
    elif getattr(holder, "holder", None) == "window":
        named = ": the window"
    elif getattr(holder, "command", None):
        named = f": the command {holder.command}"
    else:
        named = ""
    return sentence(INSTANCE_ACTIVE.format(holder=named))


def no_profile_named(option: str) -> Line:
    """Word a `--profile` value that names no profile (6.6)."""
    return sentence("No profile is named ", Echo(option), ".")


def profile_ambiguous(option: str, identifiers: Iterable[str]) -> Line:
    """Word a `--profile` value that names several profiles, listing their IDs to type (6.6)."""
    named: list[SentencePart] = []
    for index, identifier in enumerate(sorted(identifiers)):
        named.extend((", " if index else "", TypeText(identifier)))
    return sentence("Several profiles are named ", Echo(option), ". Name one of them by its ID: ", *named, ".")


def no_profile_exists() -> Line:
    """Word a command that needs a profile when none exists (6.6)."""
    return sentence("No profile exists. Create one with ", TypeText("profile create"), ".")


def name_the_profile(running_name: str | None) -> Line:
    """Word a command that needs `--profile` because several profiles exist (6.6)."""
    # The running profile's name is data, shown as prose
    running: Sequence[SentencePart] = (f" The server runs with {running_name}.",) if running_name else ()
    return sentence("Name the profile with ", TypeText("--profile"), ".", *running)


def unknown_backup(backup_id: str) -> Line:
    """Word a backup ID that the profile's backup list does not hold (6.4.1)."""
    return sentence("No backup of this profile has the ID ", Echo(backup_id), ". Run ", TypeText("backup list"),
                    " for the IDs.")


def unknown_key(key: str, show_command: str) -> Line:
    """Word a `--set` key that the loaded definitions do not hold (10.6)."""
    return sentence("Unknown key ", Echo(key), ". Run ", TypeText(show_command), " for the keys.")


def duplicate_key(key: str) -> Line:
    """Word a `--set` key given twice (10.6)."""
    return sentence("The key ", Echo(key), " is given twice.")


def invalid_set(value: str) -> Line:
    """Word a `--set` value without the key=value form."""
    return sentence("Write each change as key=value: ", Echo(value), ".")


def invalid_file(path: str) -> Line:
    """Word a `--from-file` file that is not a UTF-8 JSON object (10.6)."""
    return sentence("The file ", Echo(path), " is not a JSON object.")


def overwrite_needs_replace() -> Line:
    """Word `--overwrite` without the replacing storage choice (6.4.1)."""
    return sentence("Use ", TypeText("--overwrite"), " only with ", TypeText("--storage replace"), ".")


def usage(message: str, command: str | None) -> Line:
    """Word a command line that the parser refused, with the help command to type."""
    help_command = f"help {command}" if command else "help"
    return sentence("The command line is not valid: ", Echo(message), ". Run ", TypeText(help_command),
                    " for the commands and options.")


def confirmation_needed(nothing_changed: str = NOTHING_CHANGED) -> Line:
    """Word a confirmation that could not be asked: no terminal, or JSON without --yes (8.1).

    Another closing sentence than "Nothing was changed." comes last, so the text output ends
    with it ("The mods are downloaded; nothing was applied.", criterion 24).
    """
    if nothing_changed != NOTHING_CHANGED:
        return sentence("Confirmation is needed. Run the command again with ", TypeText("--yes"),
                        f". {nothing_changed}")
    return sentence(f"Confirmation is needed. {nothing_changed} Run the command again with ", TypeText("--yes"), ".")


def not_interactive() -> Line:
    """Word Steam sign-in without a terminal or with JSON output (8.1, D3)."""
    return sentence("Steam sign-in needs a terminal. Run it in a terminal without ", TypeText("--json"), ".")


def missing_configuration(target: str, profile_name: str) -> Line:
    """Word a configuration file of a resolved profile that does not exist (criterion 27)."""
    return sentence(f"The {target} configuration file of profile ", Value(profile_name), " does not exist. "
                    "Check the profile with ", TypeText("profile show"), ", or restore it from a backup with ",
                    TypeText("backup restore"), ".")


def missing_mission(profile_name: str) -> Line:
    """Word mission files of a resolved profile that are missing or lie outside the server folder (criterion 27)."""
    return sentence("The mission files of profile ", Value(profile_name), " are missing or lie outside the DayZ "
                    "server folder. Check the profile with ", TypeText("profile show"), ", or restore it from a "
                    "backup with ", TypeText("backup restore"), ".")


def unknown_feature(feature: str) -> Line:
    """Word a medical feature name that the profile's feature list does not hold (criterion 27)."""
    return sentence("No medical loot setting has the feature name ", Echo(feature), ". Run ",
                    TypeText("tweaks medical show"), " for the feature names.")


def not_ready(seconds: int) -> Line:
    """Word a `--wait-ready` limit that passed; the started server keeps running (section 7)."""
    return sentence(f"The server was started but was not ready within {seconds} seconds. It keeps running.")


def server_left_running(state_label: str) -> Line:
    """Word a server that stopped running under DayZ-ServerMan before it was ready (section 7)."""
    return sentence(f"The server did not become ready. Its state is now: {state_label}.")


def ready_wait_stopped() -> Line:
    """Word Ctrl+C during or before the readiness wait after a succeeded start (criterion 29)."""
    return sentence("The server was started. DayZ-ServerMan stopped waiting for it to be ready; it keeps running.")


def other_profile_running(running_name: str) -> Line:
    """Word the D11 refusal: the GUI reason, the CLI way out and the running profile (criteria 4 and 26)."""
    # The first sentence is the host reason of OTHER_PROFILE_RUNNING; its second names the window's selection
    return sentence("The running server was started with another profile. Name that profile with ",
                    TypeText("--profile"), " to stop or restart the server. The server runs with ",
                    Value(running_name), ".")


def no_runtime_profile() -> Line:
    """Word `backup create` for a profile without a runtime profile directory (criterion 30; the window's notice)."""
    return sentence("Set a runtime profile directory with ", TypeText("profile edit"), " before creating a backup.")


def recovery_clear() -> Line:
    """Word `backup recover` when no unfinished restore blocks changes."""
    return sentence("No unfinished restore blocks changes.")


def revision_pinned(subject: str, current: object, pinned: int) -> Line:
    """Word an `--expect-…` pin that differs from the stored revision, with the read to repeat (QF-45)."""
    show = "profile show --json" if subject == "profile" else "settings show --json"
    return sentence(f"The {subject} revision is now {current}, not {pinned}. Read it again with ", TypeText(show),
                    ", then pin the new number.")


def mission_occupied() -> Line:
    """Word `--storage preserve` when another profile uses the original mission folder (QF-42)."""
    return sentence("The original mission folder is in use by another profile. Use ", TypeText("--storage new"),
                    ", or ", TypeText("--storage replace"), " with ", TypeText("--overwrite"), ".")


def no_archive_file(path: str) -> Line:
    """Word an `--archive` path where no file exists (QF-43)."""
    return sentence("No file at ", Echo(path), ".")


def overwrite_needed() -> Line:
    """Word a profile restore that replaces a world without `--overwrite` (design 8.1)."""
    return sentence("This restore replaces an existing world. Run the command again with ", TypeText("--overwrite"),
                    ".")
