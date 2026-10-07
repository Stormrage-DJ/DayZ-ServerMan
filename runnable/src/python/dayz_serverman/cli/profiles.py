"""Profile resolution of a command (P4, design 6.6): `--profile` by ID or name, else the default rule."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .exit_codes import USAGE
from .output import CliFailure
from .registry import ProfileRule
from .wording import name_the_profile, no_profile_exists, no_profile_named, profile_ambiguous

# A bridge call: method name and keyword parameters
Call = Callable[..., Any]


def resolve_profile(call: Call, rule: ProfileRule, option: str | None, *,
                    optional: bool = False) -> Mapping[str, Any] | None:
    """Return the profile summary that the command acts on; refuse with exit 2 when none fits.

    `optional` lets a read without a fitting profile go on without one (`status`).
    """
    profiles = [profile for profile in call("list_profiles") if isinstance(profile, Mapping)]
    if option is not None:
        return _named(profiles, option)
    if rule is ProfileRule.WRITE_REQUIRED:
        if len(profiles) == 1:
            return profiles[0]
        _refuse_default(call, profiles)
    # READ_DEFAULT: the profile selected in the window, else the only profile
    selected = call("get_ui_preferences").get("selected_profile_id")
    chosen = next((profile for profile in profiles if profile.get("profile_id") == selected), None)
    if chosen is None and len(profiles) == 1:
        chosen = profiles[0]
    if chosen is None and not optional:
        _refuse_default(call, profiles)
    return chosen


def _named(profiles: list[Mapping[str, Any]], option: str) -> Mapping[str, Any]:
    """Return the profile with this ID, else the one whose name equals it without regard to case."""
    for profile in profiles:
        if profile.get("profile_id") == option:
            return profile
    named = [profile for profile in profiles if str(profile.get("display_name", "")).casefold() == option.casefold()]
    if len(named) == 1:
        return named[0]
    if not named:
        raise CliFailure("USAGE", no_profile_named(option), USAGE)
    raise CliFailure("USAGE", profile_ambiguous(option, (str(profile.get("profile_id")) for profile in named)),
                     USAGE)


def _refuse_default(call: Call, profiles: list[Mapping[str, Any]]) -> None:
    """Refuse a command without `--profile`: no profile at all, or several of them."""
    if not profiles:
        raise CliFailure("USAGE", no_profile_exists(), USAGE)
    raise CliFailure("USAGE", name_the_profile(_running_name(call, profiles)), USAGE)


def _running_name(call: Call, profiles: list[Mapping[str, Any]]) -> str | None:
    """Return the name of the profile that the server runs with, when the status names one."""
    running = call("get_server_status").get("profile_id")
    for profile in profiles:
        if running is not None and profile.get("profile_id") == running:
            return str(profile.get("display_name") or running)
    return None
