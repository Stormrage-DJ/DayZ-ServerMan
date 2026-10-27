"""Real-window check of the 3.16 A9 condition: load a large image through the production js_api path.

The script opens the composed editor document in one pywebview window. Its js_api is the production
MissionMapEditorApi over a BridgeFacade with the production display-read handlers. Only the plan load,
the mission folder and the native image dialog are fakes: the dialog returns the generated image path.
The page's own Choose image action then reads every part, joins the text, decodes and draws the image.

Usage (repository root, project interpreter, Microsoft Edge closed, in the foreground):
    runnable\\.venv\\Scripts\\python.exe tools\\check_background_load.py [--target 32mib|16mib|6mib] [--out FOLDER]
The A9 rule: 32 MiB first; if it fails, run 16mib (the IMAGE_BYTE_LIMIT fallback); if that fails too,
run 6mib, and a failure there returns 3.16 to the Architect (A9 reversal).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

# Make the tools folder and the application package importable when this file runs as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "runnable" / "src" / "python")]

from dayz_serverman.adapters.windows.shared_files import write_bytes_atomically  # noqa: E402
from dayz_serverman.application.mission_map_display import IMAGE_SIDE_LIMIT, MissionMapDisplay  # noqa: E402
from dayz_serverman.application.mission_map_images import ImageSelectionService  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.host.assets import source_frontend_root  # noqa: E402
from dayz_serverman.host.editor_assets import compose_editor_html  # noqa: E402
from dayz_serverman.host.map_editor_window_api import MissionMapEditorApi  # noqa: E402
from dayz_serverman.repositories.mission_map_layout import TargetClass, target_key  # noqa: E402
from tools.background_load_image import TARGETS, judge, noise_png  # noqa: E402


RUNS = 3
TIMEOUT_SECONDS = 60.0
# Wrap the page's two display calls, start the page's own Choose image action and record the timings
START_SCRIPT = r'''(() => {
  const api = window.pywebview.api, record = {start: performance.now(), calls: [], drawn: false, error: null};
  window.__a9 = record;
  for (const name of ["select_background", "read_background"]) {
    const original = api[name];
    api[name] = async (...args) => { const begun = performance.now(); const answer = await original(...args);
      record.calls.push({method: name, seconds: (performance.now() - begun) / 1000, chars: answer?.value?.data?.length ?? 0});
      record.last = performance.now(); return answer; };
  }
  window.ServerManMapBackground.choose().catch((error) => { record.error = String(error); });
  const watch = () => {
    if (window.ServerManMapRaster.drawn().includes("image")) {
      const now = performance.now();
      Object.assign(record, {drawn: true, total_s: (now - record.start) / 1000, join_s: (now - record.last) / 1000});
    } else if (performance.now() - record.start < 60000) requestAnimationFrame(watch);
  };
  requestAnimationFrame(watch);
  return true;
})()'''


class DialogWindow:
    """Stand-in for the native image dialog: it always chooses the generated file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def create_file_dialog(self, **_options: object) -> tuple[str]:
        """Return the generated image path, as the native open dialog would."""
        return (str(self._path),)


def build_api(image: Path, mission: Path, byte_limit: int) -> MissionMapEditorApi:
    """Return the production editor API over production display handlers and a fake plan load."""
    key = target_key(TargetClass.MISSION, mission)
    profile = SimpleNamespace(values=SimpleNamespace(profile_id="check"))
    plans = SimpleNamespace(_resolve=lambda _profile: (profile, mission.parent, "mpmissions\\a9.check", mission),
                            _path_change=lambda _active: [], _change_reason=lambda _active: "mission_path_changed")
    images = ImageSelectionService(byte_limit=byte_limit, side_limit=IMAGE_SIDE_LIMIT)
    display = MissionMapDisplay(plans, images)  # type: ignore[arg-type]
    association = {"profile_id": "check", "mission_root": "mpmissions\\a9.check", "mission_key": key,
                   "runtime_profile_key": None}
    plan = {"format": "dayz-serverman/mission-map-plan", "version": 1, "plan_id": "a9check",
            "association": association, "terrain": {"terrain_id": "check", "bounds": {
                "x_min": 0, "z_min": 0, "width": 8192, "height": 8192}, "bounds_source": "manual",
                "bounds_confirmed": True}, "global_abundance": 0, "objects": [], "background": None,
            "records": {"originals": [], "baseline": None, "applied": None}}
    loaded = {"profile": {"profile_id": "check", "display_name": "A9 check"}, "mission_name": "a9.check",
              "association": association, "state": "VALID", "revision": 0, "plan": plan, "read_only": False,
              "read_only_reason": None, "detail": None, "problems": [], "path_change": None,
              "capabilities": {"terrain_id": "check", "header": None, "holdable_tiers": [], "unsupported": []},
              "draft_changes": False, "evaluation": None}
    bridge = BridgeFacade({"load_mission_map": lambda _parameters: loaded, **display.handlers()})
    api = MissionMapEditorApi(bridge, "check", images)
    api._bind_window(DialogWindow(image))
    return api


