"""SteamCMD authentication and Workshop update orchestration."""

from __future__ import annotations

import time
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from ..adapters.windows.steamcmd import build_update_argv
from ..domain.workshop import (
    SUCCESS_OUTCOMES,
    ItemOutcome,
    derive_required_items,
    download_gate,
)
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier
from .content_proofs import ContentProofResolver
from .download_progress import DownloadObserver, ItemProgress, SizeRecordingCheck
from .operations.context import OperationContext
from .operations.models import OperationFailure
from .profiles import ProfileService
from .settings import SettingsService
from .steamcmd_guard import SteamCmdRunGuard, hold_steamcmd
from .download_budget import (
    RETRY_PAUSE_SECONDS, download_budget, download_groups, session_repeats, smallest_first,
)
from .steamcmd_results import SessionEvidence, classify_output, classify_repeated, terminal_outcomes
from .workshop_decision import FreshCheckSource, decide_sent_items
from .workshop_authentication import (
    authenticate_interactive,
    inspect_paths,
    record_child,
    require_settings_revision,
    resolve_authentication,
    revalidate_paths,
)
from .workshop_ports import (  # noqa: F401 - re-exported for existing importers
    SteamCmdPort,
    SteamCmdPreflightPort,
    UpdateRequest,
)
from .workshop_proofs import verify_result
from .workshop_update_result import update_result


