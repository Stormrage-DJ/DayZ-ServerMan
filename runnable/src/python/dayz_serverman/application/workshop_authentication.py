"""Interactive SteamCMD sign-in and the launch guards shared with the update run."""

from __future__ import annotations

from ..adapters.windows.process_tree import ChildEvidence
from ..adapters.windows.steamcmd import SteamCmdPaths
from ..domain.models import ManagerSettings
from ..domain.workshop import AuthenticationMode
from .operations.context import OperationContext
from .operations.models import OperationCancelled, OperationFailure
from .settings import SettingsService
from .steamcmd_guard import SteamCmdRunGuard, hold_steamcmd
from .workshop_ports import SteamCmdPort, SteamCmdPreflightPort


def authenticate_interactive(
    settings_service: SettingsService, preflight: SteamCmdPreflightPort,
    steamcmd: SteamCmdPort, expected_settings_revision: int, context: OperationContext,
    *, guard: SteamCmdRunGuard | None = None,
) -> dict[str, object]:
    """Run interactive SteamCMD sign-in with revision-stability guards.

    The SteamCMD guard is held from the preflight to the end of the sign-in run.
    """
    context.checkpoint("preflight", 5)
    # Require stored account credentials and a revision-stable settings record
    settings = settings_service.load()
    require_settings_revision(settings, expected_settings_revision)
    mode, account = resolve_authentication(settings)
    if mode != AuthenticationMode.ACCOUNT or account is None:
        raise OperationFailure(
            "AUTHENTICATION_REQUIRED",
            "Select account authentication and configure an account name first.",
        )

    def require_auth_context() -> None:
        """Before launch, confirm the revision and credentials are unchanged."""
        current = settings_service.load()
        require_settings_revision(current, expected_settings_revision)
        if resolve_authentication(current) != (mode, account):
            raise OperationFailure("REVISION_CONFLICT", "Steam authentication settings changed.")

    with hold_steamcmd(guard, context, "AUTHENTICATE_STEAMCMD", 5) as hold:
        paths = inspect_paths(preflight, settings)
        context.checkpoint("interactive_authentication", 20)
        # Confirm path identity before launching the interactive session
        revalidate_paths(preflight, paths)
        # Re-check credentials in before_launch so a stale edit cannot launch
        run = steamcmd.authenticate_interactive(
            paths, account, lambda: context.cancellation_requested,
            lambda evidence: record_child(context, evidence),
            require_auth_context,
        )
        # An unproven exit poisons the guard before the sign-in reports it
        hold.release(unproven=not run.termination_confirmed)
    require_settings_revision(settings_service.load(), expected_settings_revision)
    # Confirm the session was not cancelled and terminated provably
    if run.cancelled:
        raise OperationCancelled("interactive authentication was cancelled")
    # Require proven process-tree termination before trusting the result
    if not run.termination_confirmed:
        raise OperationFailure(
            # The reason names the sign-in, so the block sentence does not speak of a mod update (QF-075)
            "UPDATE_RESULT_UNKNOWN", "SteamCMD process-tree exit after the sign-in could not be proven.",
            recovery_required=True,
        )
    # A non-zero exit did not complete authentication
    if run.exit_code != 0:
        exit_detail = (
            f" (exit code {run.exit_code})" if run.exit_code is not None else ""
        )
        raise OperationFailure(
            "AUTHENTICATION_FAILED",
            f"SteamCMD exited before confirming interactive authentication{exit_detail}.",
        )
    return {"authenticated": True, "authentication_mode": mode.value}


def resolve_authentication(settings: ManagerSettings) -> tuple[AuthenticationMode, str | None]:
    """Resolve the configured authentication mode and account."""
    try:
        mode = AuthenticationMode(settings.steam_authentication_mode)
    except (TypeError, ValueError) as error:
        raise OperationFailure(
            "AUTHENTICATION_REQUIRED",
            "Select Steam account or anonymous authentication first.",
        ) from error
    account = settings.steam_account_name
    if mode == AuthenticationMode.ACCOUNT and not account:
        raise OperationFailure("AUTHENTICATION_REQUIRED", "Configure a Steam account name first.")
    return mode, account


def inspect_paths(preflight: SteamCmdPreflightPort, settings: ManagerSettings) -> SteamCmdPaths:
    """Inspect the SteamCMD layout, mapping failures to an operation error."""
    try:
        return preflight.inspect(settings)
    except (OSError, RuntimeError, ValueError) as error:
        raise OperationFailure("STEAMCMD_UNAVAILABLE", str(error)) from error


def revalidate_paths(preflight: SteamCmdPreflightPort, paths: SteamCmdPaths) -> None:
    """Re-check path identity before launch and reject silent drift."""
    try:
        preflight.revalidate(paths)
    except (OSError, RuntimeError, ValueError) as error:
        raise OperationFailure(
            "STEAMCMD_PATH_CHANGED",
            "SteamCMD or Workshop path identity changed. Reload settings and retry.",
        ) from error


def record_child(context: OperationContext, evidence: ChildEvidence) -> None:
    """Record child evidence so recovery can detect a live runtime."""
    context.record_evidence({
        "child": evidence.to_dict(),
        "child_state": "CHILD_LAUNCHED",
        "result_state": "UPDATE_RESULT_UNKNOWN",
    })


def require_settings_revision(settings: ManagerSettings, expected: int) -> None:
    """Reject stale settings revisions with a reload instruction."""
    if settings.revision != expected:
        raise OperationFailure("REVISION_CONFLICT", "Manager settings changed. Reload them.")
