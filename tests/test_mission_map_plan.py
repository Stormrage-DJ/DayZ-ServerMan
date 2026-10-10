"""Cover the mission map plan document: forms, version policy, limits, canonical JSON and fingerprint (D1)."""

from __future__ import annotations

import copy
import math
import unittest
from typing import Any

from dayz_serverman.domain.mission_map_plan import (
    PlanForm, canonical_json, configuration_fingerprint, has_draft_changes, parse_plan, parse_plan_text, to_portable,
)
from dayz_serverman.domain.mission_map_values import (
    GLOBAL_ABUNDANCE_STEPS, PLAN_FORMAT, PlanValidationError, PlanVersionError,
)


# Mission key and digest values of a well-formed association and record reference
MISSION_KEY = "0123456789abcdef0123456789abcdef"
RUNTIME_KEY = "fedcba9876543210fedcba9876543210"
DIGEST = "ab" * 32


def circle(identifier: str = "c1", x: float = 100.0, z: float = 100.0, radius: float = 50.0) -> dict[str, Any]:
    """Return a valid loot circle."""
    return {"id": identifier, "name": "Loot circle 1", "kind": "loot_circle", "visible": True, "locked": False,
            "centre": {"x": x, "z": z}, "radius": radius, "tier": None, "abundance": "rich"}


def square(identifier: str = "p1", x: float = 10.0, z: float = 10.0, size: float = 20.0) -> dict[str, Any]:
    """Return a valid square loot polygon with its lower-left corner at (x, z)."""
    corners = [(x, z), (x + size, z), (x + size, z + size), (x, z + size)]
    return {"id": identifier, "name": "Loot polygon 1", "kind": "loot_polygon", "visible": True, "locked": False,
            "vertices": [{"x": cx, "z": cz} for cx, cz in corners], "tier": "Tier2", "abundance": "sparse"}


def encounter(identifier: str = "e1", provider: str = "native_infected") -> dict[str, Any]:
    """Return a valid encounter of the provider with disabled linked loot."""
    native = provider != "ai_bandits"
    properties = ({"event_name": "InfectedCity", "smin": 2, "smax": 4, "dmin": 1, "dmax": 3}
                  if provider == "native_infected" else {"group_size": 2})
    return {"id": identifier, "name": "Encounter 1", "kind": "encounter", "visible": True, "locked": False,
            "centre": {"x": 500.0, "z": 500.0}, "radius": 40.0 if native else None, "provider": provider,
            "properties": properties, "excluded": False,
            "linked_loot": {"enabled": False, "tier": None, "abundance": "rich", "radius": None}}


