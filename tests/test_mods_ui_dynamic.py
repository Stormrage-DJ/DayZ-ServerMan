"""Dynamic headless Edge test of the mods workspace progress, cancel, and publication gate."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.host.assets import compose_shell_html


ROOT = Path(__file__).resolve().parents[1]
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


# Browser harness that drives the mods workspace and paints pass or fail
HARNESS = r"""
<script>
(async () => {
  let cancelled = 0;
  let published = 0;
  let previewed = 0;
  const ok = (value) => ({success: true, value});
  window.pywebview = {api: {
    get_application_snapshot: async () => ok({settings: {revision: 2,
      steam_authentication_mode: "ACCOUNT", steam_account_name: "operator"},
      operations: [], operation_session_id: "qa"}),
    list_profiles: async () => ok([{profile_id: "primary", display_name: "Primary",
      revision: 3, semantic_digest: "a".repeat(64)}]),
    get_ui_preferences: async () => ok({selected_profile_id: "primary"}),
    save_selected_profile: async () => ok({selected_profile_id: "primary"}),
    list_mod_inventory: async () => ok([{order: 1, name: "Example Mod",
      directory: "mods\\alpha", launch_scope: "client", source_kind: "workshop",
      workshop_id: "111", version: "1.2.3", state: "CURRENT", time_updated: 1}]),
    update_workshop_items: async () => ok({operation_id: "update-one", state: "QUEUED"}),
    preview_mod_publication: async () => { previewed += 1; return ok({profile_id: "primary",
      publication_fingerprint: "b".repeat(64), key_count: 2,
      targets: [{workshop_id: "111", target_relative: "mods\\alpha"}]}); },
    publish_mods_and_keys: async () => { published += 1;
      return ok({operation_id: "publish-one", state: "QUEUED"}); },
    authenticate_steamcmd: async () => ok({operation_id: "auth-one", state: "QUEUED"}),
    save_steam_settings: async () => ok({operation_id: "save-one", state: "QUEUED"}),
    request_operation_cancellation: async (id) => { if (id === "update-one") cancelled += 1;
      return ok({operation_id: id, state: "CANCELLING"}); },
  }};
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  try {
    shellState.hostReady = true;
    commitSection("mods");
    await new Promise((resolve) => setTimeout(resolve, 100));
    document.getElementById("update-workshop").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    window.ServerManMods.operationFinished({operation_id: "update-one", state: "RUNNING",
      cancellable: true, progress_phase: "download", progress_percent: 55});
    assert(document.getElementById("mods-feedback").textContent.includes("download: 55%"), "progress");
    assert(!document.getElementById("cancel-workshop-operation").hidden, "cancel visible");
    document.getElementById("cancel-workshop-operation").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(cancelled === 1, "cancel correlation");
    window.ServerManMods.operationFinished({operation_id: "update-one", kind: "UPDATE_WORKSHOP_ITEMS",
      state: "SUCCEEDED",
      result: {profile_id: "primary", download_state: "VERIFIED",
        profile_revision: 3, semantic_profile_digest: "a".repeat(64), settings_revision: 2,
        start_requested: true, start_error: "PUBLICATION_REQUIRED",
        items: [{item: {workshop_id: "111"},
          outcome: "UPDATED_VERIFIED", error_code: null}]}});
    await new Promise((resolve) => setTimeout(resolve, 0));
    const dialog = document.getElementById("mod-publication-confirmation");
    assert(dialog?.getAttribute("aria-modal") === "true", "review modal");
    assert(dialog.textContent.includes("mods\\alpha"), "safe target preview");
    assert(dialog.textContent.includes("will start this server"), "start intent review");
    const reviewButtons = [...dialog.querySelectorAll("button")];
    reviewButtons[0].focus();
    reviewButtons[0].dispatchEvent(new KeyboardEvent("keydown",
      {key: "Tab", shiftKey: true, bubbles: true, cancelable: true}));
    assert(document.activeElement === reviewButtons.at(-1), "review focus containment");
    [...dialog.querySelectorAll("button")].at(-1).click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(published === 1, "explicit publish confirmation");
    window.ServerManMods.operationFinished({operation_id: "publish-one",
      kind: "PUBLISH_MODS_AND_KEYS", state: "RUNNING", cancellable: true,
      progress_phase: "CHECK_TARGET", progress_percent: 62});
    assert(document.getElementById("mods-feedback").textContent.includes("CHECK_TARGET: 62%"),
      "publication progress");
    window.ServerManMods.operationFinished({operation_id: "publish-one",
      kind: "PUBLISH_MODS_AND_KEYS", state: "SUCCEEDED",
      result: {profile_id: "primary", publication_state: "VERIFIED",
        key_state: "VERIFIED", start_state: "STARTED", start_error: null}});
    assert(document.getElementById("mods-feedback").textContent.includes(
      "Server start was authorized and completed"), "composite success");
    const previewsBeforeFailure = previewed;
    modsState.pending = Object.freeze({id: "update-unknown", generation: modsState.generation,
      profileId: "primary"});
    window.ServerManMods.operationFinished({operation_id: "update-unknown",
      kind: "UPDATE_WORKSHOP_ITEMS", state: "SUCCEEDED",
      result: {profile_id: "primary", download_state: "UNKNOWN",
        steamcmd_exit_code: 7,
        items: [{item: {workshop_id: "111"}, outcome: "UNKNOWN_FAILED",
          error_code: "UPDATE_RESULT_UNKNOWN"}]}});
    assert(previewed === previewsBeforeFailure, "failed update must not enter publication");
    assert(document.getElementById("mods-feedback").textContent.includes(
      "exit code 7"), "failed update guidance");
    modsState.pending = Object.freeze({id: "publish-cancel", generation: modsState.generation,
      profileId: "primary"});
    window.ServerManMods.operationFinished({operation_id: "publish-cancel",
      kind: "PUBLISH_MODS_AND_KEYS", state: "SUCCEEDED",
      result: {profile_id: "primary", publication_state: "VERIFIED",
        key_state: "VERIFIED", start_state: "CANCELLED", start_error: "UPDATE_CANCELLED"}});
    assert(document.getElementById("mods-feedback").textContent.includes(
      "start was cancelled before launch"), "post-publication cancellation");
    let releasePreview;
    window.pywebview.api.preview_mod_publication = () => new Promise(
      (resolve) => { releasePreview = resolve; });
    const staleReview = window.ServerManModPublication.reviewFromUpdate({operation_id: "stale",
      result: {profile_id: "primary", profile_revision: 3,
        semantic_profile_digest: "a".repeat(64), settings_revision: 2,
        start_requested: false}});
    modsState.generation += 1;
    releasePreview(ok({profile_id: "primary", publication_fingerprint: "c".repeat(64),
      key_count: 0, targets: []}));
    await staleReview;
    assert(!document.getElementById("mod-publication-confirmation"), "stale preview suppressed");
    document.getElementById("authenticate-steamcmd").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    window.ServerManMods.operationFinished({operation_id: "auth-one", kind: "AUTHENTICATE_STEAMCMD",
      state: "SUCCEEDED",
      result: {authenticated: true}});
    assert(document.getElementById("mods-feedback").textContent.includes(
      "Credentials remain owned by SteamCMD"), "auth terminal");
    document.body.replaceChildren(); document.body.style.background = "rgb(0, 255, 0)";
  } catch (error) {
    document.body.replaceChildren(); document.body.style.background = "rgb(255, 0, 0)";
    document.body.textContent = error.message;
  }
})();
</script>
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsUiDynamicTests(unittest.TestCase):
    """Contract: the mods workspace drives progress, cancellation, and explicit review."""
    def test_progress_cancel_auth_items_and_publication_gate(self) -> None:
        """Drive update progress, cancel, auth, items, and the publication gate."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Compose the shell page with the browser harness appended
            page = root / "mods.html"
            html = compose_shell_html(ROOT / "runnable" / "src" / "frontend").replace(
                "</body>", HARNESS + "</body>"
            )
            page.write_text(html, encoding="utf-8")
            screenshot = root / "result.png"
            profile = root / "edge-data"
            # Execute the harness in headless Edge
            completed = subprocess.run([
                str(EDGE), "--headless", "--disable-gpu", "--no-first-run",
                "--hide-scrollbars", "--window-size=800,560", "--virtual-time-budget=2000",
                f"--user-data-dir={profile}", f"--screenshot={screenshot}", page.as_uri(),
            ], capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            # The harness paints the body green only when every check passed
            png = _read_stable_screenshot(screenshot, profile)
            red, green, blue = _center_rgb(png)
            self.assertGreater(green, 240)
            self.assertLess(red, 15)
            self.assertLess(blue, 15)


def _read_stable_screenshot(screenshot: Path, profile: Path) -> bytes:
    """Wait for a stable screenshot and return its bytes once Edge releases the profile."""
    deadline = time.monotonic() + 10
    previous: bytes | None = None
    while time.monotonic() < deadline:
        try:
            current = screenshot.read_bytes()
            probe = profile.with_name(profile.name + "-released")
            profile.rename(probe)
            probe.rename(profile)
            if current and current == previous:
                return current
            previous = current
        except (FileNotFoundError, PermissionError, OSError):
            pass
        time.sleep(0.05)
    raise AssertionError("Edge did not release a stable screenshot and profile")


def _center_rgb(png: bytes) -> tuple[int, int, int]:
    """Decode an 8-bit PNG and return the center pixel as an RGB triplet."""
    position, width, height, data = 8, 0, 0, bytearray()
    # Walk the PNG chunks and collect the compressed pixel data
    while position < len(png):
        length = struct.unpack(">I", png[position:position + 4])[0]
        kind = png[position + 4:position + 8]
        payload = png[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or color not in (2, 6):
                raise AssertionError("Edge screenshot has an unsupported PNG format")
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            data.extend(payload)
    raw = zlib.decompress(bytes(data))
    stride = width * channels
    rows: list[bytearray] = []
    offset = 0
    # Reconstruct each scanline using the declared PNG filter mode
    for _ in range(height):
        mode, scan = raw[offset], bytearray(raw[offset + 1:offset + 1 + stride])
        offset += stride + 1
        prior = rows[-1] if rows else bytearray(stride)
        for index in range(stride):
            left = scan[index - channels] if index >= channels else 0
            up = prior[index]
            upper_left = prior[index - channels] if index >= channels else 0
            if mode == 1:
                scan[index] = (scan[index] + left) & 255
            elif mode == 2:
                scan[index] = (scan[index] + up) & 255
            elif mode == 3:
                scan[index] = (scan[index] + ((left + up) // 2)) & 255
            elif mode == 4:
                scan[index] = (scan[index] + _paeth(left, up, upper_left)) & 255
            elif mode != 0:
                raise AssertionError("Edge screenshot uses an invalid PNG filter")
        rows.append(scan)
    # Read the RGB triplet at the center of the image
    start = (width // 2) * channels
    return tuple(rows[height // 2][start:start + 3])  # type: ignore[return-value]


def _paeth(left: int, up: int, upper_left: int) -> int:
    """Select the Paeth predictor value for PNG row filtering."""
    estimate = left + up - upper_left
    choices = ((abs(estimate - left), left), (abs(estimate - up), up),
               (abs(estimate - upper_left), upper_left))
    return min(choices)[1]


if __name__ == "__main__":
    unittest.main()
