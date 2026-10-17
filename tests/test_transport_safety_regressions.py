"""Offline transport regressions. No devices, listeners, subprocesses, or app startup.

Manager/UI-facing methods are AST-isolated so their real implementation can be
exercised without constructing the application or loading optional GUI drivers.
"""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.backend.cc_link import CustomControllerLink, decode_cc_frame
from rm_client.backend.device_egress_settings import CustomControllerSettings, EgressPermissions
from rm_client.backend.polar_transport import PolarDriveIntent, PolarLinkTransport
from rm_client.backend import polar_link, polar_spec
from rm_client.backend.referee.registry import get_profile, UnsupportedRefereeProfile
from rm_client.backend.referee.rm2026.codec import encode_map_click_cmd
from rm_client.backend.referee.wire import decode_fields
from rm_client.backend.safety import OfficialSafety
from rm_client.backend.serial_stream import TcpByteStream
from rm_client.transports import FrameAssembler


def isolated(path, name, *, owner=None, namespace=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = tree.body
    if owner:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = copy.deepcopy(next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name))
    if isinstance(node, ast.FunctionDef):
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    env = dict(namespace or {})
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), "exec"), env)
    return env[name]


AnnexBRecoveryGate = isolated("rm_client/backend/video_backend.py", "AnnexBRecoveryGate")
Bridge = isolated("tools/car_bridge/bridge_server.py", "Bridge", namespace={
    "socket": socket, "threading": threading, "time": time, "READ_SIZE": 4096,
})


def manager_method(name, **namespace):
    return isolated("rm_client/backend/manager.py", name, owner="BackendManager", namespace=namespace)


class FakeSerial:
    def __init__(self):
        self.written = []
        self.is_open = True
        self.closed_after = []

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size):
        return b""

    def close(self):
        self.closed_after = list(self.written)
        self.is_open = False


