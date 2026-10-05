"""Recovery block reasons: every reason that the host sets has a true operator sentence (QF-008, QF-075)."""
from __future__ import annotations

import re
import unittest

try:
    from tests.ui_harness_support import ROOT
except ModuleNotFoundError:
    from ui_harness_support import ROOT

from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.restores import RESTORE_KIND


# A restore block reason that has its own catalogue sentence
INSPECTED = "Mutations are blocked until restore recovery is inspected."

PACKAGE = ROOT / "runnable" / "src" / "python" / "dayz_serverman"


class BlockReasonTests(unittest.TestCase):
    """QF-008: one operator sentence per known block reason, the host sentence, and the fallback."""

    def test_every_reason_literal_of_the_host_has_a_sentence(self) -> None:
        """Each reason that the source passes to the block has its own catalogue sentence."""
        literals: set[str] = set()
        for path in PACKAGE.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            literals.update(re.findall(r'(?:block_for_recovery|block_for_missing_dayz_root|self\._block)\(\s*"([^"]+)"',
                                       source))
            literals.update(re.findall(r'"(Profile provisioning recovery[^"]+|Migration publication requires[^"]+'
                                       r'|SteamCMD process-tree exit[^"]+'
                                       r'|Mutations are blocked by an interrupted backup restore[^"]+'
                                       r'|Backup restore recovery requires[^"]+\.)"', source))
        self.assertEqual(len(literals), 20, sorted(literals))
        self.assertIn("Mutations are blocked by an interrupted mod publication while the server is not proven stopped.",
                      literals)
        known = {sentence for _fragment, sentence in wording.BLOCK_REASONS}
        for literal in sorted(literals):
            self.assertIn(wording.block_reason_text(literal), known, literal)
            self.assertFalse(wording.leaks_identifier(wording.block_reason_text(literal)))

    def test_known_reasons_host_sentence_and_fallback(self) -> None:
        """The specific sentences, a kept host sentence, and the fallback for an identifier."""
        cases = {
            "Mutations are blocked by unresolved mod publication.": "no DayZ server folder is set",
            "Mutations are blocked by unresolved mod publication recovery.": "could not be undone safely",
            "Mutations are blocked by an interrupted mod publication while the server is not proven stopped.":
                "Stop the server, then restart DayZ-ServerMan.",
            "Mutations are blocked by unresolved restore recovery.": "Open Backups",
            "Mutations are blocked by an interrupted backup restore while the server is not proven stopped.":
                "A backup restore was interrupted and must be finished.",
            "Mutations are blocked by an interrupted direct profile restore while the server is not proven stopped.":
                "A profile restore from a backup archive was interrupted and must be finished.",
            "Mutations are blocked by an interrupted profile creation while the server is not proven stopped.":
                "Creating a profile was interrupted and must be finished.",
            "Mutations are blocked until restore recovery is inspected.": "Open Backups",
            "Direct profile restore requires recovery.": "Stop the DayZ server",
            "Profile provisioning recovery requires review.": "Creating a profile was interrupted",
            "Mutations are blocked by unresolved migration recovery.": "A legacy import was interrupted",
        }
        for reason, part in cases.items():
            self.assertIn(part, wording.block_reason_text(reason), reason)
        # QF-045: a block without a catalogue sentence names its way out: a restart, or Backups for a restore
        restart = " Restart DayZ-ServerMan to check again."
        self.assertEqual(wording.block_reason_text("The configured DayZ root is unsafe."),
                         "The configured DayZ server folder is unsafe." + restart)
        self.assertEqual(wording.block_reason_text("Launch evidence could not be recorded safely."),
                         "Launch evidence could not be recorded safely." + restart)
        self.assertEqual(wording.block_reason_text("Committed restore targets could not be proven.", "RESTORE_BACKUP"),
                         "Committed restore targets could not be proven. Open Backups to finish the restore.")
        # A catalogue sentence keeps its own way out, whoever owns the block
        self.assertEqual(wording.block_reason_text(INSPECTED, "RESTORE_BACKUP"), wording.block_reason_text(INSPECTED))
        self.assertEqual(wording.BLOCK_RESTORE_OWNER, RESTORE_KIND)
        for raw in ("profile record is unavailable: INTERRUPTED_WRITE", "RECOVERY_REQUIRED", "", None,
                    "journal_state is bad"):
            self.assertEqual(wording.block_reason_text(raw), wording.BLOCK_FALLBACK + restart, raw)

    def test_each_cause_gets_its_own_true_sentence(self) -> None:
        """QF-075: a stop and restart is offered only where it helps; a sign-in never reads as a mod update."""
        profile = "A profile restore from a backup archive did not finish"
        cases = {
            # Compensation failed at run time: the next start under a stopped server undoes the restore
            "Direct profile restore requires recovery.":
                f"{profile}. Stop the DayZ server, then restart DayZ-ServerMan; "
                "it checks the unfinished restore when it starts.",
            "Direct profile restore journals could not be read.":
                f"{profile}, and its restore records cannot be read, so DayZ-ServerMan cannot finish or undo it. "
                "Restart DayZ-ServerMan to read them again.",
            "Direct profile restore recovery requires a configured DayZ root.":
                f"{profile}, and it cannot be checked because no DayZ server folder is set. In Settings, set the "
                "DayZ server folder that the restore used and change nothing else. Then save and restart DayZ-ServerMan.",
            "Direct profile restore recovery requires attention.":
                f"{profile}, and DayZ-ServerMan cannot finish or undo it safely because the DayZ server folder or "
                "the restored files changed or cannot be opened. If a drive or folder was unavailable, make it "
                "available again, then restart DayZ-ServerMan.",
            "SteamCMD process-tree exit after the sign-in could not be proven.":
                "SteamCMD did not close cleanly after the Steam sign-in, so the sign-in cannot be confirmed. "
                "Close SteamCMD, restart DayZ-ServerMan, then sign in again.",
            "SteamCMD process-tree exit could not be proven. Mutations are blocked.":
                "SteamCMD did not close cleanly, so the mod update cannot be confirmed. "
                "Close SteamCMD, restart DayZ-ServerMan, then update the mods again.",
        }
        for reason, sentence in cases.items():
            self.assertEqual(wording.block_reason_text(reason), sentence, reason)
        # The four profile restore causes and the two SteamCMD causes are six different sentences
        self.assertEqual(len(set(map(wording.block_reason_text, cases))), len(cases))

    def test_missing_folder_sentences_name_the_settings_repair(self) -> None:
        """(t) QF-069: the three no-folder sentences, first match wins, both catalogues agree in order."""
        cut = "and it cannot be checked because no DayZ server folder is set. In Settings, set the DayZ server folder"
        rows = {
            "Mutations are blocked by unresolved mod publication.":
                f"Applying mods to the server folder was interrupted, {cut} that the apply used and change nothing "
                "else. Then save and restart DayZ-ServerMan.",
            "Direct profile restore recovery requires a configured DayZ root.":
                f"A profile restore from a backup archive did not finish, {cut} that the restore used and change "
                "nothing else. Then save and restart DayZ-ServerMan.",
            "Backup restore recovery requires a configured DayZ root.":
                f"A backup restore did not finish, {cut} that the restore used and change nothing else. Then save, "
                "and open Backups or restart DayZ-ServerMan.",
        }
        for reason, sentence in rows.items():
            self.assertEqual(wording.block_reason_text(reason), sentence, reason)
            self.assertEqual(wording.block_reason_text(reason, RESTORE_KIND), sentence, reason)
            self.assertFalse(wording.leaks_identifier(sentence))
        script = (ROOT / "runnable" / "src" / "frontend" / "host_sentences.js").read_text(encoding="utf-8")
        table = script.split("const hostBlockReasons = Object.freeze([", 1)[1].split("\n]);", 1)[0]
        frontend = [(fragment, sentence or wording._IMPORT_BLOCK) for fragment, sentence in re.findall(
            r'^  \["([^"]+)", (?:"([^"]+)"|HOST_IMPORT_BLOCK)\],$', table, re.MULTILINE)]
        self.assertEqual(frontend, list(wording.BLOCK_REASONS))
