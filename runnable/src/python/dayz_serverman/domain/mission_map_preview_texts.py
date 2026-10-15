"""Backend texts and the notice rule of the apply preview (leaf 8.6).

The window and `map preview` print these texts unchanged. A notice never
refuses Apply.
"""

from __future__ import annotations

from typing import Any

# Product Owner text of 2026-10-14 14:29:00 in evidence 7.6, plain text, exact
ZOMBIE_MAX_COUNT_ZERO_TEXT = (
    "ZombieMaxCount is 0 in db/globals.xml of this mission. With this value DayZ spawns no infected, also not in "
    "the infected encounters of this plan. DayZ-ServerMan does not change this value.")
# Product Owner text of 2026-10-14 18:45:00 in evidence 7.6, plain text, exact; the 17:23:24 text is withdrawn
ZONE_SPAWN_DIST_ZERO_TEXT = (
    "ZoneSpawnDist is 0 in db/globals.xml of this mission. In testing with this value, DayZ spawned infected in "
    "the mission's own infected zones where the player stood. It spawned no infected in infected zones whose edge "
    "was 100 m or more from the player. This included infected zones made by the editor. DayZ-ServerMan does not "
    "change this value.")
# Notice codes and texts in their fixed order (Architect 2026-10-14 18:32:23, decision 3)
NOTICES = (("zombie_max_count_zero", "zombie_max_count", ZOMBIE_MAX_COUNT_ZERO_TEXT),
           ("zone_spawn_dist_zero", "zone_spawn_dist", ZONE_SPAWN_DIST_ZERO_TEXT))

# Product Owner texts of 2026-10-15 01:16:10 in evidence 8.7: a stale review (also a late file change) and no plan
PREVIEW_STALE_TEXT = ("The map plan, the profile, the settings or the mission files changed after the preview. "
                      "Nothing was applied. Preview the changes again and check them before you apply.")
NO_SAVED_PLAN_TEXT = "This mission has no saved map plan. Make a plan in the map editor, then preview it."

# Residual risk of the capture check (detailed-design D2, T2.1-M3): the preview states what the check can see
CAPTURE_RISK_TEXT = ("The original capture check uses only the editor's own records and recognizes only unchanged "
                     "editor output.")


def infected_section(caps: dict[str, Any], written: bool) -> dict[str, Any]:
    """Return the `infected` object of the preview answer from the 7.5 caps.

    A notice is present only when the infected candidate writes at least one
    included encounter and its value is 0. A null value ("not read") gives no
    notice.
    """
    notices = [{"code": code, "text": text} for code, field, text in NOTICES
               if written and caps.get(field) == 0]
    return {"events": caps["events"], "zombie_max_count": caps["zombie_max_count"],
            "zone_spawn_dist": caps["zone_spawn_dist"], "notices": notices}
