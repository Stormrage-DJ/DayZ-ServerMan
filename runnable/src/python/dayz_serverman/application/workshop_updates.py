"""SteamCMD authentication and Workshop update orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..adapters.windows.steamcmd import (
    SteamCmdPaths,
    SteamCmdRunResult,
    build_update_argv,
)
from ..adapters.windows.process_tree import ChildEvidence
from ..domain.models import ManagerSettings
from ..domain.workshop import (
    AuthenticationMode,
    ItemOutcome,
    ItemResult,
    RequiredWorkshopItem,
    derive_required_items,
    download_gate,
)
from ..repositories.applied_mod_state import AppliedModStateRepository
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier
from .operations.context import OperationContext
from .operations.models import OperationCancelled, OperationFailure
from .profiles import ProfileService
from .settings import SettingsService
from .steamcmd_results import classify_output, terminal_outcomes
from .workshop_proofs import verify_result
from .publication_identity import authentication_identity_digest
from .workshop_fast_path import find_cached_proofs


class SteamCmdPreflightPort(Protocol):
    """Port for SteamCMD path inspection and revalidation."""

    # Inspect the SteamCMD layout and return validated tool paths.
    def inspect(self, settings: ManagerSettings) -> SteamCmdPaths: ...
    # Re-check that prepared paths still identify the same tools before use.
    def revalidate(self, paths: SteamCmdPaths) -> None: ...


class SteamCmdPort(Protocol):
    """Port for interactive authentication and Workshop update runs."""

    # Run interactive sign-in and report the SteamCMD session result.
    def authenticate_interactive(
        self, paths: SteamCmdPaths, account_name: str,
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult: ...

    # Run one Workshop update command and report the SteamCMD session result.
    def run_update(
        self,
        paths: SteamCmdPaths,
        argv: tuple[str, ...],
        cancellation_requested: Callable[[], bool],
        on_launched: Callable[[ChildEvidence], None],
        before_launch: Callable[[], None],
    ) -> SteamCmdRunResult: ...


@dataclass(frozen=True)
class UpdateRequest:
    """Immutable request describing one Workshop update submission."""

    profile_id: str
    expected_profile_revision: int
    expected_semantic_profile_digest: str
    expected_settings_revision: int
    authentication_mode: AuthenticationMode
    account_name: str | None
    update_all_and_start: bool = False


class WorkshopUpdateService:
    """Orchestrate authentication and update runs with proof-based outcomes."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        preflight: SteamCmdPreflightPort,
        steamcmd: SteamCmdPort,
        verifier_factory: Callable[[Path], WorkshopCacheVerifier] = WorkshopCacheVerifier,
        applied_state: AppliedModStateRepository | None = None,
    ) -> None:
        """Store profile, settings, SteamCMD ports, and proof collaborators."""
        self._profiles = profiles
        self._settings = settings
        self._preflight = preflight
        self._steamcmd = steamcmd
        self._applied_state = applied_state
        self._verifier_factory = verifier_factory

    def authenticate(self, expected_settings_revision: int, context: OperationContext) -> dict[str, object]:
        """Run interactive SteamCMD sign-in with revision-stability guards."""
        context.checkpoint("preflight", 5)
        # Require stored account credentials and a revision-stable settings record
        settings = self._settings.load()
        self._require_settings_revision(settings, expected_settings_revision)
        mode, account = self._authentication(settings)
        if mode != AuthenticationMode.ACCOUNT or account is None:
            raise OperationFailure(
                "AUTHENTICATION_REQUIRED",
                "Select account authentication and configure an account name first.",
            )
        paths = self._inspect(settings)
        context.checkpoint("interactive_authentication", 20)
        # Confirm path identity before launching the interactive session
        self._revalidate_paths(paths)
        # Re-check credentials in before_launch so a stale edit cannot launch
        run = self._steamcmd.authenticate_interactive(
            paths, account, lambda: context.cancellation_requested,
            lambda evidence: self._record_child(context, evidence),
            lambda: self._require_auth_context(expected_settings_revision, mode, account),
        )
        self._require_settings_revision(self._settings.load(), expected_settings_revision)
        # Confirm the session was not cancelled and terminated provably
        if run.cancelled:
            raise OperationCancelled("interactive authentication was cancelled")
        # Require proven process-tree termination before trusting the result
        if not run.termination_confirmed:
            raise OperationFailure(
                "UPDATE_RESULT_UNKNOWN", "SteamCMD process-tree exit could not be proven.",
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

    def update(self, request: UpdateRequest, context: OperationContext) -> dict[str, object]:
        """Run one Workshop update and report terminal evidence for each item."""
        context.checkpoint("preflight", 5)
        # Reject stale revisions before any filesystem work
        settings, profile = self._resolve_context(request)
        paths = self._inspect(settings)
        # Resolve the ordered items the update must process
        items = derive_required_items(profile)
        context.checkpoint("resolve_items", 10)
        if not items:
            return self._result(request, (), "EMPTY")
        verifier = self._verifier_factory(paths.workshop_root)
        # Snapshot the before state used to classify outcomes
        try:
            before = {
                observation.workshop_id: observation
                for observation in verifier.observe(tuple(item.workshop_id for item in items))
            }
        except CacheVerificationError as error:
            raise OperationFailure("WORKSHOP_MANIFEST_INVALID", str(error)) from error
        # Build the SteamCMD update command for the resolved items
        argv = build_update_argv(
            paths.executable,
            request.authentication_mode,
            request.account_name,
            items,
        )
        # Re-check revisions and paths immediately before the launch
        self._resolve_context(request)
        self._revalidate_paths(paths)
        run = self._steamcmd.run_update(
            paths,
            argv,
            lambda: context.cancellation_requested,
            lambda evidence: self._record_child(context, evidence),
            lambda: self._resolve_context(request),
        )
        # Require proven termination before trusting any SteamCMD output
        if not run.termination_confirmed:
            raise OperationFailure(
                "UPDATE_RESULT_UNKNOWN",
                "SteamCMD process-tree exit could not be proven. Mutations are blocked.",
                recovery_required=True,
            )
        # Confirm nothing changed while SteamCMD was running
        self._resolve_context(request)
        if not run.cancelled:
            context.checkpoint("verify_items", 75)
        # Classify sanitized output into ordered per-item evidence
        evidence = classify_output(run.lines, items)
        # A failed process without classified evidence leaves every item unknown
        if run.exit_code != 0 and not run.cancelled and evidence.safe_summary is None:
            outcomes = {item.workshop_id: ItemOutcome.UNKNOWN_FAILED for item in items}
        else:
            outcomes = terminal_outcomes(items, evidence, cancelled=run.cancelled)
        # Reuse applied-state proofs for items already verified current
        cached = find_cached_proofs(
            settings, profile, items, outcomes, verifier, self._applied_state,
        )
        # Verify every item against the after state and collect results
        results = []
        for index, item in enumerate(items):
            if not run.cancelled:
                # Spread verification progress across the 75 to 93 band
                context.checkpoint("verify_items", 75 + min(18, index * 18 // len(items)))
            results.append(verify_result(
                item, outcomes[item.workshop_id], before[item.workshop_id], verifier,
                cached.get(item.workshop_id),
            ))
        # Final revision check before reporting any outcome
        self._resolve_context(request)
        if not run.cancelled:
            context.checkpoint("verify_set", 95)
        return self._result(
            request,
            tuple(results),
            download_gate(tuple(results)),
            process_id=run.process_id,
            steamcmd_exit_code=run.exit_code,
            steamcmd_summary=evidence.safe_summary,
        )

    def _resolve_context(self, request: UpdateRequest):
        """Reload settings and profile, rejecting any drift from the request."""
        settings = self._settings.load()
        self._require_settings_revision(settings, request.expected_settings_revision)
        profile = self._profiles.read(request.profile_id)
        if (
            profile.revision != request.expected_profile_revision
            or profile.semantic_digest != request.expected_semantic_profile_digest
        ):
            raise OperationFailure("REVISION_CONFLICT", "The selected profile changed. Reload it.")
        mode, account = self._authentication(settings)
        if mode != request.authentication_mode or account != request.account_name:
            raise OperationFailure("REVISION_CONFLICT", "Steam authentication settings changed.")
        return settings, profile

    @staticmethod
    def _authentication(settings: ManagerSettings) -> tuple[AuthenticationMode, str | None]:
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

    def _inspect(self, settings: ManagerSettings) -> SteamCmdPaths:
        """Inspect the SteamCMD layout, mapping failures to an operation error."""
        try:
            return self._preflight.inspect(settings)
        except (OSError, RuntimeError, ValueError) as error:
            raise OperationFailure("STEAMCMD_UNAVAILABLE", str(error)) from error

    def _revalidate_paths(self, paths: SteamCmdPaths) -> None:
        """Re-check path identity before launch and reject silent drift."""
        try:
            self._preflight.revalidate(paths)
        except (OSError, RuntimeError, ValueError) as error:
            raise OperationFailure(
                "STEAMCMD_PATH_CHANGED",
                "SteamCMD or Workshop path identity changed. Reload settings and retry.",
            ) from error

    def _require_auth_context(
        self, expected_revision: int, mode: AuthenticationMode, account: str,
    ) -> None:
        """Before launch, confirm the revision and credentials are unchanged."""
        current = self._settings.load()
        self._require_settings_revision(current, expected_revision)
        if self._authentication(current) != (mode, account):
            raise OperationFailure("REVISION_CONFLICT", "Steam authentication settings changed.")

    @staticmethod
    def _record_child(context: OperationContext, evidence: ChildEvidence) -> None:
        """Record child evidence so recovery can detect a live runtime."""
        context.record_evidence({
            "child": evidence.to_dict(),
            "child_state": "CHILD_LAUNCHED",
            "result_state": "UPDATE_RESULT_UNKNOWN",
        })

    @staticmethod
    def _require_settings_revision(settings: ManagerSettings, expected: int) -> None:
        """Reject stale settings revisions with a reload instruction."""
        if settings.revision != expected:
            raise OperationFailure("REVISION_CONFLICT", "Manager settings changed. Reload them.")

    @staticmethod
    def _result(
        request: UpdateRequest,
        results: tuple[ItemResult, ...],
        state: str,
        *,
        process_id: int | None = None,
        steamcmd_exit_code: int | None = None,
        steamcmd_summary: str | None = None,
    ) -> dict[str, object]:
        """Assemble the bridge payload reported for one update run."""
        # Publication is deferred, so start is recorded but never authorized
        return {
            "profile_id": request.profile_id,
            "profile_revision": request.expected_profile_revision,
            "semantic_profile_digest": request.expected_semantic_profile_digest,
            "settings_revision": request.expected_settings_revision,
            "authentication_identity_digest": authentication_identity_digest(
                request.authentication_mode, request.account_name,
            ),
            "download_state": state,
            "publication_state": "PENDING_PHASE_6_2",
            "start_requested": request.update_all_and_start,
            "start_authorized": False,
            "start_error": "PUBLICATION_REQUIRED" if request.update_all_and_start else None,
            "items": [item.to_dict() for item in results],
            "process_id": process_id,
            "steamcmd_exit_code": steamcmd_exit_code,
            "steamcmd_summary": steamcmd_summary,
        }
