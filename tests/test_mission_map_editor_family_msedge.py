"""Verify the visible unverified-family reason and that editing and saving stay available in composed Edge (6.11)."""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.mission_map_families import family_gate
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.test_mission_map_families import UNVERIFIED_TEXT
from tests.ui_harness_support import EDGE, run_editor_harness


# The backend family fields of an unverified, a verified and an unknown family; the window shows only their text
FAMILY = TOOLS + r'''
const unverified=''' + json.dumps(family_gate("deerisle").to_dict()) + r''';
const verified=''' + json.dumps(family_gate("Pripyat").to_dict()) + r''';
const unknown=''' + json.dumps(family_gate(None).to_dict()) + r''';
const poText=''' + json.dumps(UNVERIFIED_TEXT) + r''';
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms)),reason=document.getElementById("map-family-reason");
const withFamily=(family,document=plan())=>{const current=value(document);current.capabilities.family=family;return current};
const shown=()=>!reason.hidden&&getComputedStyle(reason).display!=="none"&&reason.getBoundingClientRect().height>0;
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorFamilyEdgeTests(unittest.TestCase):
    """Use synthetic plans and family fields from the backend gate, fake save answers and isolated headless profiles."""

    def test_unverified_family_shows_the_reason_and_keeps_editing_and_saving(self) -> None:
        """deerisle shows the backend text below the identity; a circle still autosaves; verified hides the text."""
        self.assertEqual(run_editor_harness(FAMILY + r'''
check(reason.hidden&&reason.textContent==="","no family field: no reason and no claim");
check(unverified.verified===false&&unverified.text===poText,"backend text is the Product Owner text");
await load(withFamily(unverified));
check(shown()&&reason.textContent===unverified.text,"the reason is visible with the exact backend text");
check(document.getElementById("map-identity").contains(reason)&&reason.parentElement.id==="map-identity","the reason is part of the identity area");
const header=document.getElementById("map-identity").getBoundingClientRect(),line=reason.getBoundingClientRect();
check(line.left>=header.left&&line.right<=header.right+1&&line.bottom<=header.bottom+1,"the reason stays inside the identity area");
check(line.top>=document.getElementById("map-identity-fields").getBoundingClientRect().bottom-1,"the reason has its own line below the fields");
const warning=document.getElementById("map-encounter-provider-reason"),before=warning.textContent;warning.textContent="x";
check(getComputedStyle(reason).color===getComputedStyle(warning).color&&getComputedStyle(reason).color!==getComputedStyle(document.body).color,"the warning colour of other reasons");
warning.textContent=before;
check(!text("map-status").startsWith("Read only")&&!document.getElementById("map-tool-circle").disabled,"not read only; tools usable");
tool("circle");click(500,400);check(model.snapshot().objects.length===1,"planning works");
await sleep(900);check(saved.length===1&&saved[0].plan.objects.length===1,"autosave sends the plan");
await load(withFamily(verified));
check(reason.hidden&&reason.textContent==="","a verified family shows no reason");
await load(withFamily(unknown));
check(shown()&&reason.textContent===unknown.text,"an unknown family shows its own text");
await load(withFamily({...unverified,text:"Short."}));
const identity=document.getElementById("map-identity-fields").getBoundingClientRect(),compact=reason.getBoundingClientRect();
check(shown()&&compact.top>=identity.bottom-1&&compact.left<=document.getElementById("map-title").getBoundingClientRect().left+1,"a short text also takes its own line");
await load(withFamily({...unverified,text:null}));
check(reason.hidden,"no text: nothing invented");
await load(withFamily({...verified,text:"stray"}));
check(reason.hidden,"a verified family hides any stray text");
''', budget=8000), "PASS")

    def test_the_reason_fits_at_the_three_window_sizes(self) -> None:
        """The full-width reason line keeps the page without overflow and the map large."""
        for size in ("1280,800", "960,600", "1920,1080"):
            with self.subTest(size=size):
                self.assertEqual(run_editor_harness(FAMILY + r'''
await load(withFamily(verified));const plain=document.getElementById("map-viewport").getBoundingClientRect();
for(const family of [unverified,unknown]){
  await load(withFamily(family));
  check(shown()&&reason.textContent===family.text,`visible: ${family.reason}`);
  check(document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight,`no page overflow: ${family.reason}`);
  const map=document.getElementById("map-viewport").getBoundingClientRect(),line=reason.getBoundingClientRect().height;
  // The menu band, the labelled toolbar and the default side panels (wide windows) leave less than the old 60 %/50 %;
  // the reason itself must not narrow the map and may take only its own line(s) of height.
  check(map.width===plain.width&&plain.height-map.height<=line+12,`the reason costs only its line: ${family.reason}`);
  check(map.width>=innerWidth*.45&&map.height>=innerHeight*.45,`large central map: ${family.reason}`);
  const area=reason.getBoundingClientRect(),header=document.getElementById("map-identity").getBoundingClientRect();
  check(area.right<=innerWidth+1&&area.bottom<=innerHeight+1&&area.bottom<=header.bottom+1,`reason inside the window and the header: ${family.reason}`);
  check(area.top>=document.getElementById("map-identity-fields").getBoundingClientRect().bottom-1,`own line: ${family.reason}`);
}
''', window_size=size), "PASS")


if __name__ == "__main__":
    unittest.main()
