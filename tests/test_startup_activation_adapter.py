"""Compare the production composition adapter to its fixed pre-extraction steps.

Reference: fa2c2b8397295d27ada5d105854b751b7dd0940d PolarRMWindow.__init__,
sync_quick_action_wheel_settings through the ready toast. No runtime Git read.
Only that constructor expression is executed, so every argument and partial
failure can be checked without allocating native owners.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rm_client.ui.polar.startup_activation import (
    StartupProfilePorts, StartupPresentationPorts, StartupScenarioPorts,
    activate_startup_session,
)

REFERENCE = '''
self.sync_quick_action_wheel_settings()
self.apply_match_mode(self.match_mode_manager.current.key, close_home=False, announce=False)
self.apply_robot_profile(self.current_robot_profile_key(), close_home=False, announce=False)
self.refresh_config_profiles()
self.refresh_wallpaper_profiles()
self.refresh_custom_ui_sound_options()
self.config_store.ensure_default(self.current_config_data())
self.load_startup_config()
self.setWindowTitle(ui_text("POLAR-RM Custom Client", chinese_enabled(self.registry)))
elimination_fx = self.registry.get("EliminationFX")
self.kill_fx.preload_preset_sounds(self.setting_mode(elimination_fx, "Preset", "Default Scrolling"))
self._startup_navigation.apply_autostart(os.environ.get("POLAR_AUTOSTART", ""))
self.sync_mode_setting()
if not self.home_visible and not self.configuration_change_pending:
    generation = self.backend.request_scenario(self.scenario_manager.current.key, registry=self.registry)
    self.capture_pending_scenario_configuration(generation)
self.toast("ClickGUI 已就绪：Jello Sigma")
'''


def adapter_code():
    tree = ast.parse((ROOT / "rm_client/ui/polar_app.py").read_text(encoding="utf-8"))
    window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PolarRMWindow")
    constructor = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    # The assembled-window abort guard nests the same adapter call in try.
    # Locate exactly one call without changing the frozen workflow/oracle.
    calls = [node for node in ast.walk(constructor) if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
             and node.value.func.id == "activate_startup_session"]
    assert len(calls) == 1, "Expected one startup activation adapter"
    call = calls[0]
    helpers = [node for node in constructor.body if isinstance(node, ast.FunctionDef)
               and node.name == "preload_startup_feedback"]
    return compile(ast.Module(body=[*helpers, call], type_ignores=[]), "<production-startup-adapter>", "exec")


class StartupAdapterTests(unittest.TestCase):
    def execute(self, code, *, visible=False, pending=False, autostart="0", fail_at=None):
        trace = []
        state = SimpleNamespace(home_visible=visible, configuration_change_pending=False)
        registry = SimpleNamespace()
        failure = RuntimeError("injected effect")

        def effect(name, result=None, mutation=None):
            def call(*args, **kwargs):
                normalized = {key: "registry" if value is registry else value for key, value in kwargs.items()}
                trace.append((name, args, normalized))
                if name == fail_at:
                    raise failure
                if mutation:
                    mutation()
                return result
            return call

        for name in ("sync_quick_action_wheel_settings", "apply_match_mode", "apply_robot_profile",
                     "refresh_config_profiles", "refresh_wallpaper_profiles", "refresh_custom_ui_sound_options",
                     "setWindowTitle", "sync_mode_setting", "capture_pending_scenario_configuration", "toast"):
            setattr(state, name, effect(name))
        state.current_robot_profile_key = effect("current_robot_profile_key", "robot-3")
        state.current_config_data = effect("current_config_data", {"profile": "baseline"})
        state.match_mode_manager = SimpleNamespace(current=SimpleNamespace(key="RMUC7v7"))
        state.scenario_manager = SimpleNamespace(current=SimpleNamespace(key="original"))
        def load():
            state.configuration_change_pending = pending
            state.scenario_manager.current.key = "loaded"
        state.load_startup_config = effect("load_startup_config", mutation=load)
        registry.get = effect("registry.get", "elimination-module")
        state.registry = registry
        state.config_store = SimpleNamespace(ensure_default=effect("ensure_default"))
        state.kill_fx = SimpleNamespace(preload_preset_sounds=effect("preload", "unused"))
        state.setting_mode = effect("setting_mode", "saved-preset")
        state._startup_navigation = SimpleNamespace(apply_autostart=effect(
            "autostart", mutation=lambda: setattr(state, "home_visible", False if autostart == "1" else visible)))
        state.backend = SimpleNamespace(request_scenario=effect("request_scenario", 57))
        scope = dict(globals(), self=state, os=SimpleNamespace(environ={"POLAR_AUTOSTART": autostart}),
                     chinese_enabled=lambda registry: True, ui_text=lambda value, chinese: value)
        try:
            exec(code, scope)
        except RuntimeError as caught:
            self.assertIs(caught, failure)
            return trace, True
        return trace, False

    def test_real_adapter_arguments_and_every_failure_prefix_match_reference(self):
        production = adapter_code()
        for visible, pending, autostart in ((False, False, "0"), (True, False, "0"),
                                            (True, False, "1"), (True, True, "1")):
            options = dict(visible=visible, pending=pending, autostart=autostart)
            expected = self.execute(REFERENCE, **options)
            self.assertEqual(self.execute(production, **options), expected)
            for name, *_ in expected[0]:
                with self.subTest(visible=visible, pending=pending, autostart=autostart, step=name):
                    self.assertEqual(self.execute(production, fail_at=name, **options),
                                     self.execute(REFERENCE, fail_at=name, **options))


    def test_attribute_lookup_order_and_every_lookup_failure_match_reference(self):
        def execute(code, fail_at=None):
            trace = []
            failure = RuntimeError("lookup failed")

            class Value:
                def __init__(self, path):
                    self.path = path

                def __getattr__(self, name):
                    path = self.path + "." + name
                    trace.append(("get", path))
                    if path == fail_at:
                        raise failure
                    return Value(path)

                def __call__(self, *args, **kwargs):
                    trace.append(("call", self.path))
                    return Value(self.path + "()")

                def __bool__(self):
                    trace.append(("bool", self.path))
                    return False

            scope = dict(globals(), self=Value("self"), os=Value("os"),
                         ui_text=lambda *args: None, chinese_enabled=lambda *args: False)
            try:
                exec(code, scope)
            except RuntimeError as caught:
                self.assertIs(caught, failure)
                trace.append(("failed", fail_at))
            return trace

        production = adapter_code()
        expected = execute(REFERENCE)
        self.assertEqual(execute(production), expected)
        for operation, path in expected:
            if operation == "get":
                with self.subTest(lookup=path):
                    self.assertEqual(execute(production, path), execute(REFERENCE, path))

    def test_reentrant_callback_replacement_is_observed_at_its_original_step(self):
        # Loading a profile (or an earlier callback) can update a bound adapter.
        # Ports must resolve it when used, not snapshot it at assembly time.
        production = adapter_code()
        for code in (REFERENCE, production):
            trace = []
            host = SimpleNamespace()
            def sync():
                trace.append("sync")
                host.refresh_config_profiles = lambda: trace.append("new-refresh")
            host.sync_quick_action_wheel_settings = sync
            host.apply_match_mode = lambda *args, **kwargs: None
            host.apply_robot_profile = lambda *args, **kwargs: None
            host.current_robot_profile_key = lambda: "robot"
            host.match_mode_manager = SimpleNamespace(current=SimpleNamespace(key="match"))
            host.refresh_config_profiles = lambda: trace.append("old-refresh")
            def stop():
                raise RuntimeError("bounded probe complete")
            host.refresh_wallpaper_profiles = stop
            with self.assertRaisesRegex(RuntimeError, "bounded probe complete"):
                exec(code, dict(globals(), self=host))
            self.assertEqual(trace, ["sync", "new-refresh"])


if __name__ == "__main__":
    unittest.main()
