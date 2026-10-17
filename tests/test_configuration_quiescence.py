"""Configuration adapters stop domain mutation on failed input quiescence."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.configuration.quiescence import quiesce_configuration_inputs
from rm_client.control import KeyboardControlBridge
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.polar.mixins.persistence_mixin import PersistenceMixin


class Host(PersistenceMixin):
    def __init__(self, failures):
        self.registry = build_default_registry()
        self.state = RobotState()
        self.state.robot_id = 1  # Switching to Infantry1 must quiesce identity.
        self.trace = []
        self.errors = {name: RuntimeError(name + " failed") for name in failures}
        def action(name):
            def call(*args, **kwargs):
                self.trace.append(name)
                if name in self.errors:
                    raise self.errors[name]
            return call
        self.control = SimpleNamespace(
            binds=dict(KeyboardControlBridge().binds), mock_output=False,
            release_all=action("release"), clear_pending=action("clear"), set_binds=Mock())
        self.backend = SimpleNamespace(lock_control=action("lock"),
            mqtt=SimpleNamespace(client_id="1", mock_output=True), set_client_id=Mock())
        self.match_mode_manager = SimpleNamespace(current=SimpleNamespace(key="RMUC7v7"), set_mode=Mock())
        self.reject_pending_configuration_edit = lambda: False
        self.current_robot_performance = lambda key: None
        self.get_setting_number = lambda *args: 1
        self.alliance_side = lambda: "Red"
        self.setting_bool = lambda module, name, default: default
        self.publish_spin_control_state = Mock()
        self.config_loading = False

    def invoke(self, caller):
        if caller == "match":
            self.apply_match_mode("RMUL3v3", close_home=False)
        elif caller == "robot":
            self.apply_robot_profile("Infantry1", close_home=False)
        elif caller == "binds":
            module = self.registry.get("RobotLink")
            module.get_setting("Bind W").value = "S"
            module.get_setting("Bind S").value = "W"
            self.sync_control_binds()
        else:
            self._quiesce_config_reload({})


class ConfigurationQuiescenceTests(unittest.TestCase):
    def test_each_caller_clears_pending_after_lock_or_release_failure(self):
        for caller in ("match", "robot", "binds", "reload"):
            for fault in ("lock", "release"):
                with self.subTest(caller=caller, fault=fault):
                    host = Host({fault})
                    with self.assertRaises(RuntimeError) as caught:
                        host.invoke(caller)
                    self.assertIs(caught.exception, host.errors[fault])
                    expected = ["lock", "clear"] if fault == "lock" else ["lock", "release", "clear"]
                    self.assertEqual(host.trace, expected)
                    host.match_mode_manager.set_mode.assert_not_called()
                    host.backend.set_client_id.assert_not_called()
                    host.control.set_binds.assert_not_called()

    def test_cleanup_failure_preserves_primary_failure_and_reports_secondary(self):
        for caller in ("match", "robot", "binds", "reload"):
            for fault in ("lock", "release"):
                with self.subTest(caller=caller, fault=fault):
                    host = Host({fault, "clear"})
                    with self.assertRaises(RuntimeError) as caught:
                        host.invoke(caller)
                    self.assertIs(caught.exception, host.errors[fault])
                    self.assertIn("clear failed", " ".join(caught.exception.__notes__))
                    self.assertEqual(host.trace[-1], "clear")
                    host.match_mode_manager.set_mode.assert_not_called()
                    host.backend.set_client_id.assert_not_called()
                    host.control.set_binds.assert_not_called()

    def test_clear_failure_alone_still_prevents_domain_mutation(self):
        for caller in ("match", "robot", "binds", "reload"):
            with self.subTest(caller=caller):
                host = Host({"clear"})
                with self.assertRaises(RuntimeError) as caught:
                    host.invoke(caller)
                self.assertIs(caught.exception, host.errors["clear"])
                self.assertEqual(host.trace, ["lock", "release", "clear"])
                host.match_mode_manager.set_mode.assert_not_called()
                host.backend.set_client_id.assert_not_called()
                host.control.set_binds.assert_not_called()


class QuiescencePolicyTests(unittest.TestCase):
    def test_success_keeps_order_and_returns_only_after_pending_clear(self):
        trace = []
        quiesce_configuration_inputs(revoke=lambda: trace.append("lock"),
            release=lambda: trace.append("release"), clear_pending=lambda: trace.append("clear"))
        self.assertEqual(trace, ["lock", "release", "clear"])

    def test_broken_diagnostic_sinks_cannot_replace_the_primary_error(self):
        class Failure(RuntimeError):
            def add_note(self, note):
                raise OSError("notes failed")
        primary = Failure("release failed")
        with patch("rm_client.configuration.quiescence._LOG.error", side_effect=OSError("logging failed")):
            with self.assertRaises(Failure) as caught:
                quiesce_configuration_inputs(revoke=lambda: None,
                    release=Mock(side_effect=primary), clear_pending=Mock(side_effect=ValueError("clear failed")))
        self.assertIs(caught.exception, primary)

    def test_spin_falling_edge_failure_keeps_its_pre_quiescence_prefix(self):
        host = Host(set())
        host.registry.get("SpinMode").enabled = True
        failure = RuntimeError("spin publication failed")
        host.publish_spin_control_state.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            host._quiesce_config_reload({"modules": {"SpinMode": {"enabled": False}}})
        self.assertIs(caught.exception, failure)
        self.assertEqual(host.trace, [])

    def test_identity_mock_mode_lookup_failure_still_clears_pending(self):
        host = Host(set())
        class Mqtt:
            client_id = "1"
            @property
            def mock_output(self):
                raise ValueError("mode unavailable")
        host.backend.mqtt = Mqtt()
        with self.assertRaisesRegex(ValueError, "mode unavailable"):
            host.invoke("robot")
        self.assertEqual(host.trace, ["lock", "clear"])
        host.backend.set_client_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()
