"""Python invocation identity only; no application/server/model startup."""
import json,os,stat,subprocess,sys,tempfile,unittest,venv
from pathlib import Path
from unittest.mock import patch
from rm_client import sim_launcher as m
from test_sim_launcher import LauncherTests

class PathTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='venv path 中文;');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def link(self,name='python link'):
        p=self.root/name;p.symlink_to(sys.executable);return p
    def test_absolute_link_remains_selected(self):
        p=self.link();self.assertEqual(p,m.python_executable(p));self.assertNotEqual(p.resolve(),m.python_executable(p))
    def test_relative_link_becomes_absolute_without_resolve(self):
        p=self.link();old=Path.cwd()
        try:os.chdir(self.root);self.assertEqual(p,m.python_executable(Path('python link')))
        finally:os.chdir(old)
    def test_parent_symlink_is_not_replaced(self):
        real=self.root/'real';real.mkdir();(real/'python').symlink_to(sys.executable);alias=self.root/'alias';alias.symlink_to(real,target_is_directory=True)
        self.assertEqual(alias/'python',m.python_executable(alias/'python'))
    def test_symlink_dotdot_path_keeps_operating_system_semantics(self):
        real=self.root/'target';real.mkdir();child=real/'child';child.mkdir();(real/'python').symlink_to(sys.executable);link=self.root/'alias';link.symlink_to(child,target_is_directory=True)
        p=link/'..'/'python';self.assertEqual(p,m.python_executable(p))
    def test_missing_rejects(self):
        with self.assertRaises(OSError):m.python_executable(self.root/'missing')
    def test_dangling_rejects(self):
        p=self.root/'broken';p.symlink_to(self.root/'none')
        with self.assertRaises(OSError):m.python_executable(p)
    def test_directory_rejects(self):
        with self.assertRaises(m.LaunchError):m.python_executable(self.root)
    def test_non_executable_rejects(self):
        p=self.root/'plain';p.write_text('not run');p.chmod(stat.S_IRUSR|stat.S_IWUSR)
        with self.assertRaises(m.LaunchError):m.python_executable(p)
    def test_space_metacharacters_remain_one_literal_path(self):
        p=self.link('python ; $(never_run)');self.assertEqual(str(p),str(m.python_executable(p)))
    def test_bound_rejects(self):
        with self.assertRaises(m.LaunchError):m.python_executable(self.link(),limit=0)
    def test_regular_asset_resolution_is_unchanged(self):
        p=self.link();self.assertEqual(p.resolve(),m.plain_file(p))
    def test_real_symlink_venv_prefix_is_not_base(self):
        env=self.root/'actual venv';venv.EnvBuilder(with_pip=False,symlinks=True).create(env)
        python=env/'bin'/'python';selected=m.python_executable(python)
        code='import sys,json;print(json.dumps([sys.prefix,sys.base_prefix,sys.executable]))'
        current=json.loads(subprocess.check_output([str(selected),'-c',code],text=True,timeout=5))
        base=json.loads(subprocess.check_output([str(python.resolve()),'-c',code],text=True,timeout=5))
        self.assertEqual(str(env),current[0]);self.assertNotEqual(current[0],current[1]);self.assertEqual(str(selected),current[2]);self.assertEqual(base[0],base[1])

class PlanTests(unittest.TestCase):
    def test_both_argv_keep_selected_paths_no_process(self):
        case=LauncherTests();case.setUp()
        try:
            nav=case.r/'nav python ; literal';client=case.r/'client python';nav.symlink_to(sys.executable);client.symlink_to(sys.executable)
            case.args.nav_python=nav;case.args.client_python=client
            with patch.object(m.subprocess,'Popen',side_effect=AssertionError('No process in make_plan')):
                plan=case.plan()
            self.assertEqual(str(nav),plan['server_argv'][0]);self.assertEqual(str(client),plan['qt_argv'][0]);self.assertFalse(case.args.out.exists())
        finally:case.tearDown()
    def test_existing_supervisor_shell_false_and_cleanup(self):
        case=LauncherTests('test_mock_second_spawn_failure_reaps_server');case.setUp()
        try:case.test_mock_second_spawn_failure_reaps_server()
        finally:case.tearDown()

def load_tests(loader,tests,pattern):return unittest.TestSuite(loader.loadTestsFromTestCase(c) for c in (PathTests,PlanTests))
if __name__=='__main__':unittest.main()
