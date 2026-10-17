"""Dynamic action audit evidence must include a connected consumer, not data alone."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.settings_action_evidence import (
    DISPATCH_SOURCE, dynamic_action_evidence, dynamic_action_routes,
)


class DynamicActionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / DISPATCH_SOURCE).read_text(encoding="utf-8")

    def test_six_exact_audio_options_have_dynamic_consumer_evidence(self):
        routes = dynamic_action_routes(self.source)
        for slot in ("Enable", "Disable", "Invalid"):
            for option in (f"Import {slot} Sound", f"Preview {slot}"):
                with self.subTest(option=option):
                    evidence = dynamic_action_evidence(option, routes)
                    self.assertEqual(len(evidence), 1)
                    self.assertIn(DISPATCH_SOURCE.as_posix(), evidence[0])
                    self.assertIn(slot.lower(), evidence[0])

    def test_near_matches_and_unregistered_slots_do_not_acquire_evidence(self):
        routes = dynamic_action_routes(self.source)
        for option in ("Import Fire Sound", "Preview Arbitrary", "Preview enable",
                       " Preview Enable", "Preview Enable ", "Save Current"):
            with self.subTest(option=option):
                self.assertEqual(dynamic_action_evidence(option, routes), [])

    def test_declarations_imports_comments_and_docstrings_are_not_consumers(self):
        source = '"""' + self.source.replace('"""', "'''") + '"""\n'
        source += 'from rm_client.configuration.commands import resolve_config_command\n'
        source += 'ACTIONS = ("Import Enable Sound", "Preview Enable")\n'
        self.assertEqual(dynamic_action_routes(source), {})
        self.assertEqual(dynamic_action_evidence("Preview Enable", {}), [])

    def test_disconnecting_resolver_or_forwarding_removes_all_evidence(self):
        for old, new in (("command = resolve_config_command(action_name)", "command = None"),
                         ("self._execute_config_command(command)", "disconnected(command)"),
                         ("resolve_config_command, resolve_qualification_command,",
                          "resolve_qualification_command,")):
            with self.subTest(disconnected=old):
                self.assertIn(old, self.source)
                self.assertEqual(dynamic_action_routes(self.source.replace(old, new)), {})

    def test_wrong_kind_or_missing_terminal_handler_cannot_hide_an_orphan(self):
        cases = (
            ("SOUND_IMPORT", "import_custom_ui_sound", "Import Enable Sound", "Preview Enable"),
            ("SOUND_PREVIEW", "_preview_ui_sound", "Preview Enable", "Import Enable Sound"),
        )
        for kind, handler, option, connected_option in cases:
            for old, new in (
                (f"command.kind is ConfigCommandKind.{kind}", "False"),
                (f"self.{handler}(command.sound_slot)", "unwired(command.sound_slot)"),
                (f"self.{handler}(command.sound_slot)", "pass"),
            ):
                with self.subTest(disconnected=old, replacement=new):
                    self.assertIn(old, self.source)
                    routes = dynamic_action_routes(self.source.replace(old, new))
                    self.assertEqual(dynamic_action_evidence(option, routes), [])
                    self.assertTrue(dynamic_action_evidence(connected_option, routes))

    def test_real_audit_keeps_the_orphan_ratchet_and_reports_dynamic_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "settings.json"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/audit_settings.py"),
                 "--max-orphans", "21", "--json", str(report)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(report.read_text(encoding="utf-8"))
        audio = [row for row in rows if row["module"] == "AudioFeedback"
                 and row["kind"] == "action"]
        self.assertEqual(len(audio), 6)
        self.assertTrue(all(row["tier"] == "DYNAMIC" and row["dynamic"] for row in audio))
        self.assertLessEqual(sum(row["tier"] == "ORPHAN" for row in rows), 21)


if __name__ == "__main__":
    unittest.main()
