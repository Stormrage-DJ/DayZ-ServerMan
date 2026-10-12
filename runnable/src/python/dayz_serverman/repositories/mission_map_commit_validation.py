"""Require staged records to describe the same snapshots, baseline and operation as their commit journal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_records import sealed
from .json_store import StagingPolicy
from .mission_map_commit_records import RecordUpdate, parse_record
from .mission_map_journal import MapJournal
from .mission_map_layout import TargetClass, original_folder
from .mission_map_records import RecordConflict, RecordFile
from .paths import PortablePaths


def validate_record_links(
    paths: PortablePaths, journal: MapJournal, updates: list[RecordUpdate],
    candidates: Mapping[tuple[str, str], bytes], owned_digest: Callable[[TargetClass, str, bytes], str | None],
) -> None:
    """Check baseline/original references and the applied record's baseline revision before staging."""
    records = [(update, parse_record(update.kind, sealed(update.record))) for update in updates]
    # An immutable candidate can never replace an earlier original or ledger entry
    for update, record in records:
        if update.kind in ("original", "ledger"):
            found = _read(paths.root / update.target_path, update.kind)
            if found is not None and found[0]["manifest_sha256"] != record["manifest_sha256"]:
                raise RecordConflict("a different create-once editor record already exists")
    base_update, baseline = next((update, record) for update, record in records if update.kind == "baseline")
    applied = next(record for update, record in records if update.kind == "applied")
    found = _read(paths.root / base_update.target_path, "baseline")
    # An identical baseline keeps its revision; only a changed baseline advances it
    revision = 0 if found is None else found[1] + (found[0]["manifest_sha256"] != baseline["manifest_sha256"])
    if (applied["baseline_revision"], applied["baseline_manifest_sha256"]) != (revision, baseline["manifest_sha256"]):
        raise ValueError("applied record baseline reference differs from the commit baseline")
    if (applied["plan_id"], applied["plan_revision"], applied["configuration_fingerprint"]) != (
            journal.plan_id, journal.plan_revision, journal.plan_fingerprint):
        raise ValueError("applied record plan identity differs from its journal")
    keys = [journal.mission_key] + ([journal.runtime_profile_key] if journal.runtime_profile_key else [])
    if baseline["target_keys"] != keys or {item["target_key"] for item in baseline["originals"]} != set(keys):
        raise ValueError("baseline targets and originals differ from its journal")
    # Resolve each original from the immutable published record or the first-capture candidate
    for reference in baseline["originals"]:
        key = reference["target_key"]
        published = _read(original_folder(paths.mission_map, key) / "original.json", "original")
        original = published[0] if published is not None else next(
            record for update, record in records if update.kind == "original" and record["target_key"] == key)
        if reference["manifest_sha256"] != original["manifest_sha256"]:
            raise ValueError("baseline original reference differs from the captured original")
    # Every DayZ-side group must be represented by equal before and after file states
    before = {(item["target_key"], item["path"]): item for item in applied["files_before"]}
    after = {(item["target_key"], item["path"]): item for item in applied["files_after"]}
    baseline_files = {(item["target_key"], item["path"]): item for item in baseline["files"]}
    ledger_files = {(record["target_key"], item["path"]): item
                    for update, record in records if update.kind == "ledger" for item in record["files"]}
    expected_files = set()
    for group in journal.groups:
        key = journal.mission_key if group.target_class == "mission" else journal.runtime_profile_key
        expected_files.add((key, group.relative_path))
        old, new = before.get((key, group.relative_path)), after.get((key, group.relative_path))
        if old is None or new is None or (old["existed"], old["sha256"], new["existed"], new["sha256"]) != (
                group.old_existed, group.old_digest, group.new_exists, group.new_digest):
            raise ValueError("applied file snapshots differ from their journal group")
        if group.new_exists and new["owned_sha256"] is None:
            raise ValueError("applied file has no owned-value digest")
        # Ledger projections must describe the actual candidate and baseline bytes
        target_class = TargetClass(group.target_class)
        projection = lambda data: None if data is None else owned_digest(target_class, group.relative_path, data)
        old_data = read_bytes_shared(Path(group.target_path)) if group.old_existed else None
        new_data = candidates.get((group.target_class, group.relative_path))
        base_file = baseline_files.get((key, group.relative_path))
        if base_file is None:
            raise ValueError("baseline omits a journal group file")
        base_data = base_update.contents.get(base_file["sha256"]) if base_file["existed"] else None
        if base_file["existed"] and base_data is None:
            raise ValueError("baseline content is missing")
        if new["owned_sha256"] != projection(new_data):
            raise ValueError("applied owned-value digest differs from its candidate")
        expected = (group.old_digest, group.new_digest, base_file["sha256"],
                    projection(old_data), projection(new_data), projection(base_data))
        item = ledger_files.get((key, group.relative_path))
        names = ("before_sha256", "after_sha256", "baseline_sha256",
                 "owned_before_sha256", "owned_after_sha256", "owned_baseline_sha256")
        if item is None or tuple(item[name] for name in names) != expected:
            raise ValueError("ledger file digests differ from the commit snapshots")
    if set(ledger_files) != expected_files:
        raise ValueError("ledger file set differs from the commit journal")


def _read(path: Path, kind: str) -> tuple[dict[str, Any], int] | None:
    """Read a checked published record while ignoring commit staging beside it."""
    return RecordFile(path, lambda raw: parse_record(kind, raw), StagingPolicy.OBSERVER).read()
