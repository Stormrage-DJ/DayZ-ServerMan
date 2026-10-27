"""The editor records of a restore as a new profile: the D5 rules of each policy and the remap (D5, D19, A10).

The plan gives the files of the fourth journal group (`repositories/profile_restore_editor.py`), the
`editor_notes` of the preview and the summary that the preview fingerprint binds. The group checks every file
again with the 10.8 rules before its journal starts, and a condition that the commit cannot prove rolls the whole
restore back.

- Originals: the D5 table for each carried original at its destination key. A backup original for the same key
  installs as a plain `original`, also under `preserve_original`. For another key it becomes a `copied_original`
  with the provenance case `restore`. Its source is the verified archive entry (Architect 2026-10-17 07:28:52 in
  evidence 10.37). The runtime profile key is always new.
- Shared: `replace_existing` is always shared at its mission key.
- Plan: remapped to the new association. Working baseline: remapped when it was built on the originals that
  become active, else built again from the active originals.
- Last applied: installed remapped when each "files after" digest equals the restored file, else under a dated
  invalidated name (AR-6). A target outside the archived association keeps its key, so such a record is always
  invalidated. One ledger entry for each destination target that it changed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.backup_editor_manifest import association_content, original_content, original_entry, record_entry
from ..domain.backups import BackupManifest
from ..domain.mission_map_records import COPIED_FIELDS, copied_original, sealed
from ..domain.models import RepositoryError
from ..domain.restore_editor_texts import editor_notes
from ..repositories.backup_editor_records import named_contents, parse_record
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_originals import restored_copy_update, supersede_updates
from ..repositories.mission_map_contents import ContentStore
from ..repositories.mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, PLAN_FILE, TargetClass, association_folder, association_profiles, invalidated_name,
    ledger_folder, target_key,
)
from ..repositories.mission_map_records import ORIGINAL_FILE, OriginalStore
from ..repositories.paths import PortablePaths
from ..repositories.profile_restore_editor_shapes import EditorFile
from .mission_map_sharing import other_targets
from .profile_restore_editor_remap import future_folder, remapped_applied, remapped_baseline, remapped_plan
from .restore_editor_files import (
    EditorArchive, applied_installs, baseline_fits, ledger_entries, rebuilt_baseline, record_bytes, sha,
)


@dataclass(frozen=True)
class EditorPlan:
    """The editor files of one restore as a new profile, its preview sentences and the summary that the preview binds."""

    files: tuple[EditorFile, ...]
    notes: list[str]
    summary: dict[str, Any]


class ProfileEditorPlanner:
    """Decide the editor records that a restore as a new profile writes for one reviewed destination."""

    def __init__(self, paths: PortablePaths, clock: Callable[[], datetime] | None = None) -> None:
        """Keep the manager layout and the clock of the dated names."""
        self.paths = paths
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(self, directory: Path, manifest: BackupManifest, root: Path, preview: dict[str, Any],
             records: Iterable[Any], operation_id: str) -> EditorPlan | None:
        """Return the editor part of the restore, or None for an archive without editor records.

        `records` are the stored profiles; the new profile is not one of them yet.
        """
        if manifest.editor is None:
            return None
        return _Build(self, EditorArchive(directory, manifest), manifest, root, preview, tuple(records),
                      operation_id).run()


class _Build:
    """One plan: the destination, the files that it collects, the D5 rows and the preview inputs."""

    def __init__(self, owner: ProfileEditorPlanner, archive: EditorArchive, manifest: BackupManifest, root: Path,
                 preview: dict[str, Any], records: tuple[Any, ...], operation_id: str) -> None:
        """Resolve the destination keys and folders of the new profile."""
        self.paths, self.area, self.archive = owner.paths, owner.paths.mission_map, archive
        self.field, self.moment, self.operation_id = manifest.editor, owner.clock(), operation_id
        self.policy, self.mission_root = preview["storage_policy"], preview["mission_root"]
        self.profile_id = preview["profile"]["profile_id"]
        # The destination folders can still be missing; their keys equal the keys once the restore creates them
        mission = future_folder(root, self.mission_root)
        runtime = future_folder(root, preview["profile"]["runtime_profile"])
        self.displays = {TargetClass.MISSION: str(mission), TargetClass.RUNTIME: str(runtime)}
        self.mission_key = target_key(TargetClass.MISSION, mission)
        self.runtime_key = target_key(TargetClass.RUNTIME, runtime)
        self.folder = association_folder(self.area, self.profile_id, self.mission_key)
        self.digests = {entry.path: entry.sha256 for entry in manifest.entries}
        self.prefix = manifest.reconstruction["mission_prefix"]
        # The shared check of the other profiles runs on the lane, before the journal starts (ruling (a))
        self.others = other_targets(root, records, self.profile_id)
        self.names = {record.values.profile_id: record.values.display_name for record in records}
        self.owner = SimpleNamespace(operation_id=operation_id, profile_id=self.profile_id,
                                     mission_key=self.mission_key, runtime_profile_key=self.runtime_key)
        self.files: dict[str, EditorFile] = {}
        self.rows: dict[str, Any] = {}
        self.inputs: dict[str, str | None] = {}
        self.skipped: list[str] = []
        self.active: dict[str, dict[str, Any] | None] = {self.mission_key: None, self.runtime_key: None}
        self.known: dict[str, bytes] = {}
        self.blobs: dict[str, bytes] = {}
        self.keys = self._keys()

    def _keys(self) -> dict[str, str]:
        """Map each archived target key to its destination key: the mission key, and the new runtime key.

        The archived runtime key is the key of the carried runtime original, else the second key of the baseline.
        """
        recorded = self.field["association"]["records"]
        runtime = [item["target_key"] for item in self.field["originals"] if item["target_class"] == "runtime"]
        if not runtime and "baseline" in recorded:
            runtime = parse_record("baseline", self.archive.read(record_entry("baseline")))["target_keys"][1:]
        return {self.field["association"]["mission_key"]: self.mission_key,
                **{key: self.runtime_key for key in runtime[:1]}}

    def run(self) -> EditorPlan:
        """Collect the files in the order originals, plan, baseline, last applied; return them with the notes."""
        recorded = self.field["association"]["records"]
        for item in self.field["originals"]:
            self.original(TargetClass(item["target_class"]), item["target_key"])
        if "plan" in recorded:
            self.plan()
        baseline = self.baseline() if "baseline" in recorded else None
        if "applied" in recorded:
            self.applied(baseline)
        rows = dict(sorted(self.rows.items()))
        summary = {"keys": {"mission": self.mission_key, "runtime": self.runtime_key},
                   "inputs": dict(sorted(self.inputs.items())), "rows": rows, "skipped": self.skipped}
        return EditorPlan(tuple(self.files.values()), editor_notes(True, self.skipped, rows, self.names), summary)

    def original(self, target_class: TargetClass, source_key: str) -> None:
        """Apply the D5 table to one carried original at its destination key and add its files."""
        destination = self.keys.get(source_key)
        if destination is None:
            return
        data = self.archive.read(original_entry(source_key))
        archived = parse_record("original", data)
        contents = {digest: self.archive.read(original_content(source_key, digest))
                    for digest in named_contents("original", archived)}
        self.known.update(contents)
        store = OriginalStore(self.area, destination, staging=StagingPolicy.OBSERVER)
        self.read(store.folder / ORIGINAL_FILE)
        try:
            found = store.read_revision()
        except RepositoryError:
            # A damaged original is shared state of a location: left for its own recovery, its class skipped
            self.rows[f"original_{target_class.value}"] = "corrupt"
            self.skipped += [] if "original_corrupt" in self.skipped else ["original_corrupt"]
            return
        # A backup original for the same key is the capture of that location; another key gets a restore copy
        plain = destination == source_key
        incoming = sealed(archived) if plain else copied_original(
            archived, target_key=destination, display_path=self.displays[target_class], mission_root=self.mission_root,
            case="restore", operation_id=self.operation_id)
        shared = self.shared(target_class, destination)
        if found is None:
            row = "install"
            # A copied original carries the verified archive bytes of its source for the 10.8 check
            self.add("original" if plain else "copied_original", self.paths.relative(store.folder / ORIGINAL_FILE),
                     record_bytes(incoming, 0), None if plain else data)
            self.content(store.folder, contents)
        elif all(found[0][name] == archived[name] for name in COPIED_FIELDS):
            row = "keep"
        elif not shared:
            row = "supersede"
            for update in supersede_updates(self.paths, self.owner, incoming, contents):  # type: ignore[arg-type]
                revision = 0 if update.expected_revision is None else update.expected_revision + 1
                self.add(update.kind, update.target_path, record_bytes(update.record, revision))
            self.content(store.folder, contents)
        else:
            row = "restored_copy"
            update = restored_copy_update(self.paths, self.owner, incoming, contents)  # type: ignore[arg-type]
            self.add(update.kind, update.target_path, record_bytes(update.record, 0))
            self.content(self.folder, contents)
        self.rows[f"original_{target_class.value}"] = row
        self.active[destination] = incoming if row in ("install", "supersede") else found[0]

    def shared(self, target_class: TargetClass, key: str) -> bool:
        """Return whether another profile has an association folder for the key or resolves to it (D2).

        `replace_existing` writes into the mission root of other profiles, so its mission target is always shared.
        """
        found = sorted((set(association_profiles(self.area, key)) - {self.profile_id})
                       | set(self.others.sharing(target_class, key)))
        self.rows[f"shared_{target_class.value}"] = found
        return bool(found) or (self.policy == "replace_existing" and target_class is TargetClass.MISSION)

    def plan(self) -> None:
        """Install the archived plan for the new association; invalid saved data installs as stored."""
        association = {"profile_id": self.profile_id, "mission_root": self.mission_root,
                       "mission_key": self.mission_key, "runtime_profile_key": self.runtime_key}
        data, valid = remapped_plan(self.archive.read(record_entry("plan")), association)
        self.add("plan", self.paths.relative(self.folder / PLAN_FILE), data)
        self.rows["plan"] = "install" if valid else "install_as_stored"

    def baseline(self) -> dict[str, Any] | None:
        """Install the remapped baseline when it was built on the originals that become active, else rebuild it."""
        archived = parse_record("baseline", self.archive.read(record_entry("baseline")))
        sources = {item["target_key"]: parse_record("original", self.archive.read(original_entry(item["target_key"])))
                   for item in self.field["originals"]}
        classes = {key: record["target_class"] for key, record in sources.items()}
        # It fits when it was built on the carried originals and each of them is active at its new key
        fits = baseline_fits(archived, sources) and all(
            key in self.keys and self.rows.get(f"original_{classes.get(key)}") in ("install", "supersede", "keep")
            for key in archived["target_keys"])
        if fits:
            record = remapped_baseline(archived, self.keys, self.active, profile_id=self.profile_id,
                                       mission_key=self.mission_key, mission_root=self.mission_root)
            self.content(self.folder, {digest: self.archive.read(association_content(digest))
                                       for digest in named_contents("baseline", archived)})
            self.rows["baseline"] = "install"
        else:
            # A class without an active original has no part (Architect ruling (g))
            record = rebuilt_baseline(self.profile_id, self.mission_key, self.mission_root, self.active,
                                      self.operation_id, self.moment)
            self.rows["baseline"] = "rebuild" if record is not None else "none"
            if record is None:
                return None
            self.content(self.folder, {item["sha256"]: self.original_bytes(item["target_key"], item["sha256"])
                                       for item in record["files"] if item["existed"]})
        self.add("baseline", self.paths.relative(self.folder / BASELINE_FILE), record_bytes(record, 0))
        return record

    def applied(self, baseline: dict[str, Any] | None) -> None:
        """Install the remapped last-applied record when it matches the restored files, else invalidate it (D5)."""
        archived = parse_record("applied", self.archive.read(record_entry("applied")))
        record = remapped_applied(archived, self.keys, baseline, profile_id=self.profile_id,
                                  mission_key=self.mission_key, mission_root=self.mission_root)
        # A target outside the archived association has no restored file, so the record never installs
        installs = applied_installs(record, self.restored)
        self.rows["applied_carried"] = "install" if installs else "invalidate"
        self.content(self.folder, {digest: self.archive.read(association_content(digest))
                                   for digest in named_contents("applied", archived)})
        name = APPLIED_FILE if installs else invalidated_name(self.moment, 1)
        self.add("applied" if installs else "invalidated", self.paths.relative(self.folder / name),
                 record_bytes(record, 0))
        # One output ledger entry for each destination target that the carried record changed (D5)
        targets = {self.mission_key: (TargetClass.MISSION, self.displays[TargetClass.MISSION]),
                   self.runtime_key: (TargetClass.RUNTIME, self.displays[TargetClass.RUNTIME])}
        # Content of a rebuilt baseline comes from the active original, not from the archive
        entries = ledger_entries(record, baseline, lambda digest: self.blobs.get(digest) or self.archive.read(
                                     association_content(digest)),
                                 targets, profile_id=self.profile_id, mission_root=self.mission_root,
                                 operation_id=self.operation_id, moment=self.moment)
        for entry in entries:
            target = ledger_folder(self.area, entry["target_key"]) / f"{entry['operation_id']}.json"
            self.add("ledger", self.paths.relative(target), record_bytes(entry, 0))
        self.rows["ledger"] = [entry["target_key"] for entry in entries]

    def restored(self, key: str, path: str) -> str | None:
        """Return the digest of a managed file after the restore: its archived file, None when the archive has none."""
        if key == self.mission_key:
            return self.digests.get(self.prefix + path)
        if key == self.runtime_key:
            return self.digests.get("runtime-profile/" + path)
        raise KeyError(key)

    def original_bytes(self, key: str, digest: str) -> bytes:
        """Return content of an active original: from the archive when it came from there, else from its store."""
        return self.known[digest] if digest in self.known else OriginalStore(self.area, key).contents.read(digest)

    def read(self, path: Path) -> None:
        """Record the digest of a current editor file as a preview input."""
        self.inputs[self.paths.relative(path)] = sha(read_bytes_shared(path)) if path.is_file() else None

    def add(self, kind: str, relative: str, data: bytes, source: bytes | None = None) -> None:
        """Add one file of the group; a copied original carries the verified archive bytes of its source."""
        self.files[relative] = EditorFile(kind, relative, data, source)

    def content(self, folder: Path, contents: dict[str, bytes]) -> None:
        """Add the content files that a record of the folder names."""
        for digest, data in sorted(contents.items()):
            self.blobs[digest] = data
            relative = self.paths.relative(ContentStore(folder).folder / digest)
            self.files.setdefault(relative, EditorFile("content", relative, data))
