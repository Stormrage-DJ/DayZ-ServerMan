"""Target-proof lookup for mod rows: is the server-folder copy proven, without a tree walk."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path, PureWindowsPath
from typing import Protocol

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    dayz_root_identity,
    safe_dayz_root,
)
from ..domain.content_proofs import target_directory_key
from ..domain.mod_row_state import TargetProof

# One target record: root identity, target directory key (NFC, case-folded), Workshop id, manifest id
TargetRecord = tuple[str, str, str, str]


class TargetRecordSource(Protocol):
    """Read-only source of target records; an unreadable source yields no record."""

    # Return every stored target record
    def target_records(self) -> Collection[TargetRecord]: ...


class TargetProofLookup:
    """Classify the target of each Workshop row from the wired record sources."""

    def __init__(self, sources: Iterable[TargetRecordSource]) -> None:
        """Store the record sources in lookup order: proof store first, then legacy."""
        self._sources = tuple(sources)

    def resolve(
        self, dayz_root: str | None, items: Iterable[tuple[str, str, str | None]],
    ) -> dict[str, TargetProof]:
        """Return the target proof per mod directory.

        Each item is (mod directory, Workshop id, installed manifest id). Every item
        is UNKNOWN when no source is wired or the DayZ root is not usable.
        """
        wanted = tuple(items)
        unknown = {directory: TargetProof.UNKNOWN for directory, _id, _manifest in wanted}
        if not self._sources or dayz_root is None:
            return unknown
        # Bind the lookup to the identity of the current server folder
        try:
            root = safe_dayz_root(Path(dayz_root))
            identity = dayz_root_identity(root)
        except (OSError, ValueError, PublicationPathError):
            return unknown
        # Read each source once; a failing source contributes no record
        records: set[TargetRecord] = set()
        for source in self._sources:
            try:
                # Apply the directory key to every record, whatever the source wrote
                records.update(
                    (root_identity, target_directory_key(key), workshop_id, manifest_id)
                    for root_identity, key, workshop_id, manifest_id in source.target_records()
                )
            except Exception:
                continue
        result: dict[str, TargetProof] = {}
        for directory, workshop_id, manifest_id in wanted:
            # A record for this root, directory, item and manifest proves the copy
            if (identity, target_directory_key(directory), workshop_id, manifest_id) in records:
                result[directory] = TargetProof.PROVEN
            elif _is_directory(root, directory):
                result[directory] = TargetProof.UNPROVEN
            else:
                result[directory] = TargetProof.MISSING
        return result


def _is_directory(root: Path, directory: str) -> bool:
    """Return whether the mod directory exists below the server folder."""
    try:
        return root.joinpath(*PureWindowsPath(directory).parts).is_dir()
    except (OSError, ValueError):
        return False