class WorkshopUpdateService:
    """Orchestrate authentication and update runs with proof-based outcomes."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        preflight: SteamCmdPreflightPort,
        steamcmd: SteamCmdPort,
        verifier_factory: Callable[[Path], WorkshopCacheVerifier] = WorkshopCacheVerifier,
        content_proofs: ContentProofResolver | None = None,
        check_source: FreshCheckSource | None = None,
        download_observer: Callable[..., DownloadObserver] | None = DownloadObserver,
        steamcmd_guard: SteamCmdRunGuard | None = None,
    ) -> None:
        """Store profile, settings, SteamCMD ports, proof and check collaborators.

        `download_observer` builds the advisory observer of the download
        folders; None runs the update without it.
        """
        self._profiles = profiles
        self._settings = settings
        self._preflight = preflight
        self._steamcmd = steamcmd
        self._content_proofs = content_proofs
        self._check_source = check_source
        self._verifier_factory = verifier_factory
        self._download_observer = download_observer
        # One SteamCMD run at a time in this manager, also against the server build check
        self._steamcmd_guard = steamcmd_guard

    def authenticate(self, expected_settings_revision: int, context: OperationContext) -> dict[str, object]:
        """Run interactive SteamCMD sign-in with revision-stability guards."""
        return authenticate_interactive(
            self._settings, self._preflight, self._steamcmd, expected_settings_revision, context,
            guard=self._steamcmd_guard,
        )

    def update(self, request: UpdateRequest, context: OperationContext) -> dict[str, object]:
        """Run one Workshop update and report terminal evidence for each item."""
        context.checkpoint("preflight", 5)
        # Reject stale revisions before any filesystem work
        settings, profile = self._resolve_context(request)
        # The guard spans preflight to the end of the run, so no other SteamCMD run falls in between
        with hold_steamcmd(self._steamcmd_guard, context, "UPDATE_WORKSHOP_ITEMS", 5) as hold:
            paths = inspect_paths(self._preflight, settings)
            # Resolve the ordered items the update must process
            items = derive_required_items(profile)
            context.checkpoint("resolve_items", 10)
            if not items:
                return update_result(request, (), "EMPTY")
            verifier = self._verifier_factory(paths.workshop_root)
            # Snapshot the before state used to classify outcomes
            try:
                before = {
                    observation.workshop_id: observation
                    for observation in verifier.observe(tuple(item.workshop_id for item in items))
                }
            except CacheVerificationError as error:
                raise OperationFailure("WORKSHOP_MANIFEST_INVALID", str(error)) from error
            # Decide from a fresh remote check which items SteamCMD must process
            context.checkpoint("check_remote", 12)
            check = None if self._check_source is None else SizeRecordingCheck(self._check_source)
            sent = decide_sent_items(check, items, before, verifier)
            # Smallest first, so one large item that runs out of time cannot hold back the small ones
            sent = smallest_first(sent, {} if check is None else check.sizes)
            # Nothing to send: no SteamCMD run follows, so the guard is free again at once
            if not sent:
                hold.release()
            # Each sent item waits for its download; rows read these advisory item phases
            progress = ItemProgress(context.publish_detail)
            sent_ids = tuple(item.workshop_id for item in sent)
            progress.queued(sent_ids, {} if check is None else check.sizes)
            # Re-check revisions immediately before the launch or the local proofs
            self._resolve_context(request)
            run = None
            sent_outcomes: dict[str, ItemOutcome] = {}
            summary = None
            if sent:
                context.checkpoint("download", 15)
                sizes = {} if check is None else check.sizes
                # The observer samples the download folders until the last run has ended
                with (self._download_observer(paths.workshop_root, progress, sent_ids)
                      if self._download_observer is not None else nullcontext()):
                    run, sent_outcomes, summary = self._download(paths, request, context, sent, sizes)
            # An unproven exit poisons the guard before the update reports it
            hold.release(unproven=run is not None and not run.termination_confirmed)
        # With an empty sent set no process starts and no process evidence exists
        cancelled = run is not None and run.cancelled
        # Require proven termination before trusting any SteamCMD output
        if run is not None and not run.termination_confirmed:
            raise OperationFailure(
                "UPDATE_RESULT_UNKNOWN",
                "SteamCMD process-tree exit could not be proven. Mutations are blocked.",
                recovery_required=True,
            )
        # Confirm nothing changed while SteamCMD was running
        self._resolve_context(request)
        if not cancelled:
            context.checkpoint("verify_items", 75)
        # Items that were not sent start as verified current; the groups decided the sent ones
        outcomes = {item.workshop_id: ItemOutcome.VERIFIED_CURRENT for item in items}
        outcomes.update(sent_outcomes)
        # Reuse stored content proofs; an item without a valid one is hashed in full
        proofs = self._content_proofs
        stored = {} if proofs is None else proofs.resolve(
            settings, profile, items, outcomes, verifier,
        )
        # After a cancelled run no full hash starts for an unsent item: without a
        # stored proof it gets the cancelled outcome of the sent items
        if cancelled:
            outcomes.update({
                item.workshop_id: ItemOutcome.CANCELLED for item in items
                if item.workshop_id not in sent_ids and item.workshop_id not in stored
            })
        # Verify every item against the after state and collect results
        results = []
        for index, item in enumerate(items):
            if not cancelled:
                # Spread verification progress across the 75 to 93 band
                context.checkpoint("verify_items", 75 + min(18, index * 18 // len(items)))
            progress.verifying(item.workshop_id)
            result = verify_result(
                item, outcomes[item.workshop_id], before[item.workshop_id], verifier,
                stored.get(item.workshop_id),
            )
            # Keep the source proof of a full hash so the next run can reuse it
            if (proofs is not None and result.proof is not None
                    and item.workshop_id not in stored):
                proofs.record_full_hash(verifier, result.proof)
            progress.finished(item.workshop_id, result.outcome in SUCCESS_OUTCOMES)
            results.append(result)
        if cancelled:
            progress.fail_unfinished()
        # Final revision check before reporting any outcome
        self._resolve_context(request)
        if not cancelled:
            context.checkpoint("verify_set", 95)
        return update_result(
            request,
            tuple(results),
            download_gate(tuple(results)),
            process_id=run.process_id if run is not None else None,
            steamcmd_exit_code=run.exit_code if run is not None else None,
            steamcmd_summary=summary,
        )

    def _download(self, paths, request: UpdateRequest, context: OperationContext, sent: tuple,
                  sizes: dict) -> tuple:
        """Download the sent items in groups: one session for the small items, then one for each large item.

        A large item could not finish in one SteamCMD attempt, so it gets its own session and does not hold
        back the items that would finish. Each group is judged on its own. Returns the last run, the outcome
        of every sent item, and the summary of the first failed group.
        """
        outcomes: dict[str, ItemOutcome] = {}
        summary = None
        run = None
        first = paths
        for group in download_groups(sent, sizes):
            if run is not None:
                # Every later session pauses first and takes a fresh fingerprint of the folders
                if not self._pause(context):
                    break
                paths = self._fresh_paths(first)
            run, group_outcomes, group_summary = self._download_group(paths, request, context, group, sizes, first)
            outcomes.update(group_outcomes)
            summary = summary or group_summary
            if run.cancelled or not run.termination_confirmed:
                break
        # Items of groups that never started end as cancelled after a cancellation
        if run is not None and (run.cancelled or context.cancellation_requested):
            outcomes.update({item.workshop_id: ItemOutcome.CANCELLED for item in sent
                             if item.workshop_id not in outcomes})
        return run, outcomes, summary

    def _download_group(self, paths, request: UpdateRequest, context: OperationContext, group: tuple,
                        sizes: dict, first) -> tuple:
        """Run SteamCMD for one group; run it again for an item it could not finish, while the budget lasts.

        SteamCMD ends a Workshop download after its own fixed time, or loses its connection, and keeps the
        files, so the next run continues the item. A large item repeats its download command within the
        session, which continues the same download job. The budget is `download_budget` of the group sizes
        from the first launch on. A failed sign-in, a missing entitlement or a content failure ends the group.
        Returns the run, the outcome of every item of the group, and the summary of its failure.
        """
        deadline = time.monotonic() + download_budget(sizes.get(item.workshop_id) for item in group)
        repeats = session_repeats(group, sizes)
        finished: tuple = ()
        remaining = group
        while True:
            # Build the SteamCMD update command for the items still to download
            argv = build_update_argv(
                paths.executable, request.authentication_mode, request.account_name, remaining,
                repeats=repeats,
            )
            revalidate_paths(self._preflight, paths)
            run = self._steamcmd.run_update(
                paths,
                argv,
                lambda: context.cancellation_requested,
                lambda evidence: record_child(context, evidence),
                lambda: self._resolve_context(request),
            )
            evidence = (classify_output(run.lines, remaining) if repeats == 1
                        else classify_repeated(run.lines, remaining[0]))
            outcome = (run, _group_outcomes(run, group, finished, remaining, evidence, repeats),
                       evidence.safe_summary)
            if (run.cancelled or not run.termination_confirmed or time.monotonic() >= deadline
                    or not _retryable(evidence)):
                return outcome
            # Every earlier item succeeded and the last one timed out or lost the connection
            index = len(evidence.ordered) - 1
            finished = (*finished, *evidence.ordered[:index])
            remaining = remaining[index:]
            if not self._pause(context):
                return outcome
            paths = self._fresh_paths(first)

    def _pause(self, context: OperationContext) -> bool:
        """Pause before the next SteamCMD session; return False when a cancellation arrived meanwhile."""
        resume = time.monotonic() + RETRY_PAUSE_SECONDS
        while time.monotonic() < resume:
            if context.cancellation_requested:
                return False
            time.sleep(0.1)
        return not context.cancellation_requested

    def _fresh_paths(self, first):
        """Fingerprint the folders again: a SteamCMD run changes the Workshop folder and Steam's download record.

        The fresh check must still name the same folders as the first one.
        """
        paths = inspect_paths(self._preflight, self._settings.load())
        if (paths.root, paths.executable, paths.workshop_root) != (first.root, first.executable,
                                                                   first.workshop_root):
            raise OperationFailure(
                "STEAMCMD_PATH_CHANGED",
                "SteamCMD or Workshop path identity changed. Reload settings and retry.",
            )
        return paths

    def _resolve_context(self, request: UpdateRequest):
        """Reload settings and profile, rejecting any drift from the request."""
        settings = self._settings.load()
        require_settings_revision(settings, request.expected_settings_revision)
        profile = self._profiles.read(request.profile_id)
        if (
            profile.revision != request.expected_profile_revision
            or profile.semantic_digest != request.expected_semantic_profile_digest
        ):
            raise OperationFailure("REVISION_CONFLICT", "The selected profile changed. Reload it.")
        mode, account = resolve_authentication(settings)
        if mode != request.authentication_mode or account != request.account_name:
            raise OperationFailure("REVISION_CONFLICT", "Steam authentication settings changed.")
        return settings, profile


def _retryable(evidence: SessionEvidence) -> bool:
    """Return whether every item but the last succeeded and the last one timed out or lost the connection."""
    if evidence.invalid or not evidence.ordered or evidence.ordered[-1][1] != ItemOutcome.CONNECTION_FAILED:
        return False
    return all(outcome == ItemOutcome.VERIFIED_CURRENT for _item, outcome in evidence.ordered[:-1])


def _group_outcomes(run, group: tuple, finished: tuple, remaining: tuple, evidence: SessionEvidence,
                    repeats: int) -> dict[str, ItemOutcome]:
    """Return the outcome of every item of one group from its earlier runs and its last run."""
    # A failed process without classified evidence leaves every item of its last run unknown; a session with
    # repeated commands can end with an error code after an earlier timeout, so there only no evidence counts
    unclassified = evidence.safe_summary is None if repeats == 1 else not evidence.ordered
    if run.exit_code != 0 and not run.cancelled and unclassified:
        return {**dict(finished), **{item.workshop_id: ItemOutcome.UNKNOWN_FAILED for item in remaining}}
    merged = SessionEvidence((*finished, *evidence.ordered), evidence.invalid, evidence.safe_summary)
    return terminal_outcomes(group, merged, cancelled=run.cancelled)
