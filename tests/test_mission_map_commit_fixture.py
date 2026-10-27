"""Reusable disposable commit candidates, records and recording A3 guard ports."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_commit import MissionMapCommit
from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords, RecordUpdate
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_layout import association_folder, original_folder, ledger_folder
from tests.test_mission_map_journal import JournalFixture
from tests.test_mission_map_records import VANILLA, OUTPUT, original, baseline, applied, sha


class RecordingPorts:
    """Record mutex, STOPPED and writer events without controlling a real server."""

    def __init__(self, events):
        """Share the fixture's event list and default to STOPPED."""
        self.events, self.state = events, ServerState.STOPPED
        self.held = False

    @contextmanager
    def guard(self, _root):
        """Record the installation mutex hold and release."""
        self.events.append("mutex")
        try:
            yield
        finally:
            self.events.append("release mutex")

    def status(self):
        """Prove the configured fake server state under the mutex."""
        self.events.append("stopped")
        return SimpleNamespace(state=self.state)

    @contextmanager
    def exclusive(self, _seconds):
        """Record the writer hold and release."""
        self.events.append("writer")
        self.held = True
        try:
            yield
        finally:
            self.held = False
            self.events.append("release writer")


class RecordingLocks(AssociationLocks):
    """Record the real association lock as the innermost hold."""

    def __init__(self, events):
        """Create the process-shared association locks and event sink."""
        super().__init__()
        self.events = events

    @contextmanager
    def hold(self, profile_id, key):
        """Hold the inherited association lock and record its boundaries."""
        with super().hold(profile_id, key):
            self.events.append("association")
            try:
                yield
            finally:
                self.events.append("release association")


class CommitFixture(JournalFixture):
    """One disposable vanilla mission with complete original, baseline, applied and ledger candidates."""

    def setUp(self):
        """Replace fixture content with a minimal vanilla prototype and create recording guard ports."""
        super().setUp()
        for target in self.mission.iterdir():
            target.unlink()
        (self.mission / "mapgroupproto.xml").write_bytes(VANILLA)
        self.events = []
        self.ports = RecordingPorts(self.events)
        self.locks = RecordingLocks(self.events)

    def candidates(self):
        """Return an apply journal and the four staged record candidates."""
        operation = "op-" + uuid.uuid4().hex
        group = self.group("mission", "mapgroupproto.xml", OUTPUT)
        # Staging and recovery paths carry this operation's generated identity
        group.staging_path = group.staging_path.replace("op-1a2b3c4d", operation)
        group.recovery_path = group.recovery_path.replace("op-1a2b3c4d", operation)
        journal = MapJournal(operation, "apply", "main", "mpmissions\\dayzOffline.chernarusplus", self.mission_key(),
                             None, None, str(self.dayz), "plan01", 4, sha(b"plan"), sha(b"preview"), 2, 7, [group], [])
        origin = original(self.mission_key())
        origin.update(operation_id=operation, mission_root=journal.mission_root, display_path=str(self.mission))
        base = baseline(key=self.mission_key())
        base.update(mission_root=journal.mission_root,
                    originals=[{"target_key": journal.mission_key, "manifest_sha256": sealed(origin)["manifest_sha256"]}])
        after = applied(key=self.mission_key())
        after.update(mission_root=journal.mission_root, operation_id=operation, plan_id=journal.plan_id,
                     configuration_fingerprint=journal.plan_fingerprint,
                     baseline_manifest_sha256=sealed(base)["manifest_sha256"])
        after["files_after"][0]["owned_sha256"] = sha(OUTPUT)
        entry = {"operation_id": operation, "operation_kind": "apply", "recorded_at": "2026-10-10T12:05:00Z",
                 "profile_id": "main", "target_class": "mission", "target_key": journal.mission_key,
                 "display_path": str(self.mission), "mission_root": journal.mission_root, "output": True,
                 "carried_operation_id": None, "files": [{"path": "mapgroupproto.xml",
                 "before_sha256": sha(VANILLA), "after_sha256": sha(OUTPUT), "baseline_sha256": sha(VANILLA),
                 "owned_before_sha256": sha(VANILLA), "owned_after_sha256": sha(OUTPUT), "owned_baseline_sha256": sha(VANILLA)}]}
        folder = association_folder(self.paths.mission_map, "main", journal.mission_key)
        records = [
            ("original", original_folder(self.paths.mission_map, journal.mission_key) / "original.json", origin),
            ("baseline", folder / "baseline.json", base), ("applied", folder / "applied.json", after),
            ("ledger", ledger_folder(self.paths.mission_map, journal.mission_key) / f"{operation}.json", entry),
        ]
        updates = [RecordUpdate(kind, self.paths.relative(path), record, contents={sha(VANILLA): VANILLA})
                   for kind, path, record in records]
        return journal, updates, {("mission", "mapgroupproto.xml"): OUTPUT}

    def service(self, fault=None, reserved=None):
        """Build the commit step with the shared locks and authoritative test adapters."""
        return MissionMapCommit(self.paths, self.repository, InstallationGuard(self.ports, self.ports),
                                self.ports, self.locks, owned_digest=lambda _class, _path, data: sha(data),
                                check_reserved=reserved or (lambda _class, _path, _data: None), fault_hook=fault)

    def run_commit(self, journal, updates, candidates, service=None):
        """Call from a simulated held operation lane and record the revalidation boundary."""
        self.events.append("lane")
        return (service or self.service()).commit(journal, updates, candidates,
                         revalidate=lambda: self.events.append("revalidate"), existing_profiles={"main"})

    def record_hooks(self, fault=None):
        """Return the real manager-record hooks for independent restart recovery."""
        from dayz_serverman.repositories.publication_contracts import RecoveryHooks
        records = CommitRecords(self.paths, self.repository, fault or (lambda _phase, _index: None))
        return RecoveryHooks(records.roll_forward, records.rollback)
