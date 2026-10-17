"""A changed authoritative duration must reach grid, presenter and completion."""
from pathlib import Path
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HoldDurationAlignmentTests(unittest.TestCase):
    def test_changed_duration_reaches_actual_grid_and_completion(self):
        program = r'''
import sys
from dataclasses import replace
from unittest.mock import patch
from rm_client.ui import economy_interaction as interaction
interaction.HOLD_SECONDS = float(sys.argv[1])
# Change the authoritative value before dependent imports: no separate patch to
# a renderer can disguise another independently hardcoded duration.
from rm_client.ui import cs2_buy_menu as grid
from rm_client.ui.economy_presentation import EconomyConfirmationState
from test_cs2_buy_matrix import Host
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
host = Host()
host.offers[0] = replace(host.offers[0], confirmation='hold')
offer = host.offers[0]
host.economy_hold_key = offer.key
host.economy_hold_started = 100.0
elapsed = interaction.HOLD_SECONDS * .75
with patch.object(grid.time, 'perf_counter', return_value=100.0 + elapsed):
    commands = grid.build_grid(host, 1920, 1080)
assert '确认中 75%' in {text.text for text in commands.texts}
confirmation = EconomyConfirmationState(offer, False, offer.key, 100.0, '', 0.0).present(now=100.0 + elapsed)
assert abs(confirmation.progress - .75) < 1e-10
assert interaction.complete_economy_hold(offer_exists=True, inside=True, elapsed=elapsed) is interaction.HoldCompletion.TOO_SHORT
assert interaction.complete_economy_hold(offer_exists=True, inside=True, elapsed=interaction.HOLD_SECONDS) is interaction.HoldCompletion.SUBMIT
assert any(action == 'economy_confirm_hold' and payload == offer.key for _,action,payload in host.hotspots)
'''
        for duration in (.4, .8, 1.2):
            with self.subTest(duration=duration):
                environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                                   PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'tests'))
                result = subprocess.run([sys.executable, '-c', program, str(duration)],
                                        cwd=ROOT, env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
