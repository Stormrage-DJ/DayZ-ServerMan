"""Build the records of one adoption commit from its review (leaf 10.35, D2 "Adoption Rules (10.2)", D4, AR-3).

One `adopt` journal without a DayZ-side group holds the records of a move, a copy, a records-only adoption, the
retired case or the restore case. The source records stay unchanged. An existing original of the new key is kept
(Architect 2026-10-17 11:42:47 in evidence 10.35).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.mission_map_adoption import ADOPT_KIND, COPY, KEPT_DIFFERENT, MOVE, PATH_CHANGE, RESTORE, RETIRED
from ..domain.mission_map_ledger import file_entry, parse_entry
from ..domain.mission_map_records import copied_original, sealed, utc_text
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_originals import (
    copied_original_update, restored_copy_update, supersede_updates,
)
from ..repositories.mission_map_commit_records import RecordUpdate
from ..repositories.mission_map_journal import MapJournal
from ..repositories.mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, FILE_SETS, INVALIDATED_GLOB, PLAN_FILE, TargetClass, association_folder, closed_name,
    ledger_folder,
)
from ..repositories.mission_map_ledger import LedgerStore
from ..repositories.mission_map_plans import PlanState
from ..repositories.mission_map_records import AssociationRecordStore, OriginalStore
from ..repositories.mission_map_retired import chain_end_retirement, path_change_updates
from ..repositories.paths import PortablePaths
from .mission_map_adoption_review import AdoptionReview
from .mission_map_adoption_sources import (
    active_original, baseline_keys, file_digest, runtime_folder, runtime_part, stored_contents,
)
from .mission_map_plans import plan_store
from .profile_restore_editor_remap import remapped_applied, remapped_baseline

# Provenance source of a working baseline that adoption builds from the original of the new key (D3)
BASELINE_SOURCE = "original"
OBSERVER = StagingPolicy.OBSERVER


@dataclass(frozen=True)
class AdoptionPublication:
    """The journal and the record updates of one adoption, and the dated history name that it writes."""

    journal: MapJournal
    updates: list[RecordUpdate]
    history_name: str | None


def build_adoption(review: AdoptionReview, profile: Any, paths: PortablePaths, *, operation_id: str,
                   moment: datetime) -> AdoptionPublication:
    """Return the adoption commit of a reviewed case; the caller has checked the fingerprint and the refusals."""
    ctx, area = review.context, paths.mission_map
    pid, key, root_text = (ctx.association[name] for name in ("profile_id", "mission_key", "mission_root"))
    source = ctx.source_key
    own = ctx.route == PATH_CHANGE or ctx.case == RETIRED
    runtime = runtime_part(area, ctx.root, profile, pid, source) if own and source is not None else None
    journal = MapJournal(operation_id, ADOPT_KIND, pid, root_text, key, runtime and runtime[0], runtime and runtime[1],
                         str(ctx.root), None, None, None, None, review.answer["profile"]["revision"],
                         review.answer["settings_revision"], [], [])
    build = _Build(paths, journal, ctx, moment, review.answer["inputs"]["files"])
    if ctx.case == RESTORE:
        build.close_invalidated()
    elif ctx.case == RETIRED:
        build.retired()
    elif ctx.case in (MOVE, COPY):
        build.move_or_copy()
    else:
        raise ValueError("the review offers no adoption")
    rename = next((update for update in build.updates if update.kind == "rename"), None)
    return AdoptionPublication(journal, build.updates, None if rename is None else Path(rename.target_path).name)


class _Build:
    """Collect the record updates of one adoption journal."""

    def __init__(self, paths: PortablePaths, journal: MapJournal, ctx: Any, moment: datetime,
                 files: dict[str, str | None]) -> None:
        """Keep the journal, the review context, the commit time and the digests of the reviewed files."""
        self.paths, self.area, self.journal, self.ctx, self.moment = paths, paths.mission_map, journal, ctx, moment
        self.files = files
        self.updates: list[RecordUpdate] = []
        self.folder = association_folder(self.area, journal.profile_id, journal.mission_key)

    def close_invalidated(self) -> None:
        """Restore case: close each invalidated last-applied record under its dated history name; nothing else."""
        for path in sorted(self.folder.glob(INVALIDATED_GLOB)):
            target = self.paths.relative(self.folder / closed_name(path.name))
            self.updates.append(RecordUpdate("history_file", target,
                                             {"source": self.paths.relative(path), "target": target}))

    def move_or_copy(self) -> None:
        """Move or copy: the new original, the baseline, the records of a move, the ledger and the retirement."""
        ctx, source = self.ctx, self.ctx.source_key
        if ctx.route == PATH_CHANGE:
            self.updates += path_change_updates(self.paths, self.journal, list(ctx.retire_keys), self.moment)
            self._plan(source)
        if ctx.records_only:
            return
        # An original of the new key is the capture of that location: it is kept, never replaced (A4)
        kept = active_original(self.area, self.journal.mission_key) if ctx.kept_original else None
        originals = {self.journal.mission_key: kept or self._copied(source)}
        keys = {source: self.journal.mission_key}
        own = ctx.route == PATH_CHANGE
        runtime_source = baseline_keys(self.area, self.journal.profile_id, source)[1:] if own else []
        runtime_key = self.journal.runtime_profile_key
        if runtime_source and runtime_key is not None and active_original(self.area, runtime_source[0]):
            originals[self.journal.runtime_profile_key] = self._copied(runtime_source[0])
            keys[runtime_source[0]] = self.journal.runtime_profile_key
        carried, different = ctx.case == MOVE, ctx.kept_original == KEPT_DIFFERENT
        baseline = self._baseline(source, keys, originals, carry=carried and not different)
        if carried and baseline is not None:
            self._applied(source, keys, baseline, check=different)
        for old, new in keys.items():
            self._ledger(old, new)

    def retired(self) -> None:
        """Retired case: the history move, the fresh records of the chain end, its marker and the D5 step 4."""
        end, steps = chain_end_retirement(self.paths, self.journal, self.moment)
        self.updates += steps
        self._plan(end)
        key, incoming = self.journal.mission_key, active_original(self.area, end)
        active = active_original(self.area, key)
        step = self.ctx.original_step
        if step == "installed":
            active = self._copied(end, case=MOVE)
        elif step in ("active", "shared"):
            record = copied_original(incoming, target_key=key, display_path=str(self.ctx.mission),
                                     mission_root=self.journal.mission_root, case=MOVE,
                                     operation_id=self.journal.operation_id)
            contents = OriginalStore(self.area, end, staging=OBSERVER).stored_contents(incoming)
            if step == "active":
                self.updates += supersede_updates(self.paths, self.journal, record, contents)
                active = record
            else:
                self.updates.append(restored_copy_update(self.paths, self.journal, record, contents))
        same = active is not None and incoming is not None and active["files"] == incoming["files"]
        baseline = self._baseline(end, {end: key}, {key: active} if active else {}, carry=same)
        if same and baseline is not None:
            self._applied(end, {end: key}, baseline, check=True)

    def _digest(self, path: str) -> str | None:
        """Return the digest of a mission file: the reviewed one, else the file now."""
        return self.files[path] if path in self.files else file_digest(self.ctx.mission, path)

    def _copied(self, source: str, case: str | None = None) -> dict[str, Any]:
        """Add the provenance-linked original of the new key of the source class; return its record (OP-3)."""
        mission = active_original(self.area, source)["target_class"] == TargetClass.MISSION.value  # type: ignore[index]
        display = str(self.ctx.mission) if mission else str(runtime_folder(self.ctx.root, self.journal))
        update = copied_original_update(self.paths, self.journal, source, display_path=display,
                                        mission_root=self.journal.mission_root, case=case or self.ctx.case)
        self.updates.append(update)
        return sealed(update.record)

    def _plan(self, source: str) -> None:
        """Add the plan of this profile that load showed read only, with the revision after its read revision."""
        inspection = plan_store(self.area, self.journal.profile_id, source, StagingPolicy.OBSERVER).inspect()
        if inspection.state is not PlanState.VALID or self.ctx.plan_revision is None:
            return
        plan = copy.deepcopy(inspection.plan)
        named = plan["association"]["runtime_profile_key"]
        plan["association"] = {"profile_id": self.journal.profile_id, "mission_root": self.journal.mission_root,
                               "mission_key": self.journal.mission_key,
                               "runtime_profile_key": None if named is None else self.journal.runtime_profile_key}
        # References of the source records are not the records of the new association (D3)
        plan["records"] = {"originals": [], "baseline": None, "applied": None}
        target = self.paths.relative(self.folder / PLAN_FILE)
        self.updates.append(RecordUpdate("plan", target, {"plan": plan}, revision=self.ctx.plan_revision + 1))

    def _baseline(self, source: str, keys: dict[str, str], originals: dict[str, dict[str, Any]], *,
                  carry: bool) -> dict[str, Any] | None:
        """Add the working baseline: the source baseline remapped, else one built from the new originals."""
        found = AssociationRecordStore(self.area, self.journal.profile_id, source, "baseline", staging=OBSERVER).read()
        current = AssociationRecordStore(self.area, self.journal.profile_id, self.journal.mission_key, "baseline",
                                         staging=OBSERVER).read()
        expected = None if current is None or self.ctx.route == PATH_CHANGE or self.ctx.case == RETIRED else current[1]
        mapped = found is not None and all(old in keys and keys[old] in originals for old in found[0]["target_keys"])
        if carry and found is not None and mapped:
            record = remapped_baseline(found[0], keys, originals, profile_id=self.journal.profile_id,
                                       mission_key=self.journal.mission_key, mission_root=self.journal.mission_root)
            folder = association_folder(self.area, self.journal.profile_id, source)
            contents = stored_contents(folder, "baseline", found[0])
        elif self.journal.mission_key in originals:
            ordered = sorted(originals, key=lambda name: name != self.journal.mission_key)
            record = sealed({
                "profile_id": self.journal.profile_id, "mission_key": self.journal.mission_key,
                "mission_root": self.journal.mission_root, "target_keys": ordered,
                "originals": [{"target_key": name, "manifest_sha256": originals[name]["manifest_sha256"]}
                              for name in ordered],
                "files": [dict(item, target_key=name) for name in ordered for item in originals[name]["files"]],
                "provenance": [{"time": utc_text(self.moment), "source": BASELINE_SOURCE,
                                "operation_id": self.journal.operation_id,
                                "paths": [item["path"] for name in ordered for item in originals[name]["files"]]}]})
            contents = {}
            for name in ordered:
                # A staged copied original has its content in the folder of its source until step 7
                link = originals[name].get("provenance")
                published = active_original(self.area, name) == originals[name]
                store = OriginalStore(self.area, name if published or not link else link["source_key"],
                                      staging=OBSERVER)
                contents.update(store.stored_contents(originals[name]))
        else:
            return None
        target = self.paths.relative(self.folder / BASELINE_FILE)
        self.updates.append(RecordUpdate("baseline", target, record, expected, contents))
        return record

    def _applied(self, source: str, keys: dict[str, str], baseline: dict[str, Any], *, check: bool) -> None:
        """Add the last-applied record of the source, remapped; it keeps the operation that wrote it (10.2).

        With `check`, each "files after" digest must equal the current mission file, as for the restores (D5).
        Else the record is not carried, and the next Apply reviews the files (R17).
        """
        found = AssociationRecordStore(self.area, self.journal.profile_id, source, "applied", staging=OBSERVER).read()
        if found is None or check and not all(
                keys.get(item["target_key"]) == self.journal.mission_key and self._digest(item["path"]) == (
                    item["sha256"] if item["existed"] else None) for item in found[0]["files_after"]):
            return
        record = remapped_applied(found[0], keys, baseline, profile_id=self.journal.profile_id,
                                  mission_key=self.journal.mission_key, mission_root=self.journal.mission_root)
        contents = stored_contents(association_folder(self.area, self.journal.profile_id, source), "applied", found[0])
        target = self.paths.relative(self.folder / APPLIED_FILE)
        self.updates.append(RecordUpdate("applied", target, record, None, contents))

    def _ledger(self, source: str, key: str) -> None:
        """Add one ledger entry for the new key that repeats the matched source output of each file (OP-5)."""
        target_class = TargetClass.MISSION if key == self.journal.mission_key else TargetClass.RUNTIME
        prefix = "" if target_class is TargetClass.MISSION else "runtime:"
        original = active_original(self.area, source) or {"files": []}
        saved = {item["path"]: (item["existed"], item["sha256"]) for item in original["files"]}
        store = LedgerStore(self.area, staging=OBSERVER)
        found = (store.read(source, path.stem) for path in sorted(ledger_folder(self.area, source).glob("*.json")))
        entries = sorted((entry for entry in found if entry is not None and entry["output"]),
                         key=lambda entry: (entry["recorded_at"], entry["operation_id"]))
        files = []
        for path in FILE_SETS[target_class]:
            digest = self.files.get(prefix + path)
            if digest is None or (True, digest) == saved.get(path):
                continue
            # The newest output entry whose after digest the reviewed file equals gives the repeated digests
            items = [item for item in (file_entry(entry, path) for entry in entries)
                     if item is not None and item["after_sha256"] == digest]
            if items:
                files.append(dict(items[-1]))
        if not files:
            return
        display = str(self.ctx.mission) if target_class is TargetClass.MISSION else str(
            runtime_folder(self.ctx.root, self.journal))
        entry = parse_entry(sealed({
            "operation_id": self.journal.operation_id, "operation_kind": ADOPT_KIND,
            "recorded_at": utc_text(self.moment),
            "profile_id": self.journal.profile_id, "target_class": target_class.value, "target_key": key,
            "display_path": display, "mission_root": self.journal.mission_root, "output": True,
            "carried_operation_id": None, "files": files}), FILE_SETS[target_class])
        self.updates.append(RecordUpdate("ledger", self.paths.relative(
            ledger_folder(self.area, key) / f"{self.journal.operation_id}.json"), entry))
