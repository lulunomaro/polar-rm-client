"""Visible original Qt spectator; auto-watch only, no claim/arm or native process."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
 p.add_argument('--sim-endpoint',required=True);p.add_argument('--ready-timeout',type=float,default=15.)
 a=p.parse_args(argv);u=urlsplit(a.sim_endpoint)
 if (u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or u.port is None or not 1024<=u.port<=65535):p.error('Explicit loopback endpoint required')
 if not 0<a.ready_timeout<=30:p.error('Ready timeout must be within (0,30]')
 if os.environ.get('QT_QPA_PLATFORM','').lower() in ('offscreen','minimal','minimalegl'):p.error('This is a visible entry; offscreen/minimal is not allowed')
 from PySide6.QtCore import QTimer
 from PySide6.QtGui import QSurfaceFormat
 from PySide6.QtWidgets import QApplication
 from rm_client.ui.sim_platform_window import PlatformWindow
 from rm_client.sim_platform_runtime import PlatformRuntime
 from rm_client.sim_viewer_readiness import observer_ready
 surface=QSurfaceFormat();surface.setVersion(3,3);surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile);QSurfaceFormat.setDefaultFormat(surface)
 app=QApplication.instance() or QApplication(['POLAR-OFFLINE-VISIBLE'])
 runtime=PlatformRuntime(a.sim_endpoint,role='spectator');window=PlatformWindow(runtime);window.show()
 started=time.monotonic();announced=False;selection_requested=False
 def check_impl():
  nonlocal announced,selection_requested
  t=runtime.transport
  with t._lock:
   can_select='red_sentry' in t.robot_ids and t.session is not None and not t.client.uncertain
  if can_select and not selection_requested:
   runtime.select_robot(window,'red_sentry');selection_requested=True
  with t._lock:
   state=t.raw_state or {};snap=dict(shown=window.isVisible(),role=t.role,session_id=t.client.session_id,uncertain=t.client.uncertain,watch_robot=t.watch_robot,watch_epoch=t.watch_epoch,episode=state.get('episode'),in_registry='red_sentry' in t.robot_ids,selection_pending=t.selection_pending,owned_robot=t.owned_robot,claim_pending=t.claim_pending,armed=runtime.armed)
  if observer_ready(snap) and not announced:
   print(json.dumps(dict(event='observer_ready',schema='rm.sim.visible-observer.v1',url=a.sim_endpoint,shown=True,watch_robot='red_sentry',watch_epoch=snap['watch_epoch'],episode=snap['episode'],role='spectator',authority='none',frame_presented=False)),flush=True);announced=True
  if not announced and time.monotonic()-started>a.ready_timeout:
   print(json.dumps(dict(event='observer_failed',reason='subscription_timeout')),flush=True);window.close();app.exit(2)
 def check():
  try:check_impl()
  except Exception as exc:
   print(json.dumps(dict(event='observer_failed',reason=type(exc).__name__+': '+str(exc))),flush=True);window.close();app.exit(2)
 import signal
 signal.signal(signal.SIGTERM,lambda *_:(window.close(),app.quit()))
 signal.signal(signal.SIGINT,lambda *_:(window.close(),app.quit()))
 timer=QTimer();timer.setInterval(30);timer.timeout.connect(check);timer.start()
 return app.exec()

if __name__=='__main__':raise SystemExit(main())
