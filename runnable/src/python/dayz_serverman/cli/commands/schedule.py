"""`schedule show`, `schedule set HH:MM stop/restart` and `schedule clear` (10.1)."""

from __future__ import annotations

from typing import Any

from ..flow import stop_if_interrupted
from ..output import CommandResult, sentence
from ..read_wording import schedule_texts
from .common import profile_id, profile_line, with_profile


def schedule_show(context: Any) -> CommandResult:
    """Show the daily stop or restart of the profile and its next and last run."""
    schedule = context.call("get_lifecycle_schedule", profile_id=profile_id(context.profile))
    summary, status = schedule_texts(schedule)
    return CommandResult(with_profile(schedule, context.profile),
                         [profile_line(context.profile), sentence(summary), sentence(status)])


def schedule_set(context: Any) -> CommandResult:
    """Save the daily stop or restart of the profile at a local time of day."""
    hour, minute = (int(part) for part in context.options.time.split(":"))
    return _save(context, hour, minute, context.options.action)


def schedule_clear(context: Any) -> CommandResult:
    """Turn the daily action off and keep the stored time, as the Overview's "None" choice does."""
    stop_if_interrupted(context.interrupts)
    stored = context.call("get_lifecycle_schedule", profile_id=profile_id(context.profile))
    return _save(context, int(stored.get("hour", 0)), int(stored.get("minute", 0)), None)


def _save(context: Any, hour: int, minute: int, action: str | None) -> CommandResult:
    """Save one schedule; Ctrl+C before the write exits 5 with nothing saved."""
    stop_if_interrupted(context.interrupts)
    saved = context.call("save_lifecycle_schedule", profile_id=profile_id(context.profile), hour=hour,
                         minute=minute, action=action)
    summary, status = schedule_texts(saved)
    value = {"operations": [], "review": None, "result": with_profile(saved, context.profile)}
    return CommandResult(value, [profile_line(context.profile), sentence(summary), sentence(status)])
