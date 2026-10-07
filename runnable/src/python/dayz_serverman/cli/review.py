"""Review rendering per preview type (design 8.2): text blocks for stdout and the raw value for JSON.

The lifecycle dialogs have no preview: their JSON review is the request they confirm. Reviews
of later phases (configuration, tweaks, restores, mods, migration) come with their commands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..application.review_wording import BACKUP_AFTER_STOP_SENTENCE, LIFECYCLE_DIALOGS
from .output import Block, Value, sentence


def lifecycle_review(action: str, profile: Mapping[str, Any], backup_after_stop: bool | None = None
                     ) -> tuple[list[Block], dict[str, Any]]:
    """Return the review of a start, stop or restart: the dialog's title and body, the profile, the backup choice.

    `backup_after_stop` is None for a start, which has no such choice.
    """
    title, body = LIFECYCLE_DIALOGS[action]
    if backup_after_stop and action != "start":
        body = f"{body} {BACKUP_AFTER_STOP_SENTENCE}"
    name = str(profile.get("display_name") or profile.get("profile_id") or "")
    blocks: list[Block] = [sentence(title), sentence(body), sentence("Profile: ", Value(name))]
    value: dict[str, Any] = {"action": action, "profile_id": profile.get("profile_id")}
    if backup_after_stop is not None:
        blocks.append(sentence(f"Backup after stop: {'yes' if backup_after_stop else 'no'}"))
        value["backup_after_stop"] = backup_after_stop
    return blocks, value
