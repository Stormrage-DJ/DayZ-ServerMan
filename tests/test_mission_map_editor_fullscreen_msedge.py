"""Verify the F11 key and the full-screen View menu toggle in composed Edge (QA-9.11-F1)."""

from __future__ import annotations

import unittest

from tests.ui_harness_support import EDGE, run_editor_harness


# The fake host records every toggle_full_screen call and can refuse the next one.
FULL_SCREEN = r'''
let toggles = 0, refuse = false;
window.pywebview.api.toggle_full_screen = async () => {
  toggles += 1;
  return refuse ? {success: false, error: {code: "INTERNAL_FAILURE", retryable: false,
    message: "The map editor could not change full-screen mode."}} : {success: true, value: {}};
};
await window.ServerManMapEditor.load(); await wait();
const button = document.getElementById("map-full-screen");
const press = (target, init) => {
  target.focus();
  const event = new KeyboardEvent("keydown", {bubbles: true, cancelable: true, ...init});
  target.dispatchEvent(event);
  return event;
};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorFullScreenEdgeTests(unittest.TestCase):
    """Use a fake host; no native window or real data is involved."""

    def test_toolbar_toggle_follows_the_button_pattern(self) -> None:
        """The button is in the View menu, keyboard reachable, named and pressed-aware."""
        self.assertEqual(run_editor_harness(FULL_SCREEN + r'''
// The redesign moved the toggle from the toolbar into the View menu; each click closes the menu.
const view = document.getElementById("map-show-view"), menu = document.getElementById("map-view-menu");
const open = () => { if (menu.hidden) view.click(); };
check(button.closest("#map-view-menu"), "the toggle is in the View menu");
check(button.type === "button" && button.classList.contains("button") && !button.disabled, "a usable button");
check(button.textContent === "Full screen (F11)" && button.title === "Full screen (F11)", "label and tooltip");
check(button.getAttribute("aria-pressed") === "false" && button.getAttribute("aria-keyshortcuts") === "F11", "aria state");
open(); button.focus(); check(document.activeElement === button, "focusable in the open menu");
button.click(); await wait();
check(toggles === 1, "one host call");
check(button.getAttribute("aria-pressed") === "true" && button.textContent === "Exit full screen (F11)", "full-screen label");
check(menu.hidden && document.activeElement === view, "focus returns to the View menu opener");
open(); button.click(); await wait();
check(toggles === 2 && button.getAttribute("aria-pressed") === "false" && button.textContent === "Full screen (F11)", "back");
refuse = true; open(); button.click(); await wait();
check(toggles === 3 && button.getAttribute("aria-pressed") === "false", "a refusal keeps the state");
const notice = document.getElementById("map-save-notice");
check(notice.textContent.length > 0 && notice.classList.contains("map-save-error"), "the reason shows as an error notice");
'''), "PASS")

    def test_f11_acts_in_fields_and_not_with_a_dialog_or_modifier(self) -> None:
        """F11 toggles from the viewport and from a text field, never reaches the default, and obeys dialogs."""
        self.assertEqual(run_editor_harness(FULL_SCREEN + r'''
const viewport = document.getElementById("map-viewport");
let event = press(viewport, {key: "F11"}); await wait();
check(toggles === 1 && event.defaultPrevented, "viewport F11 toggles and blocks the default");
check(button.getAttribute("aria-pressed") === "true", "pressed after F11");
event = press(document.getElementById("map-coordinate-mode"), {key: "F11"}); await wait();
check(toggles === 2 && event.defaultPrevented && button.getAttribute("aria-pressed") === "false", "F11 in a select");
const field = document.createElement("input"); field.type = "text"; document.body.append(field);
press(field, {key: "F11"}); await wait();
check(toggles === 3, "F11 in a text field");
press(viewport, {key: "F11", repeat: true}); await wait();
check(toggles === 3, "a held key does not repeat the toggle");
press(viewport, {key: "F11", ctrlKey: true}); press(viewport, {key: "F11", altKey: true}); await wait();
check(toggles === 3, "modified F11 does nothing");
press(viewport, {key: "Escape"}); await wait();
check(toggles === 3, "Escape does not leave full screen");
const dialog = document.createElement("dialog"); document.body.append(dialog); dialog.show();
event = press(viewport, {key: "F11"}); await wait();
check(toggles === 3 && event.defaultPrevented, "no toggle while a dialog is open; still no browser default");
dialog.close(); dialog.remove();
press(viewport, {key: "F11"}); await wait();
check(toggles === 4, "F11 works again after the dialog");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
