"""Leaf 10.33: the review blocks of the import report (D7) with the Product Owner labels of evidence 10.1."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.map_transfer_render import transfer_blocks  # noqa: E402
from dayz_serverman.cli.output import Value, block_parts, render_blocks  # noqa: E402

PROFILE = {"profile_id": "alpha", "display_name": "Alpha"}


def report(**changes) -> dict:
    """Return a Merge report with no difference, then the changes."""
    value = {"mode": "merge", "terrain_match": True, "bounds_equal": True,
             "bounds_difference": {"x_min": 0.0, "z_min": 0.0, "width": 0.0, "height": 0.0},
             "imported_terrain_id": "chernarusplus", "destination_terrain_id": "chernarusplus", "object_count": 2,
             "insertion": "top", "remapped": [], "excluded": [], "excluded_count": 0, "discarded": [],
             "unmapped": [], "global_abundance": {"destination": 0, "imported": 0, "result": 0}, "unavailable": [],
             "result_object_count": 5, "result_vertex_count": 12, "refusal": None, "placement_review": False,
             "confirmation_required": False, "destination_revision": 3}
    return {**value, **changes}


def lines(value: dict, labels: dict | None = None) -> list[str]:
    """Return the rendered lines of one review; the import labels unless others are given."""
    if labels is None:
        return render_blocks(transfer_blocks(PROFILE, value)).splitlines()
    return render_blocks(transfer_blocks(PROFILE, value, labels)).splitlines()


# A report with each difference that a label names a file for
DIFFERENT = {"terrain_match": False, "bounds_equal": False, "imported_terrain_id": "Pripyat",
             "bounds_difference": {"x_min": -10.0, "z_min": 0.0, "width": 19480.0, "height": 2.5},
             "global_abundance": {"destination": 25, "imported": -25, "result": 25}, "discarded": ["records"]}


class TransferReviewTests(unittest.TestCase):
    """Each slot of the compatibility review, in the order of the Product Owner table."""

    def test_a_report_without_differences(self) -> None:
        """Profile, heading, same terrain and bounds, counts, insertion, result; no capacity line for equal values."""
        self.assertEqual(lines(report()), [
            "Profile: Alpha", "Compatibility", "Terrain: same as this mission", "Bounds: same",
            "Objects in the file: 2",
            "Objects that get a new ID: 0", "Merge: the objects of the file go above all objects of this plan, in "
            "their order.", "The plan will have 5 objects and 12 polygon vertices."])

    def test_every_difference_and_every_list(self) -> None:
        """Terrains, bounds in metres, new IDs, capacity, the three lists, discarded fields, placement review."""
        value = report(
            terrain_match=False, bounds_equal=False, imported_terrain_id="Pripyat",
            bounds_difference={"x_min": -10.0, "z_min": 0.0, "width": 19480.0, "height": 2.5},
            remapped=[{"from": "c1"}], global_abundance={"destination": 25, "imported": -25, "result": 25},
            unavailable=[{"id": "e1", "name": "Camp", "reason": "AI_Bandits is not installed.", "kind": "encounter",
                          "provider": "ai_bandits", "event_name": None}],
            excluded=[{"id": "far", "name": None, "linked_loot": None},
                      {"id": "e2", "name": "Ambush", "linked_loot": {"enabled": True, "radius": 50.0}}],
            unmapped=[{"kind": "policy", "id": "lootPolicy", "reason": "a policy text has no place in the plan"},
                      {"kind": "zone", "id": None, "reason": "a zone needs points"}],
            discarded=["records", "background.path"], placement_review=True)
        self.assertEqual(lines(value)[2:], [
            "Terrain: Pripyat in the file, chernarusplus in this mission",
            "Bounds: the file differs by X -10, Z 0, width 19480 and height 2.5 metres", "Objects in the file: 2",
            "Objects that get a new ID: 1", "  c1",
            "Merge: the objects of the file go above all objects of this plan, in their order.",
            "Global container capacity: +25% stays. The file has -25%.",
            "Objects that are not available on this mission", "  Camp: AI_Bandits is not installed.",
            "Objects left out because they do not fit the bounds", "  far", "  Ambush (with its linked loot)",
            "Items that are not imported", "  lootPolicy: a policy text has no place in the plan",
            "  a zone needs points", "Fields of the file that are not kept: 2",
            "The plan will have 5 objects and 12 polygon vertices.",
            "The terrain or the bounds differ. Check the placement of every object before you continue."])

    def test_replace_names_the_imported_capacity(self) -> None:
        """Replace always shows the imported global value, also when it equals the destination value."""
        value = report(insertion="replace", global_abundance={"destination": 0, "imported": 0, "result": 0})
        self.assertIn("Replace: the objects of the file replace the objects of this plan.", lines(value))
        self.assertIn("Global container capacity: 0%.", lines(value))

    def test_each_reason_uses_the_existing_text(self) -> None:
        """The tier and provider texts of map show, the window text for an event, the unread-provider text."""
        entries = [{"id": "c1", "name": "Zone", "reason": "tier_not_holdable", "kind": "loot_circle"},
                   {"id": "e1", "name": "City", "reason": "event_not_offered", "kind": "encounter",
                    "provider": "native_infected", "event_name": "InfectedCity"},
                   {"id": "e2", "name": "Horde", "reason": "territory_not_registered", "kind": "encounter",
                    "provider": "native_infected"},
                   {"id": "e3", "name": "Pack", "reason": "Wolf text.", "kind": "encounter", "provider": "native_wolf"},
                   {"id": "e4", "name": "Odd", "reason": "provider_unavailable", "kind": "encounter",
                    "provider": "other"},
                   {"id": "e5", "name": "Lost", "reason": "provider_unavailable", "kind": "encounter",
                    "provider": "native_infected"},
                   {"id": "c2", "name": "Strange", "reason": "something_new", "kind": "loot_circle"}]
        self.assertEqual([line for line in lines(report(unavailable=entries)) if line.startswith("  ")], [
            "  Zone: The tier map of this mission cannot hold this tier. Choose another tier or Inherit.",
            "  City: This mission does not offer the infected event InfectedCity.",
            "  Horde: Infected areas are not available on this mission: cfgenvironment.xml does not register "
            "env/zombie_territories.xml.",
            "  Pack: Wolf text.",
            # Product Owner 2026-10-17 04:07:15 in evidence 10.33, word for word, for any provider
            "  Odd: DayZ-ServerMan could not read whether this mission supports this kind of encounter.",
            "  Lost: DayZ-ServerMan could not read whether this mission supports this kind of encounter.",
            "  Strange: This mission cannot support this operation."])

    def test_the_singular_forms_of_the_result_line(self) -> None:
        """Product Owner 2026-10-17 03:37:11: one object, one vertex, or both."""
        for (objects, vertices), text in (((1, 4), "The plan will have 1 object and 4 polygon vertices."),
                                          ((3, 1), "The plan will have 3 objects and 1 polygon vertex."),
                                          ((1, 1), "The plan will have 1 object and 1 polygon vertex."),
                                          ((0, 0), "The plan will have 0 objects and 0 polygon vertices.")):
            with self.subTest(objects=objects, vertices=vertices):
                value = report(result_object_count=objects, result_vertex_count=vertices)
                self.assertEqual(lines(value)[-1], text)

    def test_copy_has_its_own_forms_of_the_labels_that_name_a_file(self) -> None:
        """Product Owner 2026-10-17 04:37:12 in evidence 10.7: each copy form, word for word."""
        merge = lines(report(**DIFFERENT), words.COPY_LABELS)
        for line in ("Terrain: Pripyat in the source profile, chernarusplus in this mission",
                     "Bounds: the source plan differs by X -10, Z 0, width 19480 and height 2.5 metres",
                     "Objects in the source plan: 2",
                     "Merge: the objects of the source plan go above all objects of this plan, in their order.",
                     "Global container capacity: +25% stays. The source plan has -25%.",
                     "Fields of the source plan that are not kept: 1"):
            self.assertIn(line, merge)
        replace = lines(report(insertion="replace"), words.COPY_LABELS)
        self.assertIn("Replace: the objects of the source plan replace the objects of this plan.", replace)
        # The shared labels stay; no copy line names a file, and no discarded line shows for 0 fields
        self.assertIn("Global container capacity: 0%.", replace)
        self.assertFalse([line for line in merge + replace if "the file" in line])
        self.assertFalse([line for line in replace if line.startswith("Fields of")])

    def test_import_keeps_its_forms(self) -> None:
        """The import forms are the defaults and do not change."""
        merge = lines(report(**DIFFERENT))
        self.assertEqual(merge, lines(report(**DIFFERENT), words.IMPORT_LABELS))
        for line in ("Terrain: Pripyat in the file, chernarusplus in this mission",
                     "Bounds: the file differs by X -10, Z 0, width 19480 and height 2.5 metres",
                     "Objects in the file: 2",
                     "Merge: the objects of the file go above all objects of this plan, in their order.",
                     "Global container capacity: +25% stays. The file has -25%.",
                     "Fields of the file that are not kept: 1"):
            self.assertIn(line, merge)
        self.assertIn("Replace: the objects of the file replace the objects of this plan.",
                      lines(report(insertion="replace")))
        self.assertEqual(set(words.COPY_LABELS), set(words.IMPORT_LABELS))

    def test_answer_values_are_data(self) -> None:
        """Names, IDs, reasons and terrain IDs are values, never prose (11.3)."""
        value = report(terrain_match=False, imported_terrain_id="Pripyat", remapped=[{"from": "c1"}],
                       unmapped=[{"id": "P101", "reason": "AI_Bandits points are not mapped"}])
        texts = {part.text for part in block_parts(transfer_blocks(PROFILE, value)) if isinstance(part, Value)}
        self.assertLessEqual({"Pripyat", "chernarusplus", "c1", "P101", "AI_Bandits points are not mapped"}, texts)


if __name__ == "__main__":
    unittest.main()
