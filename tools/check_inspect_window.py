"""Real-window check of leaf 4.6: tier reads and Inspect mode through the production js_api path.

The script copies the Inspect files of one installed mission into a new temporary folder. It only reads the
installed mission. It builds the production MissionMapCoordinator over a disposable manager area and DayZ root
in that folder. BridgeFacade and the production MissionMapEditorApi serve it in one pywebview window. Only the
profile and settings readers are stand-ins. A driver script in the page then runs the steps. They are a tier
read of a selected zone, a tier choice, a selection change during a read, and a point Inspect. Then follow a
delete of the winning zone, Escape, and a close during a read. The driver records every Inspect call and what
the page shows.

Usage (repository root, project interpreter, Microsoft Edge closed, in the foreground):
    runnable\\.venv\\Scripts\\python.exe tools\\check_inspect_window.py [--mission FOLDER] [--out FOLDER]
The results are in <out>\\results.json and on standard output; the exit code is 0 only when every step passes.
The window uses pywebview private mode, as the other real-window tools do.
"""

from __future__ import annotations

import sys

# Write no bytecode cache next to the sources; the check writes only into its temporary folder
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

# Make the tools folder and the application package importable when this file runs as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "runnable" / "src" / "python")]

from dayz_serverman.adapters.windows.shared_files import read_bytes_shared, write_bytes_atomically  # noqa: E402
from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator  # noqa: E402
from dayz_serverman.application.mission_map_plans import MissionMapPlanService  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.host.assets import source_frontend_root  # noqa: E402
from dayz_serverman.host.editor_assets import compose_editor_html  # noqa: E402
from dayz_serverman.host.map_editor_window_api import MissionMapEditorApi  # noqa: E402
from tools.inspect_window_driver import DRIVER_SCRIPT  # noqa: E402

# The installed mission that the check reads by default; it is never written
DEFAULT_MISSION = Path(r"D:\Steam\steamapps\common\DayZServer\mpmissions\dayzOffline.chernarusplus")
# Mission files that load, the capability read and Inspect use; missing optional files are skipped
MISSION_FILES = ("areaflags.map", "cfglimitsdefinition.xml", "mapgroupproto.xml", "mapgrouppos.xml",
                 "mapgroupdirt.xml", "cfgeconomycore.xml")
# The driver gets this long for all steps; one whole-map object read takes about 2 s on chernarusplus
TIMEOUT_SECONDS = 300.0
PROFILE_ID = "check"


class Profiles:
    """Return the one check profile, as ProfileService.read does."""

    def __init__(self, mission_name: str) -> None:
        """Build the profile record on the copied mission."""
        values = ProfileInput(PROFILE_ID, "Inspect check", "DayZServer_x64.exe", "serverDZ.cfg",
                              f"mpmissions\\{mission_name}", 2302, (), (), "profile_check")
        self.record = ProfileRecord(1, values)

    def read(self, profile_id: object) -> ProfileRecord:
        """Return the record of the check profile."""
        if profile_id != PROFILE_ID:
            raise KeyError(profile_id)
        return self.record


def copy_mission(source: Path, out: Path) -> Path:
    """Copy the needed files of the installed mission into the temporary DayZ root; read the source only."""
    target = out / "DayZServer" / "mpmissions" / source.name
    target.mkdir(parents=True)
    (out / "DayZServer" / "profile_check").mkdir()
    for name in MISSION_FILES:
        if (source / name).is_file():
            write_bytes_atomically(target / name, read_bytes_shared(source / name))
    return target


def zones(bounds: dict, tiers: list[str]) -> list[dict]:
    """Return a large zone A with a tier and a small Inherit zone B above it at the terrain centre."""
    x, z = bounds["x_min"] + bounds["width"] / 2, bounds["z_min"] + bounds["height"] / 2
    extent = min(bounds["width"], bounds["height"])

    def circle(identifier: str, name: str, radius: float, tier: str | None) -> dict:
        """Return one visible, unlocked loot circle."""
        return {"id": identifier, "name": name, "kind": "loot_circle", "visible": True, "locked": False,
                "centre": {"x": x, "z": z}, "radius": radius, "tier": tier, "abundance": "rich"}

    return [circle("B", "Zone B", extent * 0.02, None), circle("A", "Zone A", extent * 0.2, tiers[-1])]


