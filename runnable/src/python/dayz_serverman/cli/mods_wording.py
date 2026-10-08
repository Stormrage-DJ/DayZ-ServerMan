"""Wording of the update, Steam and mod commands (phase 5): copies of the Mods page's texts, and CLI sentences.

Each copy names its frontend file; `tests/test_cli_mods_wording.py` checks that it appears there
unchanged. A window sentence that names a button or a page gets a CLI form here, with the
command to type as `TypeText` (11.3); the shared catalogues are not changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..application.activity_wording import error_text
from .output import Line, SentencePart, TypeText, sentence

# `frontend/mods_update_actions.js` `modsUpdateTexts`: what a finished update says when no review follows
UPDATE_TEXTS: dict[str, str] = {
    "current": "All mods are current. Nothing to download or apply.",
    "empty": "This profile has no Workshop mods.",
    "emptyRunning": "This profile has no Workshop mods. The server was not restarted.",
    "notStarted": " The server was not started.",
    "keepsRunning": " The server keeps running; nothing was applied.",
}
# `frontend/operation_messages.js` `operationDownloadResults`: the end of an update by its download state
DOWNLOAD_RESULTS: dict[str, str] = {
    "VERIFIED": "Mods are downloaded and checked.",
    "EMPTY": "This profile has no Workshop mods.",
    "FAILED": "Some mods could not be updated.",
    "UNKNOWN": "The mod update could not be confirmed.",
    "CANCELLED": "Mod update cancelled.",
}
# `frontend/diagnostic_labels.js` `modOutcomeLabels` and `modOutcomeDetails`, with the lookup's fallback
OUTCOME_TEXTS: dict[str, str] = {
    "VERIFIED_CURRENT": "Already current", "DOWNLOADED_VERIFIED": "Downloaded", "UPDATED_VERIFIED": "Updated",
    "AUTHENTICATION_FAILED": "Steam sign-in failed",
    "ENTITLEMENT_FAILED": "This Steam account may not download the item",
    "CONNECTION_FAILED": "Steam could not be reached", "CONTENT_FAILED": "Download failed",
    "CANCELLED": "Cancelled", "NOT_ATTEMPTED": "Not tried", "UNKNOWN_FAILED": "Could not verify",
}
OUTCOME_DETAILS: dict[str, str] = {
    "CACHE_VERIFICATION_FAILED": "Downloaded files could not be verified",
    "CACHE_MANIFEST_ID_MISSING": "Download record is incomplete",
}
OUTCOME_FALLBACK = "Failed"
# `frontend/mods_verify.js`: the problems of one verified mod, and the summary of a verification
VERIFY_SOURCE_PROBLEMS: dict[str, str] = {
    "MISSING": "Download missing", "FAILED": "Download could not be read",
    "CHANGED": "Download changed since it was recorded",
}
VERIFY_TARGET_PROBLEMS: dict[str, str] = {
    "NOT_APPLIED": "Not applied to the server folder", "DIFFERS": "Server copy differs from the download",
    "FAILED": "Server copy could not be read",
}
VERIFY_NOTHING = "No Workshop mods to verify"
VERIFY_ONE = "The mod is verified; the server copy matches"
# The verification's "All <n> mods verified; server copies match" and "<n> problems found", around the count
VERIFY_ALL = ("All ", " mods verified; server copies match")
VERIFY_PROBLEM = "1 problem found"
VERIFY_PROBLEMS = " problems found"
# The Mods page's word for a mod without a problem in the verification table (CLI-only)
VERIFIED = "Verified"
# `frontend/operation_messages.js` `successPresentation`: what an apply says about its server start
START_TEXTS: dict[str, str] = {
    "STARTED": " The server was started.",
    "CANCELLED": " The server start was cancelled.",
    "NOT_REQUESTED": " Server start was not requested.",
}
START_FAILED = " The server could not be started: "
# The same file `restartApplySuccess`
RESTART_START_CANCELLED = "Mods and keys applied. The server start was cancelled. The server stays stopped."
RESTART_START_FAILED = "Server stopped and mods applied, but the server could not be started: "
RESTART_BACKED_UP = "Server backed up, mods applied and server restarted."
# Kinds of the mod operations whose success sentence depends on the result
UPDATE_KIND, VERIFY_KIND = "UPDATE_WORKSHOP_ITEMS", "VERIFY_WORKSHOP_FILES"
PUBLISH_KIND, RESTART_KIND = "PUBLISH_MODS_AND_KEYS", "APPLY_MODS_AND_RESTART"
# CLI sentences of `updates check` (section 7, 10.3)
CHECK_NOT_STARTED = "No new check was started; the status below is from the last check."
CHECK_TIMEOUT = "The check did not finish in 5 minutes."
CHECK_NOT_CANCELLABLE = "The check cannot be cancelled. DayZ-ServerMan waits for its result."


def current_running() -> Line:
    """Word an apply and restart whose mods were all current (`currentRunning`, with the CLI way out)."""
    return sentence("All mods are current. The server was not restarted. To restart it anyway, run ",
                    TypeText("server restart"), ".")


def refused_advice(state: object, offered: bool) -> tuple[SentencePart, ...]:
    """Return the advice after a refused apply (`modReviewRefusedAdvice`), with the commands to type.

    `offered` says whether a restart can follow: the server runs with this profile.
    """
    restart = TypeText("mods update --restart")
    if state == "RUNNING_MANAGED" and offered:
        return (" Use ", restart, ", or stop the server first.")
    if state in ("RUNNING_MANAGED", "RUNNING_EXTERNAL"):
        return (" Stop the server first, then update again.",)
    if state == "STARTING":
        return (" Wait until the server runs, then use ", restart, ".")
    if state == "STOPPING":
        return (" Wait until the server is stopped, then update again.",)
    return (" Check the server state with ", TypeText("server status"), ".")


def use_restart(offered: bool) -> tuple[SentencePart, ...]:
    """Return the advice of a plain apply to a running server (`useRestart` or `whenStopped`)."""
    if offered:
        return (" Use ", TypeText("mods update --restart"), " instead.")
    return (" To be safe, apply the mods when the server is stopped.",)


def outcome_text(entry: Mapping[str, Any]) -> str:
    """Word one mod outcome of an update result (`modOutcomeText`)."""
    outcome = OUTCOME_TEXTS.get(str(entry.get("outcome")), OUTCOME_FALLBACK)
    detail = OUTCOME_DETAILS.get(str(entry.get("error_code")), "")
    return f"{outcome} ({detail})" if detail else outcome


def verify_problems(item: Mapping[str, Any]) -> list[str]:
    """List the problems of one verified mod (`verifyItemProblems`)."""
    problems = []
    source = VERIFY_SOURCE_PROBLEMS.get(str(item.get("source_state")))
    if source:
        problems.append(source)
    # A copy that had no readable download to compare with is not reported as unreadable
    compared = item.get("target_state") != "FAILED" or item.get("source_state") not in ("MISSING", "FAILED")
    target = VERIFY_TARGET_PROBLEMS.get(str(item.get("target_state")))
    if target and compared:
        problems.append(target)
    return problems


def verify_summary(items: list[Mapping[str, Any]]) -> str:
    """Word the end of a verification as the Mods page does: the problem count, or that all copies match."""
    count = sum(1 for item in items if verify_problems(item))
    if count:
        return VERIFY_PROBLEM if count == 1 else f"{count}{VERIFY_PROBLEMS}"
    if not items:
        return VERIFY_NOTHING
    return VERIFY_ONE if len(items) == 1 else f"{VERIFY_ALL[0]}{len(items)}{VERIFY_ALL[1]}"


def everything_current(result: Mapping[str, Any]) -> bool:
    """Report an update that found everything current: no download and every mod applied (`everythingCurrent`)."""
    entries = result.get("items") if isinstance(result.get("items"), list) else []
    return (result.get("download_state") == "VERIFIED" and result.get("process_id") is None and bool(entries)
            and all(isinstance(entry, Mapping) and isinstance(entry.get("cache_proof"), Mapping)
                    and entry["cache_proof"].get("verification_kind") == "APPLIED_STATE" for entry in entries))


def ran_no_download(record: Mapping[str, Any], kind: str) -> bool:
    """Report an update that found everything current, so its end line would claim a download (QF-52).

    The window replaces that transient sentence at once; `mods update` prints its own end instead.
    """
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    return kind == UPDATE_KIND and everything_current(result)


def success_line(record: Mapping[str, Any], kind: str, success: str) -> Line | None:
    """Return the success sentence of a mod operation from its result (`successPresentation`), else None."""
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    if kind == UPDATE_KIND and str(result.get("download_state")) in DOWNLOAD_RESULTS:
        return sentence(DOWNLOAD_RESULTS[str(result["download_state"])])
    if kind == VERIFY_KIND:
        return sentence(verify_summary([item for item in result.get("items") or [] if isinstance(item, Mapping)]))
    state = result.get("start_state")
    reason = error_text(result.get("start_error"), "")
    if kind == RESTART_KIND:
        if state == "NOT_NEEDED":
            return current_running()
        if state == "CANCELLED":
            return sentence(RESTART_START_CANCELLED)
        if state == "FAILED":
            return sentence(f"{RESTART_START_FAILED}{reason}")
        return sentence(RESTART_BACKED_UP if result.get("backup") else success)
    if kind == PUBLISH_KIND and state in START_TEXTS:
        return sentence(f"{success}{START_TEXTS[str(state)]}")
    if kind == PUBLISH_KIND and state == "FAILED":
        return sentence(f"{success}{START_FAILED}{reason}")
    return None


def account_needed() -> Line:
    """Word `steam set --mode account` without `--account`."""
    return sentence("Name the Steam account with ", TypeText("--account"), ".")


def account_not_allowed() -> Line:
    """Word `--account` with anonymous sign-in, which uses no account."""
    return sentence("Anonymous sign-in uses no account. Leave out ", TypeText("--account"), ".")


def choose_account_sign_in() -> Line:
    """Word a Steam sign-in or update without account sign-in (AUTHENTICATION_REQUIRED, with the CLI way out)."""
    return sentence("Choose Steam account sign-in first with ", TypeText("steam set --mode account --account NAME"),
                    ".")


def choose_sign_in() -> Line:
    """Word a mod update while no Steam sign-in mode is chosen (AUTHENTICATION_REQUIRED, with the CLI way out)."""
    return sentence("Choose the Steam sign-in first with ", TypeText("steam set"), ".")


def sign_in_again() -> tuple[SentencePart, ...]:
    """Return the way out after a failed Steam sign-in during an update (the window opens its sign-in form)."""
    return (" Run ", TypeText("steam login"), ", then try again.")


def backup_needs_restart() -> Line:
    """Word a backup choice without `--restart`: only a restart stops the server."""
    return sentence("Use ", TypeText("--backup-after-stop"), " or ", TypeText("--no-backup-after-stop"),
                    " only with ", TypeText("--restart"), ".")


def automatic_checks(enabled: bool) -> Line:
    """Word the saved automatic-check switch."""
    return sentence(f"Automatic update checks: {'on' if enabled else 'off'}.")


def check_not_started(force: bool) -> Line:
    """Word a check request that started nothing: a recent check, the switch, or no SteamCMD for the build."""
    if force:
        return sentence(CHECK_NOT_STARTED)
    return sentence(f"{CHECK_NOT_STARTED} Use ", TypeText("--force"), " to check now.")


def unverified_detail(result: Mapping[str, Any]) -> str:
    """Return what SteamCMD reported after an update that could not be confirmed (`reportUnverifiedUpdate`)."""
    if result.get("download_state") != "UNKNOWN":
        return ""
    code = result.get("steamcmd_exit_code")
    exit_detail = f" (exit code {code})" if isinstance(code, int) and not isinstance(code, bool) else ""
    summary = result.get("steamcmd_summary")
    if summary:
        return f" {summary}{exit_detail}"
    return f" SteamCMD exited without a verifiable update result{exit_detail}."

