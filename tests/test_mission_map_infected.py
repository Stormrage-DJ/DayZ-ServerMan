"""Verify the editor territory candidate of env/zombie_territories.xml: preservation, reapply, removal and conflicts."""

from __future__ import annotations

import hashlib
import unittest
import xml.etree.ElementTree as ET

from dayz_serverman.repositories.mission_map_infected import (
    ENCOUNTER_INVALID, EVENT_NOT_OFFERED, EVENTS_FILE_INVALID, MARKER_BEGIN, MARKER_CONFLICT, MARKER_END,
    TERRITORY_FILE_INVALID, TERRITORY_FILE_MISSING, TerritoryRefused, build_territory_candidate, check_reserved,
    infected_caps, owned_digest, owned_projection,
)

# Synthetic territory file in the installed layout: CRLF, 4 and 8 space indentation, two territories
BASELINE = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n<territory-type>\r\n'
    '    <territory color="2193199729">\r\n'
    '        <zone name="InfectedVillage" smin="0" smax="0" dmin="3" dmax="5" x="100.5" z="200" r="50"/>\r\n'
    '    </territory>\r\n    <territory color="1102461653">\r\n'
    '        <zone name="InfectedArmy" smin="1" smax="2" dmin="4" dmax="6" x="300" z="400.25" r="60"/>\r\n'
    '    </territory>\r\n</territory-type>\r\n'
).encode()
EVENTS = (b'<?xml version="1.0"?>\n<events>\n'
          b'<event name="InfectedVillage"><nominal>50</nominal><min>25</min><max>100</max></event>\n'
          b'<event name="InfectedArmy"><nominal>20</nominal><max>40</max></event>\n'
          b'<event name="Unzoned"><nominal>1</nominal><max>2</max></event>\n</events>\n')
GLOBALS = b'<variables>\n<var name="ZombieMaxCount" type="0" value="1000"/>\n</variables>\n'


def encounter(identifier: str, event: str = "InfectedVillage", *, x: float = 1000.0, z: float = 2000.5,
              radius: float = 75.0, excluded: bool = False, provider: str = "native_infected",
              counts: tuple[int, int, int, int] = (0, 1, 4, 8)) -> dict:
    """Return one D1 encounter object as the 7.3 placement will store it."""
    properties = (dict(zip(("smin", "smax", "dmin", "dmax"), counts), event_name=event)
                  if provider == "native_infected" else {})
    return {"id": identifier, "name": f"Encounter {identifier}", "kind": "encounter", "visible": True,
            "locked": False, "centre": {"x": x, "z": z}, "radius": radius if provider != "ai_bandits" else None,
            "provider": provider, "properties": properties, "excluded": excluded,
            "linked_loot": {"enabled": False, "tier": None, "abundance": "rich", "radius": None}}


def build(baseline: bytes | None = BASELINE, objects: list | None = None, **files: bytes | None):
    """Build a candidate with the synthetic events and globals unless a test replaces them."""
    return build_territory_candidate(baseline, objects or [], events=files.get("events", EVENTS),
                                     globals_xml=files.get("globals_xml", GLOBALS))


def without_block(content: bytes) -> bytes:
    """Remove the marked block lines from a candidate, which must give the baseline bytes back."""
    start = content.rfind(b"\n", 0, content.find(MARKER_BEGIN)) + 1
    end = content.find(b"\n", content.find(MARKER_END)) + 1
    return content[:start] + content[end:]


