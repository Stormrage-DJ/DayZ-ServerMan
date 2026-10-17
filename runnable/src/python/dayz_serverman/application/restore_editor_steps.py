"""The last-applied, ledger and file steps of the same-profile editor restore (D5, D19, AR-6, leaf 10.10).

`EditorBuilder` (`restore_editor_records.py`) takes these steps as a base class. A damaged per-profile record or
content file gets a dated evidence name in the same journal. Then the normal rule applies (Architect 2026-10-16
21:56:00 in evidence 10.10). The evidence name is outside the globs of `record_files`, so no reader or sweep
parses it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.backup_editor_manifest import association_content, record_entry
from ..domain.backup_inventory import mission_payload_prefix
from ..domain.restore_editor_texts import editor_notes
from ..repositories.backup_editor_records import named_contents, parse_record
from ..repositories.backup_verification import sha256_file
from ..repositories.mission_map_contents import ContentCorrupt, ContentStore
from ..repositories.mission_map_layout import APPLIED_FILE, TargetClass, corrupt_name, invalidated_name, ledger_folder
from ..repositories.mission_map_records import OriginalStore
from ..repositories.paths import PortablePaths
from ..repositories.restore_editor_targets import EditorWrite
from .restore_editor_files import (
    EditorArchive, EditorContext, EditorRestore, applied_installs, ledger_entries, record_bytes, sha,
)


class EditorRecordSteps:
    """The steps of `EditorBuilder` that its `__init__` binds: paths, context, archive, rows and file lists."""

    paths: PortablePaths
    area: Path
    ctx: EditorContext
    archive: EditorArchive
    moment: datetime
    operation_id: str
    folder: Path
    restored_digests: dict[str, str]
    contents: dict[str, EditorWrite]
    records: dict[str, EditorWrite]
    removals: dict[str, EditorWrite]
    rows: dict[str, Any]
    inputs: dict[str, str | None]
    skipped: list[str]
    known: dict[str, bytes]
    baseline_record: dict[str, Any] | None
    current_applied: bool

    def applied(self, field: dict[str, Any] | None) -> None:
        """Install the archived last-applied record when it matches the restored files, else invalidate (AR-6)."""
        path = self.folder / APPLIED_FILE
        current = self.current(path)
        carried = field is not None and "applied" in field["association"]["records"]
        data = self.archive.read(record_entry("applied")) if carried else None
        archived = None if data is None else parse_record("applied", data)
        self.current_applied, installs, record = current is not None, False, None
        if archived is not None:
            self.ledger(archived)
            installs = applied_installs(archived, self.restored)
            self.rows["applied_carried"] = "install" if installs else "invalidate"
            # The content of an invalidated record stays too; the sweep keeps it (Architect ruling (6))
            self.content(self.folder, {digest: self.archive.read(association_content(digest))
                                       for digest in named_contents("applied", archived)})
        if current is not None:
            try:
                record = parse_record("applied", current)
            except ValueError:
                # A damaged record keeps its bytes under the evidence name; the restore does not use it
                self.set_aside_corrupt(path, current)
                self.rows["applied_corrupt"] = True
        if installs:
            self.write(self.paths.relative(path), data)  # type: ignore[arg-type]
            return
        kept = [] if data is None else [data]
        # Without an installed record the current one stays only when it matches the restored files (D19)
        if record is not None:
            matches = applied_installs(record, self.restored)
            self.rows["applied_current"] = "keep" if matches else "invalidate"
            kept += [] if matches else [current]
        number = 1
        for item in kept:
            while (self.folder / invalidated_name(self.moment, number)).exists():
                number += 1
            self.write(self.paths.relative(self.folder / invalidated_name(self.moment, number)), item)
            number += 1
        if self.rows.get("applied_current") == "invalidate" or self.rows.get("applied_corrupt"):
            self.remove(path, current)

    def ledger(self, applied: dict[str, Any]) -> None:
        """Add the ledger entries of a carried last-applied record for the current target keys (D5)."""
        targets = {self.ctx.mission_key: (TargetClass.MISSION, str(self.ctx.mission))}
        if self.ctx.runtime_key is not None:
            targets[self.ctx.runtime_key] = (TargetClass.RUNTIME, str(self.ctx.runtime))
        entries = ledger_entries(applied, self.baseline_record, lambda digest: self.archive.read(
            association_content(digest)), targets, profile_id=self.ctx.profile_id,
            mission_root=self.ctx.mission_root, operation_id=self.operation_id, moment=self.moment)
        for entry in entries:
            target = ledger_folder(self.area, entry["target_key"]) / f"{entry['operation_id']}.json"
            self.write(self.paths.relative(target), record_bytes(entry, 0))
        self.rows["ledger"] = [entry["target_key"] for entry in entries]

    def restored(self, key: str, path: str) -> str | None:
        """Return the digest of a managed file after the restore: the archived file, else the current one."""
        if key == self.ctx.mission_key:
            entry, folder = mission_payload_prefix(self.ctx.mission_root) + path, self.ctx.mission
        elif key == self.ctx.runtime_key and self.ctx.runtime is not None:
            entry, folder = "runtime-profile/" + path, self.ctx.runtime
        else:
            raise KeyError(key)
        if entry in self.restored_digests:
            return self.restored_digests[entry]
        current = folder.joinpath(*path.split("/"))
        return sha256_file(current) if current.is_file() else None

    def original_content(self, key: str, digest: str) -> bytes:
        """Return content of an active original: from the archive when it came from there, else from its store."""
        return self.known[digest] if digest in self.known else OriginalStore(self.area, key).contents.read(digest)

    def current(self, path: Path) -> bytes | None:
        """Return the bytes of a current editor file and record their digest as a preview input."""
        try:
            data = read_bytes_shared(path)
        except FileNotFoundError:
            data = None
        self.inputs[self.paths.relative(path)] = None if data is None else sha(data)
        return data

    def write(self, relative: str, data: bytes) -> None:
        """Add one editor record file that the restore writes, with the digest of the file as read now."""
        self.records[relative] = EditorWrite(relative, data, self.digest(relative))

    def remove(self, path: Path, data: bytes | None) -> None:
        """Add the removal of a current editor file whose bytes the restore read."""
        relative = self.paths.relative(path)
        self.removals[relative] = EditorWrite(relative, None, None if data is None else sha(data))

    def digest(self, relative: str) -> str | None:
        """Return the digest of a current editor file by its manager-relative path, None when it is absent."""
        path = self.paths.root.joinpath(*relative.split("/"))
        return sha256_file(path) if path.is_file() else None

    def set_aside_corrupt(self, path: Path, data: bytes) -> None:
        """Keep the bytes of a damaged file under its dated evidence name beside it (ruling of 21:56:00)."""
        number = 1
        while (target := path.with_name(corrupt_name(path.name, self.moment, number))).exists():
            number += 1
        self.write(self.paths.relative(target), data)

    def content(self, folder: Path, contents: Mapping[str, bytes]) -> None:
        """Add the content files that a record of the folder names; damaged content gets its evidence name."""
        store = ContentStore(folder)
        for digest, data in sorted(contents.items()):
            path = store.folder / digest
            relative = self.paths.relative(path)
            # Only bytes whose digest equals the file name are written (Architect 22:32:18, point 3)
            if sha(data) != digest:
                raise ContentCorrupt(path, "has no verified bytes")
            if path.exists():
                try:
                    store.read(digest)
                    continue
                except ContentCorrupt:
                    self.set_aside_corrupt(path, read_bytes_shared(path))
                    self.rows.setdefault("content_corrupt", []).append(relative)
            self.contents[relative] = EditorWrite(relative, data, self.digest(relative))

    def result(self, carried: bool, skipped: list[str], names: Mapping[str, str]) -> EditorRestore | None:
        """Return the files in publication order (content, records, removals), the summary and the notes.

        A restore has an editor part when the archive carries editor records or a last Apply record exists.
        """
        writes = (*self.contents.values(), *self.records.values(), *self.removals.values())
        if not carried and not self.current_applied and not writes:
            return None
        rows, skipped = dict(sorted(self.rows.items())), skipped + self.skipped
        summary = {"inputs": dict(sorted(self.inputs.items())), "rows": rows, "skipped": skipped}
        return EditorRestore(writes, summary, editor_notes(carried, skipped, rows, names), self.ctx.profile_id,
                             self.ctx.mission_key)
