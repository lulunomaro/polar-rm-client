"""The historical package exports keep identity while pure controllers stay pure."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PolarPackageExportTests(unittest.TestCase):
    def run_isolated(self, source):
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_discovery_and_pure_policy_imports_do_not_load_qt_or_window(self):
        self.run_isolated('''
            import importlib.abc
            import sys
            class RejectQtAndWindow(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith(("PySide6", "rm_client.ui.polar_app",
                                            "rm_client.ui.polar.primitives",
                                            "rm_client.ui.polar.text_metrics")):
                        raise AssertionError("eager presentation dependency: " + fullname)
            sys.meta_path.insert(0, RejectQtAndWindow())
            import rm_client.ui.polar as polar
            assert set(polar.__all__) <= set(dir(polar))
            try:
                polar.unknown_export
            except AttributeError:
                pass
            else:
                raise AssertionError("unknown export should be rejected")
            from rm_client.ui.polar.frame_telemetry import FrameTelemetry
            from rm_client.ui.polar.tick_controller import FrameTiming
            assert FrameTelemetry().fps_samples == []
            assert FrameTiming.since(10., 9.).ui_dt > 0
        ''')

    def test_named_and_star_imports_preserve_exact_export_objects(self):
        self.run_isolated('''
            import rm_client.ui.polar as polar
            from rm_client.ui.polar import primitives, text_metrics
            expected = {name: getattr(primitives, name) for name in (
                "EconomyScenePhase", "FrameScheduler", "Hotspot", "InertialScroll",
                "ModuleListLayout", "StartupLayerCache",
            )}
            expected["TextMetricsMixin"] = text_metrics.TextMetricsMixin
            assert polar.__all__ == list(expected)
            imported = {}
            exec("from rm_client.ui.polar import *", imported)
            for name, value in expected.items():
                assert getattr(polar, name) is value, name
                assert imported[name] is value, name
                assert name in dir(polar), name
            try:
                polar.unknown_export
            except AttributeError as error:
                assert "unknown_export" in str(error)
            else:
                raise AssertionError("unknown export should not be accepted")
        ''')


if __name__ == "__main__":
    unittest.main()
