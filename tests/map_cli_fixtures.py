"""Fixture of the `map show` and `map inspect` tests: the edit root with a saved plan and helpers to replace it."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import PROFILE, PROFILE_ID, EditRoot  # noqa: E402
from test_mission_map_plan import stored_plan  # noqa: E402

from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain import mission_map_families  # noqa: E402

SHOW = ["map", "show", *PROFILE]
INSPECT = ["map", "inspect", *PROFILE]


class MapFixture(EditRoot):
    """The edit root with a saved plan of one loot circle; helpers save richer plans and read the plan file."""

    def save_objects(self, objects: list[dict]) -> None:
        """Replace the saved plan of the fixture profile by a plan with these objects (revision 1)."""
        composition = build_composition(self.manager)
        try:
            plan = stored_plan(objects)
            plan["association"] = composition.mission_map.plans.active_association(PROFILE_ID)
            plan["background"] = None
            composition.mission_map.plans.save(PROFILE_ID, plan, 0)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def plan_file(self) -> Path:
        """Return the saved plan file of the fixture profile."""
        return next((self.manager / "data").rglob("plan.json"))


# enoch, the family of the fixture mission, passed 6.12 (evidence 6.12). The tests of the 6.11 refusal take it out of
# the verified list while they run, so the fixture mission stays an unverified family
WITHOUT_FIXTURE_FAMILY = tuple(entry for entry in mission_map_families.VERIFIED_FAMILIES if entry.family != "enoch")


def keep_fixture_family_unverified(test) -> None:
    """Take the fixture family out of the verified list until the test ends."""
    patcher = patch.object(mission_map_families, "VERIFIED_FAMILIES", WITHOUT_FIXTURE_FAMILY)
    patcher.start()
    test.addCleanup(patcher.stop)
