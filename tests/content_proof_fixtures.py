"""Shared fixtures for the content proof tests: a Workshop cache, a server folder, records."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.content_proofs import (  # noqa: E402
    SourceProofRecord,
    TargetProofRecord,
)
from dayz_serverman.domain.workshop import ItemOutcome, RequiredWorkshopItem  # noqa: E402

# Fixed UTC time text in the form the verifier writes
VERIFIED_AT = "2026-10-03T12:00:00.000+00:00"


def write_manifest(base: Path, records: dict[str, tuple[str, int]]) -> None:
    """Write the Workshop manifest; each record is (manifest id, update time)."""
    installed = " ".join(
        f'"{workshop_id}" {{ "manifest" "{manifest}" "size" "5" "timeupdated" "{time}" }}'
        for workshop_id, (manifest, time) in records.items()
    )
    details = " ".join(
        f'"{workshop_id}" {{ "latest_manifest" "{manifest}" "latest_timeupdated" "{time}" }}'
        for workshop_id, (manifest, time) in records.items()
    )
    (base / "steamapps" / "workshop" / "appworkshop_221100.acf").write_text(
        '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" "NeedsDownload" "0" '
        f'"WorkshopItemsInstalled" {{ {installed} }} "WorkshopItemDetails" {{ {details} }} }}',
        encoding="utf-8",
    )


def build_cache(base: Path, items: dict[str, bytes]) -> Path:
    """Create one cached item per id with manifest id 9 and return the content root."""
    root = base / "steamapps" / "workshop" / "content" / "221100"
    for workshop_id, content in items.items():
        (root / workshop_id / "Addons").mkdir(parents=True)
        (root / workshop_id / "Addons" / "mod.pbo").write_bytes(content)
    write_manifest(base, {workshop_id: ("9", 100) for workshop_id in items})
    return root


def build_target(dayz: Path, directory: str, content: bytes) -> Path:
    """Create a server-folder copy with the same layout as a cached item."""
    target = dayz / directory
    (target / "Addons").mkdir(parents=True)
    (target / "Addons" / "mod.pbo").write_bytes(content)
    return target


def profile(mods: dict[str, str], profile_id: str = "main", digest: str = "c" * 64):
    """Return a minimal profile; `mods` maps the mod directory to its Workshop id."""
    return SimpleNamespace(
        semantic_digest=digest,
        values=SimpleNamespace(profile_id=profile_id, mods=[
            SimpleNamespace(directory=directory, launch_scope="client",
                            source=SimpleNamespace(kind="workshop", workshop_id=workshop_id))
            for directory, workshop_id in mods.items()
        ]),
    )


def required(*workshop_ids: str) -> tuple[RequiredWorkshopItem, ...]:
    """Return the required items in the given order."""
    return tuple(RequiredWorkshopItem(workshop_id, index, "client")
                 for index, workshop_id in enumerate(workshop_ids))


def current(*workshop_ids: str) -> dict[str, ItemOutcome]:
    """Return a success outcome for every id."""
    return {workshop_id: ItemOutcome.VERIFIED_CURRENT for workshop_id in workshop_ids}


def source_proof(**changes: object) -> SourceProofRecord:
    """Return a well-formed source record with optional field changes."""
    values = {
        "cache_root_identity": "1" * 64, "installed_manifest_id": "9", "time_updated": 100,
        "content_inventory_digest": "2" * 64, "regular_file_count": 1,
        "total_regular_bytes": 5, "metadata_inventory_digest": "3" * 64,
        "verified_at": VERIFIED_AT,
    }
    return SourceProofRecord(**{**values, **changes})


def target_proof(**changes: object) -> TargetProofRecord:
    """Return a well-formed target record with optional field changes."""
    values = {
        "workshop_id": "111", "installed_manifest_id": "9",
        "content_inventory_digest": "2" * 64, "target_metadata_digest": "4" * 64,
        "verified_at": VERIFIED_AT,
    }
    return TargetProofRecord(**{**values, **changes})
