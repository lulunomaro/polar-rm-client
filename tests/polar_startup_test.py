"""端到端启动验证：真实 PolarRMWindow + 大疆官方场景 + 模拟机器人 0x5A 字节流。

这条测试回答的是「上实车后能不能正常启动并操控」里能被离线证明的那部分：

    窗口构造 → 选到大疆官方场景 → 灌入下位机真实字节流 → 一帧 tick →
    HUD 显示的是机器人实测量 → WASD 意图变成上线的 m/s → fire 仍为 0

无 GL 环境走 CPU 兼容路径（QOpenGLWidget 仅告警），因此可在 CI/桌面直接跑：

    python tests/polar_startup_test.py

测试隔离：本文件会构造真实窗口，而窗口会自动保存配置；其中改绑子测试
会真的把 `Bind W` 写成 `Up` 再恢复。因此必须用**每次全新**的临时用户数据
目录，而不能尊重外部传入的 POLAR_USER_DATA_ROOT：
早期版本用的是 setdefault 语义，结果当调用方（如 tools/verify_all.py）给
所有测试共用一个隔离根目录时，本测试先把它写脏，同一根目录下再跑一次
就会在“按物理 W”上假失败——隔离没起作用，只是把污染换了个地方。
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 必须在导入任何 rm_client 模块之前设置：runtime_paths 在求路径时读它。
# 注意这里刻意**不用** setdefault：本测试会写绑定，复用外部根目录会造成
# 跨次运行的假失败（详见模块 docstring）。需要固定目录时用
# POLAR_STARTUP_USER_DATA_ROOT 显式指定，那是一个知情选择。
_fixed_root = os.environ.get("POLAR_STARTUP_USER_DATA_ROOT", "").strip()
os.environ["POLAR_USER_DATA_ROOT"] = (
    _fixed_root or tempfile.mkdtemp(prefix="polar_startup_userdata_")
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.protocol import crc8, crc16  # noqa: E402
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

NETWORK_ATTEMPTS: list[str] = []
_NETWORK_GUARD_ACTIVE = False


def _reject_python_network(event, _arguments):
    if _NETWORK_GUARD_ACTIVE and event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError(f"POLAR startup virtual-robot test must remain offline: {event}")


sys.addaudithook(_reject_python_network)

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILED.append(name)


def frame(segment_id: int, body: bytes, stamp: int) -> bytes:
    """按下位机的 len/校验语义拼一条真实字节流。"""

    seg_len = 4 + len(body)
    header = bytes((pl.POLAR_SOF, seg_len & 0xFF, segment_id & 0xFF, 0x00))
    header = header[:3] + bytes((crc8(header[:3]),))
    out = header + struct.pack("<I", stamp) + body
    return out + crc16(out).to_bytes(2, "little")


def robot_stream() -> bytes:
    return b"".join(
        (
            frame(pl.SEG_IMU, struct.pack("<6f", 1.0, 0.1, 0.05, 0.5, 0.0, 0.0), 1),
            frame(pl.SEG_JOINT_STATE, struct.pack("<ff", 0.2, 1.3), 2),
            frame(
                pl.SEG_ROBOT_STATUS,
                struct.pack("<BBHHHHHfffBHHHH", 3, 1, 400, 480, 40, 120, 460, 1.5, 2.5, 90.0, (2 << 4) | 5, 150, 8, 30, 20),
                3,
            ),
            frame(pl.SEG_BUFF, struct.pack("<BHBBHB", 1, 0, 3, 0, 40, 77), 4),
            frame(pl.SEG_ROBOT_STATE_INFO, struct.pack("<HB", 0b101, 0b00010), 5),
            frame(pl.SEG_ROBOT_MOTION, struct.pack("<fff", 1.1, -0.2, 0.3), 6),
        )
    )


def test_port_auto_selection_safety() -> None:
    """锁住“Auto 不能认贼作父”：主板自带 COM1 不是机器人。

    这直接决定车上能不能一键连对：中文 Windows 上 COM1 的描述是
    “通信端口”，不含任何可识别英文关键字，早期版本会被 Auto 优先选中。
    """

    from rm_client import serial_ports as sp

    original = sp.list_serial_ports
    try:
        # 只有主板传统串口：没有 VID/PID，必须拒绝自动选择
        sp.list_serial_ports = lambda: [
            {"device": "COM1", "description": "通信端口 (COM1)", "vid": 0, "pid": 0}
        ]
        check("仅有传统 COM1 时 Auto 不乱连", sp.resolve_port("Auto") == "Stub", sp.resolve_port("Auto"))

        # 插上机器人（STMicro Virtual COM Port）：应被选中，而不是 COM1
        sp.list_serial_ports = lambda: [
            {"device": "COM1", "description": "通信端口 (COM1)", "vid": 0, "pid": 0},
            {"device": "COM12", "description": "STMicroelectronics Virtual COM Port", "vid": 0x0483, "pid": 0x5720},
        ]
        check("Auto 选中机器人而非 COM1", sp.resolve_port("Auto") == "COM12", sp.resolve_port("Auto"))
        check("COM12 出现在选项列表里", "COM12" in sp.port_choices(), str(sp.port_choices()))

        # 蓝牙类设备必须直接负分，不参与争抢
        check(
            "蓝牙串口被排除",
            sp.score_port({"device": "COM8", "description": "Standard Bluetooth", "vid": 0x1234, "pid": 0x1}) == -100,
        )

        # 两个同等可疑的 USB 口：不猜，让操作员显式选
        sp.list_serial_ports = lambda: [
            {"device": "COM5", "description": "USB Serial", "vid": 0x1A86, "pid": 0x7523},
            {"device": "COM7", "description": "USB Serial", "vid": 0x1A86, "pid": 0x7523},
        ]
        check("两候选同分时不猜", sp.resolve_port("Auto") == "Stub", sp.resolve_port("Auto"))

        # 显式选口不受 Auto 逻辑影响
        check("显式 COM7 原样返回", sp.resolve_port("COM7") == "COM7")
        check("显式 Stub 原样返回", sp.resolve_port("Stub") == "Stub")
    finally:
        sp.list_serial_ports = original


def test_window_tick_loop_actually_drives_polar_stage() -> None:
    """证明窗口的阶段列表真的会执行 `polar.link`，而不是靠测试手动调用。

    之前的测试都是直接调 `manager.tick_polar_link(...)`，那并不能证明
    `polar_app` 的 stage 已接上。更危险的是 `_run_tick_stage` 会吞异常：
    如果那个 lambda 坏了，车上会静默失效，所有绿测试都发现不了。
    """

    import types

    from PySide6.QtWidgets import QApplication

    from polar_integration_test import Pipe, PipeSerial, VirtualRobot
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    settle_window_lifecycle(window, app)
    audio = window.registry.get("AudioFeedback")
    if audio is not None:
        audio.enabled = False

    to_robot, to_host = Pipe(), Pipe()
    robot = VirtualRobot(to_robot, to_host, consumes_chassis=True)
    endpoint = PipeSerial(to_robot, to_host)
    fake = types.ModuleType("serial")
    fake.Serial = lambda *a, **k: endpoint  # type: ignore[attr-defined]
    saved = sys.modules.get("serial")
    sys.modules["serial"] = fake

    try:
        link = window.registry.get("RobotLink")
        window.apply_scenario("OfficialEvent", close_home=False)
        settle_window_lifecycle(window, app)
        # 显式端口，绕开 Auto 的真实串口枚举
        link.get_setting("Port").value = "COM99"
        window.backend.polar.port = "COM99"
        # 保持真写：串口已被 fake，字节只是进内存管道；而且这才是实车上的真实形态
        # （mock_writes=True 会连上行一起抑制，就测不到指令回环了）。
        window.backend.polar.mock_writes = False
        robot.start()
        window.backend.polar.start()
        check("窗口侧链路已连上", window.backend.polar.connected is True, window.backend.polar.last_error)

        # 归零错误计数，以便事后判断 polar.link 是否抛过异常
        errors_before = int(getattr(window, "_tick_error_count", 0))
        window._tick_last_error = ""

        window.home_visible = False  # 让逻辑阶段进入后端 tick 分支
        for _ in range(60):
            app.processEvents()
            window.on_tick()
            time.sleep(0.01)

        tick_error = str(getattr(window, "_tick_last_error", ""))
        check(
            "polar.link 阶段未抛异常（没被 _run_tick_stage 吞掉）",
            "polar.link" not in tick_error,
            tick_error[:160],
        )
        check(
            "tick 错误计数未增长",
            int(getattr(window, "_tick_error_count", 0)) == errors_before,
            f"{errors_before} -> {getattr(window, '_tick_error_count', None)}; last={tick_error[:120]}",
        )
        # 关键断言：完全靠窗口自己的 tick 循环驱动，不手动调 manager
        check("窗口 tick 循环使链路判定为活跃", window.state.polar_link_active is True,
              str(window.state.polar_link_age_s))
        check("HUD 底盘朝向被窗口 tick 刷成真值", abs(window.state.chassis_yaw_deg - 49.85) < 0.6,
              str(window.state.chassis_yaw_deg))
        check("诊断快照由窗口 tick 写入",
              isinstance(window.state.backend_stats.get("polar_link"), dict),
              str(list(window.state.backend_stats)[:6]))
        check("机器人收到了窗口 tick 循环发出的帧", robot.host_frames_seen > 0, str(robot.host_frames_seen))
        check("上行帧携带零速（未推杆时不得动车）",
              bool(robot.received_cmds) and all(abs(c[0]) < 1e-6 for c in robot.received_cmds),
              str(robot.received_cmds[:2]))
    finally:
        robot.stop()
        close_window_safely(window, app)
        if saved is not None:
            sys.modules["serial"] = saved
        else:
            sys.modules.pop("serial", None)


def test_rebind_works_through_real_window_and_persists() -> None:
    """在真实窗口上验证改绑：真 QKeyEvent → 真派发 → 真上线字节 → 真配置往返。

    上一轮只在隔离层面测了 set_bind/resolve_slot，那不能证明：
    - ClickGUI 的 Bind 设置真的会同步到 control bridge；
    - 改绑后实际发出的 key.v 位图真的变了；
    - 改绑能穿过 save/load 存活（车上重启就丢绑定是不可接受的）。
    """

    import types
    from unittest.mock import patch

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from polar_integration_test import Pipe, PipeSerial, VirtualRobot
    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    settle_window_lifecycle(window, app)
    audio = window.registry.get("AudioFeedback")
    if audio is not None:
        audio.enabled = False

    to_robot, to_host = Pipe(), Pipe()
    robot = VirtualRobot(to_robot, to_host, consumes_chassis=True)
    endpoint = PipeSerial(to_robot, to_host)
    fake = types.ModuleType("serial")
    fake.Serial = lambda *a, **k: endpoint  # type: ignore[attr-defined]
    saved = sys.modules.get("serial")
    sys.modules["serial"] = fake
    # This test owns an in-memory robot link, not an MQTT broker. A real
    # failed broker connection correctly clears held keys asynchronously and
    # makes a serial binding assertion depend on unrelated network timing.
    mqtt_start = patch.object(window.backend.mqtt, "start", return_value=None)
    mqtt_start.start()

    try:
        window.apply_scenario("OfficialEvent", close_home=False)
        settle_window_lifecycle(window, app)
        link = window.registry.get("RobotLink")
        link.get_setting("Port").value = "COM99"
        link.get_setting("Drive Channel").value = "virtual_rc"
        link.get_setting("Allow Drive").value = True
        link.enabled = True
        link.get_setting("Client Chassis Drive").value = True
        link.get_setting("Vision NUC Occupied").value = False
        window.backend.polar.port = "COM99"
        window.backend.polar.mock_writes = False
        window.backend.polar.drive_channel = "virtual_rc"
        robot.start()
        window.backend.polar.start()
        window.home_visible = False
        window.gui_visible = False
        window.economy_visible = False

        def press(qt_key):
            ev = QKeyEvent(QEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier)
            return window.handle_control_key_event(ev, True)

        def release(qt_key):
            ev = QKeyEvent(QEvent.Type.KeyRelease, qt_key, Qt.KeyboardModifier.NoModifier)
            return window.handle_control_key_event(ev, False)

        # 基线：默认绑定下按 W 应驱动 W 槽
        window.sync_control_binds()
        check("默认绑定下按物理 W 被接受", press(Qt.Key.Key_W) is True)
        check("control 记下了 W 槽按下", window.control.key_state["W"] is True)
        release(Qt.Key.Key_W)

        # 改绑：把“前进”从 W 换到↑，走 ClickGUI 设置 + after_setting_changed 同一条路
        bind_setting = link.get_setting("Bind W")
        check("ClickGUI 暴露了 Bind W 设置项", bind_setting is not None)
        check("Up 在可绑定候选里", "Up" in (bind_setting.options or []), str(bind_setting.options)[:60])
        bind_setting.value = "Up"
        window.after_setting_changed(bind_setting)
        check("after_setting_changed 已把绑定同步到 control",
              window.control.binds.get("W") == "Up", str(window.control.binds.get("W")))

        # 改绑后：按↑ 应该驱动 W 槽，按 W 应该无效
        check("改绑后按↑被接受", press(Qt.Key.Key_Up) is True)
        check("改绑后↑驱动了 W 槽", window.control.key_state["W"] is True)
        release(Qt.Key.Key_Up)
        window.control.reset_inputs()
        check("改绑后按原物理 W 不再驱动任何槽", press(Qt.Key.Key_W) is False)
        check("且 W 槽仍为松开", window.control.key_state["W"] is False)

        # 真上线字节：驱动↑ 后，机器人收到的 0x03 应带 bit0
        window.control.reset_inputs()
        robot.received_cmds.clear()
        press(Qt.Key.Key_Up)
        for _ in range(6):
            app.processEvents()
            window.on_tick()
            time.sleep(0.02)
        keyed = [c for c in robot.received_cmds]
        check("机器人收到了上位机帧", len(keyed) > 0, str(len(keyed)))
        # 直接验证客户端提交的 intent（原始字节已被机器人消费）
        intent = window.backend.polar._intent
        check("已授权时 intent 携带 key.v bit0",
              intent is not None and (intent.key_value & 1) == 1,
              str(None if intent is None else hex(intent.key_value)))
        # Preserve the safety contract separately: a real MQTT disconnect
        # callback must revoke this same active key sample before any reuse.
        mqtt_client = types.SimpleNamespace(disconnect=lambda: None, loop_stop=lambda: None)
        window.backend.mqtt.client = mqtt_client
        window.backend.mqtt._on_disconnect(mqtt_client, None, _reason_code="test broker loss")
        check("MQTT 断开通知清除已按住的改绑驾驶键", window.control.key_state["W"] is False)
        # 注意：intent 本身保留完整驾驶意图是正常的；真正要验的是**上线字节**。
        # virtual_rc 通道下，机器人收到的 0x01 帧 speed_vector 必须全零，
        # 否则会与 key.v 通路的两套斜坡叠加。
        zero_speed_on_wire = bool(robot.received_cmds) and all(
            abs(c[0]) < 1e-9 and abs(c[1]) < 1e-9 for c in robot.received_cmds
        )
        check("virtual_rc 通道下上线的 0x01 帧 speed_vector 全零",
              zero_speed_on_wire, str(robot.received_cmds[:3]))
        release(Qt.Key.Key_Up)

        # 配置往返：绑定必须穿过 save/load 存活
        snapshot = window.current_config_data()
        saved_w = (snapshot.get("modules", {}).get("RobotLink", {}) or {}).get("settings", {}).get("Bind W")
        check("Bind W 已进入导出的配置快照", saved_w == "Up", str(saved_w))
        window.control.reset_binds()
        link.get_setting("Bind W").value = "W"
        window.sync_control_binds()
        check("已手动回到默认", window.control.binds.get("W") == "W", str(window.control.binds.get("W")))
        window.apply_config_data(snapshot, announce=False)
        settle_window_lifecycle(window, app)
        check("load 配置后绑定恢复为 Up", window.control.binds.get("W") == "Up",
              str(window.control.binds.get("W")))
        check("load 后设置项显示也为 Up", str(link.get_setting("Bind W").value) == "Up",
              str(link.get_setting("Bind W").value))
    finally:
        mqtt_start.stop()
        robot.stop()
        close_window_safely(window, app)
        if saved is not None:
            sys.modules["serial"] = saved
        else:
            sys.modules.pop("serial", None)


def _references_retired_scenario() -> list[str]:
    """扫描启动页渲染与按键表，找出引用了不存在场景的硬编码。

    存在的意义：删除一个场景时，只要有一处枚举没同步改，启动页就会
    在渲染时对不存在的键抛 KeyError（真实发生过，且单元测试全部绿着）。
    这条守卫把“枚举表 ⊆ SCENARIOS”固定成可验证性质。
    """

    import inspect

    from rm_client.scenarios import SCENARIOS
    from rm_client.ui.polar.renderers import startup_scene as ss

    known = set(SCENARIOS)
    suspects = {"TeamBench", "OfficialEvent", "CampusSimulation", "LabDebug"}
    bad: list[str] = []
    for fn in (
        ss.StartupSceneMixin.draw_startup_scene_launcher,
        ss.StartupSceneMixin.draw_liquid_startup_scene_launcher,
        ss.StartupSceneMixin.handle_startup_key,
    ):
        src = inspect.getsource(fn)
        for name in suspects:
            if '"%s"' % name in src and name not in known:
                bad.append(f"{fn.__name__}->{name}")
    return bad


def test_hud_renders_role_fields_and_survives_their_absence() -> None:
    """兵种标签上屏：不仅要能画出来，字段缺失时也不能崩。

    本会话里出现过“数据对、渲染器崩”且单元测试全绿的情形，所以光断言
    stats 里有字段不够，必须真的走一遍 QPainter 渲染。
    """

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    settle_window_lifecycle(window, app)
    audio = window.registry.get("AudioFeedback")
    if audio is not None:
        audio.enabled = False
    window.ui_audio_enabled = False

    def paint(stats) -> int:
        """把给定 polar_link 统计画进一张图，返回非背景像素数。

        面板有入场动画（初始透明度 0），只画一次会得到全黑——那不是
        “没画出来”而是动画未结算，会把断言变成假阳性。先反复调用+processEvents
        让动画结算，再取最后一帧计数（与已有像素断言同一做法）。
        """
        import time

        bg = QColor("#0b121b")
        image = QImage(520, 320, QImage.Format.Format_ARGB32_Premultiplied)
        window.state.backend_stats["polar_link"] = stats
        for _ in range(30):
            app.processEvents()
            image.fill(bg)
            painter = QPainter(image)
            try:
                window.draw_rm_indicators_hud(painter, 520)
            finally:
                painter.end()
            time.sleep(0.02)
        count = 0
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                if image.pixelColor(x, y).name() != bg.name():
                    count += 1
        return count

    base = {
        "active": True, "link_alive": True, "port": "COM7", "resolved_port": "COM7",
        "segments_received": 14, "segment_counts": {"0x0A": 6, "0x02": 6},
        "decode": {"crc8_errors": 0, "crc16_errors": 0, "length_errors": 0, "unknown_segments": 0},
        "authority": {"state": "CLIENT", "reason": ""},
        "motion": {"state": "OK", "detail": "", "commanded_mps": 1.5, "measured_mps": 1.4},
        "last_error": "", "last_error_code": "",
    }
    with_role = dict(base, **{
        "role_id": 1, "role_label": "英雄", "role_hud_code": "HERO",
        "role_chassis": "麦轮", "role_max_linear_mps": 2.0, "role_max_yaw_rps": 2.0,
        "role_known": True, "role_drive_block": "", "role_gimbal_block": "",
        "role_selected_id": 1, "role_reported_id": 1, "role_mismatch": "",
        "gimbal_egress": "polar",
    })

    try:
        # 先跨一轮预热，让面板动画彻底结算（否则首组断言会因动画未起而全 0）
        warm = paint(base)
        pixels_plain = paint({k: v for k, v in base.items()})
        check("无兵种字段时 HUD 不崩且仍产出像素", pixels_plain > 200, str(pixels_plain))

        pixels_role = paint(with_role)
        check("带兵种字段时 HUD 不崩且仍产出像素", pixels_role > 200, str(pixels_role))

        pixels_mismatch = paint(dict(with_role, role_mismatch="界面选 3 但自报 1",
                                     role_selected_id=3, role_reported_id=1))
        check("兵种不一致时 HUD 不崩", pixels_mismatch > 200, str(pixels_mismatch))

        pixels_blocked = paint(dict(with_role, role_drive_block="哨兵不允许客户端驾驶"))
        check("驾驶被兵种禁止时 HUD 不崩", pixels_blocked > 200, str(pixels_blocked))

        # 字段存在但值脏（None / 字符串）也不能崩：旧配置或规格回退时会出现
        pixels_dirty = paint(dict(with_role, role_max_linear_mps=None,
                                  role_selected_id="abc", role_hud_code=None))
        check("脏字段值下 HUD 不崩", pixels_dirty > 200, str(pixels_dirty))

        # 链路未活跃分支（走 LINK OFF 路径）也不能因新增代码崩
        pixels_off = paint({"active": False, "link_alive": False, "port": "Auto",
                            "last_error_code": "NO_ROBOT_USB", "last_error": "未找到机器人 USB 串口"})
        check("链路未活跃时 HUD 不崩", pixels_off > 200, str(pixels_off))
    except Exception as exc:
        check("HUD 渲染兵种信息不抛异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        close_window_safely(window, app)


def _run_startup_checks() -> int:
    test_port_auto_selection_safety()
    from PySide6.QtWidgets import QApplication

    from rm_client.scenarios import SCENARIO_MODES, SCENARIOS
    from rm_client.ui.polar_app import PolarRMWindow

    check(
        "启动页仍是原有三个场景，没有多出第四个模式",
        SCENARIO_MODES == ["OfficialEvent", "CampusSimulation", "LabDebug"],
        str(SCENARIO_MODES),
    )
    check("TeamBench 已彻底移除，不留悬空配置", "TeamBench" not in SCENARIOS)

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    settle_window_lifecycle(window, app)
    check("窗口构造成功", window is not None)
    # 本测试不验证音效；UI 音频会在进程退出时触发 Qt Multimedia 拆退崩溃
    # （Windows 上 0xC0000409），会污染退出码、遮住真实结果。
    audio = window.registry.get("AudioFeedback")
    if audio is not None:
        audio.enabled = False
    window.ui_audio_enabled = False

    # 启动页只能枚举 SCENARIO_MODES 里的场景：任何一处硬编码多出第四个键，
    # 渲染时就会对不存在的场景 KeyError（曾经真实发生过）。
    meta = [window.startup_scenario_meta(key)["title"] for key in SCENARIO_MODES]
    check("三个场景都有各自元数据（不是回退值）",
          len(meta) == 3 and all("POLAR" not in m for m in meta), str(meta))
    check(
        "渲染路径与按键表都不引用已删除的场景",
        not _references_retired_scenario(),
    )

    # 真正走启动页选场景的那条路径（startup_scene.apply_scenario）：
    # 它会同时同步 scenario_manager / registry / runtime / backend，
    # 只调 backend.apply_scenario 会漏掉 registry 同步。
    window.apply_scenario("OfficialEvent", close_home=False)
    settle_window_lifecycle(window, app)
    link = window.registry.get("RobotLink")
    before = (str(link.get_setting("Allow Drive").value), str(link.get_setting("Port").value),
              bool(link.enabled))
    check("apply_scenario('OfficialEvent') 不抛异常",
          window.backend.scenario_key == "OfficialEvent", window.backend.scenario_key)
    app.processEvents()
    after = (str(link.get_setting("Allow Drive").value), str(link.get_setting("Port").value),
             bool(link.enabled))
    # 队内链路是独立于场景的并行通道：切场景不得覆盖用户已选好的 COM 口、
    # 不得擅改驾驶授权、也不得把模块静默关掉。
    check("切场景不会覆写 RobotLink 的任何设置（模块自立）", before == after, f"{before} -> {after}")
    check(
        "RobotLink 在官方场景下同样可用（不靠专属模式）",
        link.get_setting("Port") is not None and link.get_setting("Drive Channel") is not None,
    )
    # 两条通道并行的直接证据：官方场景下裁判/经济链路是真发，
    # 同时队内 0x5A 链路也可用。早期把队内链路做成专属场景时，
    # 这两者发生互斥（选了官方就没队内，选了队内官方就全 mock）。
    check(
        "官方场景下经济链路为真发（不被队内链路拖回 mock）",
        window.registry.get("EconomyPanel").get_setting("Mock Requests").value is False,
        str(window.registry.get("EconomyPanel").get_setting("Mock Requests").value),
    )
    check(
        "官方场景下官方 MQTT 为真发，与队内链路并行",
        window.mqtt.mock_output is False,
        str(window.mqtt.mock_output),
    )

    # 灌入模拟下位机字节流：走 transport 的真实解码入口，不是直接塞 bridge
    transport = window.backend.polar
    transport.mock_writes = True  # 离线验证，不写真硬件
    stream = robot_stream()
    for byte in stream:  # 逐字节，模拟最坏的分片到达
        transport._rx_buffer.append(byte)
        segments, consumed = transport.codec.decode_with_consumed(bytes(transport._rx_buffer))
        if consumed:
            del transport._rx_buffer[:consumed]
        for segment in segments:
            transport._rx.append(segment)
    received = len(transport._rx)
    check("6 条下行段经分片全部解出", received == 6, str(received))

    # 跑一帧完整 tick（含新加的 polar.link 阶段）
    before = window.state.chassis_yaw_deg
    window.on_tick() if hasattr(window, "on_tick") else window.backend.tick_polar_link(
        window.state, window.registry, window.control
    )
    for _ in range(3):
        window.backend.tick_polar_link(window.state, window.registry, window.control)
    app.processEvents()

    state = window.state
    check("链路被判定为活跃", bool(state.polar_link_active), str(state.polar_link_age_s))
    check(
        "HUD 底盘朝向已是机器人实测 57.296°",
        abs(state.chassis_yaw_deg - 57.2957795) < 1e-3 and abs(state.chassis_yaw_deg - before) > 1e-6,
        f"before={before} after={state.chassis_yaw_deg}",
    )
    check("云台朝向/俯仰为真值", abs(state.gimbal_yaw_deg - 74.4845) < 1e-3 and abs(state.gimbal_pitch_deg - 11.4591) < 1e-3, str((state.gimbal_yaw_deg, state.gimbal_pitch_deg)))
    check("功率上限为真值", state.power_limit == 460, str(state.power_limit))
    check("实测速度已入 state", abs(state.measured_vx_mps - 1.1) < 1e-5, str(state.measured_vx_mps))
    check("部件错误位已入 state", state.gimbal_error is True and state.wheel_online is True)

    # 驾驶三闸门在真实对象上的行为
    link = window.registry.get("RobotLink")
    link.enabled = True
    # Allow Drive 现在也是纯模块设置（不再由场景代写），所以必须显式打开；
    # 默认值是 False（fail-closed）。
    link.get_setting("Allow Drive").value = True
    link.get_setting("Mock Writes").value = False
    link.get_setting("Client Chassis Drive").value = True
    link.get_setting("Vision NUC Occupied").value = True
    link.get_setting("Client Gimbal Control").value = True

    class FakeCommand:
        vx, vy, wz = 1.0, 0.0, 0.0

    window.control.last_command = FakeCommand()
    window.backend.tick_polar_link(state, window.registry, window.control)
    intent = transport._intent
    check("三闸齐全后底盘速度上线", intent is not None and abs(intent.vx - 1.5) < 1e-6, str(intent))
    check("视觉 NUC 占用时云台不让客户端发", intent is not None and intent.gimbal_yaw_rad == 0.0, str(intent and intent.gimbal_yaw_rad))
    check("仲裁为 PASSIVE", window.backend.polar_arbiter.state == "PASSIVE", window.backend.polar_arbiter.state)
    check("授权状态已回写供 HUD 显示", state.polar_drive_armed is True and state.polar_gimbal_client is False)

    link.get_setting("Vision NUC Occupied").value = False
    state.vision_calibrated = True
    state.target_locked = True
    state.vision_aim_yaw_deg = 30.0
    state.vision_aim_pitch_deg = 2.0
    window.backend.tick_polar_link(state, window.registry, window.control)
    intent = transport._intent
    check(
        "NUC 退出后客户端接管云台并按弧度发送",
        window.backend.polar_arbiter.state == "CLIENT" and abs(intent.gimbal_yaw_rad - struct.unpack("<f", struct.pack("<f", 0.0))[0]) >= 0.0 and intent.gimbal_yaw_rad > 0.0,
        str((window.backend.polar_arbiter.state, intent.gimbal_yaw_rad)),
    )

    # 真实上线字节：编码后 fire 必须仍为 0
    encoded = transport._frame_for(intent)
    check("上线帧长 49", len(encoded) == 49, str(len(encoded)))
    check("上线帧 fire 恒 0", encoded[4 + 4 + 36] == 0, str(encoded[4 + 4 + 36]))

    # stats 必须可 JSON 序列化（会被写进现场日志）
    stats = window.backend.stats()
    check("stats 含 polar_link 段", "polar_link" in stats, str(list(stats)[:6]))
    try:
        text = json.dumps(stats["polar_link"], default=str)
        check("polar_link stats 可 JSON 序列化", len(text) > 10, "")
    except Exception as exc:
        check("polar_link stats 可 JSON 序列化", False, repr(exc))
    check(
        "stats 报告了已解码段计数",
        stats["polar_link"]["segment_counts"].get("0x1D") == 1,
        str(stats["polar_link"]["segment_counts"]),
    )

    # 急停必须立刻把链路控制锁零
    window.backend.trigger_estop()
    window.backend.tick_polar_link(state, window.registry, window.control)
    check("急停后仲裁为 ESTOP", state.polar_authority_state == "ESTOP", state.polar_authority_state)
    check("急停后底盘意图被拒", state.polar_drive_armed is False)
    check("急停传导到队内链路", transport._estop_active is True)

    # 诊断行必须真的被渲染出来：hotspot 注册 + 非背景像素，两个都要。
    # （面板透明度动画 initial=0.0，只靠一次绘制会拿到空图，必须跨时间预热。）
    try:
        import time

        from PySide6.QtGui import QColor, QImage, QPainter

        image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        bg = QColor("#0b121b")
        for _ in range(30):
            app.processEvents()
            image.fill(bg)
            painter = QPainter(image)
            window.draw_rm_indicators_hud(painter, 1280)
            painter.end()
            time.sleep(0.03)
        window.hotspots.clear()
        image.fill(bg)
        painter = QPainter(image)
        window.draw_rm_indicators_hud(painter, 1280)
        painter.end()

        nonblank = 0
        for yy in range(70, 300, 3):
            for xx in range(1000, 1270, 3):
                if image.pixelColor(xx, yy).name() != bg.name():
                    nonblank += 1
        check(
            "RMIndicatorsHud 真的画出了像素",
            nonblank > 200,
            f"nonblank={nonblank}",
        )
        actions = {getattr(h, "action", None) for h in window.hotspots}
        check(
            "链路诊断行已渲染（polar_link hotspot 已注册）",
            "polar_link" in actions,
            str(sorted(str(a) for a in actions))[:160],
        )
        diag = [h for h in window.hotspots if getattr(h, "action", None) == "polar_link"]
        tip = str(getattr(diag[0], "tooltip", "")) if diag else ""
        check(
            "诊断 tooltip 包含端口与 CRC 错误信息",
            "端口" in tip and "CRC8" in tip,
            tip[:120],
        )
        payloads = {getattr(h, "payload", None) for h in window.hotspots if getattr(h, "action", None) == "polar_link"}
        check(
            "实测速度/云台俯仰遥测行已渲染（新真值不再无人读）",
            "telemetry" in payloads,
            str(sorted(str(p) for p in payloads)),
        )
        telem = [h for h in window.hotspots if getattr(h, "payload", None) == "telemetry"]
        telem_tip = str(getattr(telem[0], "tooltip", "")) if telem else ""
        check(
            "遥测 tooltip 带实测速度与云台控制权状态",
            "实测速度" in telem_tip and "云台控制权" in telem_tip,
            telem_tip[:120],
        )
    except Exception as exc:
        check("RMIndicatorsHud 渲染", False, repr(exc))

    # 失焦/关闭路径：必须归零后才关端口
    window.control.release_all(window.state, window.registry)
    window.backend.tick_polar_link(window.state, window.registry, window.control)
    check("失焦释放按键后驾驶意图归零", transport._intent is None or (transport._intent.vx, transport._intent.vy) == (0.0, 0.0))

    close_window_safely(window, app)

    # 最后跑：验证窗口自身的 tick 循环能驱动 polar.link 阶段
    test_window_tick_loop_actually_drives_polar_stage()

    # 验证驾驶键改绑在真实窗口上生效且能持久化
    test_rebind_works_through_real_window_and_persists()
    test_hud_renders_role_fields_and_survives_their_absence()

    check("虚拟机器人启动验证未尝试 Python 网络访问", not NETWORK_ATTEMPTS, str(NETWORK_ATTEMPTS))
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("ALL POLAR_STARTUP TESTS PASSED")
    return 0


def main() -> int:
    from unittest.mock import patch
    from rm_client.backend.mqtt_backend import RmMqttBackend
    from rm_client.backend.video_backend import RmVideoBackend
    global _NETWORK_GUARD_ACTIVE
    NETWORK_ATTEMPTS.clear()
    _NETWORK_GUARD_ACTIVE = True
    try:
        # These are unrelated native network sources, not control/safety
        # policies. Real serial framing and in-memory robot delivery stay on.
        with patch.object(RmMqttBackend, "start", return_value=None), \
             patch.object(RmVideoBackend, "start", return_value=None):
            return _run_startup_checks()
    finally:
        _NETWORK_GUARD_ACTIVE = False


if __name__ == "__main__":
    raise SystemExit(main())
