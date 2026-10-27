"""The editor files of one same-profile restore: the D5 rows, the plan revision rule and AR-6 (D19, leaf 10.10).

The restore journal writes these files itself as manager-root groups (`repositories/restore_editor_targets.py`).
It nests no D4 journal and does not stage through `CommitRecords` (Architect 2026-10-16 16:49:30 in evidence
10.8, ruling (b)). The rules of 10.8 apply through `check_original_kinds`, the derived record paths and the
record builders.

- Originals: the D5 table. A backup original for the same key installs as a plain `original`.
- Plan: installed with the revision after the higher of the current and the archived one. A current plan that
  is invalid saved data first gets a dated set-aside name (R15); an archived invalid plan installs as stored.
- Working baseline: installed when built on the active originals, else built again from them (D19, ruling (g)).
- Last applied: installed when each "files after" digest equals the restored file. Otherwise the record, and the
  current record, get a dated invalidated name (AR-6). A schema 3 archive invalidates the current record.
- Ledger: one entry for each carried last-applied record, also when the record is invalidated (D5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.backup_editor_manifest import association_content, original_content, original_entry, record_entry
from ..domain.models import RepositoryError
from ..domain.restores import RestoreTarget
from ..repositories.backup_editor_records import named_contents, parse_record
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_kinds import check_original_kinds
from ..repositories.mission_map_commit_originals import restored_copy_update, supersede_updates
from ..repositories.mission_map_contents import ContentCorrupt
from ..repositories.mission_map_layout import (
    BASELINE_FILE, PLAN_FILE, TargetClass, association_folder, association_profiles, set_aside_name,
)
from ..repositories.mission_map_plans import PlanState
from ..repositories.mission_map_records import ORIGINAL_FILE, AssociationRecordStore, OriginalStore
from ..repositories.paths import PortablePaths
from ..repositories.restore_editor_targets import EditorWrite
from .mission_map_plans import plan_store
from .mission_map_sharing import OtherTargets
from .restore_editor_files import (
    EditorArchive, EditorContext, EditorOwner, baseline_fits, rebuilt_baseline, record_bytes, restored_plan,
)
from .restore_editor_steps import EditorRecordSteps


class EditorBuilder(EditorRecordSteps):
    """Collect the editor files, the inputs and the D5 rows of one restore; the other steps are in the base class."""

    def __init__(self, paths: PortablePaths, context: EditorContext, archive: EditorArchive,
                 targets: tuple[RestoreTarget, ...], operation_id: str, moment: datetime,
                 sharing: Callable[[], OtherTargets]) -> None:
        """Bind the restore inputs; the shared check runs only when a D5 row needs it."""
        self.paths, self.area, self.ctx, self.archive, self.moment = paths, paths.mission_map, context, archive, moment
        self.operation_id, self._targets_of_others = operation_id, sharing
        self.owner = EditorOwner(operation_id, context.profile_id, context.mission_key, context.runtime_key)
        self.folder = association_folder(self.area, context.profile_id, context.mission_key)
        self.restored_digests = {item.entry_path: item.snapshot_digest for item in targets}
        self.contents: dict[str, EditorWrite] = {}
        self.records: dict[str, EditorWrite] = {}
        self.removals: dict[str, EditorWrite] = {}
        self.rows: dict[str, Any] = {}
        self.inputs: dict[str, str | None] = {}
        # A runtime profile folder that cannot be resolved has no key, so its class takes no part
        self.active: dict[str, dict[str, Any] | None] = {
            key: None for key in (context.mission_key, context.runtime_key) if key is not None}
        self.current_applied: bool = False
        # Skip reasons that the builder finds: `original_corrupt` (Architect 2026-10-16 21:56:00)
        self.skipped: list[str] = []
        self.known: dict[str, bytes] = {}
        self.baseline_record: dict[str, Any] | None = None
        self._sharing: OtherTargets | None = None

    def originals(self, field: dict[str, Any], *, runtime: bool) -> None:
        """Apply the D5 table to the original of each target class; without `runtime` the runtime one is skipped."""
        for target_class, key in ((TargetClass.MISSION, self.ctx.mission_key), (TargetClass.RUNTIME, self.ctx.runtime_key)):
            if key is None:
                continue
            store = OriginalStore(self.area, key, staging=StagingPolicy.OBSERVER)
            self.current(store.folder / ORIGINAL_FILE)
            try:
                found = store.read_revision()
            except RepositoryError:
                # A damaged original is shared state of a location: left for its own recovery, its class skipped
                self.original_corrupt(target_class)
                continue
            self.active[key] = None if found is None else found[0]
            item = next((item for item in field["originals"] if item["target_class"] == target_class.value), None)
            # A carried original of another runtime key is not installed (ruling (5)); the class keeps its original
            if item is None or (target_class is TargetClass.RUNTIME and not runtime):
                continue
            data = self.archive.read(original_entry(key))
            incoming = parse_record("original", data)
            contents = {digest: self.archive.read(original_content(key, digest))
                        for digest in named_contents("original", incoming)}
            self.known.update(contents)
            written = (set(self.records), set(self.contents))
            try:
                row = self._original(target_class, store, found, incoming, data, contents)
            except ContentCorrupt:
                # Damaged content without verified archive bytes for it: the original counts as damaged (22:32:18).
                # The files that this class added are dropped again.
                for files, kept in ((self.records, written[0]), (self.contents, written[1])):
                    for relative in set(files) - kept:
                        del files[relative]
                self.active[key] = None
                self.original_corrupt(target_class)
                continue
            self.rows[f"original_{target_class.value}"] = row
            if row in ("install", "supersede"):
                self.active[key] = incoming

    def read_active(self) -> None:
        """Read the active originals without a carried record set; a damaged original gives its class no part."""
        for key in self.active:
            store = OriginalStore(self.area, key, staging=StagingPolicy.OBSERVER)
            self.current(store.folder / ORIGINAL_FILE)
            try:
                self.active[key] = store.read()
            except RepositoryError:
                self.active[key] = None

    def original_corrupt(self, target_class: TargetClass) -> None:
        """Mark the class of a damaged original: its original, its baseline part and its notes are skipped."""
        self.rows[f"original_{target_class.value}"] = "corrupt"
        if "original_corrupt" not in self.skipped:
            self.skipped.append("original_corrupt")

    def _original(self, target_class: TargetClass, store: OriginalStore, found: tuple[dict[str, Any], int] | None,
                  incoming: dict[str, Any], data: bytes, contents: Mapping[str, bytes]) -> str:
        """Return the D5 row of one carried original and add its files; check them with the 10.8 rules."""
        target = self.paths.relative(store.folder / ORIGINAL_FILE)
        if found is None or found[0]["manifest_sha256"] == incoming["manifest_sha256"]:
            # The notes name the other profiles of an original that installs or stays (row 6)
            self.shared(target_class, store.target_key)
            if found is not None:
                return "keep"
            row, staged = "install", [("original", target, incoming, None)]
            self.write(target, data)
            self.content(store.folder, contents)
        elif not self.shared(target_class, store.target_key):
            row, updates = "supersede", supersede_updates(self.paths, self.owner, incoming, contents)
            staged = [(item.kind, item.target_path, item.record, None if item.kind == "superseded" else found)
                      for item in updates]
            for item in updates:
                revision = 0 if item.expected_revision is None else item.expected_revision + 1
                self.write(item.target_path, record_bytes(item.record, revision))
            self.content(store.folder, contents)
        else:
            row, update = "restored_copy", restored_copy_update(self.paths, self.owner, incoming, contents)
            staged = [(update.kind, update.target_path, update.record, None)]
            self.write(update.target_path, record_bytes(update.record, 0))
            self.content(self.folder, contents)
        check_original_kinds(self.area, self.owner, staged)  # type: ignore[arg-type]
        return row

    def shared(self, target_class: TargetClass, key: str) -> bool:
        """Return whether another profile has an association folder for the key or resolves to it (D2)."""
        if self._sharing is None:
            self._sharing = self._targets_of_others()
        found = (set(association_profiles(self.area, key)) - {self.ctx.profile_id}) | set(
            self._sharing.sharing(target_class, key))
        self.rows[f"shared_{target_class.value}"] = sorted(found)
        return bool(found)

    def plan(self, field: dict[str, Any]) -> None:
        """Install the archived plan; invalid saved data of the current plan is first set aside (R15)."""
        path = self.folder / PLAN_FILE
        current = self.current(path)
        if "plan" not in field["association"]["records"]:
            return
        inspection = plan_store(self.area, self.ctx.profile_id, self.ctx.mission_key, StagingPolicy.OBSERVER).inspect()
        if current is not None and inspection.state not in (PlanState.VALID, PlanState.MISSING):
            kept = self.folder / set_aside_name(self.moment)
            if kept.exists():
                raise RepositoryError("a set-aside plan with this time already exists; try again")
            self.write(self.paths.relative(kept), current)
            self.rows["plan_set_aside"] = True
        data, valid = restored_plan(self.archive.read(record_entry("plan")),
                                    (self.ctx.profile_id, self.ctx.mission_key), inspection.revision)
        self.write(self.paths.relative(path), data)
        self.rows["plan"] = "install" if valid else "install_as_stored"

    def baseline(self, field: dict[str, Any] | None) -> None:
        """Install the archived working baseline when it fits the active originals, else build it again (D19).

        Without carried records (`field` None), only a damaged baseline changes. It is set aside, and then it is
        built again from the active originals (Architect 2026-10-16 22:32:18).
        """
        store = AssociationRecordStore(self.area, self.ctx.profile_id, self.ctx.mission_key, "baseline",
                                       staging=StagingPolicy.OBSERVER)
        path = self.paths.relative(store.folder / BASELINE_FILE)
        current = self.current(store.folder / BASELINE_FILE)
        try:
            found = store.read()
        except RepositoryError:
            # A damaged baseline keeps its bytes under the evidence name; then the normal rule applies
            found = None
            self.set_aside_corrupt(store.folder / BASELINE_FILE, current or b"")
            self.rows["baseline_corrupt"] = True
        if field is None and not self.rows.get("baseline_corrupt"):
            return
        carried = field is not None and "baseline" in field["association"]["records"]
        data = self.archive.read(record_entry("baseline")) if carried else None
        archived = self.baseline_record = None if data is None else parse_record("baseline", data)
        if archived is not None and baseline_fits(archived, self.active):
            self.write(path, data)
            self.content(self.folder, {digest: self.archive.read(association_content(digest))
                                       for digest in named_contents("baseline", archived)})
            self.rows["baseline"] = "install"
            return
        rebuilt = None
        if (archived is not None or self.rows.get("baseline_corrupt")
                or (found is not None and not baseline_fits(found[0], self.active))):
            rebuilt = rebuilt_baseline(self.ctx.profile_id, self.ctx.mission_key, self.ctx.mission_root, self.active,
                                       self.operation_id, self.moment)
        if rebuilt is None:
            self.rows["baseline"] = "keep"
            if self.rows.get("baseline_corrupt"):
                self.remove(store.folder / BASELINE_FILE, current)
            return
        self.write(path, record_bytes(rebuilt, 0 if found is None else found[1] + 1))
        self.content(self.folder, {digest: self.original_content(item["target_key"], digest)
                                   for item in rebuilt["files"] if item["existed"] and (digest := item["sha256"])})
        self.rows["baseline"] = "rebuild"
