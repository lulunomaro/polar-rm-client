"""CPU/raster-only cold settings benchmark; never a GPU/hardware acceptance test.

Run each source revision in a fresh process with the same Qt/font assets:
  QT_QPA_PLATFORM=offscreen python tools/profile_settings_open.py --source PATH
Use --profile PATH for Python call timing. Does not send backend commands, play
sounds, save settings, or connect devices. Audio enumeration is Qt's read-only
query only when profiling the audio tab. Render quality is never modified.
"""
from __future__ import annotations
import argparse
import cProfile
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--opens', type=int, default=3)
parser.add_argument('--profile', type=Path)
parser.add_argument('--tabs', action='store_true')
args = parser.parse_args()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD', '1')
sys.path.insert(0, str(args.source.resolve()))

from types import SimpleNamespace as NS
from unittest.mock import Mock
from PySide6 import __version__ as qt_version
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from rm_client.control import KeyboardControlBridge
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.cs2_settings_center import build_settings_center
from rm_client.ui.fonts import FontBook

app = QApplication.instance() or QApplication([])
class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1280,720)
        self.registry, self.state = build_default_registry(), RobotState()
        self.control = KeyboardControlBridge()
        self.backend = Mock(mqtt=NS(mock_output=True))
        self.backend.trigger_estop.return_value = (True, '')
        self.frame_scheduler = Mock(); self.toast = Mock()
        self.auto_save_config = Mock(); self.after_setting_changed = Mock()
        self.invalidate_transient_overlay_scene = Mock(); self.invalidate_gui_cache = Mock()
        # The real client owns this bank before showing its startup scene.
        self.fonts = FontBook(); self.fonts.load_from_project_assets(args.source)
        self.fonts.font('jello_light',18)
    def key_name(self, event):
        return event.text().upper()

host = Host(); host.show(); app.processEvents()
report = {'qt': qt_version, 'platform': app.platformName(), 'source': str(args.source.resolve()),
          'bundled_fonts_present': (args.source/'assets/fonts').is_dir(),
          'hardware': False, 'opens': []}
for index in range(args.opens):
    profile = cProfile.Profile() if args.profile and index == 0 else None
    if profile: profile.enable()
    started = time.perf_counter(); dialog = build_settings_center(host)
    built = time.perf_counter(); dialog.show(); shown = time.perf_counter()
    app.processEvents(); presented = time.perf_counter()
    if profile: profile.disable(); profile.dump_stats(str(args.profile))
    result = {'index': index, 'build_ms': (built-started)*1000,
              'show_ms': (shown-built)*1000, 'event_ms': (presented-shown)*1000,
              'widgets_on_open': len(dialog.findChildren(QWidget)), 'tabs': []}
    if args.tabs:
        QTest.qWait(480)
        for tab_index in range(1, dialog.tabs.count()):
            before = time.perf_counter(); dialog.tabs.setCurrentIndex(tab_index)
            event_end = time.perf_counter(); page = dialog.tabs.currentWidget()
            builder = getattr(page, 'builder', None)
            deadline = before + 10
            while builder is not None and not builder.ready and not builder.error and time.perf_counter() < deadline:
                QTest.qWait(5)
            result['tabs'].append({'name': dialog.tabs.tabText(tab_index),
                'select_handler_ms': (event_end-before)*1000,
                'ready_elapsed_ms': (time.perf_counter()-before)*1000,
                'slices': getattr(builder,'slices',None), 'max_slice_ms': getattr(builder,'max_ms',None),
                'error': getattr(builder,'error',None), 'ready': getattr(builder,'ready',True)})
    report['opens'].append(result)
    dialog.reject(); QTest.qWait(160)
    dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
host.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
print(json.dumps(report, ensure_ascii=False, indent=2))
