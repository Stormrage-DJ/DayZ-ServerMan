"""Operator wording of the reviews and confirmation dialogs that the CLI shows before a change (A9, design 11.1).

Python sibling of `activity_wording.py`. Each text is a copy of a literal in the frontend file
named beside it; `tests/test_cli_review.py` checks that every one appears there unchanged.
Texts of later reviews (profile delete, restore, mod publication) come with their commands.
"""

from __future__ import annotations

# `frontend/overview_lifecycle_dialog.js`: title and body of the Overview's start, stop and restart dialogs
LIFECYCLE_DIALOGS: dict[str, tuple[str, str]] = {
    "start": ("Start DayZ server?", "Start the selected profile now."),
    "stop": ("Save and stop DayZ?", "Request a graceful save and wait for DayZ to close."),
    "restart": ("Save and restart DayZ?", "Save and stop the managed process, then start the selected profile."),
}
# The same file: the sentence that a stop or restart with "Backup after stop" adds to the body
BACKUP_AFTER_STOP_SENTENCE = "A verified backup will be created after DayZ stops."