class InfectedTerritoryCandidateTests(unittest.TestCase):
    """Cover the 7.5 contract: one marked editor territory rebuilt from the working baseline."""

    def plan(self) -> list[dict]:
        """Return included, excluded and unrelated objects in plan order."""
        circle = {"id": "loot", "name": "Loot circle 1", "kind": "loot_circle", "visible": True, "locked": False,
                  "centre": {"x": 1.0, "z": 1.0}, "radius": 5.0, "tier": None, "abundance": "rich"}
        return [encounter("a"), circle, encounter("skip", excluded=True), encounter("bandit", provider="ai_bandits"),
                encounter("b", "InfectedArmy", x=0.00001, z=12913.3, radius=80.0, counts=(2, 2, 0, 3))]

    def test_original_bytes_stay_equal_and_one_marked_territory_is_added(self) -> None:
        """Preservation: removing the block gives the baseline; the block has the exact native zone form."""
        candidate = build(objects=self.plan())
        self.assertEqual(without_block(candidate.content), BASELINE)
        expected = (b'    <!-- dayz-serverman mission-map begin -->\r\n    <territory color="4294901760">\r\n'
                    b'        <zone name="InfectedVillage" smin="0" smax="1" dmin="4" dmax="8" x="1000" z="2000.5" r="75"/>\r\n'
                    b'        <zone name="InfectedArmy" smin="2" smax="2" dmin="0" dmax="3" x="0.00001" z="12913.3" r="80"/>\r\n'
                    b'    </territory>\r\n    <!-- dayz-serverman mission-map end -->\r\n</territory-type>\r\n')
        self.assertTrue(candidate.content.endswith(expected))
        self.assertEqual(candidate.encounter_ids, ("a", "b"))
        # The candidate parses, and each original zone keeps its attributes
        root = ET.fromstring(candidate.content)
        self.assertEqual(len(root.findall("territory")), 3)
        self.assertEqual(root.find("territory/zone").attrib["x"], "100.5")

    def test_owned_projection_is_the_marked_block(self) -> None:
        """Projection: exactly the bytes from the begin marker to the end marker; empty without markers."""
        candidate = build(objects=self.plan())
        start, end = candidate.content.find(MARKER_BEGIN), candidate.content.find(MARKER_END) + len(MARKER_END)
        self.assertEqual(candidate.owned, candidate.content[start:end])
        self.assertEqual(owned_projection(candidate.content), candidate.owned)
        self.assertEqual(owned_projection(BASELINE), b"")
        self.assertEqual(owned_digest(BASELINE), hashlib.sha256(b"").hexdigest())
        # An admin edit outside the block keeps the owned digest; one inside changes it
        outside = candidate.content.replace(b'x="100.5"', b'x="101"')
        inside = candidate.content.replace(b'r="75"', b'r="76"')
        self.assertEqual(owned_digest(outside), owned_digest(candidate.content))
        self.assertNotEqual(owned_digest(inside), owned_digest(candidate.content))

    def test_reapply_from_the_same_baseline_is_byte_identical(self) -> None:
        """Idempotent reapply: the element is rebuilt from the baseline, never from the applied file."""
        first, second = build(objects=self.plan()), build(objects=self.plan())
        self.assertEqual(first, second)
        # A changed encounter changes only the block
        moved = self.plan()
        moved[0]["radius"] = 90.0
        third = build(objects=moved)
        self.assertEqual(without_block(third.content), BASELINE)
        self.assertNotEqual(third.owned, first.owned)

    def test_removal_returns_the_baseline_bytes(self) -> None:
        """Removal: no included encounter gives the baseline unchanged and an empty owned block."""
        for objects in ([], [encounter("a", excluded=True)], [encounter("bandit", provider="ai_bandits")]):
            candidate = build(objects=objects)
            self.assertEqual((candidate.content, candidate.owned, candidate.encounter_ids), (BASELINE, b"", ()))
        # An absent file stays absent while nothing needs it; its null projection differs from an empty block
        absent = build(None)
        self.assertEqual((absent.content, absent.owned, absent.encounter_ids), (None, None, ()))
        self.assertIsNone(owned_projection(None))
        self.assertIsNone(owned_digest(None))
        self.assertNotEqual(owned_digest(None), owned_digest(BASELINE))
        self.assertRefused(TERRITORY_FILE_MISSING, None, [encounter("a")])

    def test_marker_conflicts_refuse_the_file(self) -> None:
        """Conflict: marker text in the baseline, or damaged markers in a current file, is never rewritten."""
        applied = build(objects=self.plan()).content
        variants = (applied, BASELINE.replace(b"</territory-type>", b"<!-- DAYZ-SERVERMAN  Mission-Map end -->\r\n"
                                              b"</territory-type>"))
        for baseline in variants:
            self.assertRefused(MARKER_CONFLICT, baseline, [])
            with self.assertRaises(TerritoryRefused):
                check_reserved(baseline)
        check_reserved(BASELINE)
        # Damaged current files: end marker missing, markers reversed, a second block
        reversed_markers = applied.replace(MARKER_BEGIN, b"@").replace(MARKER_END, MARKER_BEGIN).replace(b"@", MARKER_END)
        damaged = (applied.replace(MARKER_END, b""), reversed_markers, applied + MARKER_BEGIN + MARKER_END)
        for data in damaged:
            with self.assertRaises(TerritoryRefused) as caught:
                owned_projection(data)
            self.assertEqual(caught.exception.reason, MARKER_CONFLICT)

    def test_layout_and_encoding_variants(self) -> None:
        """LF files get LF lines, a BOM stays, and an end tag after content starts the block on a new line."""
        lf = BASELINE.replace(b"\r\n", b"\n")
        candidate = build(lf, [encounter("a")])
        self.assertNotIn(b"\r", candidate.content)
        self.assertEqual(without_block(candidate.content), lf)
        bom = build(b"\xef\xbb\xbf" + lf, [encounter("a")])
        self.assertTrue(bom.content.startswith(b"\xef\xbb\xbf<?xml"))
        inline = lf.replace(b"    </territory>\n</territory-type>", b"    </territory></territory-type>")
        content = build(inline, [encounter("a")]).content
        self.assertIn(b"    </territory>\n    " + MARKER_BEGIN, content)
        self.assertTrue(content.endswith(MARKER_END + b"\n</territory-type>\n"))
        # An end tag inside an earlier comment is not the root end tag
        commented = BASELINE.replace(b"<territory-type>\r\n", b"<territory-type>\r\n<!-- </territory-type> -->\r\n")
        content = build(commented, [encounter("a")]).content
        self.assertEqual(without_block(content), commented)
        self.assertTrue(content.endswith(MARKER_END + b"\r\n</territory-type>\r\n"))

    def test_unsupported_input_refuses_with_a_named_reason(self) -> None:
        """Invalid layouts, events and encounters refuse the whole file."""
        self.assertRefused(TERRITORY_FILE_INVALID, BASELINE + b"<!-- trailing -->\r\n", [encounter("a")])
        self.assertRefused(TERRITORY_FILE_INVALID, b"<events/>", [])
        self.assertRefused(TERRITORY_FILE_INVALID, b"<territory-type>", [])
        self.assertRefused(EVENTS_FILE_INVALID, BASELINE, [encounter("a")], events=b"<events>")
        refused = self.assertRefused(EVENT_NOT_OFFERED, BASELINE, [encounter("a"), encounter("u", "Unzoned")])
        self.assertEqual(refused.object_id, "u")
        refused = self.assertRefused(ENCOUNTER_INVALID, BASELINE, [encounter("bad", counts=(3, 1, 0, 0))])
        self.assertEqual(refused.object_id, "bad")

    def test_preview_caps_come_from_events_and_globals(self) -> None:
        """Preview: event nominal and max of each used event and ZombieMaxCount; unknown values are None."""
        caps = build(objects=self.plan()).caps
        self.assertEqual(caps, {"events": {"InfectedArmy": {"nominal": 20, "max": 40},
                                           "InfectedVillage": {"nominal": 50, "max": 100}},
                                "zombie_max_count": 1000})
        duplicate = EVENTS.replace(b"</events>", b'<event name="InfectedArmy"><nominal>9</nominal></event></events>')
        caps = infected_caps(duplicate, None, ["InfectedArmy", "Missing", "InfectedVillage"])
        self.assertEqual(caps["events"]["InfectedArmy"], {"nominal": None, "max": None})
        self.assertEqual(caps["events"]["Missing"], {"nominal": None, "max": None})
        self.assertIsNone(caps["zombie_max_count"])
        self.assertIsNone(infected_caps(None, b'<variables><var name="ZombieMaxCount" value="-1"/></variables>',
                                        [])["zombie_max_count"])
        # A duplicated ZombieMaxCount, nominal or max is ambiguous, so it gives no cap
        twice = GLOBALS.replace(b"</variables>", b'<var name="ZombieMaxCount" type="0" value="5"/>\n</variables>')
        self.assertIsNone(infected_caps(None, twice, [])["zombie_max_count"])
        for child, other in (("nominal", "max"), ("max", "nominal")):
            value = {"nominal": "20", "max": "40"}[child]
            doubled = EVENTS.replace(f"<{child}>{value}</{child}>".encode(),
                                     f"<{child}>{value}</{child}><{child}>7</{child}>".encode())
            values = infected_caps(doubled, None, ["InfectedArmy"])["events"]["InfectedArmy"]
            self.assertIsNone(values[child])
            self.assertIsNotNone(values[other])

    def assertRefused(self, reason: str, baseline: bytes | None, objects: list, **files: bytes) -> TerritoryRefused:
        """Assert one named refusal and return it for further checks."""
        with self.assertRaises(TerritoryRefused) as caught:
            build(baseline, objects, **files)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception


if __name__ == "__main__":
    unittest.main()
