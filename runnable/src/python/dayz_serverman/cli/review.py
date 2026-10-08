"""Review rendering per preview type (design 8.2): text blocks for stdout and the raw value for JSON.

The lifecycle dialogs have no preview: their JSON review is the request they confirm. A backup
restore and a profile restore show the window's review of their preview. Reviews of later
phases (configuration, tweaks, mods, migration) come with their commands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..application.activity_wording import plain_sentence
from ..application import review_wording as texts
from ..application.review_wording import BACKUP_AFTER_STOP_SENTENCE, LIFECYCLE_DIALOGS
from .output import Block, Label, Table, Value, sentence


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


def backup_create_review(profile: Mapping[str, Any]) -> list[Block]:
    """Return the review of `backup create`: the title and body of the window's dialog (criterion 30)."""
    name = str(profile.get("display_name") or profile.get("profile_id") or "")
    return [sentence(texts.BACKUP_CREATE_TITLE), sentence("Server: ", Value(name), f". {texts.BACKUP_CREATE_BODY}")]


def restore_review(preview: Mapping[str, Any], local_time: Any) -> list[Block]:
    """Return the review of `backup restore`: the Backups page's summary, then its confirmation dialog.

    `local_time` words the backup's creation time as the read commands do.
    """
    blocks: list[Block] = [
        sentence(texts.RESTORE_REVIEW_TITLE, Value(local_time(preview.get("created_at")) or "")),
        sentence(f"{preview.get('replacement_count', 0)} replacements; {preview.get('creation_count', 0)} creations."),
    ]
    plan = plain_sentence(preview.get("recovery_plan"))
    if plan:
        blocks.append(sentence(plan))
    for target in preview.get("targets") or []:
        if isinstance(target, Mapping):
            kind = texts.RESTORE_TARGET_KINDS.get(str(target.get("target_kind")), texts.RESTORE_TARGET_FALLBACK)
            action = texts.RESTORE_ACTIONS.get(str(target.get("action")), texts.RESTORE_ACTION_FALLBACK)
            blocks.append(sentence(f"{kind} — {action}: ", Value(str(target.get("target_relative", "")))))
    title, warning = texts.RESTORE_DIALOG
    return [*blocks, sentence(title), sentence(warning)]


def profile_restore_review(preview: Mapping[str, Any], archive_name: str,
                           names: Mapping[str, str]) -> list[Block]:
    """Return the review of `profile restore`: the restore dialog's list, notices and world replacement.

    `names` maps the profile IDs of a replaced world to their display names.
    """
    labels = texts.PROFILE_RESTORE_LABELS
    profile = preview.get("profile") if isinstance(preview.get("profile"), Mapping) else {}
    policy = texts.STORAGE_POLICIES.get(str(preview.get("storage_policy")), texts.STORAGE_POLICY_FALLBACK)
    blocks: list[Block] = [
        sentence(texts.PROFILE_RESTORE_TITLE), sentence("Archive: ", Value(archive_name)),
        _profile_table(labels["profile"], ((str(profile.get("display_name", "")), str(profile.get("profile_id", ""))),)),
        *(_row(labels[key], str(profile.get(key, ""))) for key in ("server_config", "runtime_profile")),
        _row(labels["mission_root"], str(preview.get("mission_root", ""))),
        _row(labels["storage"], f"storage_{preview.get('instance_id')}"),
        _row(labels["ports"], f"{preview.get('game_port')} / query {preview.get('steam_query_port')}"),
        sentence(f"{labels['storage_policy']}: ", Label(policy)),
        sentence(texts.PROFILE_RESTORE_SENTENCE),
    ]
    # The host's warnings, as the dialog shows them
    blocks.extend(sentence(text) for text in map(plain_sentence, preview.get("warnings") or []) if text)
    if not preview.get("server_executable_present", True):
        blocks.append(sentence(texts.EXECUTABLE_MISSING))
    missing = [str(item) for item in preview.get("missing_mods") or []]
    if missing:
        blocks.append(sentence("Missing mods: ", Value(", ".join(missing))))
    if preview.get("storage_policy") == "replace_existing":
        affected = [str(item) for item in preview.get("affected_profile_ids") or []]
        blocks += [sentence(texts.REPLACE_WORLD_LEAD),
                   _profile_table("Profile", tuple((names.get(item, item), item) for item in affected)),
                   sentence(texts.REPLACE_WORLD_TAIL)]
    return blocks


def _row(label: str, value: str) -> Block:
    """Return one "Label: value" line of stored data."""
    return sentence(f"{label}: ", Value(value))


def _profile_table(heading: str, rows: tuple[tuple[str, str], ...]) -> Table:
    """Return profiles as a table: the display name first, the ID in its own input column (criterion 18)."""
    return Table((heading, "ID"), rows, input_columns=frozenset({1}), value_columns=frozenset({0}))