class TransportSafetyTests(unittest.TestCase):
    def manager_runtime(self):
        """Real lifecycle boundary with no sockets, devices, Qt, or git probe."""
        from rm_client.backend.manager import BackendManager
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        with patch.dict(os.environ, {"POLAR_USER_DATA_ROOT": root.name}), patch.object(
                BackendManager, "_build_commit", return_value="TEST"):
            backend = BackendManager(workspace=root.name)
        self.assertTrue(backend.field_log.close(timeout=1))
        backend.field_log = SimpleNamespace(emit=lambda *a, **k: None,
            close=lambda **k: True, snapshot=lambda: {
                "pending_records": 0, "dropped_records": 0, "write_errors": 0,
                "closed": True, "worker_active": False, "closing": False})
        self.addCleanup(backend.shutdown)
        return backend

    def make_transport(self):
        transport = PolarLinkTransport(port="TEST", mock_writes=False)
        serial = FakeSerial()
        transport._serial, transport.connected = serial, True
        return transport, serial

    def test_estop_is_immediate_and_no_nonzero_afterwards(self):
        transport, serial = self.make_transport()
        transport.submit(PolarDriveIntent(vx=1.0))
        transport.trigger_estop()
        # No manual pump is allowed to hide a broken synchronous stop path.
        frames = [f for f in serial.written if f[2] == 1]
        self.assertEqual(len(frames), 1)
        self.assertEqual(struct.unpack_from("<fff", frames[0], 8), (0.0, 0.0, 0.0))
        count = len(serial.written)
        transport._write(transport._frame_for(PolarDriveIntent(vx=2.0)))
        self.assertEqual(len(serial.written), count)
        self.assertEqual(transport.zero_frames_sent, 1)

    def test_stop_writes_neutral_before_close(self):
        transport, serial = self.make_transport()
        transport.submit(PolarDriveIntent(vx=1.0))
        transport.stop()
        self.assertFalse(serial.is_open)
        self.assertTrue(transport._estop_active)
        frames = [f for f in serial.closed_after if f[2] == 1]
        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(struct.unpack_from("<fff", f, 8) == (0.0, 0.0, 0.0) for f in frames))

    def test_rc_releases_active_writer_once_but_never_seizes_idle_rc(self):
        transport, serial = self.make_transport()
        transport.drive_channel = "both"
        transport.trigger_estop()
        self.assertNotIn(3, [f[2] for f in serial.written])
        transport.release_estop()
        transport._zeros_until = 0
        transport.submit(PolarDriveIntent(key_value=1))
        transport._pump_tx(time.perf_counter())
        serial.written.clear()
        transport.trigger_estop()
        self.assertEqual([f[2] for f in serial.written].count(3), 1)
        for _ in range(4):
            transport._pump_tx(time.perf_counter())
        self.assertEqual([f[2] for f in serial.written].count(3), 1)
        rc = next(f for f in serial.written if f[2] == 3)
        self.assertEqual(struct.unpack_from("<H", rc, 28)[0], 0)

    def test_tcp_no_data_does_not_enter_blocking_recv(self):
        sock = SimpleNamespace(recv=lambda n: self.fail("no-data read attempted recv"))
        stream = TcpByteStream("unused", 1)
        stream._sock, stream.is_open = sock, True
        with patch("rm_client.backend.serial_stream.select.select", return_value=([], [], [])):
            self.assertEqual(stream.read(), b"")
        self.assertTrue(stream.is_open)

    def test_tcp_eof_closes_and_is_not_timeout(self):
        sock = SimpleNamespace(recv=lambda n: b"", shutdown=lambda x: None, close=lambda: None)
        stream = TcpByteStream("unused", 1)
        stream._sock, stream.is_open = sock, True
        with patch("rm_client.backend.serial_stream.select.select", return_value=([sock], [], [])):
            with self.assertRaises(ConnectionError):
                stream.read()
        self.assertFalse(stream.is_open)
        self.assertIsNone(stream._sock)

    def test_transport_eof_ends_epoch_and_discards_old_intent(self):
        transport, serial = self.make_transport()
        transport._running = True
        transport.submit(PolarDriveIntent(vx=1.0))
        transport._rx_buffer.extend(b"old")
        transport._drain_rx = lambda: (_ for _ in ()).throw(ConnectionError("EOF"))
        transport._loop()
        self.assertFalse(transport._running)
        self.assertFalse(transport.connected)
        self.assertIsNone(transport._serial)
        self.assertIsNone(transport._intent)
        self.assertEqual(transport._rx_buffer, b"")
        self.assertFalse(serial.is_open)

    def test_bridge_timeout_then_eof_returns_without_busy_loop(self):
        events = iter([socket.timeout(), b"abc", b""])
        calls = []
        def recv(size):
            calls.append(size)
            value = next(events)
            if isinstance(value, Exception):
                raise value
            return value
        connection = SimpleNamespace(recv=recv, settimeout=lambda n: None, sendall=lambda b: None)
        serial = FakeSerial()
        bridge = Bridge(serial_device="fake", log=lambda *a: None)
        bridge._pump(connection, serial)
        self.assertEqual(len(calls), 3)
        self.assertEqual(serial.written, [b"abc"])
        self.assertEqual(bridge.bytes_client_to_serial, 3)

    def test_cc_release_bypasses_limiter_and_preserves_cursor(self):
        frames = []
        link = CustomControllerLink(mock_output=False, write_bytes=frames.append)
        self.assertTrue(link.send_keymouse(pressed=["W"], dx=17, now=1.0)[0])
        xy = (link.cursor.x, link.cursor.y)
        self.assertTrue(link.release_all(now=1.001)[0])
        self.assertTrue(link.wire_neutral)
        self.assertEqual((link.cursor.x, link.cursor.y), xy)
        self.assertEqual(decode_cc_frame(frames[-1])["data"][:2], b"\0\0")
        self.assertFalse(link.send_keymouse(pressed=["W"], now=1.002)[0])
        self.assertEqual(len(frames), 2)

    def test_cc_ordinary_key_up_uses_neutral_exception(self):
        link = CustomControllerLink(mock_output=True)
        self.assertTrue(link.send_keymouse(pressed=["W"], now=1.0)[0])
        self.assertTrue(link.send_keymouse(pressed=[], now=1.001)[0])
        self.assertTrue(link.wire_neutral)

    def test_cc_short_release_is_not_success(self):
        link = CustomControllerLink(mock_output=False, write_bytes=lambda frame: 1)
        self.assertFalse(link.release_all(now=1.0)[0])
        self.assertFalse(link.wire_neutral)

    def test_cc_all_disable_edges_release(self):
        configure = manager_method("_configure_cc_egress")
        for edge in ("estop", "allow_drive", "module", "egress"):
            with self.subTest(edge=edge):
                released = []
                control = SimpleNamespace(cc_enabled=True, cc_release_pending=False,
                    release_custom_controller=lambda: released.append(True))
                link = SimpleNamespace()
                manager = SimpleNamespace(_cc_was_enabled=True, _cc_link_for=lambda settings: link)
                settings = CustomControllerSettings(requested_value=edge != "egress")
                permissions = EgressPermissions(edge != "module", edge != "allow_drive", edge == "estop")
                configure(manager, control, settings, permissions)
                self.assertFalse(control.cc_enabled)
                self.assertEqual(released, [True])

    def test_cc_failed_release_stays_pending_then_clears(self):
        release = isolated("rm_client/control.py", "release_custom_controller", owner="KeyboardControlBridge")
        outcomes = iter([(False, "offline"), (True, "ok")])
        control = SimpleNamespace(cc_link=SimpleNamespace(release_all=lambda: next(outcomes)), cc_release_pending=False)
        release(control)
        self.assertTrue(control.cc_release_pending)
        release(control)
        self.assertFalse(control.cc_release_pending)

    def test_cc_reconfigure_retains_old_target_if_release_failed(self):
        link = SimpleNamespace(port="OLD", max_hz=30, mock_output=False, wire_neutral=False,
                               release_all=lambda: (False, "offline"))
        locks = []
        manager = SimpleNamespace(_cc_link=link, safety=SimpleNamespace(lock_control=locks.append))
        settings = CustomControllerSettings(port_value="NEW", mock_value=False)
        method = manager_method("_cc_link_for", cc_link=SimpleNamespace(CustomControllerLink=CustomControllerLink))
        self.assertIs(method(manager, settings), link)
        self.assertTrue(manager._cc_reconfigure_blocked)
        self.assertTrue(locks)

    def test_annexb_every_single_split_and_bytewise_for_both_codecs(self):
        for codec, stream in (
            ("h264", b"\0\0\0\1\x67sps\0\0\1\x68pps\0\0\0\1\x65idr"),
            ("h265", b"\0\0\1\x40\x01vps\0\0\0\1\x42\x01sps\0\0\1\x44\x01pps\0\0\0\1\x26\x01idr"),
        ):
            for split in range(1, len(stream)):
                gate = AnnexBRecoveryGate(codec)
                outputs = [gate.feed(stream[:split]), gate.feed(stream[split:])]
                self.assertEqual(b"".join(x for x in outputs if x is not None), stream, (codec, split))
            gate = AnnexBRecoveryGate(codec)
            outputs = [gate.feed(bytes([value])) for value in stream]
            self.assertEqual(b"".join(x for x in outputs if x is not None), stream)

    def test_annexb_missing_parameters_and_oversize_stay_blocked(self):
        gate = AnnexBRecoveryGate("h264")
        self.assertIsNone(gate.feed(b"\0\0\1\x65idr"))
        self.assertIsNone(gate.feed(b"\0\0\1\x67" + b"x" * (gate.PARAMETER_LIMIT + 10)))
        self.assertLessEqual(len(gate._pending_parameter), gate.PARAMETER_LIMIT)
        self.assertIsNone(gate.feed(b"\0\0\1\x68pps\0\0\1\x65idr"))
        self.assertTrue(gate.blocked)

    def test_decimal_segment_ids_share_validated_inputs(self):
        base = json.loads(polar_spec.SPEC_PATH.read_text())
        for value in (42, "42", "0x2A"):
            raw = copy.deepcopy(base)
            raw["downlink_segments"].append({"id": value, "name": "Extension", "struct": "<f",
                "inputs": ["value"], "outputs": [{"name": "reading", "from": "value"}]})
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "spec.json"
                path.write_text(json.dumps(raw))
                spec = polar_spec.load_spec(path)
            self.assertTrue(spec.ok, spec.error_text())
            self.assertEqual(spec.segment_inputs[42], ["value"])
            old = dict(polar_link._SEGMENT_FORMATS)
            try:
                with patch.object(polar_spec, "get_spec", return_value=spec):
                    outputs, inputs = polar_link._apply_spec()
                self.assertEqual(inputs[42], ["value"])
                self.assertEqual(outputs[42][0]["from"], "value")
            finally:
                polar_link._SEGMENT_FORMATS.clear()
                polar_link._SEGMENT_FORMATS.update(old)

    @staticmethod
    def packet(frame_id, index=0, total=1, data=b"x"):
        return struct.pack(">HHI", frame_id, index, total) + data

    def test_udp_out_of_order_completion_and_duplicates_are_discarded(self):
        assembler = FrameAssembler()
        self.assertIsNone(assembler.push_packet(self.packet(10, 0, 2, b"a")))
        self.assertEqual(assembler.push_packet(self.packet(11)), b"x")
        self.assertIsNone(assembler.push_packet(self.packet(10, 1, 2, b"b")))
        self.assertIsNone(assembler.push_packet(self.packet(11)))
        self.assertEqual(assembler.buffered_bytes, 0)
        self.assertEqual(assembler.stale_packet_count, 2)

    def test_udp_wrap_restart_and_sender_reset_recover(self):
        assembler = FrameAssembler()
        with patch("rm_client.transports.time.monotonic", return_value=1.0):
            for number in (65534, 65535, 0, 1):
                self.assertEqual(assembler.push_packet(self.packet(number)), b"x")
            self.assertEqual(assembler.sequence_discontinuities, 0)
            self.assertIsNone(assembler.push_packet(self.packet(0)))
        # Restarting the sender's counter cannot wedge the receiver forever:
        # a bounded 2 s no-progress interval starts a recovery-only epoch.
        with patch("rm_client.transports.time.monotonic", return_value=3.01):
            self.assertEqual(assembler.push_packet(self.packet(0)), b"x")
            self.assertEqual(assembler.sequence_discontinuities, 1)
        assembler.clear()
        self.assertEqual(assembler.push_packet(self.packet(0)), b"x")

    def test_udp_gap_is_reported_for_decoder_recovery(self):
        assembler = FrameAssembler()
        assembler.push_packet(self.packet(5))
        assembler.push_packet(self.packet(7))
        self.assertEqual(assembler.sequence_discontinuities, 1)

    def test_map_wire_semantics_and_conservative_interval(self):
        fields = decode_fields(encode_map_click_cmd(mode=1, mark_type=3))
        self.assertEqual(fields[3][0], 3)
        self.assertEqual(fields[6][0], 1)
        safety = OfficialSafety()
        safety.configure(mock_writes=False, allow_mqtt_publish=True, enforce_official_limits=True)
        safety.configure_referee_limits(get_profile().uplink_hz)
        safety.arm_match(["core_kmc"])
        safety.update_stage(4)
        safety.mark_published("MapClickCmd", now=1.0)
        self.assertFalse(safety.can_publish("MapClickCmd", now=1.2)[0])
        self.assertFalse(safety.can_publish("MapClickCmd", now=3.999)[0])
        self.assertTrue(safety.can_publish("MapClickCmd", now=4.0)[0])

    def test_map_downlink_category_uses_official_field_three(self):
        from rm_client.backend.referee.rm2026.state_adapter import Rm2026StateAdapter
        from rm_client.model import RobotState
        state = RobotState()
        adapter = Rm2026StateAdapter(SimpleNamespace())
        for category, expected in ((1, "attack"), (2, "defense"), (3, "warning"), (4, "custom")):
            adapter._apply_one(state, "MapClickInfo", {"mode": category, "type": 1, "map_x": 1.0, "map_y": 2.0})
            self.assertEqual(state.map_markers[-1]["kind"], expected)

    def test_video_gap_resets_gate_before_feeding_more_interframes(self):
        callback = isolated("rm_client/backend/video_backend.py", "_on_assembled_frame",
            owner="RmVideoBackend", namespace={"time": time,
            "detect_frame_codec": isolated("rm_client/backend/video_backend.py", "detect_frame_codec")})
        queued = []
        backend = SimpleNamespace(_lock=threading.Lock(), _assembler=SimpleNamespace(sequence_discontinuities=0),
            _udp_sequence_discontinuities=0, h265_recovery=AnnexBRecoveryGate("h265"), codec="unknown",
            _frame_times=[], _bytes_this_second=0, _last_stats_at=time.perf_counter(), dropped_frames=0,
            decode_h265=True, external_on_frame=None,
            h265_decoder=SimpleNamespace(restart_count=1, last_error="", available=True,
                                         write=lambda data: queued.append(data) or True))
        gop = b"\0\0\1\x40\x01vps\0\0\1\x42\x01sps\0\0\1\x44\x01pps\0\0\1\x26\x01idr"
        callback(backend, gop)
        self.assertEqual(len(queued), 1)
        backend._assembler.sequence_discontinuities = 1
        callback(backend, b"\0\0\1\x02\x01slice")
        self.assertEqual(len(queued), 1)
        self.assertTrue(backend.h265_recovery.blocked)
        callback(backend, gop)
        self.assertEqual(len(queued), 2)

    def test_registry_endpoints_are_copied_without_mutating_scenario(self):
        from dataclasses import replace
        from rm_client.scenarios import SCENARIOS
        method = manager_method("_config_from_registry", replace=replace, PORT_DEFAULT="Auto", BAUD_DEFAULT=115200)
        settings = {
            "MQTTLink": {"Host": "10.0.0.10", "Port": 4444},
            "UDPVideo": {"Host": "127.0.0.2", "Port": 5555},
            "SerialBridge": {"Port": "TEST", "Baud": 57600},
            "CameraFeed": {"Source": "Native 0x0310"},
            "RobotLink": {"Port": "ROBOT", "Baud": 230400},
        }
        registry = {key: SimpleNamespace(get_setting=lambda name, values=values:
                    SimpleNamespace(value=values[name]) if name in values else None)
                    for key, values in settings.items()}
        baseline = SCENARIOS["OfficialEvent"]
        configured = method(baseline, registry)
        self.assertEqual(configured.connection.mqtt_host, "10.0.0.10")
        self.assertEqual(configured.connection.udp_port, 5555)
        self.assertEqual(configured.connection.polar_port, "ROBOT")
        self.assertEqual(configured.connection.serial_baud, 57600)
        self.assertEqual(configured.connection.video_source, "Native 0x0310")
        self.assertEqual(baseline.connection.mqtt_host, "192.168.12.1")
        self.assertIs(configured.safety, baseline.safety)

    def test_scenario_sets_identity_and_endpoints_before_any_start(self):
        from rm_client.scenarios import SCENARIOS
        backend = self.manager_runtime()
        starts = []
        for name in ("mqtt", "udp", "serial", "polar"):
            resource = getattr(backend, name)
            callback = lambda name=name: starts.append((name, backend.mqtt.client_id,
                backend.mqtt.host, backend.scenario_key, backend.safety.control_state.value))
            setattr(resource, "request_start" if name == "polar" else "start", callback)
        backend.apply_scenario("OfficialEvent", robot_id=103)
        self.assertEqual({item[0] for item in starts}, {"mqtt", "udp", "serial", "polar"})
        self.assertTrue(all(item[1:] == ("103", SCENARIOS["OfficialEvent"].connection.mqtt_host,
                                       "OfficialEvent", "LOCKED") for item in starts))
        starts.clear()
        backend.apply_scenario("OfficialEvent", start=False)
        self.assertFalse(starts)

    def test_identity_failures_remain_locked(self):
        backend = self.manager_runtime()
        safety = backend.safety
        safety.arm_match(["core_kmc"])
        stopped = []
        backend.mqtt.stop = lambda **kwargs: stopped.append(kwargs)
        with self.assertRaises(ValueError):
            backend.set_client_id(999)
        self.assertEqual(safety.control_state.value, "LOCKED")
        self.assertEqual(stopped, [{"publish_status": False, "wait": False}])
        safety.arm_match(["core_kmc"])
        backend.mqtt.publish_estop_zero = lambda: (_ for _ in ()).throw(OSError("publish failed"))
        with self.assertRaises(OSError):
            backend.set_client_id(103)
        self.assertEqual(safety.control_state.value, "LOCKED")

    def test_cc_pending_release_never_replays_motion(self):
        send = isolated("rm_client/control.py", "_send_custom_controller", owner="KeyboardControlBridge")
        releases = []
        control = SimpleNamespace(cc_release_pending=True, cc_enabled=True,
            release_custom_controller=lambda: releases.append(True),
            cc_link=SimpleNamespace(send_keymouse=lambda **kwargs: self.fail("replayed movement")))
        send(control, SimpleNamespace(keyboard_value=1))
        self.assertEqual(releases, [True])

    def test_polar_short_release_does_not_claim_neutral(self):
        transport, serial = self.make_transport()
        transport.drive_channel = "both"
        transport._drive_was_armed = True
        serial.write = lambda payload: 1
        transport._flush_zero_burst(blocking=False)
        self.assertTrue(transport._drive_was_armed)
        self.assertFalse(transport.connected)

    def test_epoch_reset_clears_old_telemetry_and_input(self):
        events = []
        backend = SimpleNamespace(safety=SimpleNamespace(lock_control=lambda reason: events.append("lock")),
            polar_bridge=SimpleNamespace(reset=lambda: events.append("telemetry")),
            polar=SimpleNamespace(request_estop=lambda: events.append("intent")),
            _polar_gimbal_last=(1, 2), polar_chassis_state_seen=True)
        manager_method("_invalidate_polar_epoch")(backend, "changed")
        self.assertEqual(events, ["lock", "telemetry", "intent"])
        self.assertIsNone(backend._polar_gimbal_last)
        self.assertFalse(backend.polar_chassis_state_seen)

    def test_tcp_retry_works_with_stub_serial_port_and_resets_epoch(self):
        events = []
        polar = SimpleNamespace(port="Stub", transport="tcp", host="configured", connected=False,
            _running=False, resolved_port="tcp://configured:50570", send_hz=50, last_error="")
        def start():
            events.append("start")
            polar.connected = True
        polar.request_start = start
        backend = SimpleNamespace(polar=polar, _polar_last_connect_attempt=0.0,
            _invalidate_polar_epoch=lambda reason: events.append("reset"),
            field_log=SimpleNamespace(emit=lambda *args, **kwargs: None))
        manager_method("_retry_polar_connect", time=SimpleNamespace(perf_counter=lambda: 10.0), POLAR_RETRY_S=2.0)(backend)
        self.assertEqual(events, ["reset", "start"])

    def test_estop_latches_all_local_outputs_even_when_network_raises(self):
        safety = OfficialSafety()
        calls = []
        backend = SimpleNamespace(safety=safety, _km_seq=1,
            mqtt=SimpleNamespace(publish_estop_zero=lambda: (_ for _ in ()).throw(OSError("MQTT unavailable"))),
            publish_km_release=lambda: (_ for _ in ()).throw(OSError("KM unavailable")),
            serial=SimpleNamespace(request_estop=lambda: calls.append("serial")),
            polar=SimpleNamespace(request_estop=lambda: calls.append("polar")),
            field_log=SimpleNamespace(emit=lambda *args, **kwargs: None))
        result = manager_method("trigger_estop", time=time)(backend)
        self.assertFalse(result[0])
        self.assertTrue(safety.estop_active)
        self.assertEqual(calls, ["serial", "polar"])

    def test_old_w_snapshot_cannot_return_after_new_telemetry(self):
        keys = {"W": True}
        control = SimpleNamespace(last_command=SimpleNamespace(keyboard_value=1), last_payload=b"old W",
            reset_inputs=lambda: keys.update(W=False), clear_pending=lambda: None)
        safety = OfficialSafety()
        bridge = SimpleNamespace(active=True)
        bridge.reset = lambda: setattr(bridge, "active", False)
        backend = self.manager_runtime()
        backend._dt7_control_ref = control
        backend.polar_bridge = bridge
        safety = backend.safety
        backend._invalidate_polar_epoch("endpoint changed")
        bridge.active = True  # First fresh telemetry on the new connection.
        self.assertFalse(keys["W"])
        self.assertIsNone(control.last_command)
        self.assertEqual(control.last_payload, b"")
        self.assertEqual(safety.control_state.value, "LOCKED")

    def test_scenario_reload_preserves_preexisting_operator_estop(self):
        backend = self.manager_runtime()
        backend.safety.trigger_estop()
        backend.apply_scenario("LabDebug", start=False)
        self.assertTrue(backend.safety.estop_active)

    def test_missing_opencv_is_visible_and_never_claims_decoder_ready(self):
        import builtins
        original_import = builtins.__import__
        def missing_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ModuleNotFoundError("test: cv2 is unavailable")
            return original_import(name, *args, **kwargs)
        start = isolated("rm_client/backend/video_backend.py", "start", owner="ContinuousMjpegDecoder")
        decoder = SimpleNamespace(_running=False, _proc=None, available=True, last_error="")
        with patch("builtins.__import__", side_effect=missing_cv2):
            self.assertFalse(start(decoder))
        self.assertFalse(decoder.available)
        self.assertIn("cv2", decoder.dependency_error)
        self.assertEqual(decoder.last_error, decoder.dependency_error)

    def test_shutdown_network_exception_still_zeros_and_closes_every_output(self):
        backend = self.manager_runtime()
        calls = []
        backend.mqtt.connected, backend.mqtt.mock_output = True, False
        backend.mqtt.publish_estop_zero = lambda: (_ for _ in ()).throw(OSError("MQTT unavailable"))
        for label, resource, method in (
                ("mqtt stop", backend.mqtt, "stop"),
                ("serial zero", backend.serial, "request_estop"),
                ("serial stop", backend.serial, "stop"),
                ("polar zero", backend.polar, "request_estop"),
                ("polar stop", backend.polar, "stop"),
                ("udp stop", backend.udp, "stop"),
                ("decoder stop", backend.native_decoder, "stop")):
            original = getattr(resource, method)
            def record(*args, label=label, original=original, **kwargs):
                calls.append(label)
                return original(*args, **kwargs)
            setattr(resource, method, record)
        backend.shutdown()
        self.assertTrue(backend.safety.estop_active)
        self.assertLess(calls.index("polar zero"), calls.index("polar stop"))
        self.assertLess(calls.index("serial zero"), calls.index("serial stop"))
        self.assertEqual(set(calls), {"serial zero", "polar zero", "mqtt stop", "serial stop",
                                     "polar stop", "udp stop", "decoder stop"})
        self.assertIn("MQTT unavailable", backend.lifecycle_snapshot.failures[0].error)

    def test_v2_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(UnsupportedRefereeProfile, "NOT supported"):
            get_profile("rm2026-v2.0.0")

    def test_cc_shipped_key_table_matches_documented_hid_values(self):
        from rm_client.backend.cc_link import HID_USAGE, load_key_usage, keys_to_two_usages
        raw = json.loads((ROOT / "configs/cc_key_usage.json").read_text())
        for name, value in raw.items():
            if not name.startswith("_"):
                self.assertEqual(value, HID_USAGE[name.upper()], name)
        self.assertEqual(keys_to_two_usages(["V"], load_key_usage())[0], 25)
        self.assertEqual(keys_to_two_usages(["S"], load_key_usage())[0], 22)


if __name__ == "__main__":
    unittest.main()

