"""CLI routing never falls through from malformed simulator flags to hardware."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

class EntryTests(unittest.TestCase):
    root=Path(__file__).resolve().parents[1]
    def run_cli(self,*args):
        env=os.environ.copy();env['QT_QPA_PLATFORM']='offscreen'
        return subprocess.run([sys.executable,str(self.root/'main.py'),*args],cwd=self.root,env=env,capture_output=True,text=True,timeout=5)
    def test_explicit_v2_reaches_its_own_parser(self):
        result=self.run_cli('--sim-platform-v2','--help')
        self.assertEqual(result.returncode,0);self.assertIn('--sim-role',result.stdout);self.assertIn('platform v2',result.stdout)
    def test_v1_still_reaches_unchanged_parser(self):
        result=self.run_cli('--mujoco-operator','--help')
        self.assertEqual(result.returncode,0);self.assertIn('--mujoco-operator',result.stdout);self.assertNotIn('--sim-role',result.stdout)
    def test_invalid_v2_role_is_rejected(self):
        result=self.run_cli('--sim-platform-v2','--sim-role','admin')
        self.assertEqual(result.returncode,2);self.assertIn('invalid choice',result.stderr)
    def test_invalid_v2_switch_value_is_rejected(self):
        result=self.run_cli('--sim-platform-v2=true')
        self.assertEqual(result.returncode,2);self.assertIn('ignored explicit argument',result.stderr)

if __name__=='__main__':unittest.main()
