"""Shared pieces of the read commands: the profile line (6.6), labelled values, times and the P3 name rule."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ...adapters.windows.console import is_terminal
from ..bridge_client import CliBridgeError
from ..exit_codes import FAILED
from ..output import CliFailure, Line, SentencePart, Value, sentence


def profile_name(profile: Mapping[str, Any] | None) -> str:
    """Return the display name of a profile summary, else its stored ID as data."""
    if profile is None:
        return ""
    return str(profile.get("display_name") or profile.get("profile_id") or "")


def profile_line(profile: Mapping[str, Any] | None) -> Line:
    """Return the first line of a read with a profile: "Profile: <display name>" (6.6)."""
    if profile is None:
        return sentence("Profile: none")
    return sentence("Profile: ", Value(profile_name(profile)))


def profile_id(profile: Mapping[str, Any] | None) -> str | None:
    """Return the ID of the resolved profile, or None."""
    return None if profile is None else str(profile.get("profile_id"))


def with_profile(value: Any, profile: Mapping[str, Any] | None) -> Any:
    """Return an object value with `profile_id` set to the profile used (6.6); other values stay as they are."""
    if isinstance(value, Mapping) and profile is not None:
        return {**value, "profile_id": profile_id(profile)}
    return value


def labelled(label: str, *pieces: SentencePart) -> Line:
    """Return one "Label: value" line; plain strings after the label are stored data."""
    return sentence(f"{label}: ", *(Value(piece) if isinstance(piece, str) else piece for piece in pieces))


def local_time(text: object) -> str:
    """Return an ISO time as local "YYYY-MM-DD HH:MM", or the text as it is when it cannot be read."""
    if not isinstance(text, str) or not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def duration_text(seconds: object) -> str:
    """Return a connection time in operator words, or an empty text when it is not known."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        return ""
    minutes = int(seconds) // 60
    if minutes < 1:
        return "under a minute"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def names_allowed(context: Any) -> bool:
    """P3: names only with `--names`, or in text mode when stdout is a terminal (6.6)."""
    if getattr(context.options, "names", False):
        return True
    if context.output.json_mode:
        return False
    # QF-30: NUL counts as a terminal for isatty on Windows; a console check decides
    return is_terminal(context.output.stdout)


def yes_no(flag: object) -> str:
    """Word a stored on/off value."""
    return "Yes" if flag else "No"


def automatic_checks(context: Any) -> bool:
    """Return the saved choice of automatic update checks, which words a missing check."""
    return context.call("get_ui_preferences").get("automatic_update_checks", True) is not False


def read_data(call: Callable[[], Any], missing: Callable[[], Line]) -> Any:
    """Run one read whose operator names all resolved; its data that is not there exits 1 (criterion 27).

    The JSON code stays the bridge's NOT_FOUND; the text names the missing data and the way out.
    """
    try:
        return call()
    except CliBridgeError as error:
        if error.code != "NOT_FOUND":
            raise
        raise CliFailure(error.code, missing(), FAILED, bool(error.error.get("retryable"))) from error
