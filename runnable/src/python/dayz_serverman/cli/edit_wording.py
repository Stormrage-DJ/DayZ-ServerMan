"""CLI-only sentences of the editing commands of phase 6: value typing, profile keys and the edit reviews.

Every sentence is built from typed parts (`cli/output.py`): a key or a value that the operator
typed is an `Echo`, a command or an option to type is `TypeText` (criterion 18, design 11.3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..application.activity_wording import plain_sentence
from .output import Echo, Line, SentencePart, TypeText, Value, sentence

# What a `--set` value must be, by the type that the loaded definitions give its key (10.6)
EXPECTED_BOOLEAN = "on or off (also true or false, yes or no, 1 or 0)"
EXPECTED_INTEGER = "a whole number"
EXPECTED_NUMBER = "a number"
EXPECTED_ARRAY = "a JSON array"
EXPECTED_OBJECT = "a JSON object"
# The review of `tweaks set` when the preview takes over the marked block of the file (8.2)
MARKER_TAKEN_OVER = "The marked block of the file is taken over."
# A secret configuration value in the review: never printed (8.2)
SECRET_NOW, SECRET_NEW = "(hidden)", "(changed)"
# `application/profile_deletion.py`: the reason of a profile deletion while the server is not stopped
DELETE_NEEDS_STOP = "Stop the DayZ server before deleting a profile."
# `application/mission_configuration.py`: the reason of a conversion when the starter loadout has no legacy block
NOTHING_TO_CONVERT = " has no recognizable legacy starter loadout to convert."
# The starter loadout file that the reason names, as stored data
STARTER_FILE = "init.c"


def invalid_value(key: str, text: str, expected: str) -> Line:
    """Word a `--set` value that does not convert to the type of its key (10.6)."""
    return sentence("The value ", Echo(text), " of ", Echo(key), f" must be {expected}.")


def no_changes() -> Line:
    """Word an edit without any change to make."""
    return sentence("Name at least one change with ", TypeText("--set"), " or ", TypeText("--from-file"), ".")


def profile_id_fixed() -> Line:
    """Word `profile edit` that would change the profile ID (10.4)."""
    return sentence("A profile edit keeps the profile ID. Create a new profile with ", TypeText("profile create"),
                    " instead.")


def create_values_missing(keys: Iterable[str]) -> Line:
    """Word `profile create` without a value that the new profile needs, with each option to add."""
    pieces: list[SentencePart] = []
    for index, key in enumerate(keys):
        pieces.extend((", " if index else "", TypeText(f"--set {key}=VALUE")))
    return sentence("The new profile needs more values. Add ", *pieces, ".")


def changes_validated(count: int) -> Line:
    """Word the number of changes that the preview validated (the window's review notice)."""
    return sentence(f"{count} validated change(s).")


def nothing_to_convert() -> Line:
    """Word `tweaks convert-loadout` when the starter loadout has no legacy block (criterion 30)."""
    return sentence(Value(STARTER_FILE), NOTHING_TO_CONVERT)


def delete_needs_stop() -> Line:
    """Word `profile delete` while the server is not stopped (rule 5; the operation's reason)."""
    return sentence(DELETE_NEEDS_STOP)


# `frontend/profile_create.js`: the end of a profile creation, without the window's selection ("and selected")
PROFILE_READY = "Profile created. It is ready to start."
PROFILE_REUSED = "Profile recreated. Existing server files were reused; it is ready to start."
PROFILE_ATTENTION = "Profile created, but needs attention: "
PROFILE_ATTENTION_FALLBACK = "Check its paths."


def profile_created(result: Mapping[str, Any]) -> Line:
    """Word the end of `profile create` as the Profiles page does, from the operation's readiness."""
    readiness = result.get("readiness") if isinstance(result.get("readiness"), Mapping) else {}
    if readiness.get("ready"):
        return sentence(PROFILE_REUSED if result.get("reused_files") is True else PROFILE_READY)
    # Host reasons are shown only as operator sentences (criterion 18)
    reasons = [text for text in map(plain_sentence, readiness.get("reasons") or []) if text]
    return sentence(PROFILE_ATTENTION, " ".join(reasons) or PROFILE_ATTENTION_FALLBACK)
