"""Pin the window texts of the preview dialog to the Product Owner texts in composed Edge (8.10).

Sources: Product Owner 2026-10-15 15:57:10 (decisions 3, 4, 5 and 10) and
16:03:40 (the slots and the unfinished outline) in evidence 8.10, copied
word for word. The reused 8.7 choice names and the 7.4 linked-loot line are
pinned too.
"""

from __future__ import annotations

import json
import unittest

from tests.ui_harness_support import EDGE, run_editor_harness

# Fixed texts by their place in ServerManMapPreviewWords
FIXED = {
    "titles.apply": "Apply map plan", "titles.previous": "Restore previous state", "titles.original": "Restore original",
    "confirm.apply": "Apply", "confirm.previous": "Restore previous state", "confirm.original": "Restore original",
    "cancel": "Cancel", "close": "Close", "again": "Preview again",
    "stop.apply": "Stop the DayZ server before you apply. DayZ-ServerMan does not apply while the server runs.",
    "stop.restore": "Stop the DayZ server before you restore. DayZ-ServerMan does not restore while the server runs.",
    "stale.apply": "The mission files or the map editor records can have changed after this preview. Preview again "
                   "before you apply.",
    "stale.restore": "The mission files or the map editor records can have changed after this preview. Preview again "
                     "before you restore.",
    "headings.refusals": "Apply is not possible", "headings.notices": "Check before you apply",
    "headings.external": "Files changed outside the map editor", "headings.encounterChoices": "Unavailable encounters",
    "headings.changes": "Map plan changes", "headings.encounters": "Encounters", "headings.loot": "Loot report",
    "headings.files": "Mission files", "headings.restoreRefusals": "Restore is not possible",
    "headings.restoreNotices": "Check before you restore", "headings.writes": "What the restore writes",
    "headings.overwritten": "Outside changes that the restore overwrites",
    "headings.kept": "Outside changes that the restore keeps",
    "bounds": "The terrain bounds of this plan are not confirmed. Apply cannot write loot tiers until you confirm the "
              "bounds under Terrain bounds.",
    "outline": "The outline you are drawing is not finished. It is not part of the plan, so Apply does not write it.",
    "others.apply": "Other profiles use this mission folder or have map editor records for it. Apply changes the "
                    "mission files for every profile that uses this folder.",
    "others.previous": "Other profiles use this mission folder or have map editor records for it. The restore changes "
                       "the mission files for every profile that uses this folder.",
    "externalIntro": "Choose for each file: keep the change or replace the file.",
    "replaceNote": "DayZ-ServerMan saves a copy of the current file before it replaces the file.",
    "keep": "Keep the change", "replace": "Replace the file",
    "encounterIntro": "Apply cannot write these encounters on this mission. Exclude each one from this Apply to "
                      "continue.",
    "linkedLoot": "Its linked loot is still applied.",
    "noPrevious": "No zone or encounter list of a last Apply is recorded, so every zone and encounter shows as added.",
    "added": "Added", "changed": "Changed", "removed": "Removed",
    "written": "Infected encounters that Apply writes",
    "excluded": "Excluded in the plan. Apply does not write these encounters.",
    "affected": "Files that Apply changes",
    "coordinated": "Changes made by other DayZ-ServerMan settings, for example medical loot zones. Apply keeps them.",
    "restoreAffected": "Files that the restore changes",
    "restoreCoordinated": "Changes made by other DayZ-ServerMan settings, for example medical loot zones. The restore "
                          "overwrites them.",
}
# Texts with values, with sample values; {time} is checked with the editor's date and time form
FILLED = [
    ["entries", [1], "1 entry"], ["entries", [7], "7 entries"],
    ["remaining", [1], "1 choice is still needed before you can apply."],
    ["remaining", [4], "4 choices are still needed before you can apply."],
    ["exclude", ["Village pack"], "Exclude Village pack from this Apply"],
    ["deletedProfile", ["gone"], "gone (deleted profile)"],
    ["notOverwritten", ["cfgeventspawns.xml"],
     "cfgeventspawns.xml changed outside the map editor. The restore does not change this file."],
    ["global", [25, -25], "Global container capacity: +25% (last Apply: -25%)"],
    ["global", [-50, None], "Global container capacity: -50%"], ["global", [0, 0], "Global container capacity: 0%"],
]
# {change} of the previous-state line by record kind
CHANGES = {"apply": "Apply", "restore_original": "Restore original", "restore_previous": "Restore previous state",
           "medical": "medical loot zones change"}


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PreviewWordingEdgeTests(unittest.TestCase):
    """Every window text of the dialog equals its Product Owner text."""

    def test_window_texts_equal_the_product_owner_texts(self) -> None:
        """Fixed texts, texts with values and the two record-time lines."""
        self.assertEqual(run_editor_harness(
            "const fixed=" + json.dumps(FIXED) + ",filled=" + json.dumps(FILLED) + ",changes=" + json.dumps(CHANGES)
            + r''';
const words=window.ServerManMapPreviewWords,at="2026-10-15T08:30:00Z";
const shown=new Date(at).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"});
for(const [path,text] of Object.entries(fixed)){const value=path.split(".").reduce((part,name)=>part[name],words);
  check(value===text,`${path}: ${value}`);}
for(const [name,values,text] of filled){const value=words[name](...values);check(value===text,`${name}: ${value}`);}
for(const [kind,change] of Object.entries(changes)){
  check(words.previousWrites(kind,at)===`The restore writes the mission files as they were before the last recorded change: ${change}, ${shown}.`,kind);}
check(words.originalWrites(at)===`The restore writes the original that DayZ-ServerMan saved on ${shown}.`,"original line");
check(shown!==at&&words.time("not a time")==="not a time","a readable time; a bad value stays as it is");
''', budget=3000), "PASS")


if __name__ == "__main__":
    unittest.main()