def run_checks(window: object, results: dict, out: Path) -> None:
    """Run the timed loads after the page loaded; each run chooses the image again."""
    try:
        deadline = time.perf_counter() + TIMEOUT_SECONDS
        while window.evaluate_js("document.body.dataset.mapReady") != "true":
            if time.perf_counter() > deadline:
                raise RuntimeError("the editor document did not load")
            time.sleep(0.1)
        for index in range(RUNS):
            window.evaluate_js("window.ServerManMapEditor.load()")
            time.sleep(1.0)
            window.evaluate_js(START_SCRIPT)
            deadline = time.perf_counter() + TIMEOUT_SECONDS
            run = {}
            while time.perf_counter() < deadline:
                run = json.loads(window.evaluate_js("JSON.stringify(window.__a9)") or "{}")
                if run.get("drawn") or run.get("error"):
                    break
                time.sleep(0.05)
            run.update(index=index, verdict=judge(run))
            results["runs"].append(run)
            write_bytes_atomically(out / "results.json", json.dumps(results, indent=2).encode("utf-8"))
    except Exception as error:  # every failure of the run is a recorded outcome
        results["error"] = f"{type(error).__name__}: {error}"
    finally:
        results["passed"] = bool(results["runs"]) and all(run["verdict"]["passed"] for run in results["runs"])
        write_bytes_atomically(out / "results.json", json.dumps(results, indent=2).encode("utf-8"))
        window.destroy()


def main(argv: list[str] | None = None) -> int:
    """Generate the image, open one window and run the check; return 0 only when every run passes."""
    parser = argparse.ArgumentParser(description="Check the 3.16 A9 background load in a real pywebview window.")
    parser.add_argument("--target", choices=sorted(TARGETS), default="32mib")
    parser.add_argument("--out", type=Path, help="output folder (default: a new temporary folder)")
    args = parser.parse_args(argv)
    out = (args.out or Path(tempfile.mkdtemp(prefix="background-load-"))).resolve()
    mission = out / "mpmissions" / "a9.check"
    mission.mkdir(parents=True, exist_ok=True)
    image = out / f"a9-{args.target}.png"
    write_bytes_atomically(image, noise_png(IMAGE_SIDE_LIMIT, TARGETS[args.target]))
    results = {"target": args.target, "byte_limit": TARGETS[args.target], "image_bytes": image.stat().st_size,
               "pixels": [IMAGE_SIDE_LIMIT, IMAGE_SIDE_LIMIT], "runs": []}
    import webview

    # Use the window settings of the application host
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    api = build_api(image, mission.resolve(), max(TARGETS[args.target], TARGETS["32mib"]))
    window = webview.create_window("A9 background load check", html=compose_editor_html(source_frontend_root()),
                                   js_api=api, width=1280, height=800, text_select=True, zoomable=False)
    webview.start(run_checks, (window, results, out), gui="edgechromium", debug=False, http_server=False,
                  private_mode=True)
    print(json.dumps({key: results[key] for key in results if key != "runs"}, indent=2))
    for run in results["runs"]:
        print(f"run {run['index']}: total {run.get('total_s')} s, join {run.get('join_s')} s, "
              f"slowest call {run['verdict']['slowest_call_s']} s, passed {run['verdict']['passed']}")
    print(f"Results: {out / 'results.json'}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
