"""The apply review of `mods update` (8.2; 10.3 step 6): the variant per request and server state, and its text.

A copy of the Mods page's `publicationVariant` and `publicationReviewLines` (`frontend/mod-publication.js`),
with one difference that 10.3 fixes: the CLI never switches between a start and a restart by
itself. `--start` needs a stopped server and `--restart` a server that this manager runs; any
other state is the "changed" row, which applies nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...application.review_wording import (
    MOD_KEYS_ADDED, MOD_KEYS_IN_PLACE, MOD_REVIEW_SERVER_STATES, MOD_REVIEW_STATE_UNCONFIRMED, MOD_REVIEW_TEXTS,
)
from ..mods_wording import refused_advice, use_restart
from ..output import Block, SentencePart, Value, sentence

TEXTS = MOD_REVIEW_TEXTS
# What a variant submits: the plain apply (with the start that the update recorded), or the restart
PUBLISH, RESTART = "publish", "restart"


@dataclass(frozen=True)
class Variant:
    """One row of the review: its key, what the confirmation submits (None: nothing) and its sentence."""

    key: str
    submit: str | None
    sentence: tuple[SentencePart, ...]


def plan_writes(preview: Mapping[str, Any]) -> bool:
    """Report whether the reviewed plan writes into the server folder (`publicationPlanWrites`)."""
    targets = [item for item in preview.get("targets") or [] if isinstance(item, Mapping)]
    return any(item.get("current") is not True for item in targets) or _number(preview.get("missing_key_count")) > 0


def variant(start: bool, restart: bool, status: Mapping[str, Any], profile_id: str | None,
            preview: Mapping[str, Any], backup: bool) -> Variant:
    """Choose the row of the review for the request and the server state read now (10.3 step 6)."""
    state = status.get("state")
    # The window offers a restart only while the server runs with this profile (D11)
    offered = state == "RUNNING_MANAGED" and status.get("profile_id") in (None, profile_id)
    changed = Variant("changed", None, (TEXTS["changed"],))
    if start:
        return Variant("start", PUBLISH, (TEXTS["start"],)) if state == "STOPPED" else changed
    if restart:
        if state != "RUNNING_MANAGED":
            return changed
        key, text = ("restart-backup", TEXTS["restartBackup"]) if backup else ("restart", TEXTS["restart"])
        return Variant(key, RESTART, (text,))
    if state == "STOPPED":
        return Variant("plain", PUBLISH, (TEXTS["noStart"],))
    named = state_sentence(state)
    # A plain apply while the server is not stopped follows the policy that the host reports (D10: refuse)
    if preview.get("plain_apply_guarded") is not True:
        return Variant("attempt", PUBLISH, (named, TEXTS["inUse"], *use_restart(offered)))
    if plan_writes(preview):
        return Variant("refused", None, (named, TEXTS["refused"], *refused_advice(state, offered)))
    return Variant("plain", PUBLISH, (TEXTS["noStart"],))


def state_sentence(state: object) -> str:
    """Word the server state that the review read (`reviewStateSentence`)."""
    return MOD_REVIEW_SERVER_STATES.get(str(state), MOD_REVIEW_STATE_UNCONFIRMED)


def review_blocks(chosen: Variant, preview: Mapping[str, Any], short: bool) -> list[Block]:
    """Return the review text: a question's title, lead and sentence, or the "not applied" form; then the plan."""
    if chosen.submit is not None:
        blocks: list[Block] = [sentence(TEXTS["title"]), sentence(TEXTS["lead"]), sentence(*chosen.sentence)]
    else:
        blocks = [sentence(TEXTS["notAppliedTitle"]), sentence(*chosen.sentence), sentence(TEXTS["notAppliedLead"])]
    return [*blocks, *plan_lines(preview, short)]


def plan_lines(preview: Mapping[str, Any], short: bool) -> list[Block]:
    """List what the plan writes: the mod folders that are not current and the key files (`publicationReviewLines`)."""
    targets = [item for item in preview.get("targets") or [] if isinstance(item, Mapping)]
    folders: list[Block] = [] if short else [
        sentence("Workshop ", Value(str(item.get("workshop_id", ""))), ": ", Value(str(item.get("target_relative", ""))))
        for item in targets if item.get("current") is not True]
    missing, total = _number(preview.get("missing_key_count")), _number(preview.get("key_count"))
    keys = sentence(f"{missing} of {total} {MOD_KEYS_ADDED}" if missing > 0 else f"{total} {MOD_KEYS_IN_PLACE}")
    if folders:
        return [*folders, keys]
    return [sentence(TEXTS["short"]), keys] if missing > 0 else [sentence(TEXTS["nothing"])]


def _number(value: object) -> int:
    """Return a count of the preview, or 0 when it is not a whole number."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
