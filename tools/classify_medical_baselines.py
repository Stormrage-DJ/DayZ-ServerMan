"""Read-only classification of the stored `medical_loot_zones` baselines of installed missions (6.4).

For each mission folder, the script classifies the legacy `.dayz_manager_backups/mapgroupproto.xml` baseline
against the current `mapgroupproto.xml` through the 6.3 element mapping, and prints a Markdown table. It also
lists the manager-owned baselines; the manager keys them by profile, so the service method
`MedicalFeatureService.classify_zone_baseline` classifies them for their profile's mission.

The script only reads. It writes no mission file, baseline or manager metadata.

Usage (repository root, project interpreter):
    runnable\\.venv\\Scripts\\python.exe tools\\classify_medical_baselines.py MPMISSIONS [--baselines FOLDER]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the application package importable when this file runs as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows.shared_files import read_bytes_shared  # noqa: E402
from dayz_serverman.repositories.medical_zone_classification import LEGACY, classify_zone_baseline  # noqa: E402


def classify_missions(missions: Path) -> list[str]:
    """Return one Markdown table row for each mission folder that holds a `mapgroupproto.xml`."""
    rows = []
    for mission in sorted(path for path in missions.iterdir() if (path / "mapgroupproto.xml").is_file()):
        legacy = mission / ".dayz_manager_backups" / "mapgroupproto.xml"
        if not legacy.is_file():
            rows.append(f"| {mission.name} | none | not classified | - | - | - |")
            continue
        result = classify_zone_baseline(read_bytes_shared(mission / "mapgroupproto.xml"),
                                        read_bytes_shared(legacy), LEGACY)
        state = "-" if result.enabled is None else ("on" if result.enabled else "off")
        verdict = "mappable" if result.mappable else "unmappable"
        endings = f"{result.baseline_line_endings} / {result.current_line_endings}"
        rows.append(f"| {mission.name} | {result.location} | {verdict} | {state} | {endings} | {result.reason or '-'} |")
    return rows


def main() -> int:
    """Print the classification table of the installed missions and the manager-owned baselines."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("missions", type=Path, help="the mpmissions folder of the DayZ server")
    parser.add_argument("--baselines", type=Path, default=REPO_ROOT / "runnable" / "data" / "tweak-baselines",
                        help="the manager baseline store")
    arguments = parser.parse_args()
    print("| Mission | Baseline | Result | State | Line endings (baseline / current) | Reason |")
    print("|---|---|---|---|---|---|")
    print("\n".join(classify_missions(arguments.missions)))
    # Manager-owned baselines belong to a profile, so only their place is listed here
    owned = sorted(arguments.baselines.glob("*/medical_loot_zones.original.xml")) if arguments.baselines.is_dir() else []
    print(f"\nManager-owned medical_loot_zones baselines: {len(owned)}")
    for path in owned:
        print(f"- profile {path.parent.name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
