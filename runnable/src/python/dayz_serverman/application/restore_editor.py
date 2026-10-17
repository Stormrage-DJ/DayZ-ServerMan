"""The editor part of a same-profile backup restore: the active association and its lock (D19, leaf 10.10).

`RestoreService` asks `RestoreEditor.build` for the editor files of one restore, in the preview and again in the
apply. The preview binds the summary and the notes of the answer, so a changed record or another D5 row makes it
stale. The apply holds the association lock from the WRITE_JOURNAL checkpoint, after the folder writer side.

The editor part never blocks the restore (Architect 2026-10-16 21:00:06 in evidence 10.10, ruling (5)). A part
that does not fit the active target keys is skipped and named in `skipped`. Unsafe paths still refuse, through the
checks of the payload targets and of the editor targets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol

from ..domain.backups import BackupManifest
from ..domain.restore_editor_texts import editor_notes
from ..domain.restores import RestoreTarget
from ..repositories.mission_map_layout import TargetClass, target_key
from ..repositories.paths import PortablePaths
from .mission_configuration import _contained
from .mission_map_locks import AssociationLocks
from .mission_map_plans import MissionMapPlanService, MissionPathChanged
from .mission_map_sharing import UNRESOLVED, other_targets
from .restore_editor_files import EditorArchive, EditorContext, EditorRestore
from .restore_editor_records import EditorBuilder
from .settings import SettingsValidationError


class ProfileList(Protocol):
    """The profile list whose targets the shared check resolves."""

    def list(self) -> tuple[Any, ...]:
        """Return every stored profile record."""
        ...


class RestoreEditor:
    """Decide the editor files of one same-profile backup restore and hold its association lock."""

    def __init__(self, paths: PortablePaths, plans: MissionMapPlanService, locks: AssociationLocks,
                 profiles: ProfileList, *, clock: Callable[[], datetime] | None = None) -> None:
        """Keep the manager layout, the plan service, the process's association locks and the profile list."""
        self.paths, self._plans, self._locks, self._profiles = paths, plans, locks, profiles
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def hold(self, restore: EditorRestore) -> Iterator[None]:
        """Hold the association lock of the restore; the caller takes it after the folder writer side (D2)."""
        held = self._locks.hold(restore.profile_id, restore.mission_key) if restore.writes else nullcontext()
        with held:
            yield

    def build(self, profile_id: str, directory: Path, manifest: BackupManifest,
              targets: tuple[RestoreTarget, ...], *, operation_id: str) -> EditorRestore | None:
        """Return the editor part of the restore, or None when it has none (no carried record, no last Apply)."""
        field = manifest.editor
        context, skipped = self._context(profile_id)
        records = self._profiles.list()
        names = {record.values.profile_id: record.values.display_name for record in records}
        if context is None:
            # Nothing is restored, and the records of the active folder stay unchanged (D2)
            if field is None:
                return None
            return EditorRestore((), {"inputs": {}, "rows": {}, "skipped": skipped},
                                 editor_notes(True, skipped, {}, names), profile_id, "")
        # An archive of another mission key restores nothing; the active key takes "Backup without an original"
        used = field
        if field is not None and field["association"]["mission_key"] != context.mission_key:
            skipped, used = skipped + ["mission_key"], None
        runtime = next((item for item in (used or {}).get("originals", []) if item["target_class"] == "runtime"), None)
        if runtime is not None and runtime["target_key"] != context.runtime_key:
            skipped = skipped + ["runtime_key"]
        # The shared check of the other profiles runs on the lane, before the journal starts (ruling (a))
        build = EditorBuilder(self.paths, context, EditorArchive(directory, manifest), targets, operation_id,
                              self._clock(), lambda: other_targets(context.root, records, profile_id))
        if used is not None:
            build.originals(used, runtime="runtime_key" not in skipped)
            build.plan(used)
        else:
            build.read_active()
        build.baseline(used)
        build.applied(used)
        return build.result(field is not None, skipped, names)

    def _context(self, profile_id: str) -> tuple[EditorContext | None, list[str]]:
        """Resolve the active association, or give the reason why the editor part is skipped.

        A runtime profile folder that cannot be resolved leaves the runtime class out (key None).
        """
        try:
            profile, root, mission_root, mission = self._plans._resolve(profile_id)
            mission_key = target_key(TargetClass.MISSION, mission)
            changed = self._plans.load(profile.values.profile_id)["path_change"] is not None
        except MissionPathChanged:
            return None, ["path_change"]
        except UNRESOLVED + (SettingsValidationError,):
            return None, ["mission_unresolved"]
        if changed:
            return None, ["path_change"]
        runtime, runtime_key = None, None
        relative = profile.values.runtime_profile
        try:
            if relative is not None:
                runtime = _contained(root, root.joinpath(*PureWindowsPath(relative).parts), directory=True)
                runtime_key = target_key(TargetClass.RUNTIME, runtime)
        except UNRESOLVED:
            runtime, runtime_key = None, None
        return EditorContext(root, profile.values.profile_id, mission_root, mission, mission_key, runtime,
                             runtime_key), []
