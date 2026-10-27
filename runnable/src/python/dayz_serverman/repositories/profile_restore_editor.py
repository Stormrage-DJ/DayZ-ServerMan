"""The fourth group of the profile-restore journal: the editor records of the restored profile (A10, D5, D19).

Journal version 2 adds this group to the three groups of version 1. The group holds plain editor record files with
their digests and needs no task 6 seam. It writes the files itself: it nests no D4 journal and does not stage
through `CommitRecords`. Each file is checked with the parts of 10.8: the derived record paths, the record parsers
and `check_original_kinds`.

Only records of the new profile ID are written, with its ledger entries and the protected originals that
`check_original_kinds` admits. Every target is new, except an active original that a D5 supersede replaces; that
one is held under a derived name until the commit. Recovery derives every path again from the journal.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..adapters.windows.shared_files import remove_file, rename_directory, rename_new_file
from ..domain.mission_map_ledger import parse_entry
from .backup_editor_records import parse_record
from .json_store import StagingPolicy
from .mission_map_commit_kinds import check_original_kinds
from .mission_map_layout import (
    DELETED_FOLDER, FILE_SETS, TargetClass, association_folder, deleted_name, ledger_folder, original_folder,
    profile_folder,
)
from .mission_map_records import ORIGINAL_FILE, ORIGINAL_KINDS, OriginalStore, restored_copy_file, superseded_file
from .paths import PortablePaths
from .profile_restore_inventory import inventory, safe_exists
from .profile_restore_editor_shapes import EDITOR_ROLE, EditorFile, match_file
from .profile_restore_preparation import remove_owned, write_bytes

# Kinds that make the group the writer of an original folder, so content may go there
ORIGINAL_WRITERS = frozenset(("original", "copied_original"))
# The record parser of each kind; a plan installs as stored and is not parsed (Architect ruling (g))
PARSED_AS = {"baseline": "baseline", "applied": "applied", "invalidated": "applied",
             **{kind: "original" for kind in ORIGINAL_KINDS}}


class ProfileEditorGroup:
    """Plan, stage, publish, verify, roll back and clean up the editor group below one manager root."""

    def __init__(self, paths: PortablePaths, clock: Callable[[], datetime] | None = None) -> None:
        """Bind the manager layout whose editor area the group writes, and the clock of the dated names."""
        self.paths = paths
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def remnant(self, profile_id: str) -> bool:
        """Return whether an earlier profile with this ID left files in its editor folder; a link refuses.

        Only an interrupted deletion leaves such a folder, and no profile record owns it. An empty folder is reused.
        """
        found = inventory(profile_folder(self.paths.mission_map, profile_id))
        return found is not None and (found["type"] != "directory" or bool(found["files"]))

    def move_remnant(self, profile_id: str) -> bool:
        """Move a remnant profile folder into the deleted folder under a free dated name, as a deletion does.

        It runs before the journal starts; the deleted folder is inert. A failed move raises OSError and refuses
        the restore. Return whether a folder moved.
        """
        if not self.remnant(profile_id):
            return False
        deleted, name = self.paths.mission_map / DELETED_FOLDER, deleted_name("profile", profile_id, self._clock())
        deleted.mkdir(exist_ok=True)
        # An existing name is never replaced
        for number in range(1, 101):
            target = deleted / (name if number == 1 else f"{name}-{number}")
            if not safe_exists(target):
                rename_directory(profile_folder(self.paths.mission_map, profile_id), target)
                return True
        raise OSError("the deleted editor folder has no free name")

    def plan(self, operation_id: str, profile_id: str, files: Iterable[EditorFile]) -> dict[str, Any]:
        """Check the files before the journal starts and return the group with the state of each target.

        Content files come first, so a published record never names missing content. Content that already exists
        with its bytes is not written again.
        """
        owner = SimpleNamespace(operation_id=operation_id, profile_id=profile_id)
        items, staged, written, homes, sources = [], [], set(), [], {}
        for file in sorted(files, key=lambda file: file.kind != "content"):
            found = match_file(file.kind, file.relative, profile_id)
            try:
                record = self._checked(file, found, operation_id, profile_id)
            except (KeyError, TypeError, AttributeError) as error:
                raise ValueError("Restore editor record is invalid.") from error
            before = inventory(self.target(file.relative))
            # The original kinds go to the 10.8 check with the original that is published at their target
            if file.kind in ORIGINAL_KINDS:
                store = OriginalStore(self.paths.mission_map, found.group("key"), staging=StagingPolicy.OBSERVER)
                staged.append((file.kind, file.relative, record,
                               store.read_revision() if file.kind in ORIGINAL_WRITERS else None))
            if file.kind in ORIGINAL_WRITERS:
                written.add(found.group("key"))
            if file.source is not None:
                sources[file.relative] = parse_record("original", file.source)
            if file.kind == "content" and before is not None and before["sha256"] == found.group("sha"):
                continue
            items.append({"kind": file.kind, "relative": file.relative, "before": before, "after": None,
                          "published": False, "held": False})
            homes.append(found.groupdict().get("original"))
        # The 10.8 rules of the original kinds: create once, and a supersede only with its kept record
        replaced = check_original_kinds(self.paths.mission_map, owner, staged, sources)  # type: ignore[arg-type]
        # Every other target is new, and content goes only to an original folder that the group writes
        for item, home in zip(items, homes):
            if (item["before"] is not None and item["relative"] not in replaced) or home not in (None, *written):
                raise ValueError("Restore editor target already exists or is not owned by the restore.")
        created = self._created(item["relative"] for item in items)
        return {"role": EDITOR_ROLE, "manager_root": str(self.paths.root), "created": created, "files": items}

    def _checked(self, file: EditorFile, found: re.Match[str], operation_id: str, profile_id: str) -> Any:
        """Parse a record file and require the path that the 10.8 layout derives from its identity.

        The path shape already names the restored profile, so a derived path that equals it does too.
        """
        area, target = self.paths.mission_map, self.target(file.relative)
        if file.kind in ("content", "plan"):
            # Content is named by its digest; a plan installs as stored (Architect ruling (g))
            if file.kind == "content" and hashlib.sha256(file.data).hexdigest() != found.group("sha"):
                raise ValueError("Restore editor content does not match its name.")
            return None
        if file.kind == "ledger":
            raw = json.loads(file.data.decode("utf-8"))
            fields = {name: value for name, value in raw.items() if name not in ("schema_version", "revision")}
            record = parse_entry(fields, FILE_SETS[TargetClass(raw.get("target_class"))])
            # A ledger path names no profile, so the entry must name the restored profile itself
            derived = (ledger_folder(area, record["target_key"]) / f"{record['operation_id']}.json"
                       if record["profile_id"] == profile_id else None)
        else:
            record = parse_record(PARSED_AS[file.kind], file.data)
            if file.kind == "superseded":
                derived = superseded_file(area, record["target_key"], operation_id)
            elif file.kind == "restored_copy":
                derived = restored_copy_file(area, found.group("profile"), found.group("key"),
                                             TargetClass(record["target_class"]), operation_id)
            elif file.kind in ORIGINAL_WRITERS:
                derived = original_folder(area, record["target_key"]) / ORIGINAL_FILE
            else:
                # A baseline, a last-applied or an invalidated record names the association of its folder
                derived = association_folder(area, record["profile_id"], record["mission_key"]) / target.name
        if derived != target:
            raise ValueError("Restore editor record names another target than its path.")
        return record

    def _created(self, relatives: Iterable[str]) -> list[str]:
        """Return the missing folders above the targets, each after its parent; the group creates them."""
        folders: list[str] = []
        for relative in relatives:
            missing, parent = [], self.target(relative).parent
            while parent != self.paths.root and not safe_exists(parent):
                missing.append(parent)
                parent = parent.parent
            folders += [name for name in map(self.paths.relative, reversed(missing)) if name not in folders]
        return folders

    def target(self, relative: str) -> Path:
        """Return the target of a manager-relative editor path."""
        return self.paths.root.joinpath(*relative.split("/"))

    def stage_root(self, operation_id: str) -> Path:
        """Return the private staging folder of one restore beside the editor area."""
        return self.paths.data / f".serverman-restore-{operation_id}-editor.stage"

    def file_paths(self, record: dict[str, Any], item: dict[str, Any]) -> tuple[Path, Path, Path]:
        """Derive the target, staging and holding paths of one file again; never trust journal paths."""
        match_file(item["kind"], item["relative"], record["profile_id"])
        target = self.target(item["relative"])
        stage = self.stage_root(record["operation_id"]).joinpath(*item["relative"].split("/"))
        return target, stage, target.with_name(f".{target.name}.{record['operation_id']}.restore-holding")

    def validate_root(self, group: dict[str, Any]) -> None:
        """Refuse a group of another manager root."""
        if Path(group["manager_root"]).resolve(strict=False) != self.paths.root:
            raise ValueError("Restore journal roots differ from current settings.")

    def stage(self, record: dict[str, Any], group: dict[str, Any], files: Iterable[EditorFile]) -> None:
        """Write each file to its staging path and keep its digest; refuse a target that changed since the plan."""
        data = {file.relative: file.data for file in files}
        for item in group["files"]:
            target, stage, holding = self.file_paths(record, item)
            if inventory(target) != item["before"] or safe_exists(stage) or safe_exists(holding):
                raise ValueError("Restore target changed before staging.")
            write_bytes(stage, data[item["relative"]])
            item["after"] = inventory(stage)
            if item["after"]["sha256"] != hashlib.sha256(data[item["relative"]]).hexdigest():
                raise OSError("Staged restore payload failed verification.")

    def publish(self, record: dict[str, Any], group: dict[str, Any], write: Callable[[dict[str, Any]], None],
                hook: Callable[[str], None]) -> None:
        """Create the folders, hold a replaced original, then give each staged file its name; each step is journaled."""
        for relative in group["created"]:
            self.target(relative).mkdir(exist_ok=True)
        for item in group["files"]:
            target, stage, holding = self.file_paths(record, item)
            if inventory(target) != item["before"] or inventory(stage) != item["after"]:
                raise ValueError("Restore target or staged content changed before publication.")
            if item["before"] is not None:
                rename_new_file(target, holding)
                item["held"] = True
                write(record)
                hook("HELD_EDITOR")
            rename_new_file(stage, target)
            item["published"] = True
            write(record)
            hook("PUBLISHED_EDITOR_FILE")

    def verify(self, record: dict[str, Any], group: dict[str, Any]) -> None:
        """Keep a committed group only when each published file still has its staged bytes."""
        self.validate_root(group)
        for item in group["files"]:
            if item["after"] is None or inventory(self.file_paths(record, item)[0]) != item["after"]:
                raise ValueError("Committed restore content changed; automatic recovery is blocked.")

    def rollback(self, record: dict[str, Any], group: dict[str, Any]) -> None:
        """Return each target to its state before the restore; changed content blocks instead of being removed."""
        self.validate_root(group)
        for item in reversed(group["files"]):
            target, stage, holding = self.file_paths(record, item)
            current, held = inventory(target), inventory(holding)
            # Remove only bytes that this restore published
            if current is not None and current == item["after"] and current != item["before"]:
                remove_file(target)
                current = None
            elif current is not None and current != item["before"]:
                raise ValueError("Restore destination changed externally; compensation is blocked.")
            if held is not None:
                if held != item["before"] or current is not None:
                    raise ValueError("Restore holding copy changed; compensation is blocked.")
                rename_new_file(holding, target)
                current = inventory(target)
            if current != item["before"]:
                raise ValueError("Original restore content is unavailable; recovery is required.")
            # Staging of a file whose digest the journal holds must still have it; earlier staging is private
            staged = inventory(stage)
            if staged is not None and item["after"] is not None and staged != item["after"]:
                raise ValueError("Restore stage changed externally.")
            remove_owned(stage)
        # Remove the folders that the group created, deepest first; a folder with other content blocks
        for relative in reversed(group["created"]):
            folder = self.target(relative)
            if safe_exists(folder):
                if any(folder.iterdir()):
                    raise ValueError("Restore editor folder changed externally; compensation is blocked.")
                folder.rmdir()
        self._remove_stage_root(record)

    def cleanup(self, record: dict[str, Any], group: dict[str, Any]) -> None:
        """After the commit, remove the held original and the staging folder; the superseded record keeps the original."""
        for item in group["files"]:
            _target, stage, holding = self.file_paths(record, item)
            for path, expected in ((stage, item["after"]), (holding, item["before"])):
                actual = inventory(path)
                if actual is not None:
                    if actual != expected:
                        raise ValueError("Restore cleanup content changed externally.")
                    remove_file(path)
        self._remove_stage_root(record)

    def _remove_stage_root(self, record: dict[str, Any]) -> None:
        """Remove the private staging folder once it holds no file of its own."""
        root = self.stage_root(record["operation_id"])
        found = inventory(root)
        if found is not None:
            if found["type"] != "directory" or found["files"]:
                raise ValueError("Restore stage changed externally.")
            remove_owned(root)
