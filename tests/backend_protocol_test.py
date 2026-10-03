"""后端对齐测试：协议编解码往返 / 三模式安全门控 / UDP codec 检测 / 状态应用。

运行：
    .\\.venv\\Scripts\\python.exe tests\\backend_protocol_test.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rm_client.backend import protocol as proto  # noqa: E402
from rm_client.backend.manager import BackendManager  # noqa: E402
from rm_client.backend.safety import OfficialSafety  # noqa: E402
from rm_client.backend.video_backend import (  # noqa: E402
    ContinuousMjpegDecoder,
    RmVideoBackend,
    detect_frame_codec,
    resolve_ffmpeg_executable,
)
from rm_client.backend.serial_backend import RmSerialBackend  # noqa: E402
from rm_client.backend.mqtt_backend import RmMqttBackend  # noqa: E402
from rm_client.model import RobotState, build_default_registry  # noqa: E402
from rm_client.native_viewer_telemetry import find_h264_sps_offset  # noqa: E402
from rm_client.scenarios import SCENARIOS  # noqa: E402

PASS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS
    if not condition:
        raise AssertionError(f"FAIL {name}: {detail}")
    PASS += 1
    print(f"  ok {name}")


def test_uplink_roundtrip() -> None:
    print("[protocol] uplink roundtrip")
    payload = proto.encode_robot_performance_selection_command(shooter=3, chassis=4, sentry_control=0)
    fields = proto.decode_fields(payload)
    check("perf shooter=3", proto._u(fields, 1) == 3)
    check("perf chassis=4", proto._u(fields, 2) == 4)

    payload = proto.encode_dart_command(target_id=5, open_gate=True, launch_confirm=False)
    fields = proto.decode_fields(payload)
    check("dart target=5", proto._u(fields, 1) == 5)
    check("dart open", proto._bool(fields, 2) is True)
    check("dart confirm", proto._bool(fields, 3) is False)

    payload = proto.encode_map_click_cmd(
        is_send_all=1, robot_id=bytes([1, 3]), mode=2, enemy_id=103,
        ascii_code=65, mark_type=3, map_x=12.5, map_y=7.25,
    )
    fields = proto.decode_fields(payload)
    check("map is_send_all", proto._u(fields, 1) == 1)
    check("map robot_id fixed 7 bytes", proto._b(fields, 2) == bytes([1, 3, 0, 0, 0, 0, 0]))
    check("map mode", proto._u(fields, 3) == 2)
    check("map enemy", proto._u(fields, 4) == 103)
    check("map ascii", proto._u(fields, 5) == 65)
    check("map type", proto._u(fields, 6) == 3)
    check("map x", abs(proto._f(fields, 7) - 12.5) < 1e-4)
    check("map y", abs(proto._f(fields, 8) - 7.25) < 1e-4)

    payload = proto.encode_keyboard_mouse_control(
        mouse_x=100, mouse_y=-200, left_button_down=True, keyboard_value=0b1010,
    )
    fields = proto.decode_fields(payload)
    check("km mouse_x", proto._i(fields, 1) == 100)
    check("km mouse_y", proto._i(fields, 2) == -200)
    check("km left", proto._bool(fields, 4) is True)
    check("km key", proto._u(fields, 6) == 0b1010)

    payload = proto.encode_custom_control(bytes([1, 2, 3]) * 10)
    fields = proto.decode_fields(payload)
    check("cc len", len(proto._b(fields, 1)) == 30)
    vision = proto.encode_vision_control_payload(
        sequence=513, yaw_deg=12.34, pitch_deg=-5.67,
        target_id=103, tracking=True, fire=False,
    )
    check("vision custom payload bounded", len(vision) <= 30)
    check("vision custom payload versioned", vision[:4] == b"PRM1" and vision[-1] == 1)
    spin = proto.encode_spin_control_payload(
        sequence=514, enabled=True, direction="CCW", speed_dps=420,
        power_guard=True,
    )
    check("spin custom payload bounded", len(spin) == 12 and len(spin) <= 30)
    check("spin custom payload versioned", spin[:6] == b"PRM1\x01\x02")
    check("spin custom payload direction/flags", spin[-2:] == bytes([2, 3]))
    manager = BackendManager(state=RobotState())
    manager.mqtt.mock_output = True
    ok, reason = manager.publish_spin_control(
        sequence=9, enabled=False, direction="CW", speed_dps=500,
        power_guard=False,
    )
    spin_wire = manager.mqtt.publish_records[-1]
    check("spin manager mock publishes", ok and reason == "mock")
    check("spin manager uses CustomControl", spin_wire.topic == "CustomControl")
    spin_outer = proto.decode_fields(spin_wire.payload)
    check("spin manager wraps team payload", proto._b(spin_outer, 1)[:6] == b"PRM1\x01\x02")
    manager.shutdown()

    for name, fn in [
        ("AssemblyCommand", lambda: proto.encode_assembly_command(1, 3)),
        ("HeroDeployModeEventCommand", lambda: proto.encode_hero_deploy_mode_event_command(1)),
        ("RuneActivateCommand", lambda: proto.encode_rune_activate_command(1)),
        ("SentryCtrlCommand", lambda: proto.encode_sentry_ctrl_command(2)),
        ("AirSupportCommand", lambda: proto.encode_air_support_command(1)),
        ("CommonCommand", lambda: proto.encode_common_command(1, 100)),
        ("MapClickCmd", lambda: proto.encode_map_click_cmd(map_x=1.0, map_y=2.0)),
    ]:
        check(f"{name} encodes", len(fn()) > 0)


def test_downlink_decode() -> None:
    print("[protocol] downlink decode")
    payload = b"".join([
        proto.encode_uint(1, 2),   # current_round
        proto.encode_uint(2, 3),   # total_rounds
        proto.encode_uint(3, 42),  # red_score
        proto.encode_uint(4, 33),  # blue_score
        proto.encode_uint(5, 4),   # stage = FIGHTING
        proto.encode_int(6, 90),   # countdown
        proto.encode_int(7, 10),   # elapsed
        proto.encode_bool(8, True),
    ])
    data = proto.decode_message("GameStatus", payload)
    check("GameStatus round", data["current_round"] == 2)
    check("GameStatus stage", data["current_stage"] == 4)
    check("GameStatus countdown", data["stage_countdown_sec"] == 90)
    check("GameStatus paused", data["is_paused"] is True)

    payload = b"".join([
        proto.encode_uint(1, 5000),
        proto.encode_uint(2, 1),
        proto.encode_uint(4, 1500),
        proto.encode_uint(6, 4900),
        proto.encode_uint(9, 1200),
        proto.encode_packed_uints(11, [600, 520, 400, 100]),
        proto.encode_packed_ints(12, [10, 20, 30]),
    ])
    data = proto.decode_message("GlobalUnitStatus", payload)
    check("GUS base", data["base_health"] == 5000)
    check("GUS outpost", data["outpost_health"] == 1500)
    check("GUS enemy base", data["enemy_base_health"] == 4900)
    check("GUS robot_health", data["robot_health"] == [600, 520, 400, 100])
    check("GUS bullets", data["robot_bullets"] == [10, 20, 30])

    payload = b"".join([
        proto.encode_uint(1, 7),
        proto.encode_float(2, 42.5),
        proto.encode_uint(4, 58),
        proto.encode_uint(9, 180),
        proto.encode_bool(10, False),
        proto.encode_bool(12, True),
    ])
    data = proto.decode_message("RobotDynamicStatus", payload)
    check("RDS health", data["current_health"] == 7)
    check("RDS heat", abs(data["current_heat"] - 42.5) < 1e-4)
    check("RDS ammo", data["remaining_ammo"] == 180)
    check("RDS remote_heal", data["can_remote_heal"] is True)

    payload = b"".join([
        proto.encode_float(1, 10.5),   # x
        proto.encode_float(2, 7.25),   # y
        proto.encode_float(4, 90.0),   # yaw
        proto.encode_uint(5, 4),       # robot_id
    ])
    data = proto.decode_message("RobotPosition", payload)
    check("POS id", data["robot_id"] == 4)
    check("POS x", abs(data["x"] - 10.5) < 1e-4)
    check("POS yaw", abs(data["yaw"] - 90.0) < 1e-4)

    # RadarInfoToClient 嵌套消息
    sub = b"".join([
        proto.encode_uint(1, 10),
        proto.encode_uint(2, 20),
        proto.encode_uint(3, 1),
    ])
    payload = proto.encode_bytes(1, sub) + proto.encode_bytes(1, sub)
    data = proto.decode_message("RadarInfoToClient", payload)
    check("Radar count", len(data["robots"]) == 2)
    check("Radar highlight", data["robots"][0]["is_high_light"] == 1)

    payload = b"".join([
        proto.encode_uint(1, 3),
        proto.encode_uint(2, 8),
        proto.encode_float(3, 2.5),
    ])
    data = proto.decode_message("RuneStatusSync", payload)
    check("Rune status", data["rune_status"] == 3)
    check("Rune rings", abs(data["average_rings"] - 2.5) < 1e-4)


def test_safety_modes() -> None:
    print("[safety] hard gate modes")
    lab = SCENARIOS["LabDebug"]
    campus = SCENARIOS["CampusSimulation"]
    official = SCENARIOS["OfficialEvent"]

    safety = OfficialSafety("LabDebug")
    safety.from_scenario(lab)
    check("fail-closed before GameStatus", safety.is_fighting() is False)
    ok, reason = safety.can_publish("DartCommand")
    check("LabDebug blocks real publish", ok is False and "mock" in reason)

    safety = OfficialSafety("CampusSimulation")
    safety.from_scenario(campus)
    safety.update_stage("4")
    ok, reason = safety.can_publish("CommonCommand")
    check("Campus fighting remains locked", ok is False and "LOCKED" in reason)
    token = safety.begin_qualification({"economy"}, ttl_s=30.0, now=10.0)
    ok, _ = safety.can_publish(
        "CommonCommand",
        now=10.0,
        intent="economy",
        qualification_token=token.value,
    )
    check("qualification token allows specified intent", ok is True)
    ok, reason = safety.can_publish("KeyboardMouseControl", now=10.0, intent="core_kmc", qualification_token=token.value)
    check("qualification token cannot widen intent", ok is False)

    safety = OfficialSafety("OfficialEvent")
    safety.from_scenario(official)
    safety.update_stage("4")
    safety.arm_match({"core_kmc"})
    ok, _ = safety.can_publish("KeyboardMouseControl", now=20.0, intent="core_kmc")
    check("armed core KMC allowed", ok is True)
    safety.mark_published("KeyboardMouseControl", now=20.0)
    ok, reason = safety.can_publish("KeyboardMouseControl", now=20.01, intent="core_kmc")
    check("KMC 75Hz limited", ok is False and "75" in reason)
    ok, reason = safety.can_publish("CustomControl", now=20.02, intent="prm1_spin")
    check("PRM1 remains separately locked", ok is False and "LOCKED MOCK" in reason)

    safety.trigger_estop()
    ok, reason = safety.can_publish("DartCommand")
    check("Estop blocks", ok is False and "E-stop" in reason)
    safety.release_estop()
    ok, reason = safety.can_publish("KeyboardMouseControl", intent="core_kmc")
    check("Estop release requires re-arm", ok is False and "LOCKED" in reason)


def test_video_codec_detect() -> None:
    print("[video] codec detect")
    check("mjpeg detect", detect_frame_codec(b"\xff\xd8\xff\xe0rest") == "mjpeg")
    nal = b"\x00\x00\x00\x01" + bytes([(32 << 1) | 1]) + b"\x01\x02"
    check("h265 detect", detect_frame_codec(nal) == "h265")
    check("unknown detect", detect_frame_codec(b"hello") == "unknown")


def test_decoded_video_timestamp_and_source_selection() -> None:
    print("[video] decoded timestamp + source selection")

    class FakeDecoder:
        def __init__(self, frame: object = None, timestamp: float = 0.0) -> None:
            self.available = True
            self.frame = frame
            self.timestamp = timestamp
            self.frames_decoded = 0
            self.starts = 0
            self.restarts = 0
            self.writes: list[bytes] = []
            self.input_chunks_dropped = 0

        def latest_with_timestamp(self, _timeout_s: float = 0.5):
            return self.frame, self.timestamp

        def latest(self, _timeout_s: float = 0.5):
            return self.frame

        def latest_timestamp(self) -> float:
            return self.timestamp

        def start(self) -> bool:
            self.available = True
            self.starts += 1
            return True

        def restart(self, **_kwargs: object) -> bool:
            self.available = True
            self.restarts += 1
            return True

        def write(self, payload: bytes) -> bool:
            self.writes.append(bytes(payload))
            return True

        def input_queue_depth(self) -> int:
            return 0

        def stop(self, **_kwargs: object) -> None:
            self.available = False

    now = time.perf_counter()
    mjpeg = object()
    h265 = object()
    backend = RmVideoBackend()
    backend.latest_bgr = mjpeg
    backend.latest_bgr_at = now
    backend.last_frame_at = now
    backend.h265_decoder = FakeDecoder(h265, now + 0.001)  # type: ignore[assignment]
    check("decoded freshness probe avoids packet-only state", backend.decoded_frame_fresh(1.0))
    frame, timestamp = backend.latest_video_frame(1.0)
    check("freshest UDP decode wins", frame is h265 and timestamp > now)

    # Encoded traffic must not make an old decoded MJPEG image look fresh.
    backend.h265_decoder.available = False
    backend.h265_decoder.timestamp = now - 5.0
    backend.latest_bgr_at = now - 5.0
    backend.last_frame_at = time.perf_counter()
    frame, timestamp = backend.latest_video_frame(0.1)
    check("encoded timestamp cannot refresh stale image", frame is None and timestamp == 0.0)
    check("stale decoded freshness resets", not backend.decoded_frame_fresh(0.1))
    check("stale MJPEG image released", backend.clear_stale_decoded_frame(0.1))
    check("released MJPEG cannot freeze", backend.latest_bgr is None and backend.latest_bgr_at == 0.0)

    backend.running = True
    backend._h265_next_restart_at = 0.0
    backend._request_h265_restart()
    restart_thread = backend._h265_restart_thread
    if restart_thread is not None:
        restart_thread.join(timeout=1.0)
    check(
        "UDP decoder restart stays off receive thread",
        backend.h265_decoder.available
        and backend.h265_decoder.restarts == 1
        and backend.h265_decoder_restarts == 1,
    )
    backend.running = False

    queued_decoder = ContinuousMjpegDecoder("h264")
    queued_decoder._proc = object()
    queued_decoder._running = True
    accepted = all(
        queued_decoder.write(b"x")
        for _ in range(queued_decoder.INPUT_QUEUE_CHUNKS)
    )
    rejected = not queued_decoder.write(b"overflow")
    check("decoder input queue is bounded and non-blocking", accepted and rejected)
    check("decoder input drop is observable", queued_decoder.input_chunks_dropped == 1)
    queued_decoder._running = False
    queued_decoder._proc = None

    # Synthetic is a renderer-owned test pattern. It must never inherit a
    # stale UDP/native frame through BackendManager's Auto source fallback.
    manager_stub = object.__new__(BackendManager)
    manager_stub.native_decoder = FakeDecoder(object(), now + 2.0)
    manager_stub.udp = type(
        "FakeUdp",
        (),
        {"latest_video_frame": lambda self, _timeout: (object(), now + 1.0)},
    )()
    frame, frame_at, source = BackendManager.latest_video_frame(
        manager_stub,
        1.0,
        "Synthetic",
    )
    check(
        "synthetic source cannot reuse backend frame",
        frame is None and frame_at == 0.0 and source == "none",
    )
    queued_decoder._drain_input_queue()

    state = RobotState()
    manager = BackendManager(state=state)
    native = object()
    udp = object()
    fake_native = FakeDecoder(native, now + 0.01)
    manager.native_decoder.stop()
    manager.native_decoder = fake_native  # type: ignore[assignment]
    manager.udp.latest_video_frame = lambda _timeout=0.5: (udp, now + 0.02)  # type: ignore[method-assign]
    frame, _timestamp, source = manager.latest_video_frame(1.0, "Auto")
    check("manager auto picks freshest source", frame is udp and source == "udp")
    frame, _timestamp, source = manager.latest_video_frame(1.0, "Native 0x0310")
    check("manager honors native source", frame is native and source == "native-0x0310")

    # Authoritative Hero deploy owns Native 0x0310 and stops the normal UDP
    # path. The latest-frame selector must not fall back to a differently
    # coloured/fresher UDP frame until a 1->0 sync restores the normal source.
    manager._normal_video_source = "udp hevc"
    manager._active_video_source = "udp hevc"
    manager.udp.running = True
    manager.udp.stop = lambda: setattr(manager.udp, "running", False)  # type: ignore[method-assign]
    manager.udp.start = lambda: setattr(manager.udp, "running", True)  # type: ignore[method-assign]
    manager.udp.set_decode_enabled = lambda _enabled: None  # type: ignore[method-assign]
    manager._apply_one(state, "DeployModeStatusSync", {"status": 1})
    check("Hero deploy stops UDP decoder", not manager.udp.running)
    check("Hero deploy selects Native 0x0310", manager._active_video_source == "native 0x0310")
    frame, _timestamp, source = manager.latest_video_frame(1.0, "Native 0x0310")
    check("Hero has no UDP fallback", frame is native and source == "native-0x0310")
    fake_native.frame = None
    frame, timestamp, source = manager.latest_video_frame(1.0, "Native 0x0310")
    check("Hero native loss yields no camera/UDP frame", frame is None and timestamp == 0.0 and source == "none")
    fake_native.frame = native
    manager._apply_one(state, "DeployModeStatusSync", {"status": 0})
    check("Hero exit restores normal UDP source", manager.udp.running and manager._active_video_source == "udp hevc")
    # The isolation checks above intentionally stop the Native decoder. Reset
    # the fake's counters before exercising transport-gap restart semantics.
    fake_native.available = True
    fake_native.starts = 0
    fake_native.restarts = 0
    fake_native.writes.clear()
    manager._native_0310_decoder_started_once = False
    manager._native_0310_decoder_restarts = 0
    manager.set_active_video_source("Native 0x0310")

    def packet(sequence: int, reset: bool, payload: bytes) -> bytes:
        header = (0x01 if reset else 0x00) | (0x02 if len(payload) > 255 else 0x00)
        block = bytes((header, sequence & 0xFF, len(payload) & 0xFF)) + payload
        return block + bytes(300 - len(block))

    sps_a = b"lead\x00\x00\x00\x01\x67sps-a"
    sps_b = b"\x00\x00\x01\x67sps-b"
    recovery_a = sps_a + b"\x00\x00\x01\x68pps-a\x00\x00\x01\x65idr-a"
    recovery_b = sps_b + b"\x00\x00\x01\x68pps-b\x00\x00\x01\x65idr-b"
    check("native SPS finder 4-byte", find_h264_sps_offset(sps_a) == 4)
    check("native SPS finder 3-byte", find_h264_sps_offset(sps_b) == 0)
    check("native SPS finder rejects non-SPS", find_h264_sps_offset(b"\x00\x00\x01\x65idr") is None)
    manager._apply_one(state, "CustomByteBlock", {"data": packet(10, True, b"a" * 20)})
    check(
        "native reset blocks until SPS",
        state.backend_stats["native_video_0310"]["blocked_until_sps"]
        and not state.backend_stats["native_video_0310"]["payload_written"],
    )
    manager._apply_one(state, "CustomByteBlock", {"data": packet(11, False, recovery_a)})
    manager._apply_one(state, "CustomByteBlock", {"data": packet(13, False, b"b" * 20)})
    check(
        "native gap blocks corrupt continuation",
        state.backend_stats["native_video_0310"]["blocked_until_sps"]
        and not state.backend_stats["native_video_0310"]["payload_written"],
    )
    manager._apply_one(state, "CustomByteBlock", {"data": packet(14, False, recovery_b)})
    manager._apply_one(state, "CustomByteBlock", {"data": packet(14, False, recovery_b)})
    info = state.backend_stats["native_video_0310"]
    check("native stream retains no unbounded bytearray", not hasattr(manager, "_native_0310_buffer"))
    check("native sequence gap counted", info["dropped_chunks"] == 1)
    check("native duplicate rejected", info["out_of_order_chunks"] == 1 and not info["payload_accepted"])
    check(
        "native resumes exactly at SPS",
        len(fake_native.writes) == 2 and not info["blocked_until_sps"],
    )
    check(
        "native payload writes bounded",
        len(fake_native.writes) == 2
        and info["bytes_since_reset"] == 40 + len(recovery_a) + len(recovery_b),
    )
    check("native gaps do not synchronously restart ffmpeg", fake_native.restarts == 0)
    manager._native_0310_decoder_started_once = True
    fake_native.available = False
    manager._apply_one(state, "CustomByteBlock", {"data": packet(15, False, b"continuation")})
    check(
        "native decoder crash waits for SPS",
        state.backend_stats["native_video_0310"]["blocked_until_sps"]
        and len(fake_native.writes) == 2,
    )
    manager._apply_one(state, "CustomByteBlock", {"data": packet(16, False, recovery_b)})
    info = state.backend_stats["native_video_0310"]
    check(
        "native decoder restarts only at SPS",
        fake_native.starts == 1
        and info["decoder_restarts"] == 1
        and fake_native.writes[-1].endswith(b"idr-b"),
    )
    manager.shutdown()


def test_serial_backend() -> None:
    print("[serial] mock + reject video-link 0x0311")
    serial = RmSerialBackend(port="Stub", baud=115200, mock_writes=True)
    serial.start()
    check("stub not connected", serial.connected is False)
    ok, reason = serial.write_frame(0x0311, b"\x00" * 30)
    check("serial 0x0311 rejected", ok is False and "0x0311" in reason)
    ok, reason = serial.write_frame(0x0201, b"\x00")
    check("other serial mock write ok", ok is True and reason == "mock")
    serial.stop()


def test_manager_updates_state() -> None:
    print("[manager] apply_updates -> RobotState")
    state = RobotState()
    manager = BackendManager(state=state, client_id="3", scenario_key="LabDebug")
    # 直接喂解码队列（不依赖真实 MQTT）
    from collections import deque
    import threading as _t
    manager.mqtt._decoded.append(("GameStatus", "GameStatus", {
        "current_round": 1, "total_rounds": 3, "red_score": 10, "blue_score": 8,
        "current_stage": 4, "stage_countdown_sec": 300, "stage_elapsed_sec": 120,
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("GlobalUnitStatus", "GlobalUnitStatus", {
        "base_health": 5000, "outpost_health": 1500, "enemy_base_health": 4800,
        "robot_health": [600, 520], "robot_bullets": [10, 20, 30, 40, 50],
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotModuleStatus", "RobotModuleStatus", {
        "power_manager": 1, "rfid": 1, "capacitor": 1,
        "__present__": frozenset({1, 2, 3}),
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotPosition", "RobotPosition", {
        "robot_id": 103, "x": 12.0, "y": 7.0, "yaw": 90.0,
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RuneStatusSync", "RuneStatusSync", {
        "rune_status": 3, "activated_arms": 4, "average_rings": 2.5,
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("PenaltyInfo", "PenaltyInfo", {
        "penalty_type": 1, "penalty_effect_sec": 30, "total_penalty_num": 1,
    }, time.perf_counter()))
    count = manager.apply_updates(state)
    check("applied 6 messages", count == 6)
    check("state stage MATCH", state.current_stage == "MATCH")
    check("state red_score", state.red_score == 10)
    check("state base_health", state.base_health == 5000)
    check("state official_robot_health", state.official_robot_health == [600, 520])
    check("state official_robot_bullets", state.official_robot_bullets == [10, 20, 30, 40, 50])
    check(
        "infantry projectile allowance mapped",
        state.projectile_allowance_17mm == 30 and state.projectile_allowance_42mm == 0,
    )
    check("state module_status", state.robot_module_status.get("power_manager") == 1)
    check("state positions", state.robot_positions[0]["robot_id"] == 103)
    check("state rune phase", state.energy_rune_phase == "ACTIVE")
    check("state rune progress", state.energy_rune_progress == 250)
    check("state active_penalty", state.active_penalty["penalty_type"] == 1)

    # RobotStaticStatus can arrive after GlobalUnitStatus.  The retained
    # own-team vector must be remapped immediately instead of waiting another
    # 1 Hz global sample or retaining the Infantry value.
    manager.mqtt._decoded.append(("RobotStaticStatus", "RobotStaticStatus", {
        "robot_id": 101,
        "__present__": frozenset({4}),
    }, time.perf_counter()))
    check("hero static status applied", manager.apply_updates(state) == 1)
    check(
        "blue hero projectile allowance remapped",
        state.projectile_allowance_17mm == 0 and state.projectile_allowance_42mm == 10,
    )

    # An authoritative empty vector is fail-unknown and must clear the prior
    # value rather than showing stale ammunition.
    manager.mqtt._decoded.append(("GlobalUnitStatus", "GlobalUnitStatus", {
        "robot_bullets": [],
        "__present__": frozenset({12}),
    }, time.perf_counter()))
    check("empty projectile vector applied", manager.apply_updates(state) == 1)
    check(
        "empty projectile vector clears HUD",
        state.official_robot_bullets == []
        and state.projectile_allowance_17mm == 0
        and state.projectile_allowance_42mm == 0,
    )

    # 性能值推导（英雄近战 → shooter=chassis=3）
    registry = build_default_registry()
    values = BackendManager.performance_values(registry)
    check("perf default infantry", values["shooter"] == 2 and values["chassis"] == 2)
    manager.shutdown()


def test_real_present_metadata_and_fault_isolation() -> None:
    print("[manager] real decoder metadata + per-message isolation")
    state = RobotState()
    manager = BackendManager(state=state, client_id="3", scenario_key="LabDebug")
    modules = proto.decode_message(
        "RobotModuleStatus",
        proto.encode_uint(1, 1) + proto.encode_uint(2, 1) + proto.encode_uint(3, 0),
    )
    performance = proto.decode_message(
        "RobotPerformanceSelectionSync",
        proto.encode_uint(1, 2) + proto.encode_uint(2, 1) + proto.encode_uint(3, 0),
    )
    manager.mqtt._decoded.append(("RobotModuleStatus", "RobotModuleStatus", modules, time.perf_counter()))
    manager.mqtt._decoded.append(("bad", "RobotModuleStatus", {"power_manager": object()}, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotPerformanceSelectionSync", "RobotPerformanceSelectionSync", performance, time.perf_counter()))
    count = manager.apply_updates(state)
    check("present metadata does not crash", count == 2)
    check("module values applied", state.robot_module_status["power_manager"] == 1)
    check("performance values applied after bad message", state.performance_sync["shooter"] == 2)
    check("bad message counted", manager.stats()["message_apply"]["errors"] == 1)
    manager.shutdown()


def test_map_click_downlink_and_health_order() -> None:
    print("[protocol] V1.3.1 map direction + 5+5 health order")
    payload = proto.encode_map_click_cmd(
        is_send_all=1,
        robot_id=bytes((1, 2)),
        mode=1,
        enemy_id=103,
        ascii_code=ord("C"),
        mark_type=1,
        map_x=8.5,
        map_y=4.25,
    )
    decoded = proto.decode_message("MapClickInfo", payload)
    check("map downlink decoded", decoded["enemy_id"] == 103 and abs(decoded["map_x"] - 8.5) < 1e-4)

    state = RobotState(robot_id=103, alliance_color="Blue")
    state.official_robot_health = [610, 510, 410, 310, 710, 600, 500, 400, 300, 700]
    manager = BackendManager(state=state)
    manager.sync_frontend_state(state)
    rows = {row["id"]: row for row in state.robot_rows}
    check("blue ally order", rows[101]["hp"] == 610 and rows[107]["hp"] == 710)
    check("blue enemy order", rows[1]["hp"] == 600 and rows[7]["hp"] == 700)
    manager.shutdown()


def test_shutdown_neutral_frame_and_readiness_consumption() -> None:
    print("[manager] neutral shutdown + consumed readiness")

    class PublishResult:
        rc = 0

    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, bytes, int]] = []

        def publish(self, topic, payload, qos=0, **_kwargs):
            self.messages.append((str(topic), bytes(payload), int(qos)))
            return PublishResult()

        def disconnect(self):
            return None

        def loop_stop(self, **_kwargs):
            return None

    state = RobotState()
    manager = BackendManager(state=state, scenario_key="OfficialEvent")
    fake = FakeClient()
    manager.mqtt.client = fake
    manager.mqtt.connected = True
    manager.mqtt.mock_output = False
    report = manager.apply_readiness(state, live_evidence={"video_source": "udp"})
    check("readiness consumed into state", state.backend_stats["readiness"] is report)
    check("readiness remains fail closed", report["verdict"] == "NOT READY FOR REAL MATCH")
    manager.shutdown()
    kmc = [item for item in fake.messages if item[0] == "KeyboardMouseControl"]
    check("shutdown emits neutral KMC", bool(kmc))
    check("shutdown neutral payload exact", kmc[-1][1] == proto.encode_keyboard_mouse_control())
    check("shutdown latches local estop", manager.safety.estop_active and manager.serial.estop_active)
    status = [item for item in fake.messages if item[0] == "ClientStatus"]
    check("shutdown publishes retained offline", bool(status) and status[-1][1] == b'{"online":false}')


def test_scenario_restart_does_not_publish_offline() -> None:
    print("[manager] reconnect retained-status safety")

    class PublishResult:
        rc = 0

    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, bytes, int]] = []

        def publish(self, topic, payload, qos=0, **_kwargs):
            self.messages.append((str(topic), bytes(payload), int(qos)))
            return PublishResult()

        def disconnect(self):
            return None

        def loop_stop(self, **_kwargs):
            return None

    manager = BackendManager(state=RobotState(), scenario_key="OfficialEvent")
    fake = FakeClient()
    manager.mqtt.client = fake
    manager.mqtt.connected = True
    manager.mqtt.mock_output = False
    manager.apply_scenario("LabDebug", start=False)
    check("restart emits neutral KMC", any(item[0] == "KeyboardMouseControl" for item in fake.messages))
    check("restart omits retained offline", not any(item[0] == "ClientStatus" for item in fake.messages))
    check("start false keeps transports stopped", not manager.mqtt._started and not manager.udp.running)
    check("new scenario starts fail closed", manager.safety.estop_active is False and manager.safety.is_fighting() is False)
    manager.shutdown()


def test_backend_lock_requests_input_clear() -> None:
    print("[manager] control epoch clears host input")
    manager = BackendManager(state=RobotState(), scenario_key="LabDebug")
    reasons: list[str] = []
    manager.input_clear_hook = reasons.append
    manager.lock_control("unit-test lock")
    check("explicit lock requests input clear", reasons == ["unit-test lock"])

    # Simulate a link that has remained down longer than the two-second grace
    # period. Reconnect keeps LOCKED and may never replay the stale input.
    manager.mqtt.connected = False
    manager.mqtt.disconnected_at = time.perf_counter() - 2.1
    manager.tick()
    check("MQTT timeout requests input clear", reasons[-1] == "MQTT disconnected for more than 2 seconds")
    generation = manager.stats()["safety_evidence"]["input_clear_generation"]
    manager.mqtt.connected = True
    manager.tick()
    check("reconnect does not re-arm", manager.safety.control_state.value == "LOCKED")
    check("input clear generation is observable", generation >= 2)
    manager.shutdown()


def test_protocol_regressions() -> None:
    print("[protocol] regression fixes")
    check("empty payload -> {}", proto.decode_message("GameStatus", b"") == {})
    check("truncated varint -> {}", proto.decode_message("GameStatus", b"\x08\x80") == {})
    payload = proto.encode_uint(1, 0xFFFFFFFFFFFFFFFF)
    fields = proto.decode_fields(payload)
    check("varint masked 64-bit", proto._u(fields, 1) == 0xFFFFFFFFFFFFFFFF)
    payload = proto.encode_uint(4, 3) + proto.encode_uint(9, 600)
    data = proto.decode_message("RobotStaticStatus", payload)
    check("present marker", data["__present__"] == frozenset({4, 9}))
    check("missing field default", data["robot_id"] == 3 and data["max_health"] == 600)
    payload = proto.encode_map_click_cmd()
    fields = proto.decode_fields(payload)
    check("robot_id default 7 bytes", len(proto._b(fields, 2)) == 7)
    check("MapClickCmd uplink", "MapClickCmd" in proto.UPLINK_MESSAGES)
    check("MapClickInfo downlink", "MapClickInfo" in proto.DOWNLINK_MESSAGES)
    check("MapClickInfo not uplink", "MapClickInfo" not in proto.UPLINK_MESSAGES)
    try:
        proto.encode_custom_control(b"x" * 31)
        check("custom_control >30 raises", False)
    except ValueError:
        check("custom_control >30 raises", True)
    check("custom_control 30 ok", len(proto.encode_custom_control(b"x" * 30)) > 0)
    check("topic unknown -> empty", proto.topic_message_name("CustomLog/1") == "")
    check("topic known", proto.topic_message_name("GameStatus") == "GameStatus")
    payload = proto.encode_packed_uints(1, [1, 2]) + proto.encode_uint(1, 3)
    fields = proto.decode_fields(payload)
    check("mixed packed+unpacked", proto._packed(fields, 1) == [1, 2, 3])


def test_serial_partial_frame() -> None:
    print("[serial] partial frame retention")
    from rm_client.protocol import RefereeFrameCodec
    codec = RefereeFrameCodec()
    frame_a = codec.encode(0x0311, b"\x01" * 20)
    frame_b = codec.encode(0x0311, b"\x02" * 20)
    stream = frame_a + frame_b[:5]
    frames, consumed = codec.decode_with_consumed(stream)
    check("one complete frame", len(frames) == 1)
    check("consumed == len(frame_a)", consumed == len(frame_a))
    check("partial retained", len(stream) - consumed == 5)


def test_absent_string_field_is_not_the_same_as_an_empty_one() -> None:
    """`_s()` 里那个 `or default` 把两件事压成了同一句。

    第 115 轮。一次 `or_drop` 普查（AST，92 个生产文件）指出
    `backend/protocol.py:187` 是个真风险位置，本轮实测确认。

    ```python
    def _b(fields, field) -> bytes:
        values = fields.get(field)
        if not values:
            return b""                       # <- 字段缺失
        ...
    def _s(fields, field, default="") -> str:
        return _b(fields, field).decode("utf-8", errors="replace") or default
    ```

    `_b()` 对「**字段不存在**」与「**字段在、但内容是空**」都返回 `b""`，
    那个 `or` 于是把空字符串也换成了 `default`。

    ## ⚠ 本条**钉的是现状**，不是「应该怎样」

    我第一版写的是「字段在且为空时必须原样留成 `""`」——
    **它当场就红了，因为代码给的是 `default`。**

    写一条产品并不满足的断言，等于**把一个猜测写成契约**。
    而且这里改的是**线上协议的取字段语义**：裁判那边发来的
    「昵称为空」「队伍名为空」到底该显示成 `""` 还是 `"UNKNOWN"`，
    **是一个产品问题，不是一条测试能替我定的**。

    所以本条只做一件事：**把这个混淆钉成可见的事实**，
    谁想改它，得先决定改完之后下游那些调用点各自意味着什么。

    ## 实测的三条

    * 字段缺失     -> `"UNKNOWN"`
    * 字段在但为空 -> `"UNKNOWN"`   ← **与上面同义，这正是被混淆的地方**
    * 字段有内容   -> `"ABC"`
    """
    from rm_client.backend.protocol import _s

    check("字段缺失时用 default", _s({}, 7, "UNKNOWN") == "UNKNOWN",
          f"拿到 {_s({}, 7, 'UNKNOWN')!r}")
    check(
        "字段在但内容为空时**也**落到 default（现状：两者被混淆）",
        _s({7: [b""]}, 7, "UNKNOWN") == "UNKNOWN",
        f"拿到 {_s({7: [b'']}, 7, 'UNKNOWN')!r} —— 混淆已经不存在了？那该重写这条",
    )
    check("字段有内容时原样返回",
          _s({7: [b"ABC"]}, 7, "UNKNOWN") == "ABC",
          f"拿到 {_s({7: [b'ABC']}, 7, 'UNKNOWN')!r}")
    check("非 bytes 的取值也走同一条路（`_b` 返回 b''）",
          _s({7: [123]}, 7, "UNKNOWN") == "UNKNOWN",
          f"拿到 {_s({7: [123]}, 7, 'UNKNOWN')!r}")
    check(
        "default 本身为空串时两条路径本就同义（这一条两侧一致）",
        _s({}, 7, "") == "" and _s({7: [b""]}, 7, "") == "",
        "default 为空串时，`or` 的两半给不出可区分的结果",
    )


def test_codec_regressions() -> None:
    print("[video] codec regressions")
    check("h264 sps", detect_frame_codec(b"\x00\x00\x00\x01\x67\x64\x00\x2a") == "h264")
    check("trailing start code no crash", detect_frame_codec(b"\x00\x00\x00\x01") == "unknown")
    check("prefixed mjpeg", detect_frame_codec(b"\x00\x00\x00\x2a\xff\xd8\xff") == "mjpeg")
    check("hevc vps", detect_frame_codec(b"\x00\x00\x00\x01\x41\x01\x01\x02") == "h265")


def test_mqtt_publish_rc() -> None:
    print("[mqtt] publish rc + estop mock + client_id")
    from rm_client.scenarios import SCENARIOS
    s = OfficialSafety("OfficialEvent")
    s.from_scenario(SCENARIOS["OfficialEvent"])
    s.update_stage("4")
    s.arm_match({"core_kmc"})
    backend = RmMqttBackend(host="127.0.0.1", port=3333, client_id="3",
                            mock_output=False, safety=s)
    backend.connected = True

    class _InfoErr:
        rc = 4  # MQTT_ERR_NO_CONN

    class _FakeErr:
        def publish(self, topic, payload, qos=1):
            return _InfoErr()

    backend.client = _FakeErr()
    ok, reason = backend.publish_topic("DartCommand", b"x")
    check("disconnected publish rc -> False", ok is False and "rc=4" in reason)

    class _InfoOk:
        rc = 0

    class _FakeOk:
        def __init__(self):
            self.calls = []
        def publish(self, topic, payload, qos=1):
            self.calls.append((topic, payload))
            return _InfoOk()

    backend.client = _FakeOk()
    ok, reason = backend.publish_topic("DartCommand", b"x")
    check("connected publish ok", ok is True and reason == "ok")
    ok, reason = backend.publish_topic("DartCommand", b"x")
    check("10Hz after real publish", ok is False and "10" in reason)

    backend2 = RmMqttBackend(host="127.0.0.1", port=3333, client_id="3",
                             mock_output=True, safety=s)
    s.trigger_estop()
    ok, reason = backend2.publish_topic("DartCommand", b"x")
    check("estop blocks mock", ok is False and bool(reason))
    s.release_estop()

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState
    m = BackendManager(state=RobotState(), client_id="3", scenario_key="LabDebug")
    m.mqtt._started = True
    m.set_client_id(7)
    check("client_id updated", m.mqtt.client_id == "7")
    m.shutdown()




def test_backend_frontend_sync() -> None:
    print("[frontend] backend -> HUD data sync")
    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState
    state = RobotState()
    manager = BackendManager(state=state, client_id="3", scenario_key="OfficialEvent")
    check(
        "official scene clears mock projectile allowance",
        state.projectile_allowance_17mm == 0 and state.projectile_allowance_42mm == 0,
    )
    manager.mqtt._decoded.append(("GlobalUnitStatus", "GlobalUnitStatus", {
        "robot_health": [600, 520, 400, 300, 200, 580, 480, 380, 280, 0],
        "robot_bullets": [],
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotPosition", "RobotPosition", {
        "robot_id": 3, "x": 8.2, "y": 7.6, "yaw": 90.0, "__present__": frozenset({1, 2, 4, 5}),
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotStaticStatus", "RobotStaticStatus", {
        "robot_id": 3, "alive_state": 1, "max_health": 600, "level": 2,
        "__present__": frozenset({3, 4, 8, 9}),
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotDynamicStatus", "RobotDynamicStatus", {
        "current_health": 520, "remaining_ammo": 180,
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("RobotModuleStatus", "RobotModuleStatus", {
        "power_manager": 1, "capacitor": 1,
    }, time.perf_counter()))
    manager.mqtt._decoded.append(("Buff", "Buff", {
        "robot_id": 3, "buff_type": 1, "buff_level": 1, "buff_max_time": 30, "buff_left_time": 12,
    }, time.perf_counter()))
    count = manager.apply_updates(state)
    check("applied 6", count == 6)
    check("frontend synced flag", state.backend_frontend_synced is True)
    check("robot_rows real hp", state.robot_rows[0]["id"] == 1 and state.robot_rows[0]["hp"] == 600)
    local_row = next(r for r in state.robot_rows if r["id"] == 3)
    check("local row hp", local_row["hp"] == 520)
    check("local row pos", local_row["x"] == 8.2 and local_row["y"] == 7.6)
    check("local row modules", local_row["modules"].get("power_manager") == 1)
    enemy = next(r for r in state.robot_rows if r["id"] == 101)
    check("enemy row hp", enemy["hp"] == 580 and enemy["state"] == "ALIVE")
    dead = next(r for r in state.robot_rows if r["id"] == 107)
    check("dead row", dead["hp"] == 0 and dead["state"] == "DEAD")
    check("map_robots real", any(m["id"] == 3 and m["x"] == 8.2 for m in state.map_robots))
    check("buff_rows real", any(b["name"] == "Attack Boost" and b["remaining"] == 12 for b in state.buff_rows))
    attack_buff, defense_buff, buff_detail = state.buff_indicator_state()
    check(
        "authoritative buff drives HUD indicator",
        attack_buff is True and defense_buff is False and "Attack Boost 12s" in buff_detail,
    )
    fallback = RobotState()
    fallback.small_buff = False
    fallback.large_buff = True
    check(
        "LabDebug buff indicator keeps explicit fallback",
        fallback.buff_indicator_state() == (False, True, ""),
    )
    fallback.official_state_active = True
    fallback.buff_rows = []
    check(
        "official state never reuses mock buff fallback",
        fallback.buff_indicator_state() == (False, False, ""),
    )
    fallback.buff_rows = [{"robot_id": 103, "buff_type": 1, "active": True, "remaining": 9}]
    check(
        "other robot buff does not light local indicator",
        fallback.buff_indicator_state() == (False, False, ""),
    )
    fallback.buff_rows = [{"buff_type": 2, "active": False, "remaining": 0}]
    check(
        "authoritative inactive buff suppresses mock fallback",
        fallback.buff_indicator_state() == (False, False, ""),
    )
    check("empty module status adds no HUD row", fallback.robot_module_indicator_state() == ())
    fallback.robot_module_status = {
        "power_manager": 1,
        "small_shooter": 0,
        "big_shooter": 1,
        "video_transmission": 0,
        "armor": 1,
        "main_controller": 0,
    }
    check(
        "official module status maps to stable HUD chips",
        fallback.robot_module_indicator_state()
        == (("PWR", True), ("SHT", True), ("CV", False), ("ARM", True), ("MAIN", False)),
    )
    rows_before = [dict(row) for row in state.robot_rows]
    map_before = [dict(row) for row in state.map_robots]
    buffs_before = [dict(row) for row in state.buff_rows]
    health_before = state.current_health
    state.tick(1.0 / 60.0)
    check(
        "official rows survive 60Hz model tick",
        state.robot_rows == rows_before and state.map_robots == map_before,
    )
    check(
        "official values survive 60Hz model tick",
        state.current_health == health_before and state.buff_rows == buffs_before,
    )

    # The first official MapClick must clear old mock data before, not after,
    # applying the marker; otherwise it disappears on the same frame.
    marker_state = RobotState()
    marker_state.tick(1.0 / 60.0)
    marker_manager = BackendManager(state=marker_state, client_id="3", scenario_key="OfficialEvent")
    marker_manager.mqtt._decoded.append(("MapClickInfo", "MapClickInfo", {
        "ascii": ord("A"), "map_x": 4.5, "map_y": 6.5, "type": 1,
        "__present__": frozenset({2, 6, 7, 8}),
    }, time.perf_counter()))
    check("first MapClick applied", marker_manager.apply_updates(marker_state) == 1)
    check(
        "first MapClick retained",
        len(marker_state.map_markers) == 1 and marker_state.map_markers[0]["label"] == "A",
    )
    marker_state.tick(1.0 / 60.0)
    check("MapClick survives next model tick", len(marker_state.map_markers) == 1)
    marker_manager.shutdown()

    # A scenario/transport epoch change resets cumulative injury baselines so
    # the first post-reconnect sample cannot look like a fresh projectile hit.
    state.injury_stats[103] = {"small_projectile_damage": 25}
    state.injury_stats_initialized = True
    state.projectile_damage_small_total = 25
    state.projectile_damage_large_total = 50
    revision_before = state.projectile_hit_revision
    manager._reset_state_for_scenario(state)
    check(
        "scenario reset clears injury baseline",
        not state.injury_stats
        and not state.injury_stats_initialized
        and state.projectile_damage_small_total == 0
        and state.projectile_damage_large_total == 0,
    )
    check(
        "scenario reset preserves monotonic hit revision",
        state.projectile_hit_revision == revision_before,
    )
    check(
        "scenario reset clears authoritative projectile vector",
        state.official_robot_bullets == [],
    )
    manager.shutdown()




def test_native_0310_video_pipeline() -> None:
    print("[native] 0x0310 H.264 reassembly -> persistent decoder")
    import subprocess
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        print("  skip (no ffmpeg)")
        return
    proc = subprocess.run(
        [
            ffmpeg, "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=160x120:rate=30:duration=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-f", "h264", "pipe:1",
        ],
        capture_output=True, timeout=30,
    )
    data = proc.stdout
    check("h264 bytes generated", len(data) > 100)
    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState
    state = RobotState()
    manager = BackendManager(state=state, client_id="3", scenario_key="OfficialEvent")
    manager.set_active_video_source("Native 0x0310")
    chunks = [data[i:i + 297] for i in range(0, len(data), 297)]
    for idx, chunk in enumerate(chunks):
        length = len(chunk)
        header = 0
        if length > 255:
            header |= 0x02
        if idx == 0:
            header |= 0x01  # reset
        packet = bytes([header, idx & 0xFF, length & 0xFF]) + chunk
        packet = packet + b"\x00" * (300 - len(packet))  # 固定 300 字节
        manager.mqtt._decoded.append((
            "CustomByteBlock", "CustomByteBlock",
            {"data": packet, "__present__": frozenset({1})},
            time.perf_counter(),
        ))
    manager.apply_updates(state)
    deadline = time.time() + 8.0
    frames = 0
    while time.time() < deadline and frames <= 0:
        time.sleep(0.3)
        manager.stats()
        info = state.backend_stats.get("native_video_0310", {})
        frames = int(info.get("decoded_frames", 0) or 0)
    check("native decoder ready", bool(state.backend_stats.get("native_video_0310", {}).get("decoder_ready", False)))
    check("native decoded frames", frames > 0)
    check("native latest frame", manager.native_decoder.latest(1.0) is not None)
    manager.shutdown()


def test_persistent_h264_decoder() -> None:
    print("[decoder] ContinuousMjpegDecoder h264 end-to-end")
    import subprocess
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        print("  skip (no ffmpeg)")
        return
    from rm_client.backend.video_backend import ContinuousMjpegDecoder
    proc = subprocess.run(
        [
            ffmpeg, "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=160x120:rate=30:duration=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-f", "h264", "pipe:1",
        ],
        capture_output=True, timeout=30,
    )
    data = proc.stdout
    dec = ContinuousMjpegDecoder("h264")
    check("decoder start", dec.start() is True)
    for i in range(0, len(data), 8192):
        dec.write(data[i:i + 8192])
    deadline = time.time() + 6.0
    frame = None
    while time.time() < deadline and frame is None:
        time.sleep(0.25)
        frame = dec.latest(1.0)
    dec.stop()
    check("h264 frame decoded", frame is not None and getattr(frame, "ndim", 0) == 3)




def test_mjpeg_stream_splitter() -> None:
    print("[decoder] MJPEG stream splitter (no ffmpeg needed)")
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    from rm_client.backend.video_backend import consume_mjpeg_stream
    img1 = np.zeros((40, 60, 3), dtype=np.uint8)
    img1[:] = (0, 0, 255)
    img2 = np.zeros((40, 60, 3), dtype=np.uint8)
    img2[:] = (0, 255, 0)
    ok1, j1 = cv2.imencode(".jpg", img1)
    ok2, j2 = cv2.imencode(".jpg", img2)
    check("jpeg encode ok", bool(ok1) and bool(ok2))
    stream = j1.tobytes() + j2.tobytes()
    buf = bytearray()
    frames: list[bytes] = []
    # 逐字节喂入，模拟网络分块 + 跨块标志
    for byte in stream:
        buf.append(byte)
        consume_mjpeg_stream(buf, frames.append)
    check("two frames split", len(frames) == 2)
    check("splitter drained", len(buf) == 0)


def main() -> None:
    test_uplink_roundtrip()
    test_downlink_decode()
    test_safety_modes()
    test_video_codec_detect()
    test_decoded_video_timestamp_and_source_selection()
    test_serial_backend()
    test_manager_updates_state()
    test_real_present_metadata_and_fault_isolation()
    test_map_click_downlink_and_health_order()
    test_shutdown_neutral_frame_and_readiness_consumption()
    test_scenario_restart_does_not_publish_offline()
    test_backend_lock_requests_input_clear()
    test_protocol_regressions()
    test_serial_partial_frame()
    test_absent_string_field_is_not_the_same_as_an_empty_one()
    test_codec_regressions()
    test_mqtt_publish_rc()
    test_backend_frontend_sync()
    test_native_0310_video_pipeline()
    test_persistent_h264_decoder()
    test_mjpeg_stream_splitter()
    print(f"\\nALL PASSED ({PASS} checks)")


if __name__ == "__main__":
    main()
