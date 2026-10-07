"""`updates status` (10.1); `updates check` and `updates auto` come with phase 5."""

from __future__ import annotations

from typing import Any

from ..output import CommandResult, Line, sentence
from ..read_wording import build_summary, mods_summary
from ..wording import way_out
from .common import automatic_checks, labelled, local_time, profile_id, profile_line, with_profile


def updates_status(context: Any) -> CommandResult:
    """Show the mod update state and the server build state of the last checks."""
    status = context.call("get_update_status", profile_id=profile_id(context.profile))
    automatic = automatic_checks(context)
    lines: list[Line] = [profile_line(context.profile), way_out(f"Mods: {mods_summary(status, automatic)}")]
    mods = status.get("mods") or {}
    if mods.get("last_success_at"):
        lines.append(labelled("Mods last checked", local_time(mods["last_success_at"])))
    build = status.get("server_build")
    lines.append(way_out(f"DayZ server: {build_summary(build, automatic)}"))
    if isinstance(build, dict):
        if build.get("installed_build") is not None:
            lines.append(labelled("Installed build", str(build["installed_build"])))
        if build.get("available_build") is not None:
            lines.append(labelled("Build on Steam", str(build["available_build"])))
        if build.get("last_success_at"):
            lines.append(labelled("Server build last checked", local_time(build["last_success_at"])))
    lines.append(sentence(f"Automatic update checks: {'on' if automatic else 'off'}."))
    return CommandResult(with_profile(status, context.profile), lines)
