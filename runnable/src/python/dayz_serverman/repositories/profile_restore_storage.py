"""Joint publication and conservative crash recovery of profile restore groups."""

import os
import shutil
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ..adapters.windows.shared_files import rename_directory
from .profile_restore_inventory import inventory, safe_exists
from .profile_restore_preparation import group_paths, stage_groups, remove_owned
from .profile_restore_journal import ProfileRestoreJournal


class ProfileRestoreStorage:
    """Publish under a durable journal and compensate only matching operation content."""

    def __init__(self, journals: ProfileRestoreJournal, profile_root: Path, recovery_root: Path,
                 phase_hook: Callable[[str], None] | None = None, disk_usage: Callable[[Path], Any] = shutil.disk_usage) -> None:
        """Bind storage roots and injectable failure/space probes."""
        self.journals = journals
        self.profile_root = profile_root
        self.recovery_root = recovery_root
        self.hook = phase_hook or (lambda phase: None)
        self.disk_usage = disk_usage

    def phase(self, record: dict, phase: str) -> None:
        """Durably publish the next phase before exposing its failure boundary."""
        previous = record["phase"]
        record["phase"] = phase
        try:
            self.journals.write(record)
        except Exception:
            record["phase"] = previous
            raise
        self.hook(phase)

    def restore(self, root: Path, operation_id: str, directory: Path, manifest: Any, preview: dict[str, Any],
                config: bytes, mapping: dict[str, str], before: dict | None, checkpoint: Callable[[str, int], None]) -> dict[str, Any]:
        """Stage whole groups and exclusively publish a revision-zero profile record."""
        for prior in self.journals.records():
            if prior["operation_id"] == operation_id:
                if prior["phase"] == "COMMITTED":
                    self.verify_committed(prior, root)
                    return prior["result"]
                raise ValueError("Restore operation already requires recovery.")
        identifier = preview["profile"]["profile_id"]
        replacement = preview["storage_policy"] == "replace_existing"
        mission_relative = preview["mission_root"] + (f"\\storage_{preview['instance_id']}" if replacement else "")
        result = {key: preview[key] for key in ("profile", "mission_root", "instance_id", "storage_policy", "missing_mods", "server_executable_present")}
        result.update(operation_id=operation_id, state="COMMITTED", recovery_copy=None,
                      backup_id=manifest.backup_id, manifest_digest=manifest.manifest_digest,
                      preview_fingerprint=preview["preview_fingerprint"], request=preview["request"])
        reasons = (["Install missing mods: " + ", ".join(preview["missing_mods"])] if preview["missing_mods"] else [])
        if not preview["server_executable_present"]:
            reasons.append("Install the server executable before starting.")
        result["readiness"] = {"ready": not reasons, "reasons": reasons, "server_started": False}
        result["selected_storage_present"] = manifest.reconstruction["selected_storage_present"]
        groups = [{"role": role, "relative": relative, "before": old, "after": None,
                   "published": False, "held": False, "recovery": None}
                  for role, relative, old in (("generated", f"serverman\\{identifier}", None),
                                              ("mission", mission_relative, before), ("profile", f"{identifier}.json", None))]
        record = {"type": "DIRECT_PROFILE_RESTORE", "version": 1, "operation_id": operation_id,
                  "phase": "PLANNED", "dayz_root": str(root), "profile_root": str(self.profile_root),
                  "profile_id": identifier, "groups": groups, "result": result, "cleanup_complete": False}
        self.preflight(record, root, manifest)
        self.phase(record, "PLANNED")
        try:
            self.phase(record, "PREPARING")
            checkpoint("PREPARING", 20)
            for group in groups:
                target, stage, holding = group_paths(record, group, root, self.profile_root)
                if inventory(target) != group["before"] or safe_exists(stage) or safe_exists(holding):
                    raise ValueError("Restore target changed before staging.")
                target.parent.mkdir(parents=True, exist_ok=True)
            stage_groups(record, directory, manifest, config, mapping, (root, self.profile_root), self.recovery_root)
            self.phase(record, "PREPARED")
            checkpoint("PREPARED", 50)
            self.phase(record, "PUBLISHING")
            checkpoint("PUBLISHING", 60)
            for group in groups:
                if group["role"] == "profile":
                    self.phase(record, "FILES_PUBLISHED")
                    self.phase(record, "PROFILE_PUBLISHING")
                    checkpoint("PROFILE_PUBLISHING", 90)
                self.publish(record, group, root)
                self.hook("PUBLISHED_" + group["role"].upper())
            self.phase(record, "PROFILE_PUBLISHED")
            result["recovery_copy"] = groups[1]["recovery"]
            self.verify_committed(record, root)
            self.phase(record, "COMMITTED")
        except Exception:
            if record["phase"] == "COMMITTED":
                # A failure after the durable commit cannot undo the completed transaction.
                result["cleanup_pending"] = True
                return result
            try:
                self.rollback(record, root)
            except Exception as recovery_error:
                record["phase"] = "RECOVERY_REQUIRED"
                self.journals.write(record)
                raise RuntimeError("Direct profile restore requires recovery; existing data was preserved.") from recovery_error
            raise
        try:
            self.cleanup(record, root)
            record["cleanup_complete"] = True
            self.journals.write(record)
        except Exception:
            result["cleanup_pending"] = True
        return result

    def preflight(self, record: dict, root: Path, manifest: Any) -> None:
        """Check destination and recovery volumes independently before any copying."""
        total = sum(entry.size for entry in manifest.entries) * 2 + 1_048_576
        volumes = {}
        for directory in (root, self.profile_root, self.recovery_root):
            safe_exists(directory)
            parent = directory
            while not parent.exists():
                parent = parent.parent
            volumes[parent.anchor.casefold()] = parent
        previous = record["groups"][1]["before"]
        if previous is not None:
            total += sum(item["size"] for item in previous["files"].values()) * 2
        for directory in volumes.values():
            if self.disk_usage(directory).free < total:
                raise OSError("Restore destination or recovery volume has insufficient free space.")

    def publish(self, record: dict, group: dict, root: Path) -> None:
        """Hold the original tree and publish complete staged content without merging worlds."""
        target, stage, holding = group_paths(record, group, root, self.profile_root)
        if inventory(target) != group["before"] or inventory(stage) != group["after"]:
            raise ValueError("Restore target or staged content changed before publication.")
        if group["before"] is not None:
            rename_directory(target, holding)
            group["held"] = True
            self.journals.write(record)
            self.hook("HELD_" + group["role"].upper())
        if safe_exists(target):
            raise ValueError("Restore destination was created concurrently.")
        if group["after"] is None:
            # A backup of an absent selected world restores absence, not an empty tree.
            if group["role"] != "mission" or record["result"]["selected_storage_present"]:
                raise ValueError("Restore publication is missing staged content.")
        elif group["role"] == "profile":
            os.link(stage, target)
        else:
            rename_directory(stage, target)
        group["published"] = True
        self.journals.write(record)

    def verify_committed(self, record: dict, root: Path) -> None:
        """Keep a committed profile only when all its published bytes still match."""
        self.validate_roots(record, root)
        for group in record["groups"]:
            target, _stage, _holding = group_paths(record, group, root, self.profile_root)
            expected_absence = group["role"] == "mission" and record["result"]["storage_policy"] == "replace_existing" and not record["result"]["selected_storage_present"]
            if (group["after"] is None and not expected_absence) or inventory(target) != group["after"]:
                raise ValueError("Committed restore content changed; automatic recovery is blocked.")

    def rollback(self, record: dict, root: Path) -> None:
        """Undo even fully published work unless its commit was durably recorded."""
        self.validate_roots(record, root)
        self.phase(record, "COMPENSATING")
        for group in reversed(record["groups"]):
            target, stage, holding = group_paths(record, group, root, self.profile_root)
            current = inventory(target)
            held = inventory(holding)
            if current == group["after"] and group["after"] is not None:
                remove_owned(target)
                current = None
            elif current != group["before"] and current is not None:
                raise ValueError("Restore destination changed externally; compensation is blocked.")
            if held is not None:
                if held != group["before"] or current is not None:
                    raise ValueError("Restore holding copy changed; compensation is blocked.")
                rename_directory(holding, target)
                current = inventory(target)
            if current != group["before"]:
                raise ValueError("Original restore content is unavailable; recovery is required.")
            if safe_exists(stage):
                if group["after"] is not None and inventory(stage) != group["after"]:
                    raise ValueError("Restore stage changed externally.")
                remove_owned(stage)
        self.phase(record, "ROLLED_BACK")

    def cleanup(self, record: dict, root: Path) -> None:
        """Discard verified operation-private staging/holding content, retaining recovery copies."""
        for group in record["groups"]:
            _target, stage, holding = group_paths(record, group, root, self.profile_root)
            for path, expected in ((stage, group["after"]), (holding, group["before"])):
                actual = inventory(path)
                if actual is not None:
                    if actual != expected:
                        raise ValueError("Restore cleanup content changed externally.")
                    remove_owned(path)

    def validate_roots(self, record: dict, root: Path) -> None:
        """Refuse recovery against another installation or profile repository."""
        if Path(record["dayz_root"]).resolve(strict=True) != root.resolve(strict=True) or Path(record["profile_root"]).resolve(strict=False) != self.profile_root.resolve(strict=False):
            raise ValueError("Restore journal roots differ from current settings.")

    def inspect(self, root: Path) -> dict[str, Any]:
        """Recover incomplete transactions and report any mismatch as a mutation blocker."""
        diagnostics = []
        try:
            for record in self.journals.records():
                if record["phase"] == "ROLLED_BACK":
                    continue
                if record["phase"] == "COMMITTED":
                    self.validate_roots(record, root)
                    # Finished cleanup ends recovery ownership; later normal server writes are valid.
                    if not record["cleanup_complete"]:
                        self.verify_committed(record, root)
                        self.cleanup(record, root)
                        record["cleanup_complete"] = True
                        self.journals.write(record)
                else:
                    self.rollback(record, root)
        except Exception:
            diagnostics.append({"code": "RECOVERY_REQUIRED", "message": "Direct profile restore recovery is blocked by changed or inaccessible data."})
        return {"blocked": bool(diagnostics), "diagnostics": diagnostics}
