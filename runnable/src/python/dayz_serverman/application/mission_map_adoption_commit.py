"""The adoption commit step in the lane (leaf 10.35, AR-3, D4).

Lock order: lane, installation mutex, revalidation, folder writer side, association lock, journal. There is no
`STOPPED` proof. Adoption writes only manager-root records and renames only manager-root folders. A running
server writes none of the managed files that the revalidation compares (AR-3).

Adoption has its own preflight. The Apply preflight of `mission_map_commit.py` stays unchanged and keeps refusing
`copied_original` and every original replacement. Both reach `check_original_kinds`. This preflight calls it
before the first write. The record staging of the shared commit step calls it again (Architect 2026-10-16
16:49:30 in evidence 10.8).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..domain.mission_map_adoption import ADOPT_KIND
from ..domain.mission_map_records import sealed
from ..repositories.backup_verification import path_has_reparse
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_kinds import check_original_kinds
from ..repositories.mission_map_commit_records import RecordUpdate, parse_record
from ..repositories.mission_map_journal import MapJournal, record_path_kind
from ..repositories.mission_map_journal_paths import adoption_kind, journal_paths_safe, record_targets
from ..repositories.mission_map_journal_store import parse_journal
from ..repositories.mission_map_publication import MissionMapPublicationError
from ..repositories.mission_map_records import ORIGINAL_KINDS, RecordFile
from .mission_map_commit import MissionMapCommit, _probe


class AdoptionCommit:
    """Commit one adoption journal through the one shared commit step, its locks and its recovery owner."""

    def __init__(self, base: MissionMapCommit) -> None:
        """Keep the shared commit step that composition builds for Apply, the restores and adoption."""
        self.base = base

    def commit_point(self, operation_id: str) -> str | None:
        """Return where the journal of a failed adoption stands (AR-4); the rule of the shared commit step."""
        return self.base.commit_point(operation_id)

    def commit(self, journal: MapJournal, updates: list[RecordUpdate], *,
               revalidate: Callable[[], None]) -> dict[str, object]:
        """Commit in the AR-3 lock order through the public records-only method; a refusal writes nothing."""
        return self.base.commit_records(journal, updates, revalidate=revalidate, preflight=self._preflight)

    def _preflight(self, journal: MapJournal, updates: list[RecordUpdate]) -> None:
        """Refuse a malformed journal, unsafe or foreign paths, a reused operation and a refused original kind."""
        base = self.base
        parse_journal(_probe(journal, updates), journal.operation_id)
        if journal.operation_kind != ADOPT_KIND or journal.groups or journal.phase != "PREPARED" or not updates or \
                not journal_paths_safe(journal, Path(journal.dayz_root), base.paths):
            raise MissionMapPublicationError("PATH_INVALID", "Mission map adoption paths are unsafe.")
        if base.journals.path_for(journal.operation_id).exists() or \
                base.journals.retired_path_for(journal.operation_id).exists():
            raise MissionMapPublicationError("REVISION_CONFLICT", "Mission map operation already has a journal.")
        allowed = record_targets(journal, base.paths)
        staged = []
        for update in updates:
            kind = allowed.get(update.target_path) or adoption_kind(journal, base.paths, update.target_path)
            if kind != record_path_kind(update.kind) or path_has_reparse(base.paths.root / update.target_path):
                raise MissionMapPublicationError("PATH_INVALID", "Mission map record path is unsafe.")
            record = parse_record(update.kind, sealed(update.record))
            if update.kind in ORIGINAL_KINDS:
                found = RecordFile(base.paths.root / update.target_path, lambda raw, kind=kind: parse_record(kind, raw),
                                   StagingPolicy.OBSERVER).read()
                staged.append((update.kind, update.target_path, record, found))
        # The D5 and 10.8 rules of every original kind, before the first write
        check_original_kinds(base.paths.mission_map, journal, staged)
