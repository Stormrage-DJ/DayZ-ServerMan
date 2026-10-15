"""The object order since the last Apply in the Apply preview (8.10, Architect 2026-10-15 15:46:42, ruling 1).

The plan order sets zone precedence (R06). The preview answer reports a new
relative order of the objects of both plans as `objects.order_changed`, with
the Product Owner text. Its fingerprint binds both fields.
"""

from __future__ import annotations

import hashlib
import unittest

from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.domain.mission_map_plan import canonical_json
from dayz_serverman.domain.mission_map_preview_changes import object_changes
from dayz_serverman.domain.mission_map_preview_texts import CAPTURE_RISK_TEXT, ORDER_CHANGED_TEXT
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_infected import encounter
from tests.test_mission_map_plan import circle

# Product Owner texts of 2026-10-15 15:57:10 in evidence 8.10 (decisions 1 and 2), copied word for word
PO_ORDER = ("The order of zones or encounters in the Layers list changed since the last Apply. Where zones overlap, "
            "the zone that is higher in the list wins. So this Apply can change the mission files in ways that no "
            "listed change explains.")
PO_CAPTURE = ("Before the first Apply on this mission folder, DayZ-ServerMan saves the current mission files as the "
              "original. It does not save map editor output as the original, but it recognizes only unchanged map "
              "editor output that this DayZ-ServerMan wrote. If these files contain other map editor output, Restore "
              "original writes that output back.")


def order(objects: list[dict], previous: list[dict] | None) -> tuple[bool, str | None]:
    """Return the order flag and the order text of one comparison."""
    result = object_changes(objects, previous)
    return result["order_changed"], result["order_text"]


class OrderRuleTests(unittest.TestCase):
    """The rule over the IDs of both plans; added and removed objects alone are no reorder."""

    def test_texts_equal_the_product_owner_texts(self) -> None:
        """The order text and the new capture-check text are the Product Owner texts word for word."""
        self.assertEqual(ORDER_CHANGED_TEXT, PO_ORDER)
        self.assertEqual(CAPTURE_RISK_TEXT, PO_CAPTURE)

    def test_a_reorder_only(self) -> None:
        """Two objects swap places: no object is listed, but the order changed."""
        a, b, c = circle("a"), circle("b"), circle("c")
        result = object_changes([b, a, c], [a, b, c])
        self.assertEqual((result["added"], result["changed"], result["removed"]), ([], [], []))
        self.assertEqual((result["order_changed"], result["order_text"]), (True, PO_ORDER))
        self.assertEqual(order([a, c, b], [a, b, c]), (True, PO_ORDER))
        self.assertEqual(order([a, b, c], [a, b, c]), (False, None))

    def test_a_reorder_with_a_removed_object(self) -> None:
        """A removed object takes no place; the kept objects decide the order."""
        a, b, c = circle("a"), circle("b"), circle("c")
        result = object_changes([c, a], [a, b, c])
        self.assertEqual([item["id"] for item in result["removed"]], ["b"])
        self.assertEqual((result["order_changed"], result["order_text"]), (True, PO_ORDER))
        self.assertEqual(order([a, c], [a, b, c]), (False, None))
        # An added object in between and a removed one keep the relative order of the others
        self.assertEqual(order([circle("n"), a, circle("m"), c], [a, b, c]), (False, None))
        self.assertEqual(order([b], [a, b, c]), (False, None))

    def test_no_previous_objects(self) -> None:
        """Without a previous plan, or with an empty one, nothing can be reordered."""
        a, b = circle("a"), circle("b")
        self.assertEqual(order([b, a], None), (False, None))
        self.assertEqual(object_changes([b, a], None)["previous"], False)
        self.assertEqual(order([b, a], []), (False, None))
        self.assertEqual(order([], [a, b]), (False, None))

    def test_every_object_kind_and_a_changed_configuration(self) -> None:
        """An encounter moved above a zone counts; a changed configuration in place is no reorder."""
        a, e = circle("a"), encounter("e")
        self.assertEqual(order([e, a], [a, e]), (True, PO_ORDER))
        moved = dict(a, radius=75)
        result = object_changes([moved, e], [a, e])
        self.assertEqual(([item["id"] for item in result["changed"]], result["order_changed"]), (["a"], False))


class OrderAnswerTests(ApplyFixture):
    """The field after a real Apply, and its fingerprint binding."""

    def test_a_reorder_after_apply_is_in_the_answer_and_in_the_fingerprint(self) -> None:
        """Apply A over B, then save B over A: the answer says so, and a review without it is stale."""
        first, second = dict(zone("rich"), id="a", name="Zone A"), dict(zone("sparse", 100.0, 100.0, 30.0), id="b")
        self.save([first, second], 25)
        self.run_apply()
        same = self.review().answer["objects"]
        self.assertEqual((same["order_changed"], same["order_text"]), (False, None))
        self.save([second, first], 25, expected=0)
        review = self.review()
        objects = review.answer["objects"]
        self.assertEqual((objects["added"], objects["changed"], objects["removed"]), ([], [], []))
        self.assertEqual((objects["order_changed"], objects["order_text"]), (True, PO_ORDER))
        # The fingerprint is the digest of the whole answer, so it covers both fields
        bound = {name: value for name, value in review.answer.items() if name != "fingerprint"}
        self.assertEqual(review.fingerprint, self.digest(bound, review))
        hidden = dict(bound, objects=dict(objects, order_changed=False, order_text=None))
        self.assertNotEqual(self.digest(hidden, review), review.fingerprint)
        with self.assertRaises(PreviewStale):
            self.preview.revalidate("main", self.digest(hidden, review))
        self.assertEqual(self.preview.revalidate("main", review.fingerprint).fingerprint, review.fingerprint)

    def test_a_first_apply_has_no_order_change(self) -> None:
        """Before any Apply there is no previous plan, so the flag is false and the text null."""
        self.save([dict(zone("rich"), id="a"), dict(zone("sparse", 100.0, 100.0, 30.0), id="b")])
        objects = self.review().answer["objects"]
        self.assertEqual((objects["previous"], objects["order_changed"], objects["order_text"]), (False, False, None))

    @staticmethod
    def digest(answer: dict, review) -> str:
        """Return the preview fingerprint of an answer: version, DayZ root and answer, as canonical JSON."""
        return hashlib.sha256(canonical_json({"version": 1, "dayz_root": str(review.context.root),
                                              "answer": answer})).hexdigest()


if __name__ == "__main__":
    unittest.main()
