"""Task 2.5 in headless Edge: the 6.5 markers do not change the operation bar, and the Python phase lookup
gives the same answers as the frontend lookup (design 11.2)."""

from __future__ import annotations

import json
import unittest

try:
    from tests.test_operator_wording import backend_kinds, backend_phases
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from test_operator_wording import backend_kinds, backend_phases
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application import phase_wording

# Each marker: kind, phase, percent before the marker, marker percent, last phase before the marker (6.5)
MARKERS = (
    ("START_SERVER", "preflight", 10, 11, "preflight"),
    ("STOP_SERVER", "STOP_SERVER", 20, 21, "STOP_SERVER"),
    ("RESTART_SERVER", "preflight", 10, 12, "preflight"),
    ("RESTART_SERVER", "STOP_SERVER", 15, 16, "STOP_SERVER"),
    ("APPLY_MODS_AND_RESTART", "STOP_SERVER", 8, 9, "STOP_SERVER"),
    ("DELETE_PROFILE", "running", 0, 1, None),
)

# Push each marker record and the record without the marker; the bar must render the same
MARKER_RENDER = r"""
const markers = %s;
await start();
// Both versions of a record use one operation id, so the rest of the bar is the same for both renders
const render = async (id, extra) => { await push(record(id, extra.kind, extra.state, extra)); await wait(20);
  return bar().innerHTML; };
for (const [kind, phase, before, marker, beforePhase] of markers) {
  for (const state of ["RUNNING", "FAILED"]) {
    const end = state === "FAILED" ? {terminal_error: {code: "EXTERNAL_PROCESS", message: "This cannot be done.",
      retryable: false}, finished_at: "2026-10-03T10:01:00.000+00:00", progress_phase: "failed"} : {};
    const id = `${kind}-${phase}-${state}`;
    const without = await render(id, {kind, state, progress_phase: phase, progress_percent: before,
      last_working_phase: beforePhase, ...end});
    const withMarker = await render(id, {kind, state, progress_phase: phase, progress_percent: marker,
      last_working_phase: phase, ...end});
    check(without === withMarker, `${id}: ${without} !== ${withMarker}`);
    check(!withMarker.includes(`${marker}%%`), `${id} shows the marker percent`);
  }
}
"""

# Compare the frontend lookups with the answers of application/phase_wording.py
LOOKUPS = r"""
const expected = %s;
const labels = window.ServerManOperationLabels;
const wrong = [];
for (const [kind, phase, text, determinate, working] of expected.phases) {
  const answer = labels.phase(kind, phase);
  if (answer.text !== text || answer.determinate !== determinate) wrong.push(`${kind}/${phase}: ${answer.text}`);
  if (labels.isWorkingPhase(phase) !== working) wrong.push(`working ${phase}`);
}
for (const [state, text] of expected.states) if (labels.state(state) !== text) wrong.push(`state ${state}`);
for (const [kind, text] of expected.cancelling) if (labels.cancelling(kind) !== text) wrong.push(`cancel ${kind}`);
check(wrong.length === 0, wrong.slice(0, 10).join("; "));
"""


def expected_lookups() -> dict[str, list]:
    """Return the Python answers for every kind and every backend, lane and terminal phase."""
    kinds = sorted(backend_kinds() | {"LIFECYCLE", "UNKNOWN_KIND"})
    phases = sorted(backend_phases() | {"accepted", "queued", "running", *phase_wording.TERMINAL_PHASES,
                                        "BACKUP_DISCOVER", "BACKUP_PUBLISH", "brand_new_phase", ""})
    rows = [[kind, phase, *phase_wording.phase_text(kind, phase), phase_wording.is_working_phase(phase)]
            for kind in kinds for phase in phases]
    states = [[state, phase_wording.state_text(state)] for state in (*phase_wording.STATE_TEXTS, "NEW_STATE")]
    cancelling = [[kind, phase_wording.cancelling_text(kind)] for kind in kinds]
    return {"phases": rows, "states": states, "cancelling": cancelling}


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MarkerRenderTests(unittest.TestCase):
    """Rule 6 of the QF-8 ruling (no GUI change) and the phase lookup parity of 11.2."""

    def test_marker_records_render_like_records_without_the_marker(self) -> None:
        """Running and failed rows of each marker record equal the rows without the marker."""
        self.assertEqual(run_shell_harness(MARKER_RENDER % json.dumps(MARKERS), budget=8000), "PASS")

    def test_phase_lookup_equals_the_frontend(self) -> None:
        """`phase_text`, `is_working_phase`, `state_text` and `cancelling_text` answer as the frontend does."""
        self.assertEqual(run_shell_harness(LOOKUPS % json.dumps(expected_lookups())), "PASS")


if __name__ == "__main__":
    unittest.main()