def build_api(out: Path, mission: Path) -> tuple[MissionMapEditorApi, dict]:
    """Return the production editor API over the production coordinator, with a saved plan of two zones."""
    root = out / "DayZServer"
    settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(root), revision=1))
    service = MissionMapPlanService(Profiles(mission.name), settings, out / "manager" / "data" / "mission-map")  # type: ignore[arg-type]
    coordinator = MissionMapCoordinator(service)
    loaded = coordinator.load_mission_map({"profile_id": PROFILE_ID})
    tiers = loaded["capabilities"]["holdable_tiers"]
    if loaded["plan"] is None or not tiers:
        raise RuntimeError("the copied mission has no usable tier map header or no holdable tier")
    plan = loaded["plan"]
    plan["terrain"]["bounds_confirmed"] = True
    plan["objects"] = zones(plan["terrain"]["bounds"], tiers)
    coordinator.save_mission_map_plan({"profile_id": PROFILE_ID, "plan": plan, "expected_revision": None,
                                       "background_selection_id": None})
    setup = {"mission": mission.name, "bounds": plan["terrain"]["bounds"], "holdable_tiers": tiers,
             "tier_writes": coordinator.load_mission_map({"profile_id": PROFILE_ID})["capabilities"]["tier_writes"]}
    return MissionMapEditorApi(BridgeFacade(coordinator.handlers()), PROFILE_ID, None), setup


def run_checks(window: object, results: dict, out: Path) -> None:
    """Start the page driver after the page loaded, wait for its record, and close the window."""
    try:
        deadline = time.perf_counter() + 60.0
        while window.evaluate_js("document.body.dataset.mapReady") != "true":
            if time.perf_counter() > deadline:
                raise RuntimeError("the editor document did not load")
            time.sleep(0.1)
        window.evaluate_js(DRIVER_SCRIPT)
        deadline = time.perf_counter() + TIMEOUT_SECONDS
        record: dict = {}
        while time.perf_counter() < deadline:
            record = json.loads(window.evaluate_js("JSON.stringify(window.__check)") or "{}")
            if record.get("done") or record.get("error"):
                break
            time.sleep(0.2)
        results.update(steps=record.get("steps", []), calls=record.get("calls", []), driver_error=record.get("error"))
        if not record.get("done") and not record.get("error"):
            results["driver_error"] = "the driver did not finish in time"
    except Exception as error:  # every failure of the run is a recorded outcome
        results["error"] = f"{type(error).__name__}: {error}"
    finally:
        steps = results.get("steps", [])
        results["passed"] = bool(steps) and all(step.get("passed") for step in steps) and not results.get(
            "driver_error") and not results.get("error")
        write_bytes_atomically(out / "results.json", json.dumps(results, indent=2).encode("utf-8"))
        # The close step settled the document for a close; the native close follows here
        window.destroy()


def main(argv: list[str] | None = None) -> int:
    """Copy the mission, open one window and run the check; return 0 only when every step passes."""
    parser = argparse.ArgumentParser(description="Check 4.6 tier reads and Inspect mode in a real pywebview window.")
    parser.add_argument("--mission", type=Path, default=DEFAULT_MISSION, help="installed mission folder (read only)")
    parser.add_argument("--out", type=Path, help="new output folder (default: a new temporary folder)")
    args = parser.parse_args(argv)
    source = args.mission.resolve(strict=True)
    out = (args.out or Path(tempfile.mkdtemp(prefix="inspect-window-"))).resolve()
    # The output never lies inside the DayZ installation of the source mission
    if out.is_relative_to(source.parent.parent):
        raise SystemExit("The output folder must lie outside the DayZ installation.")
    out.mkdir(parents=True, exist_ok=True)
    mission = copy_mission(source, out)
    api, setup = build_api(out, mission)
    results: dict = {"source": str(source), "out": str(out), "setup": setup}
    import webview

    # Use the window settings of the application host
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    window = webview.create_window("4.6 Inspect check", html=compose_editor_html(source_frontend_root()),
                                   js_api=api, width=1280, height=800, text_select=True, zoomable=False)
    api._bind_window(window)
    webview.start(run_checks, (window, results, out), gui="edgechromium", debug=False, http_server=False,
                  private_mode=True)
    print(json.dumps({"passed": results["passed"], "setup": setup, "error": results.get("error"),
                      "driver_error": results.get("driver_error"),
                      "steps": [{key: step.get(key) for key in ("name", "passed", "seconds", "detail")}
                                for step in results.get("steps", [])]}, indent=2))
    print(f"Results: {out / 'results.json'}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
