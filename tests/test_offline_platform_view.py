"""Synthetic label/entry checks; real camera qualification is a separate tool."""
from pathlib import Path
from types import SimpleNamespace
import os,subprocess,sys,tempfile,unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from rm_client.ui.sim_platform_window import PlatformWindow,PolarRMWindow
class Painter:
 def __init__(self):self.text=[]
 def isActive(self):return True
 def save(self):pass
 def restore(self):pass
 def setPen(self,*a):pass
 def setFont(self,*a):pass
 def drawText(self,*a):self.text.append(a[-1])
class OfflineViewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def paint(self,raw):
  t=SimpleNamespace(raw_state=raw,role='spectator',snapshot=lambda:(None,None,0.,-1,None),max_frame_age=2.5)
  subject=SimpleNamespace(_simulation_runtime=SimpleNamespace(transport=t,armed=False,note='watch'))
  p=Painter()
  with patch.object(PolarRMWindow,'paint_overlay',return_value=True):PlatformWindow.paint_overlay(subject,None,painter=p,logical_size=(1360,900))
  return p.text
 def test_explicit_offline_profile_displays_modeled_clock(self):
  lines=self.paint(dict(clock_profile=dict(kind='offline_modeled_latency_v1',barrier_state='native_barrier')))
  self.assertTrue(any('OFFLINE / SIL' in x and 'modeled delays' in x and 'native_barrier' in x for x in lines))
 def test_live_or_missing_profile_does_not_display_offline(self):
  for value in ({},dict(clock_profile={'kind':'wall_deadline_live'})):
   lines=self.paint(value);self.assertFalse(any('OFFLINE' in x for x in lines));self.assertTrue(any('OBSERVER' in x for x in lines))
 def test_optimized_entry_refuses_before_creating_output(self):
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as td:
   out=Path(td)/'uncreated';r=subprocess.run([sys.executable,'-O',str(root/'tools/verify_offline_platform.py'),'--out',str(out)],capture_output=True,text=True)
   self.assertNotEqual(r.returncode,0);self.assertIn('without -O',r.stderr);self.assertFalse(out.exists())
if __name__=='__main__':unittest.main()