def stored_plan(objects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a valid stored plan on a 1,000 m square terrain at the origin."""
    return {
        "format": PLAN_FORMAT, "version": 1, "plan_id": "plan01",
        "association": {"profile_id": "main", "mission_root": "mpmissions\\dayzOffline.test",
                        "mission_key": MISSION_KEY, "runtime_profile_key": None},
        "terrain": {"terrain_id": "test.terrain", "bounds": {"x_min": 0.0, "z_min": 0.0, "width": 1000.0,
                    "height": 1000.0}, "bounds_source": "manual", "bounds_confirmed": True},
        "global_abundance": 0,
        "objects": [circle(), square(), encounter()] if objects is None else objects,
        "background": {"file_name": "map.png", "sha256": DIGEST, "byte_size": 10, "pixel_width": 64,
                       "pixel_height": 64, "path": "C:\\maps\\map.png",
                       "calibration": {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -1.0, "f": 64.0}},
        "records": {"originals": [], "baseline": None, "applied": None},
    }


def problems_of(test: unittest.TestCase, raw: object, form: PlanForm = PlanForm.STORED) -> list[str]:
    """Return the problem messages of a document that must fail validation."""
    with test.assertRaises(PlanValidationError) as caught:
        parse_plan(raw, form)
    return [problem.message for problem in caught.exception.problems]


class PlanDocumentTests(unittest.TestCase):
    """Verify both forms, the version policy and the document-level rules."""

    def test_valid_stored_plan_parses_and_reparses_unchanged(self) -> None:
        """A valid stored plan parses, and its parsed form is a fixed point."""
        plan = parse_plan(stored_plan(), PlanForm.STORED)
        self.assertEqual([item["id"] for item in plan["objects"]], ["c1", "p1", "e1"])
        self.assertEqual(parse_plan(copy.deepcopy(plan), PlanForm.STORED), plan)
        # The canonical text parses back to the same plan
        self.assertEqual(parse_plan_text(canonical_json(plan), PlanForm.STORED), plan)

    def test_portable_form_omits_stored_only_fields(self) -> None:
        """to_portable drops plan_id, association, records and the image path; the result parses as portable."""
        plan = parse_plan(stored_plan(), PlanForm.STORED)
        portable = to_portable(plan)
        self.assertEqual(set(portable), {"format", "version", "terrain", "global_abundance", "objects", "background"})
        self.assertNotIn("path", portable["background"])
        self.assertEqual(parse_plan(portable, PlanForm.PORTABLE), portable)
        # The stored plan keeps its path, and stored-only fields are unknown in the portable form
        self.assertEqual(plan["background"]["path"], "C:\\maps\\map.png")
        self.assertTrue(problems_of(self, stored_plan(), PlanForm.PORTABLE))
        self.assertTrue(problems_of(self, portable, PlanForm.STORED))

    def test_version_policy(self) -> None:
        """Version 1 parses; a higher one is newer; a missing marker, lower or non-integer version is invalid."""
        raw = stored_plan()
        raw["version"] = 2
        with self.assertRaises(PlanVersionError) as caught:
            parse_plan(raw, PlanForm.STORED)
        self.assertIn("newer build", str(caught.exception))
        # A newer version is refused before its unknown fields are looked at
        raw["future_field"] = {"anything": 1}
        self.assertRaises(PlanVersionError, parse_plan, raw, PlanForm.PORTABLE)
        for version in (0, -1, "1", 1.0, True, None):
            raw = stored_plan()
            raw["version"] = version
            with self.subTest(version=version), self.assertRaises(PlanValidationError) as invalid:
                parse_plan(raw, PlanForm.STORED)
            self.assertNotIsInstance(invalid.exception, PlanVersionError)
        missing = stored_plan()
        del missing["format"]
        self.assertTrue(problems_of(self, missing))

    def test_prototype_brush_export_is_refused(self) -> None:
        """A prototype brush export has no format marker, so import refuses it."""
        brush = {"version": 1, "brushes": [{"x": 0.5, "y": 0.5, "r": 0.01, "tier": 4}]}
        self.assertIn("format marker", problems_of(self, brush, PlanForm.PORTABLE)[0])

    def test_unknown_and_missing_fields_are_refused(self) -> None:
        """Unknown fields are refused at every closed level; missing fields too."""
        edits = [lambda raw: raw.update(extra=1), lambda raw: raw["terrain"]["bounds"].update(depth=1),
                 lambda raw: raw["records"].update(notes=None), lambda raw: raw["background"].pop("sha256")]
        for edit in edits:
            raw = stored_plan()
            edit(raw)
            with self.subTest(raw=raw):
                self.assertTrue(problems_of(self, raw))

    def test_global_abundance_steps(self) -> None:
        """Only the seven slider positions are accepted, as integers."""
        for step in GLOBAL_ABUNDANCE_STEPS:
            raw = stored_plan()
            raw["global_abundance"] = step
            self.assertEqual(parse_plan(raw, PlanForm.STORED)["global_abundance"], step)
        for value in (10, -75, 150, 0.0, 25.0, True, "0", None):
            raw = stored_plan()
            raw["global_abundance"] = value
            with self.subTest(value=value):
                self.assertTrue(problems_of(self, raw))

    def test_association_records_and_background_rules(self) -> None:
        """Stored-only sections and the background are validated."""
        edits = [
            lambda raw: raw["association"].update(profile_id="Main Profile"),
            lambda raw: raw["association"].update(mission_root="C:\\mpmissions\\x"),
            lambda raw: raw["association"].update(mission_root="mpmissions\\..\\x"),
            lambda raw: raw["association"].update(mission_key="ABC"),
            lambda raw: raw["records"].update(originals=[{"target_key": RUNTIME_KEY, "manifest_sha256": DIGEST}]),
            lambda raw: raw["records"].update(baseline={"revision": -1, "manifest_sha256": DIGEST}),
            lambda raw: raw["records"].update(applied={"operation_id": "op/1", "manifest_sha256": DIGEST}),
            lambda raw: raw["background"].update(file_name="maps\\map.png"),
            lambda raw: raw["background"].update(pixel_width=0),
        ]
        for edit in edits:
            raw = stored_plan()
            edit(raw)
            with self.subTest(raw=raw):
                self.assertTrue(problems_of(self, raw))
        # Originals of both targets, a baseline, a last apply and a missing image path are valid
        raw = stored_plan()
        raw["association"]["runtime_profile_key"] = RUNTIME_KEY
        raw["records"] = {"originals": [{"target_key": MISSION_KEY, "manifest_sha256": DIGEST},
                                        {"target_key": RUNTIME_KEY, "manifest_sha256": DIGEST}],
                          "baseline": {"revision": 3, "manifest_sha256": DIGEST},
                          "applied": {"operation_id": "op-1", "manifest_sha256": DIGEST}}
        raw["background"]["path"] = None
        self.assertEqual(len(parse_plan(raw, PlanForm.STORED)["records"]["originals"]), 2)

    def test_object_problems_are_collected_and_classified(self) -> None:
        """Each failing object is listed once; only bounds rules are marked bounds-dependent."""
        raw = stored_plan([circle("a"), circle("b", x=2000.0), circle("A"), circle("d", radius=0.5),
                           circle("e", radius=1500.0)])
        with self.assertRaises(PlanValidationError) as caught:
            parse_plan(raw, PlanForm.STORED)
        found = [(problem.object_index, problem.object_id, problem.bounds_dependent)
                 for problem in caught.exception.problems]
        self.assertEqual(found, [(1, "b", True), (2, "A", False), (3, "d", False), (4, "e", True)])

    def test_object_and_vertex_limits(self) -> None:
        """At most 2,000 objects, and at most 50,000 polygon vertices in the plan."""
        many = [circle(f"c{index}") for index in range(2000)]
        self.assertEqual(len(parse_plan(stored_plan(many), PlanForm.STORED)["objects"]), 2000)
        self.assertTrue(problems_of(self, stored_plan(many + [circle("extra")])))
        # 98 polygons of 512 vertices hold 50,176 vertices
        angles = [2 * math.pi * index / 512 for index in range(512)]
        ring = [{"x": 500 + 400 * math.cos(angle), "z": 500 + 400 * math.sin(angle)} for angle in angles]
        polygons = [dict(square(f"p{index}"), vertices=ring) for index in range(98)]
        self.assertIn("50000", problems_of(self, stored_plan(polygons))[0])
        self.assertEqual(len(parse_plan(stored_plan(polygons[:97]), PlanForm.STORED)["objects"]), 97)

    def test_terrain_is_validated(self) -> None:
        """An invalid terrain object makes the document invalid; detailed bounds rules are geometry tests."""
        raw = stored_plan([])
        raw["terrain"]["bounds"]["width"] = 0.0
        self.assertIn("width", problems_of(self, raw)[0])

    def test_text_rules(self) -> None:
        """Size, UTF-8, NaN, repeated keys and nesting depth are checked on the text."""
        text = canonical_json(stored_plan())
        self.assertTrue(parse_plan_text(text, PlanForm.STORED))
        bad = [b"\xff" + text, text.replace(b'"radius":50.0', b'"radius":NaN'),
               text[:-1] + b',"plan_id":"again"}', b"[" * 100_000 + b"]" * 100_000,
               text[:-1] + b',"x":' + b"[" * 17 + b"]" * 17 + b"}", b" " * (8 * 1024 * 1024) + text]
        for data in bad:
            with self.subTest(data=data[:40]), self.assertRaises(PlanValidationError):
                parse_plan_text(data, PlanForm.STORED)


class PlanFingerprintTests(unittest.TestCase):
    """Verify canonical JSON, the configuration fingerprint, Draft changes and new objects."""

    def test_canonical_json_form(self) -> None:
        """Sorted keys, no whitespace, non-ASCII text kept as UTF-8."""
        self.assertEqual(canonical_json({"b": 1, "a": [1.5, "Pripyať"]}), '{"a":[1.5,"Pripyať"],"b":1}'.encode())
        self.assertRaises(ValueError, canonical_json, {"a": float("nan")})

    def test_fingerprint_ignores_presentation_and_records(self) -> None:
        """Names, visibility, lock, background, records and association do not change the fingerprint."""
        plan = parse_plan(stored_plan(), PlanForm.STORED)
        fingerprint = configuration_fingerprint(plan)
        changed = copy.deepcopy(plan)
        changed["objects"][0].update(name="Renamed", visible=False, locked=True)
        changed["background"] = None
        changed["records"]["applied"] = {"operation_id": "op", "manifest_sha256": DIGEST}
        changed["association"]["mission_root"] = "mpmissions\\other"
        changed["terrain"]["terrain_id"] = "other"
        self.assertEqual(configuration_fingerprint(changed), fingerprint)
        # Integer and float coordinates normalize to the same content
        raw = stored_plan()
        raw["objects"][0]["centre"] = {"x": 100, "z": 100}
        self.assertEqual(configuration_fingerprint(parse_plan(raw, PlanForm.STORED)), fingerprint)

    def test_fingerprint_follows_configuration(self) -> None:
        """Order, geometry, tier, abundance, global value, bounds and encounter fields change it."""
        plan = parse_plan(stored_plan(), PlanForm.STORED)
        fingerprint = configuration_fingerprint(plan)
        edits = [
            lambda value: value["objects"].reverse(),
            lambda value: value["objects"][0].update(radius=51.0),
            lambda value: value["objects"][0].update(tier="Tier1"),
            lambda value: value["objects"][1].update(abundance="normal"),
            lambda value: value.update(global_abundance=25),
            lambda value: value["terrain"]["bounds"].update(width=999.0),
            lambda value: value["objects"][2].update(excluded=True),
            lambda value: value["objects"][2]["linked_loot"].update(enabled=True),
            lambda value: value["objects"][2]["properties"].update(smax=5),
        ]
        for edit in edits:
            changed = copy.deepcopy(plan)
            edit(changed)
            self.assertNotEqual(configuration_fingerprint(changed), fingerprint)

    def test_draft_changes(self) -> None:
        """Without a last apply, objects or a global value are changes; otherwise fingerprints are compared."""
        empty = parse_plan(stored_plan([]), PlanForm.STORED)
        self.assertFalse(has_draft_changes(empty, None))
        self.assertTrue(has_draft_changes(dict(empty, global_abundance=-25), None))
        plan = parse_plan(stored_plan(), PlanForm.STORED)
        self.assertTrue(has_draft_changes(plan, None))
        self.assertFalse(has_draft_changes(plan, configuration_fingerprint(plan)))
        self.assertTrue(has_draft_changes(plan, configuration_fingerprint(empty)))

    def test_fingerprint_treats_integral_numbers_alike(self) -> None:
        """-0.0, 0.0 and 0, and 2.0 and 2 in open properties, give one fingerprint; window JSON loses the difference."""
        raw = stored_plan([circle("c1", x=0.0), encounter("w", "native_wolf")])
        plan = parse_plan(raw, PlanForm.STORED)
        changed = copy.deepcopy(plan)
        changed["objects"][0]["centre"]["x"] = -0.0
        changed["objects"][1]["properties"]["group_size"] = 2.0
        self.assertEqual(configuration_fingerprint(changed), configuration_fingerprint(plan))
        changed["objects"][1]["properties"]["group_size"] = 2.5
        self.assertNotEqual(configuration_fingerprint(changed), configuration_fingerprint(plan))


if __name__ == "__main__":
    unittest.main()
