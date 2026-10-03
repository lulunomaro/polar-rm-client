"""后端管理器：把 SharkClient 风格后端对齐本地三种运行场景。

LabDebug / CampusSimulation / OfficialEvent 各自：
- MQTT：真实连接（可收）、按场景决定是否真实发布（LabDebug mock）
- UDP：监听图传端口、codec 检测、H.264/H.265/MJPEG 解码、统计
- 串口：仅保留非图传链路接口；明确拒绝把 0x0311 写入 115200 串口
- 安全：KMC/CustomControl 75Hz、事件命令 10Hz、阶段门控与急停

同时把全量下行消息解码结果写入 RobotState，并提供 V1.3.1 上行编码入口。
"""

from __future__ import annotations

import logging
import json
import time
import threading
from typing import Any, Callable

from ..model import RobotState
from ..native_viewer_telemetry import parse_vehicle_telemetry, parse_video_0310_chunk
from .. import robot_roles
from ..scenarios import SCENARIOS
from ..settings_access import collect_settings
from . import cc_link
from . import dt7_link
from . import protocol as proto
from .mqtt_backend import RmMqttBackend
from .polar_bridge import (
    PolarControlArbiter,
    PolarMotionVerifier,
    PolarStateBridge,
    scale_drive,
)
from .polar_link import (
    SEG_CHASSIS_STATE,
    SEG_ROBOT_MOTION,
    SEG_ROBOT_STATUS,
    SPIN_CMD_NONE,
    SPIN_CMD_OFF,
    SPIN_CMD_ON,
    deg_to_rad,
)
from .polar_transport import PolarDriveIntent, PolarLinkTransport
from .safety import OfficialSafety
from .serial_backend import RmSerialBackend
from .video_backend import AnnexBRecoveryGate, ContinuousMjpegDecoder, RmVideoBackend
from .field_log import RotatingJsonlLogger
from .qualification import QualificationEvidenceStore, QualificationManager
from ..runtime_paths import RuntimePaths

#: ``RobotLink`` 模块里、队内链路每帧要看的设置项。
#:
#: 提成模块级常量是因为它以前内联在 ``tick_polar_link`` 的字典推导式里 ——
#: 方法本身已经 560 行，再往中间塞一个 25 项的元组字面量，加一项要翻半天。
#: 现在这里是唯一声明处，而且 tools/test_settings_are_read.py 那套
#: "设置项有没有人读"的审计可以直接对着它核对。
ROBOT_LINK_SETTING_NAMES: tuple[str, ...] = (
    "Allow Drive",
    "Link Mode",
    "Bridge Host",
    "Bridge Port",
    "Mock Writes",
    "Allow Spin Key",
    "Radio KM Egress",
    "Custom Controller Egress",
    "Direct DT7 Egress",
    "DT7 Port",
    "DT7 Real TX",
    "DT7 Allow Fire Keys",
    "CC Port",
    "CC Hz",
    "CC Mock Writes",
    "Client Chassis Drive",
    "Client Gimbal Control",
    "Vision NUC Occupied",
    "Speed Scale %",
    "Yaw Scale %",
    "Gimbal Slew",
    "Drive Channel",
    "Send Hz",
    "Port",
    "Baud",
)

# 官方性能体系取值（对齐 messages.proto 与 Shark RoboModeSettings）
HERO_MELEE = 3
HERO_RANGE = 4
SHOOTER_COOLING = 1
SHOOTER_BURST = 2
CHASSIS_HEALTH = 1
CHASSIS_POWER = 2
SENTRY_AUTOMATIC = 0
SENTRY_SEMI_AUTO = 1

# 队内链路未连上时的重连间隔（秒）。Auto 重连会重新枚举串口。
POLAR_RETRY_S = 2.0
# RobotLink 里这两个控件的"没动过"长什么样：拿它们当哨兵，
# 默认值时让场景配置继续说话（见 tick_polar_link 里的 precedence 注释）
PORT_DEFAULT = "Auto"
BAUD_DEFAULT = 115200

_RUNE_PHASE = {1: "INACTIVE", 2: "ACTIVATING", 3: "ACTIVE"}
# ??? ID ? ?????? 1-9 / ?? 101-109??? = ?? + 100?
ROBOT_ID_NAMES = {1: "Hero", 2: "Engineer", 3: "Infantry", 4: "Infantry", 5: "Infantry",
                  6: "Aerial", 7: "Sentry", 8: "Dart", 9: "Radar"}
# ?? BuffType??? Shark mqtt_protocol.ts?
BUFF_TYPE_NAMES = {1: "Attack Boost", 2: "Defense Boost", 3: "Heat Cooling",
                   4: "Chassis Power", 5: "Health Regen", 6: "Exchange Ammo", 7: "Terrain"}
DEFAULT_MAX_HEALTH = 600
_STAGE_STR = {0: "NOT_STARTED", 1: "PREPARE", 2: "SELF_CHECK", 3: "COUNTDOWN", 4: "MATCH", 5: "SETTLEMENT"}
_HEALTH_ROLE_ORDER = (1, 2, 3, 4, 7)
_LOG = logging.getLogger(__name__)


class BackendManager:
    """本地三种模式的 SharkClient 对齐后端。"""

    def __init__(
        self,
        workspace: Any = None,
        state: RobotState | None = None,
        client_id: str = "3",
        scenario_key: str = "LabDebug",
    ) -> None:
        self.paths = RuntimePaths.discover()
        self.workspace = workspace if workspace is not None else self.paths.workspace_root
        self.state = state
        self.scenario_key = scenario_key
        self.safety = OfficialSafety(scenario_key)
        self.mqtt = RmMqttBackend(
            host="127.0.0.1", port=3333, client_id=client_id,
            mock_output=True, safety=self.safety,
        )
        self.udp = RmVideoBackend(host="127.0.0.1", port=3334)
        self.serial = RmSerialBackend(port="Stub", baud=115200, mock_writes=True, safety=self.safety)
        # 队内 StandardRobot++ 0x5A 链路。与 referee 串口是两条独立物理链路：
        # 那条走官方 0xA5 裁判帧，这条走机器人自己的上位机协议。
        self.polar = PolarLinkTransport(
            port="Stub", baud=115200, mock_writes=True, safety=self.safety
        )
        self.polar_bridge = PolarStateBridge()
        self.polar_arbiter = PolarControlArbiter()
        self.polar_motion = PolarMotionVerifier()
        # 队内链路自动重连节律：启动后才插机器人、机器人中途重启都能接回。
        self._polar_last_connect_attempt = 0.0
        self._polar_last_logged_error = ""
        self._polar_gimbal_last: tuple[float, float] | None = None
        # 自转指令（0x04）状态机，见 _update_spin_command。
        #: 客户端是否曾经主动要求过“开自转”。只有为真时才允许发 OFF——
        #: 否则驾驶员用自己遥控器开的自转会被客户端擅自关掉。
        self._spin_ever_on = False
        #: 开始发 OFF 的时刻；无 0x1F 回读时用它做有界压制。
        self._spin_off_since = 0.0
        #: 当前正压在线上的指令，供诊断/HUD 读取。
        self._spin_assert = SPIN_CMD_NONE
        #: 是否收到过下行 0x1F。没收到就说明固件未打 host_bridge 补丁，
        #: 自转只能降级为“指令已发、无法确认”，不得假装成功。
        self.polar_chassis_state_seen = False
        self.native_decoder = ContinuousMjpegDecoder("h264")
        self._native_recovery = AnnexBRecoveryGate("h264")
        # Native 0x0310 is a continuous stream.  Retaining every payload in a
        # bytearray caused unbounded memory growth during a full match even
        # though ffmpeg already consumes the bytes.  Keep counters and sequence
        # recovery state only.
        self._native_0310_bytes_since_reset = 0
        self._native_0310_last_sequence: int | None = None
        self._native_0310_dropped_chunks = 0
        self._native_0310_out_of_order_chunks = 0
        self._native_0310_recovery_dropped_chunks = 0
        self._native_0310_blocked_until_sps = True
        self._native_0310_decoder_restarts = 0
        self._native_0310_decoder_started_once = False
        self._native_next_restart_at = 0.0
        # 第 98–99 轮：native 解码器的阻塞重启跑在工作线程上。
        self._native_restart_thread = None
        self._native_last_health = {}
        self._native_restart_backoff_s = 0.5
        self._native_failure_epoch_handled = False
        self._apply_error_count = 0
        self._last_apply_error = ""
        self._official_state_updates_count = 0
        self._mqtt_disconnect_lock_applied = False
        self._mqtt_disconnect_lock_at = 0.0
        self._mqtt_reconnected_locked_at = 0.0
        self._active_video_source = ""
        self._normal_video_source = ""
        self._hero_native_override = False
        self._estop_triggered_at = 0.0
        self._last_estop_zero_ok = False
        self._last_estop_zero_reason = "not run"
        self._input_clear_generation = 0
        self.input_clear_hook: Callable[[str], None] | None = None
        self._last_logged_stage = ""
        self.field_log = RotatingJsonlLogger(self.paths.logs_root)
        self._last_spin_note = ''
        self.safety.input_clear_hook = self._request_input_clear
        self.mqtt.event_hook = self._log_backend_event
        self.udp.event_hook = self._log_backend_event
        build_commit = self._build_commit()
        self.qualification = QualificationManager(
            QualificationEvidenceStore(self.paths.evidence_root, build_commit=build_commit)
        )
        self.config = SCENARIOS.get(scenario_key, SCENARIOS["LabDebug"])
        self._apply_config(self.config)

    # ------------------------------------------------------------------
    # 场景应用
    # ------------------------------------------------------------------
    def _apply_config(self, config: Any) -> None:
        self.config = config
        self.safety.from_scenario(config)
        # 走 set_address 而不是直接赋值：赋值不会重连（见 mqtt_backend.set_address）
        self.mqtt.set_address(config.connection.mqtt_host, config.connection.mqtt_port)
        self.mqtt.mock_output = not bool(config.safety.allow_mqtt_publish)
        self.udp.host = config.connection.udp_host
        self.udp.port = int(config.connection.udp_port)
        self.serial.port = config.connection.serial_port
        self.serial.baud = int(config.connection.serial_baud)
        self.serial.mock_writes = bool(config.safety.mock_writes)
        # 队内链路：端口与驾驶能力位随场景切换。非台架场景 polar_port 保持
        # Stub，transport 会直接不启动，因此不会意外往串口写任何东西。
        self.polar.port = str(getattr(config.connection, "polar_port", "Stub"))
        self.polar.baud = int(getattr(config.connection, "polar_baud", 115200))
        self.polar.send_hz = float(getattr(config.connection, "polar_send_hz", 50.0))
        # 队内链路的写入开关不在此推导：它只由 RobotLink 模块自己的
        # “Mock Writes” 设置项决定（见 _configure_polar_link）。
        # 这里先置 fail-closed 默认，等模块表态后再打开。
        self.polar.mock_writes = True
        if self.state is not None:
            self.state.mock_simulation_enabled = config.key == "LabDebug"
            if config.key != "LabDebug" and not self.state.official_state_active:
                self._clear_mock_frontend_state(self.state)
        self.set_active_video_source(config.connection.video_source)

    def sync_mqtt_link(self, registry: Any) -> str:
        """让 MQTTLink 的 Host/Port **真的决定**连到哪个 broker。

        以前这两个设置项只有人写、没有人读：``scenarios.py`` 把场景值抄到界面上，
        操作员改了以后后端一行都不动，屏幕上留下的地址是假的。
        非法输入退回当前有效值，并且把有效值写回设置项 —— 宁可覆盖你的手误，
        也不让界面显示一个根本没在用的地址。
        """
        module = registry.get("MQTTLink") if registry is not None else None
        if module is None:
            return ""
        host_setting = module.get_setting("Host")
        port_setting = module.get_setting("Port")
        if host_setting is None or port_setting is None:
            return ""
        want_host = str(host_setting.value or "").strip()
        note = ""
        try:
            want_port = int(port_setting.value)
        except (TypeError, ValueError):
            want_port = -1
        if not want_host or " " in want_host:
            note = "MQTT 地址为空或含空格，沿用 " + str(self.mqtt.host)
            want_host = str(self.mqtt.host)
        if not 1000 <= want_port <= 9999:
            note = (note + "；" if note else "") + "MQTT 端口非法，沿用 " + str(self.mqtt.port)
            want_port = int(self.mqtt.port)
        changed = self.mqtt.set_address(want_host, want_port)
        # 设置项永远等于真正在用的地址：UI 不骗人
        if str(host_setting.value) != self.mqtt.host:
            host_setting.value = str(self.mqtt.host)
        #
        # 端口这一行刻意用 `str(...)` 比较，与上面主机那一行同款，**而不是**
        # `int(port_setting.value or 0) != int(self.mqtt.port)`。
        #
        # 原因是上面 `:250` 已经把 `int(port_setting.value)` 的 ValueError 接住、
        # 退回上一个可用端口了；如果这里再 `int()` **同一个**坏值，异常会从
        # `sync_mqtt_link` 抛出去 —— 而这个函数是 UI 每拍（60 Hz）调的，
        # 于是操作员在 MQTTLink 的 Port 里打错一个字之后，
        # 每 16 ms 记一次 ValueError，`backend_stats["ui_tick"]["last_error"]`
        # 被永久钉住，**而且 `self.mqtt.last_error` 永远设不上**，
        # 那句"端口非法，沿用 N"的现场提示就再也不会出现。
        #
        # （broker 地址本身**不受影响**：`set_address` 在上面 :259 已经跑完。
        #  坏掉的是"回写设置项"与"留一句提示"，不是连不上 broker。）
        if str(port_setting.value) != str(int(self.mqtt.port)):
            port_setting.value = int(self.mqtt.port)
        self.mqtt.last_error = note
        if changed:
            print(f"[mqtt] broker 改为 {self.mqtt.host}:{self.mqtt.port}")
        return note

    @staticmethod
    def _clear_mock_frontend_state(state: RobotState) -> None:
        state.robot_rows.clear()
        state.map_robots.clear()
        state.map_markers.clear()
        state.buff_rows.clear()
        # Official/Campus scenes must not display the LabDebug countdown as
        # referee ammunition.  The next GlobalUnitStatus sample repopulates the
        # applicable calibre; until then zero is explicitly "no source".
        state.projectile_allowance_17mm = 0
        state.projectile_allowance_42mm = 0
        state.backend_frontend_synced = False

    def _begin_official_state(self, state: RobotState, timestamp: float) -> None:
        if not state.official_state_active:
            self._clear_mock_frontend_state(state)
        state.official_state_active = True
        state.official_state_at = max(float(state.official_state_at), float(timestamp))

    def apply_scenario(self, key: str, *, start: bool = True) -> None:
        key = key if key in SCENARIOS else "LabDebug"
        self.shutdown(restart=True)
        self.scenario_key = key
        self._hero_native_override = False
        self._normal_video_source = ""
        if self.state is not None:
            self._reset_state_for_scenario(self.state)
        config = SCENARIOS[key]
        self._apply_config(config)
        # shutdown() deliberately latches local E-stop.  A scenario switch has
        # already emitted the neutral frame and now creates a fresh fail-closed
        # safety epoch; it must not inherit the old software latch forever.
        self.safety.release_estop()
        if not start:
            return
        # 三种模式都启动 MQTT（可收裁判数据；发布按场景门控）
        self.mqtt.start()
        # UDP socket/HEVC decoder are started only when that source is selected.
        # Native 0x0310 arrives on MQTT and owns its own lazy decoder.
        if self._active_video_source in {"shark udp", "udp", "udp hevc", "hevc", "h265"}:
            self.udp.start()
        # 串口：Stub 或真实端口
        self.serial.start()
        # 队内 0x5A 链路是**与裁判系统并行的独立通道**，不属于任何一个场景：
        # 正式比赛本来就要同时用裁判系统（血量/增益/能量）和机器人 USB（驾驶与本体遥测）。
        # 所以按“有没有配真实端口”决定要不要起，而不是按场景名。
        # 端口为 ""/"Stub" 时 transport 自己会拒连，不会往任何串口写字节。
        self.polar.start()

    def _retry_polar_connect(self) -> None:
        """操作员经常是先启动客户端、后插机器人，或者中途重启机器人。

        `polar.start()` 原本只在 `apply_scenario` 里调一次，所以那种情况下
        链路会永远停在未连接，界面上只会看到 LNK 不亮。这里在“场景要求队内
        链路但未连上”时按固定间隔重试；Auto 也会重新探测端口枚举，因此
        插上新 COM 号能自动接上。
        """

        # 重试的判据是“用户给这条链路配了真端口”，而不是场景名：
        # 否则在大疆官方场景下选了 COM3 也永远不会重连。
        if str(self.polar.port or "").strip() in ("", "Stub", "None"):
            return
        if self.polar.connected or self.polar._running:
            return
        now = time.perf_counter()
        if now - self._polar_last_connect_attempt < POLAR_RETRY_S:
            return
        self._polar_last_connect_attempt = now
        was_connected = self.polar.connected
        self.polar.start()
        if self.polar.connected and not was_connected:
            self.field_log.emit(
                "polar_link_up",
                port=self.polar.resolved_port,
                requested_port=self.polar.port,
                send_hz=self.polar.send_hz,
            )
        elif not self.polar.connected and self._polar_last_connect_attempt > 0.0:
            # 不刷屏：只在端口曾经可用又掉线时记一次
            if self.polar.last_error and self.polar.last_error != self._polar_last_logged_error:
                self._polar_last_logged_error = self.polar.last_error
                self.field_log.emit("polar_link_unavailable", error=self.polar.last_error)

    #: 没有 0x1F 回读时，OFF 指令最多压制这么久然后放手。
    #: 必须有限：无限发 OFF 会永久锁死驾驶员用物理遥控器开自转的能力。
    #: 取 1.0s：足够覆盖固件 150ms 新鲜度窗口与几次丢帧，又不会长到影响操作。
    SPIN_OFF_ASSERT_S = 1.0

    def _update_spin_command(self, *, desired: bool, reported: Any) -> int:
        """把“想要自转开/关”翻成上行 0x04 的显式指令，并用 0x1F 回读收敛。

        三条规则，每条都对应一个真实风险：

        1) 想要开 -> 持续发 ON（**电平**语义）。
           固件 HOST_SPIN_CMD_VALID_MS=150ms 内没刷新就把指令收回 NONE，
           所以必须每 tick 重发，不能只发一次。

        2) 想要关 -> 发 OFF，**直到 0x1F 确认已停**就改发 NONE。
           必须收敛到 NONE：否则会永久压制驾驶员用物理遥控器开自转的能力。
           无回读时用 SPIN_OFF_ASSERT_S 做有界压制兼底。

        3) 从未要求过开 -> 一直发 NONE，完全不干预。
           操作员可能已经用自己的遥控器开了自转，客户端无权替他关。

        返回当前压在线上的指令，供诊断快照与 HUD 使用。
        """

        now = time.perf_counter()
        if desired:
            self._spin_ever_on = True
            self._spin_off_since = 0.0
            cmd = SPIN_CMD_ON
        elif not self._spin_ever_on:
            # 客户端从未主张过自转，不碰驾驶员的状态
            cmd = SPIN_CMD_NONE
        elif reported is None:
            # 固件未打 host_bridge 补丁（不发 0x1F），无法闭环：
            # 有界发 OFF 后放手，宁可“可能没关掉”也不能锁死遥控器。
            if self._spin_off_since <= 0.0:
                self._spin_off_since = now
            if now - self._spin_off_since >= self.SPIN_OFF_ASSERT_S:
                self._spin_ever_on = False
                self._spin_off_since = 0.0
                cmd = SPIN_CMD_NONE
            else:
                cmd = SPIN_CMD_OFF
        elif bool(reported):
            # 回读说还在转，继续发 OFF
            self._spin_off_since = 0.0
            cmd = SPIN_CMD_OFF
        else:
            # 回读确认已停 -> 交还控制权
            self._spin_ever_on = False
            self._spin_off_since = 0.0
            cmd = SPIN_CMD_NONE

        self._spin_assert = cmd
        self.polar.set_spin_command(cmd)
        return cmd

    def tick_polar_link(
        self,
        state: RobotState | None = None,
        registry: Any = None,
        control: Any = None,
    ) -> dict[str, Any]:
        """队内 0x5A 链路的单帧泵：下行入 HUD，已授权的意图上线。

        必须排在 tick 阶段表的**最后**：`RobotState.tick()` 的桌面仿真会写同一批
        字段（底盘/云台朝向、buff、能量），只有后写才能覆盖先写。
        """

        target = state if state is not None else self.state
        if target is None:
            return {"active": False}
        self._retry_polar_connect()
        for segment in self.polar.drain_segments():
            self.polar_bridge.apply(segment)
            if segment.segment_id == SEG_ROBOT_MOTION:
                self.polar_motion.note_motion_segment()
            elif segment.segment_id == SEG_ROBOT_STATUS:
                # 0x0A 带测速模块坐标：没有 0x1E 时用它以位移判定车是否动。
                self.polar_motion.note_position(
                    self.polar_bridge.overlay.get("polar_self_x", 0.0),
                    self.polar_bridge.overlay.get("polar_self_y", 0.0),
                )
            elif segment.segment_id == SEG_CHASSIS_STATE:
                # 0x1F 只有打了 host_bridge 补丁的固件才会发。
                # 它是自转指令唯一的闭环证据，必须单独记一笔。
                self.polar_chassis_state_seen = True

        bridge = self.polar_bridge
        target.polar_link_active = bridge.active
        target.polar_link_age_s = min(bridge.link_age_s, 999.0)
        target.polar_segments_received = sum(bridge.segment_counts.values())
        target.polar_dropped_segments = bridge.dropped
        target.polar_debug_packages = dict(bridge.debug_packages)
        target.polar_module_errors = dict(bridge.module_errors)
        target.polar_overlay = dict(bridge.overlay)
        target.apply_polar_overlay()

        module = registry.get("RobotLink") if registry is not None else None
        # 取法搬到 settings_access.collect_settings，**逐位不变**（取的是原始
        # Setting 对象而不是 value，后面还要读 options/kind）。
        # 名单单独提成模块级常量：它是"这个模块关心哪些设置项"的唯一声明处，
        # 以前内联在字典推导式里，谁加一项要翻到方法中间。
        settings = collect_settings(module, ROBOT_LINK_SETTING_NAMES)
        # RobotLink 的 Port / Baud 要落到传输层：以前只有场景初值
        # （config.connection.polar_port）被用过一次，界面上选口/改波特率**一点作用都没有**,
        # 而出错提示还写着"在 RobotLink.Port 里显式选口"，等于指了一条不起作用的路。
        #
        #  precedence 规则（明确写死，别让两个来源打架）：
        #   * 模块设置保持默认（Auto / 115200）=> 场景配置说了算，行为与接线前逐位相同
        #   * 操作员显式改过 => 以模块设置为准（"我在界面上选了 COM7 就得是 COM7"）
        #   * 显式清空 Port 不回退到"猜一个口"，保持上一次的确定值
        # 之所以不无脑让模块赢：默认值是 Auto，官方场景下会突然去自动找串口，
        # 选错口的后果比"没生效"严重得多。
        port_setting = settings.get("Port")
        if port_setting is not None:
            want_port = str(port_setting.value or "").strip()
            if want_port and want_port != PORT_DEFAULT:
                if want_port != str(self.polar.port or ""):
                    self.polar.port = want_port
                    if self.polar.connected:
                        self.polar.stop()
                    self.polar.last_error = ""
                    self.polar.last_error_code = ""
        baud_setting = settings.get("Baud")
        if baud_setting is not None:
            try:
                want_baud = int(baud_setting.value)
            except (TypeError, ValueError):
                want_baud = 0
            # C 板那条是 USB CDC，固件不看波特率；但换成 UART 桥一类接法时它就是有效参数。
            # 界面既然给了这个框，后端就得读它 —— 但同样只在被显式改过时才覆盖场景。
            if want_baud != BAUD_DEFAULT and 1200 <= want_baud <= 3_000_000 \
                    and want_baud != int(self.polar.baud):
                self.polar.baud = want_baud
                if self.polar.connected:
                    self.polar.stop()
        # 驾驶授权与队内写入开关都属于模块自己，**不能**取场景标志：
        # 大疆官方场景的 allow_polar_drive=False 只表示“本场景不自动授权”，
        # 开不了“操作员手动开了”的情形。早期版本拿它当闸门，导致官方场景下
        # 无论怎么勾选都发不出指令（现由集成测试固定断言）。
        allow_setting = settings["Allow Drive"]
        allow_drive = bool(allow_setting.value) if allow_setting is not None else False
        mock_setting = settings["Mock Writes"]
        if mock_setting is not None:
            self.polar.mock_writes = bool(mock_setting.value)
        slew_setting = settings["Gimbal Slew"]
        if slew_setting is not None and self.polar_arbiter.slew != float(slew_setting.value):
            self.polar_arbiter.slew = max(1.0, min(45.0, float(slew_setting.value)))
        # 发送频率归模块设置管。原来它只在 __init__ 里从场景配置
        # （config.connection.polar_send_hz）读一次，界面上的 “Send Hz” 滑块
        # 根本没人读——拖了、存了、重启也还在，但频率一点没变。
        # 场景配置仍作为初值（见 __init__），模块设置是运行期的唯一真相。
        # D 路：把"字节从哪儿来"这一层切到车端桥上。协议层不动一个字节。
        mode_setting_obj = settings.get("Link Mode")
        if mode_setting_obj is not None:
            want_mode = str(mode_setting_obj.value or "serial").strip().lower()
            want_mode = want_mode if want_mode in ("serial", "tcp") else "serial"
            host_obj = settings.get("Bridge Host")
            port_obj = settings.get("Bridge Port")
            want_host = str(getattr(host_obj, "value", "") or "").strip()
            try:
                want_port = int(getattr(port_obj, "value", 50570) or 50570)
            except (TypeError, ValueError):
                want_port = 50570
            if (self.polar.transport, self.polar.host, self.polar.tcp_port) != (
                    want_mode, want_host, want_port):
                # 换链路等于换一条连接：旧句柄必须关，否则还在往老地方写
                if want_mode == "tcp" and not want_host:
                    self.polar.last_error = "TCP 模式要先填桥接主机地址"
                    self.polar.last_error_code = "NO_BRIDGE_HOST"
                else:
                    was_running = bool(self.polar.connected or self.polar._running)
                    self.polar.stop()
                    self.polar.transport = want_mode
                    self.polar.host = want_host
                    self.polar.tcp_port = want_port
                    if was_running:
                        self.polar.start()
        hz_setting = settings.get("Send Hz")
        if hz_setting is not None:
            try:
                self.polar.set_send_hz(float(hz_setting.value))
            except (TypeError, ValueError):
                # 垃圾值不得把链路带倒：保持当前频率，不报错不中断 tick
                pass
        # 驾驶上行通道：virtual_rc 复用固件已生效的 WASD 驾驶逻辑，
        # 只需固件补一处 VIRTUAL_RC_CTRL → rc_ctrl 接线。
        channel_setting = settings.get("Drive Channel")
        if channel_setting is not None:
            channel = str(channel_setting.value or "robot_cmd").strip().lower().replace(" ", "_")
            self.polar.drive_channel = channel if channel in (
                "robot_cmd",
                "virtual_rc",
                "both",
            ) else "robot_cmd"

        self.polar_arbiter.request_takeover(
            client_gimbal_enabled=bool(
                settings["Client Gimbal Control"].value
            )
            if settings["Client Gimbal Control"] is not None
            else False,
            vision_nuc_occupied=bool(settings["Vision NUC Occupied"].value)
            if settings["Vision NUC Occupied"] is not None
            else True,
        )

        # 急停优先于一切：同帧锁零，且不再给云台/底盘产生任何非零意图。
        # 这里不提前 return：否则下面的诊断快照与授权字段会被跳过，HUD 会卡在旧值。
        estopped = bool(getattr(self.safety, "estop_active", False))
        if estopped:
            self.polar.trigger_estop()
            self._polar_gimbal_last = None
        else:
            self.polar.release_estop()

        plan = None
        if (
            not estopped
            and self.polar_arbiter.may_command_gimbal
            and bridge.active
            and getattr(target, "vision_calibrated", False)
            and getattr(target, "target_locked", False)
            and not robot_roles.gimbal_block_reason(
                int(getattr(target, "robot_id", 0) or 0)
            )
        ):
            plan = self.polar_arbiter.plan_gimbal(
                float(getattr(target, "vision_aim_yaw_deg", 0.0) or 0.0),
                float(getattr(target, "vision_aim_pitch_deg", 0.0) or 0.0),
                bridge.overlay.get("gimbal_yaw_deg"),
                bridge.overlay.get("gimbal_pitch_deg"),
            )
        self._polar_gimbal_last = plan

        chassis_setting = settings["Client Chassis Drive"]
        command = getattr(control, "last_command", None) if control is not None else None
        vx = vy = wz = 0.0
        # 兵种对齐：限幅与可驾驶性查固件参数表（rm_client/robot_roles.py）。
        # 机器人会在 0x0A 里上报自己的编号（`polar_reported_robot_id`），那才是
        # 实际连上的那台车，所以限幅跟它走；操作员选的 `robot_id` 用作交叉校验。
        selected_id = int(getattr(target, "robot_id", 0) or 0)
        reported_id = int(getattr(target, "polar_reported_robot_id", 0) or 0)
        role_id = reported_id or selected_id
        role = robot_roles.limits_for(role_id)
        role_block = robot_roles.drive_block_reason(role_id)
        # 选错车是真实事故源：限速不同（英雄 2.0 / 步兵 1.5），而且给哨兵下
        # 驾驶指令会破坏它的自动巡线。两边都知道时不一致就必须告警。
        role_mismatch = (
            ""
            if not (selected_id and reported_id and selected_id != reported_id)
            else f"界面选的兵种是 {selected_id}，但机器人自报编号是 {reported_id}；"
                 f"限幅与驾驶权限已按实际车辆（{role.label}）执行，请核对界面选择"
        )

        drive_armed = bool(
            not estopped
            and not role_block
            and allow_drive
            and module is not None
            and module.enabled
            and chassis_setting is not None
            and chassis_setting.value
            and bridge.active
        )
        # 无线键鼠出口（0x0311）：界面上那个开关只代表"操作员想开"，
        # 这里补齐另外三道闸——没急停、RobotLink 模块启用、驾驶已授权。
        # 与 drive_armed 的区别是**不要求 polar bridge.active**：这条出口的
        # 设计目的就是不插 USB 线也能控车，拿 USB 链路状态当闸门会把它自己
        # 锁死。（图传那条路自己的频控/门控在 MqttBackend 与车上固件里。）
        radio_setting = settings.get("Radio KM Egress")
        radio_km_wanted = bool(radio_setting is not None and radio_setting.value)
        if control is not None:
            radio_km_live = bool(
                radio_km_wanted
                and not estopped
                and allow_drive
                and module is not None
                and module.enabled
            )
            control.radio_km_enabled = radio_km_live
            # 车上回读：km_rx = C 板实际收到的 0x0311 帧数，km_key = 合并后的键位。
            # 只有插着 USB 0x5A 时才拿得到；拔了线就是"不知道"，绝不能当成"没收到"。
            dbg = bridge.debug_packages or {}
            rx_raw = dbg.get("km_rx")
            key_raw = dbg.get("km_key")
            from ..control import describe_km_policy
            pol_name, pol_uses, pol_hint = describe_km_policy(dbg.get("km_pol"))
            control.radio_km_policy = pol_name
            control.radio_km_policy_hint = pol_hint
            try:
                rx_now = int(float(rx_raw)) if rx_raw is not None else None
            except (TypeError, ValueError):
                rx_now = None
            try:
                control.radio_km_robot_key = (int(float(key_raw))
                                              if key_raw is not None else -1)
            except (TypeError, ValueError):
                control.radio_km_robot_key = -1
            prev_rx = getattr(self, "_km_rx_prev", None)
            growing = rx_now is not None and prev_rx is not None and rx_now > prev_rx
            self._km_rx_prev = rx_now if rx_now is not None else prev_rx
            control.radio_km_robot_rx = -1 if rx_now is None else rx_now
            control.radio_km_robot_rx_growing = bool(growing)
            if not radio_km_wanted:
                control.radio_km_link_state = "off"
                control.radio_km_link_note = "未开启"
            elif not radio_km_live:
                control.radio_km_link_state = "blocked"
                control.radio_km_link_note = "闸门未过"
            elif rx_now is None:
                control.radio_km_link_state = "no-readback"
                control.radio_km_link_note = "车上计数读不到（插一下 USB 0x5A 就能看到）"
            elif growing:
                control.radio_km_link_state = "live"
                # "帧到了"和"这一档会用它的键位"是两件事，必须分开说：
                # 默认档 CL_KM_RC 下计数照涨但键位永远不会参与，只报"已收到"会骗人
                control.radio_km_link_note = (
                    f"车上已收到 {rx_now} 帧 0x0311，但档位 {pol_name} 不参与"
                    if pol_uses is False
                    else f"车上已收到 {rx_now} 帧 0x0311（档位 {pol_name}）")
            elif rx_now == 0:
                control.radio_km_link_state = "silent"
                control.radio_km_link_note = (
                    "车上一个 0x0311 都没收到。注意：官方要求图传发送端串口 921600"
                    "（规程表 1-1），而本固件只配了 USART1/USART3/USART6，"
                    "全仓没有 921600 那一路 —— 所以光把线接到 C 板也不会收到。"
                    "两条可行做法：① 用 C 路（直连 DT7，不依赖图传接线）；"
                    "② 车载电脑收 0x0311 后经 HostKmFeed 喂进 C 板。")
            else:
                control.radio_km_link_state = "stalled"
                control.radio_km_link_note = f"计数停在 {rx_now}，不再增长：链路中途断了"
            if not radio_km_wanted:
                control.radio_km_gate_reason = ""
            elif radio_km_live:
                # 生效了就把旧的"没生效"原因清掉，别让诊断行骗人。
                control.radio_km_gate_reason = ""
            else:
                # 操作员开了开关却一帧都不发，最常见原因就是下面这三条。
                control.radio_km_gate_reason = (
                    "无线键鼠出口未生效："
                    + ("急停激活" if estopped else
                       ("模块 RobotLink 未启用" if not (module is not None and module.enabled)
                        else "未授予 Allow Drive"))
                )
        # ---- 自定义控制器出口（0x0306 -> 选手端 -> DT7 空口）----
        # 三道闸与 0x0311 那条一致（非急停 / 模块启用 / Allow Drive），
        # 两条区别要记清：这条**不经过 MQTT、也不经过车上图传串口**，
        # 所以 0x0311 判成 NO-RX 时它依然可用；代价是选手端必须在跑，
        # 且"键鼠来源"只能有一个（2026 协议 2.2.1）。
        # ---- 直连 DT7 出口：同样三道闸，但不要求选手端在跑、也不要求 0x5A 在线 ----
        dt7_setting = settings.get("Direct DT7 Egress")
        dt7_wanted = bool(dt7_setting is not None and dt7_setting.value)
        if control is not None and dt7_wanted:
            prof = dt7_link.load_profile()
            port = str(getattr(settings.get("DT7 Port"), "value", "Auto") or "Auto")
            # "DT7 Real TX" 是显式硬件闸：不勾就只产帧不碰串口。原来这里连
            # mock_output 都不传，等于 C 路在客户端里根本发不出一个字节。
            real_tx = bool(getattr(settings.get("DT7 Real TX"), "value", False))
            link = getattr(self, "_dt7_link", None)
            if (link is None or link.port != port or link.profile != prof
                    or link.mock_output == real_tx):
                if link is not None:
                    try:
                        link.close()
                    except Exception as exc:  # noqa: BLE001
                        # 第 54 轮：原来是 `pass`。
                        #
                        # DJI VCOM 是**独占**端口。`close()` 失败 → 句柄泄漏，
                        # 而下一行把旧对象**丢掉**，所以泄漏的句柄就**永远到了**。
                        # 接下来新链接打开会失败，操作员看到的是「端口被占用」，
                        # 而真因是**旧链接**，且**已无处可往**。
                        #
                        # 控制流**不变**（继续换链接），只是现场能看到。
                        self.last_dt7_close_error = f"{type(exc).__name__}: {exc}"
                        self.dt7_close_failures = getattr(self, "dt7_close_failures", 0) + 1
                link = dt7_link.Dt7KeyMouseLink(port=port, profile=prof,
                                                mock_output=not real_tx)
                self._dt7_link = link
            control.dt7_link = link
            dt7_live = bool(not estopped and allow_drive
                            and module is not None and module.enabled)
            # 松手帧要覆盖**每一条**降级路径：急停、撤 Allow Drive、模块停用。
            # 旧代码只在急停时补，另外两条路是"直接停发" —— 而 DT7 那侧没有
            # 过期自动清零，键会粘在车上继续控。
            if getattr(self, "_dt7_was_enabled", False) and not dt7_live:
                control.release_direct_dt7()
            # 键位掩码按显式开关拼：默认只有驾驶四键；SHIFT 要 Allow Spin Key，
            # Q/E 要单独的 DT7 Allow Fire Keys（默认关，开火权限不偷偷外送）
            from ..control import (DT7_DEFAULT_KEY_MASK, DT7_FIRE_KEY_MASK,
                                   DT7_SPIN_KEY_MASK)
            mask = int(DT7_DEFAULT_KEY_MASK)
            spin_setting = settings.get("Allow Spin Key")
            if spin_setting is not None and bool(spin_setting.value):
                mask |= int(DT7_SPIN_KEY_MASK)
            fire_setting = settings.get("DT7 Allow Fire Keys")
            if fire_setting is not None and bool(fire_setting.value):
                mask |= int(DT7_FIRE_KEY_MASK)
            control.dt7_key_mask = mask
            control.dt7_enabled = dt7_live
            if not dt7_live:
                control.dt7_last_error = "直连 DT7 出口未生效（急停/未授权/模块未启用）"
            elif str(prof.get("_profile_error") or ""):
                control.dt7_last_error = "DT7 配置不可用：" + str(prof["_profile_error"])
            else:
                control.dt7_last_error = ""
        elif control is not None:
            if getattr(self, "_dt7_was_enabled", False):
                # 开关被关掉：同样要松手，别让最后一次按键留在车上
                control.release_direct_dt7()
            control.dt7_enabled = False
        self._dt7_was_enabled = bool(control is not None and control.dt7_enabled)
        cc_setting = settings.get("Custom Controller Egress")
        cc_wanted = bool(cc_setting is not None and cc_setting.value)
        if control is not None and cc_wanted:
            link_obj = self._cc_link_for(settings)
            control.cc_link = link_obj
            control.cc_enabled = bool(not estopped and allow_drive
                                      and module is not None and module.enabled)
            spin = settings.get("Allow Spin Key")
            control.cc_key_mask = 0x0F | (0x10 if (spin is not None and bool(spin.value)) else 0)
            if estopped and getattr(self, "_cc_was_enabled", False):
                # 急停必须把选手端那侧的键鼠也归零。放在写原因**之前**：
                # release 成功时会清 cc_last_error，先写原因就被它抹掉了。
                control.release_custom_controller()
            if not control.cc_enabled:
                control.cc_last_error = (
                    "自定义控制器出口未生效："
                    + ("急停激活" if estopped else
                       ("模块 RobotLink 未启用" if not (module is not None and module.enabled)
                        else "未授予 Allow Drive")))
        elif control is not None:
            control.cc_enabled = False
        self._cc_was_enabled = bool(control is not None and control.cc_enabled)
        if drive_armed and command is not None:
            speed_scale = float(settings["Speed Scale %"].value or 100) / 100.0
            yaw_scale = float(settings["Yaw Scale %"].value or 100) / 100.0
            vx, vy, wz = scale_drive(
                command.vx,
                command.vy,
                command.wz,
                # 限幅跟兵种走：英雄麦轮 2.0，全向轮步兵 1.5
                max_linear_mps=role.max_linear_mps * speed_scale,
                max_yaw_rps=role.max_yaw_rps * yaw_scale,
            )

        # 指令-实测交叉校验：把「链路正常但车不动」变成可读告警。
        motion_state = self.polar_motion.update(
            link_active=bridge.active,
            commanded_vx=vx,
            commanded_vy=vy,
            commanded_wz=wz,
            measured_vx=bridge.overlay.get("measured_vx_mps"),
            measured_vy=bridge.overlay.get("measured_vy_mps"),
            measured_wz=bridge.overlay.get("measured_wz_rps"),
        )
        motion_stats = self.polar_motion.stats()
        target.polar_motion_state = motion_state
        target.polar_motion_detail = str(motion_stats["detail"] or "")

        # 把仲裁结果回写到 state：上车时操作员必须能直接看到指令现在是否生效。
        arbiter_stats = self.polar_arbiter.stats()
        target.polar_drive_armed = drive_armed
        target.polar_gimbal_client = self.polar_arbiter.may_command_gimbal
        target.polar_authority_state = "ESTOP" if estopped else str(arbiter_stats["state"])
        target.polar_authority_reason = (
            "急停激活，队内链路控制已锁零" if estopped else str(arbiter_stats["reason"] or "")
        )
        # 诊断快照必须每帧自己刷：`apply_updates` 只在收到官方 MQTT 消息时
        # 才写 backend_stats，而台架/实车场景根本没有裁判机流量。
        target.backend_stats["polar_link"] = self._polar_link_stats(
            arbiter_stats=arbiter_stats, motion_stats=motion_stats
        )

        # “受击后小陀螺”走 0x5A。
        #
        # 这里曾经把 SpinMode 的启用状态翻成 0x03 的 SHIFT 键位，那是**错的**：
        # 固件对 SHIFT 是边沿翻转（chassis_mecanum.c:177 置 1 / :223 置 0 /
        # chassis_omni.c:96 取反，而松手 :187 / :107 不改变 spin_flag），
        # 客户端因此只能开、关不掉，再开一次反而把自转关掉。
        # 现在改走上行 0x04 的显式“设定”指令，并用下行 0x1F 回读闭环确认。
        #
        # 刻意**不**挂在 drive_armed 上：
        #   1) 0x04 不覆盖驾驶员物理遥控器（只维护 spin_flag），没有 0x03
        #      那种“冻结真实摇杆”的风险；
        #   2) “我只想开自转、不想用客户端开车”是合理诉求，
        #      原来的写法让它根本发不出去。
        # 设置项名仍叫 “Allow Spin Key”（不改名以免弄坏已保存的配置），
        # 但它现在闸的是 0x04 自转指令，而 SHIFT 键位永不再从客户端注入。
        spin_setting = settings.get("Allow Spin Key")
        allow_spin_cmd = bool(spin_setting.value) if spin_setting is not None else False
        spin_module = registry.get("SpinMode") if registry is not None else None
        spin_desired = bool(
            allow_spin_cmd
            and not estopped
            and bridge.active
            and spin_module is not None
            and spin_module.enabled
        )
        # 0x1F 回读。未打 host_bridge 补丁的固件不发这一段，此时为 None，
        # 客户端据此降级为“指令已发、无法确认”，而不是假装成功。
        spin_reported = bridge.overlay.get("polar_spin_active")
        if spin_reported is None:
            spin_reported = bridge.overlay.get("spin_active")
        spin_cmd = self._update_spin_command(desired=spin_desired, reported=spin_reported)
        # 无线时点了自转却"没反应"，以前界面一句解释都没有：0x04 要有 0x5A 链路才走得出去。
        # 这里把"为什么没发"与"怎么才能发"一次说清，省掉一次白跑。
        target.polar_spin_note = spin_block_reason(
            allow_spin=allow_spin_cmd, link_active=bool(bridge.active),
            estop=bool(estopped), module_on=bool(spin_module is not None and spin_module.enabled),
            reported=spin_reported)
        # 这句理由以前只存进 state 就没人读了：界面只挂一个短标记，操作员看不到办法。
        # 所以在"第一次出现/内容变了"时把全文打到日志和 field_log（50Hz 重算，
        # 不做边沿触发的话一秒刷 50 行，等于没有）。
        note = str(target.polar_spin_note or "")
        if note != self._last_spin_note:
            self._last_spin_note = note
            if note:
                _LOG.warning("spin command not sent: %s", note)
                self.field_log.emit("spin_command_blocked", reason=note,
                                    link_active=bool(bridge.active),
                                    reported=spin_reported)
        # 云台直控请求（0x04 param0 bit0，电平语义，见 gimbal_host 桥）。
        # 三个条件缺一不可："Client Gimbal Control" 开着、链路在线、非急停。
        # 未打桥的固件忽略该字节，所以发它不改变老固件任何行为；打了桥的
        # 固件也只在 s[0] 中间档才提档进 GIMBAL_AUTO_AIM——下方安全档与
        # 上方原语义都不受影响。
        gimbal_setting = settings.get("Client Gimbal Control")
        gimbal_host_request = bool(
            gimbal_setting is not None and gimbal_setting.value
            and bridge.active and not estopped
        )
        self.polar.set_gimbal_host_request(gimbal_host_request)
        # C 路鼠标与"上位机云台直控请求"在固件里是有先后的：中间档一旦收到
        # 0x04 param0 bit0 就进 GIMBAL_AUTO_AIM，那时 DT7 的鼠标增量不参与云台
        # （gimbal_yaw_pitch_direct.c:236-243）。这不是 bug，是固件仲裁；
        # 但必须**在这一拍算出请求位之后**再写提示，读上一拍的旧值会留残影
        # （就是这么被测试抓到的）。
        if control is not None:
            control.dt7_mouse_note = (
                "云台直控请求占着（0x04 param0 bit0）："
                "固件中间档走 AUTO_AIM，DT7 鼠标不控云台"
                if (getattr(control, "dt7_enabled", False) and gimbal_host_request) else "")
        # 驾驶键位只留 W/S/A/D：SHIFT 已改走 0x04，此处永不再置位。
        key_value = int(getattr(command, "keyboard_value", 0) or 0) if drive_armed else 0

        self.polar.submit(
            PolarDriveIntent(
                vx=vx,
                vy=vy,
                wz=wz,
                gimbal_pitch_rad=deg_to_rad(plan[1]) if plan else 0.0,
                gimbal_yaw_rad=deg_to_rad(plan[0]) if plan else 0.0,
                friction_on=False,
                tracking=bool(plan),
                # 固件 `rc_ctrl.key.v` 位序与官方 keyboard_value 完全一致，直接复用。
                key_value=key_value,
                # 恒 False：自转已改走 0x04，不允许再通过 0x03 注入 SHIFT。
                allow_spin_key=False,
            )
        )
        return {
            "active": bridge.active,
            "drive_armed": drive_armed,
            "estopped": estopped,
            "motion_state": motion_state,
            "authority": self.polar_arbiter.state if not estopped else "ESTOP",
            "gimbal_target_deg": plan,
            "error": self.polar.last_error or bridge.last_error,
            # 兵种对齐可观测：操作员要能看出“现在按的是哪台车、它的限速多少、
            # 为什么不让驾驶”，而不是只看到 DRV 不亮。
            "role_id": role_id,
            "role_label": role.label,
            "role_hud_code": role.hud_code,
            "role_chassis": role.chassis,
            "role_max_linear_mps": round(role.max_linear_mps, 3),
            "role_max_yaw_rps": round(role.max_yaw_rps, 3),
            "role_known": role.known,
            "role_drive_block": role_block,
            "role_gimbal_block": robot_roles.gimbal_block_reason(role_id),
            "role_selected_id": selected_id,
            "role_reported_id": reported_id,
            "role_mismatch": role_mismatch,
            # 云台出口归属：让操作员看得见“现在角度是由哪条通道在发”，
            # 否则双出口互斥被抑制时只会表现为“自瞄没反应”而无解释。
            "gimbal_egress": self.gimbal_egress_owner(target, registry),
        }

    def _reset_state_for_scenario(self, state: RobotState) -> None:
        """Discard data from the previous transport epoch before reconnecting."""

        self._clear_mock_frontend_state(state)
        # 上一台机器人的队内链路真值不能残留到下一个场景：否则 HUD 会拿
        # 旧机器人姿态接着显示，驾驶门控也会误判链路仍活。
        state.polar_overlay = {}
        state.polar_link_active = False
        state.polar_link_age_s = float("inf")
        state.polar_debug_packages = {}
        state.polar_module_errors = {}
        state.polar_ally_hp = {}
        state.polar_ground_positions = {}
        self.polar_bridge.reset()
        self.polar_arbiter.clear_lockout()
        self.polar_motion._ever_had_feedback = False
        self.polar_motion._last_feedback_segment_at = None
        self.polar_motion._moving_since = None
        self.polar_motion.state = "IDLE"
        self.polar_motion.detail = ""
        state.official_state_active = False
        state.official_state_at = 0.0
        state.official_robot_health.clear()
        state.official_robot_bullets.clear()
        state.robot_positions.clear()
        state.buffs.clear()
        state.penalties.clear()
        state.active_penalty.clear()
        state.radar_detections.clear()
        state.radar_detections_at = 0.0
        state.path_plan.clear()
        state.tech_core.clear()
        state.performance_sync.clear()
        state.rune_status.clear()
        state.sentry_status.clear()
        state.dart_status.clear()
        state.sentry_ctrl_result.clear()
        state.special_mechanisms.clear()
        state.robot_statics.clear()
        state.robot_dynamics.clear()
        state.robot_modules.clear()
        state.robot_module_status.clear()
        state.injury_stats.clear()
        state.injury_stats_initialized = False
        state.projectile_damage_small_total = 0
        state.projectile_damage_large_total = 0
        state.last_projectile_hit_at = 0.0
        state.last_projectile_damage_delta = 0
        state.last_projectile_kind = ""
        state.last_projectile_killer_id = 0
        state.deploy_mode = 0
        state.deploy_status_revision += 1
        state.deploy_status_at = 0.0
        state.current_stage = "NOT_STARTED"

    @staticmethod
    def _sync_local_projectile_allowance(state: RobotState) -> None:
        """Map GlobalUnitStatus.robot_bullets onto this robot's HUD calibre.

        The referee array is own-team-only and ordered by roles 1/2/3/4/7,
        independent of alliance colour.  Hero consumes 42 mm; the remaining
        listed roles use the 17 mm HUD slot (Engineer normally reports zero).
        Unknown robot IDs or short/missing arrays remain fail-unknown instead
        of reusing a stale LabDebug value.
        """

        bullets = getattr(state, "official_robot_bullets", None) or []
        role_id = int(getattr(state, "robot_id", 0)) % 100
        try:
            index = _HEALTH_ROLE_ORDER.index(role_id)
        except ValueError:
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = 0
            return
        if index >= len(bullets):
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = 0
            return
        allowance = max(0, int(bullets[index]))
        if role_id == 1:
            state.projectile_allowance_17mm = 0
            state.projectile_allowance_42mm = allowance
        else:
            state.projectile_allowance_17mm = allowance
            state.projectile_allowance_42mm = 0

    def tick(self) -> None:
        if self.state is not None:
            self.safety.update_stage(self.state.current_stage)
        self.serial.tick()
        if not self.mqtt.connected and self.mqtt.disconnected_at > 0.0:
            if time.perf_counter() - self.mqtt.disconnected_at > 2.0 and not self._mqtt_disconnect_lock_applied:
                self.lock_control("MQTT disconnected for more than 2 seconds")
                self._mqtt_disconnect_lock_applied = True
                self._mqtt_disconnect_lock_at = time.perf_counter()
        elif self.mqtt.connected:
            # Reconnect clears the detection edge, but intentionally never
            # restores MATCH_ARMED.
            if self._mqtt_disconnect_lock_applied:
                self._mqtt_reconnected_locked_at = time.perf_counter()
                self.field_log.emit(
                    "mqtt_reconnected_locked",
                    disconnected_lock_at=self._mqtt_disconnect_lock_at,
                    safety_state=self.safety.control_state.value,
                )
            self._mqtt_disconnect_lock_applied = False
        self._recover_video_decoders()

    def _recover_video_decoders(self) -> None:
        """Apply decoder health timeouts off the transport callback path.

        An ffmpeg process can remain alive while producing no frame (bad GOP,
        transmitter reset) or can leave a stale last image. Both conditions
        are treated exactly like packet loss: reset the bounded Annex-B gate,
        restart with backoff, and wait for a fresh parameter-set/keyframe.
        """

        if self.udp.decode_h265 and self.udp.running and self.udp.h265_decoder is not None:
            # 第 100 轮：`health()` 与 `_request_h265_restart()` **都会去拿 `_h265_restart_lock`**，
            # 而这把锁正被重启 worker 整段持有。
            # 子代理**实测**：ffmpeg 健康时锁持有 **55–62 ms**，卡死时 **610 ms**。
            #
            # `_request_h265_restart()` 自己**有**「已有重启在跑就返回」的守卫
            # （`video_backend.py:480`）—— **但那道守卫在 `with self._h265_restart_lock:` 之后**，
            # **它要先等锁**。**它在等一把此刻正被别人拿着的锁。**
            # 而本方法跑在 60 Hz 拍上，等的就是拍。
            #
            # 空闲时（没有重启在跑）这两调用都很便宜，所以**只在锁被占时跳过探测**：
            # **不新建线程、不改探测频率**（空闲时仍是每拍一次），
            # 只是**不在别人握着锁的时候排队**。
            #
            # `video_backend.py` 在冻结名单里，**所以这里只读它的属性、不改它**。
            in_flight = getattr(self.udp, "_h265_restart_thread", None)
            if not (in_flight is not None and in_flight.is_alive()):
                health = self.udp.h265_decoder.health()
                if health.get("first_frame_timeout") or health.get("stale"):
                    self.udp.h265_recovery.reset()
                    self.udp._request_h265_restart()

        native_selected = self._active_video_source in {
            "native", "native 0x0310", "0x0310",
        } or int(getattr(self.state, "deploy_mode", 0) if self.state is not None else 0) == 1
        if not native_selected:
            return
        health = self.native_decoder.health()
        crashed = bool(
            self._native_0310_decoder_started_once
            and not self.native_decoder.available
            and self._native_0310_bytes_since_reset > 0
            and not self._native_failure_epoch_handled
        )
        if not (crashed or health.get("first_frame_timeout") or health.get("stale")):
            return
        now = time.perf_counter()
        if now < self._native_next_restart_at:
            return
        # 第 98–99 轮：`restart()` 整个 stop+start 原来是在 **60 Hz 拍上同步**跑的。
        #
        # 子代理在同类 h265 路径上**实测**：健康时 55–62 ms / 卡死时 **610 ms**，
        # 而一拍只有 16.7 ms；退避是 0.5→5 秒，所以不健康时会**反复卡**。
        # **同仓已有正确写法**：UDP 那边走 `_request_h265_restart()`，判定在调用方、
        # **阻塞的重启丢给工作线程**。这里照搬那个形状。
        #
        # ⚠ **退避门限必须在这里就推后**：否则在重启还在跑的那几百毫秒里，
        # **每一拍都会再开一个线程**。
        self._native_last_health = dict(health)
        self._native_next_restart_at = now + self._native_restart_backoff_s
        if self._native_restart_thread is not None and self._native_restart_thread.is_alive():
            return
        self._native_restart_thread = threading.Thread(
            target=self._native_restart_worker,
            name="native-decoder-restart",
            daemon=True,
        )
        self._native_restart_thread.start()

    def _native_restart_worker(self) -> None:
        """在**工作线程**里做 native 解码器的 stop+start。

        判定逻辑留在 60 Hz 拍上（那里只读 health）；**只有阻塞的重启本身**在这里。

        下面是原同步实现**逐字搬迁**的。包括一个容易丢的细节：
        **翻倍只在失败时**——它原来的缩进就在 `else:` 里面，搬迁时很容易被挪到 `if/else` 之外。
        `manager_safety_branch_audit_test` 的「重启成功：退避被复位成 0.5s」钉的就是它。"""
        try:
            restarted = bool(self.native_decoder.restart(clear_latest=True))
        except Exception as exc:  # noqa: BLE001 — 诊断线程不应该把拍带崩
            self._native_failure_epoch_handled = False
            self.field_log.emit(
                "decoder_restart",
                source="native_0310",
                restarted=False,
                health={"error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self._native_failure_epoch_handled = restarted
        self._native_recovery.reset()
        self._native_0310_blocked_until_sps = True
        if restarted:
            self._native_0310_decoder_restarts += 1
            self._native_0310_decoder_started_once = True
            self._native_restart_backoff_s = 0.5
            self._native_next_restart_at = 0.0
        else:
            self._native_next_restart_at = (
                time.perf_counter() + self._native_restart_backoff_s
            )
            self._native_restart_backoff_s = min(
                5.0, self._native_restart_backoff_s * 2.0
            )
        self.field_log.emit(
            "decoder_restart",
            source="native_0310",
            restarted=restarted,
            health=self._native_last_health,
        )

    def _build_commit(self) -> str:
        try:
            if self.paths.manifest_path.is_file():
                data = json.loads(self.paths.manifest_path.read_text(encoding="utf-8-sig"))
                return str(data.get("git_commit", "UNKNOWN"))
            marker = self.paths.app_root / "BUILD_COMMIT.txt"
            if marker.is_file():
                return marker.read_text(encoding="ascii").strip()
        except Exception:
            pass
        # Source runs are explicitly not field-certifiable, but binding them
        # to HEAD still makes tests and local evidence deterministic.
        try:
            import subprocess
            result = subprocess.run(
                ["git", "-C", str(self.paths.app_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "UNKNOWN"

    def _log_backend_event(self, event: str, **payload: Any) -> None:
        if event == "referee_rx" and isinstance(payload.get("payload"), bytes):
            self.field_log.referee_payload(str(payload.get("topic", "")), payload["payload"])
            return
        self.field_log.emit(event, **payload)

    def _request_input_clear(self, reason: str) -> None:
        """Clear host input whenever a control authorization epoch ends.

        The backend deliberately owns the request so disconnect and scenario
        transitions cannot depend on a particular GUI path.  The callback is
        optional for headless tests; production installs it immediately after
        constructing ``KeyboardControlBridge``.
        """

        self._input_clear_generation += 1
        hook = self.input_clear_hook
        if hook is not None:
            try:
                hook(str(reason))
            except Exception as exc:
                self.field_log.emit(
                    "input_clear_error",
                    reason=reason,
                    generation=self._input_clear_generation,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
        self.field_log.emit(
            "input_clear",
            reason=reason,
            generation=self._input_clear_generation,
            callback_installed=hook is not None,
        )

    def set_active_video_source(self, source_mode: str, *, _hero_transition: bool = False) -> None:
        normalized = str(source_mode or "").strip().lower()
        normalized = {
            "shark udp": "udp hevc",
            "udp": "udp hevc",
            "hevc": "udp hevc",
            "h265": "udp hevc",
            "native": "native 0x0310",
            "0x0310": "native 0x0310",
        }.get(normalized, normalized)
        if not _hero_transition:
            self._normal_video_source = normalized
        # DeployModeStatusSync=1 owns the video path regardless of the normal
        # CameraFeed setting. The UDP socket/decoder is stopped during that
        # authoritative interval, while Native 0x0310 remains lazy until its
        # first complete SPS/PPS/IDR is received.
        if self._hero_native_override:
            normalized = "native 0x0310"
        udp_selected = normalized in {"shark udp", "udp", "udp hevc", "hevc", "h265"}
        if (
            normalized == self._active_video_source
            and (not udp_selected or self.udp.running)
        ):
            return
        self._active_video_source = normalized
        if udp_selected and not self.udp.running:
            # The socket and decoder are both lazy. This avoids an unused HEVC
            # process and an unnecessary UDP bind in Webcam/Native sessions.
            self.udp.start()
        elif not udp_selected and self.udp.running:
            self.udp.stop()
        self.udp.set_decode_enabled(udp_selected)
        if normalized not in {"native", "native 0x0310", "0x0310"} and int(
            getattr(self.state, "deploy_mode", 0) if self.state is not None else 0
        ) != 1:
            self.native_decoder.stop()
            self._native_recovery.reset()
            self._native_0310_blocked_until_sps = True

    def sync_frontend_state(self, state: RobotState) -> None:
        """????????????????? HUD ????

        - robot_rows  ? official_robot_health + robot_statics/dynamics/modules
        - map_robots ? robot_positions?????/???????????
        - buff_rows  ? Buff ??
        ???????? mock????????? mock???????
        """
        has_real = (
            bool(state.official_robot_health)
            or bool(state.robot_positions)
            or bool(state.robot_statics)
            or bool(state.robot_dynamics)
            or bool(state.robot_modules)
        )
        if not has_real:
            return
        state.backend_frontend_synced = True

        health = [int(v) for v in state.official_robot_health[:10]]
        own_blue = int(state.robot_id) >= 100 or str(state.alliance_color).lower() == "blue"
        ally_ids = tuple(role + (100 if own_blue else 0) for role in _HEALTH_ROLE_ORDER)
        enemy_ids = tuple(role + (0 if own_blue else 100) for role in _HEALTH_ROLE_ORDER)
        health_by_id = {
            robot_id: health[index]
            for index, robot_id in enumerate(ally_ids + enemy_ids)
            if index < len(health)
        }
        observed_ids = {
            *(int(key) for key in state.robot_statics),
            *(int(key) for key in state.robot_dynamics),
            *(int(key) for key in state.robot_modules),
            *(int(item.get("robot_id", 0)) for item in state.robot_positions),
        }
        robot_ids = list(ally_ids + enemy_ids) if health else []
        robot_ids.extend(sorted(robot_id for robot_id in observed_ids if robot_id > 0 and robot_id not in robot_ids))
        rows: list[dict[str, Any]] = []
        for robot_id in robot_ids:
            base = robot_id if robot_id < 100 else robot_id - 100
            statics = state.robot_statics.get(robot_id, {})
            dynamics = state.robot_dynamics.get(robot_id, {})
            global_hp = max(0, int(health_by_id.get(robot_id, 0)))
            dynamic_hp = int(dynamics.get("current_health", 0))
            # ??? 10Hz RobotDynamicStatus??????????? 1Hz ??????
            hp = dynamic_hp if robot_id == int(state.robot_id) and dynamic_hp > 0 else global_hp or dynamic_hp
            hp_max = int(statics.get("max_health", 0)) or DEFAULT_MAX_HEALTH
            alive_state = int(statics.get("alive_state", 1))
            if alive_state == 2 or hp <= 0:
                row_state = "DEAD"
            elif robot_id == int(state.robot_id):
                row_state = "LOCAL"
            else:
                row_state = "ALIVE"
            rows.append({
                "id": robot_id,
                "name": ROBOT_ID_NAMES.get(base, "Robot"),
                "hp": max(0, hp),
                "max": max(1, hp_max),
                "state": row_state,
                "x": None,
                "y": None,
                "modules": state.robot_modules.get(robot_id, {}),
            })
        positions = {int(p.get("robot_id", -1)): p for p in state.robot_positions}
        for row in rows:
            pos = positions.get(int(row["id"]))
            if pos:
                row["x"] = float(pos.get("x", 0.0))
                row["y"] = float(pos.get("y", 0.0))
                row["yaw"] = float(pos.get("yaw", 0.0))
        state.robot_rows = rows

        if positions:
            map_robots: list[dict[str, Any]] = []
            for robot_id, pos in positions.items():
                base = robot_id if robot_id < 100 else robot_id - 100
                global_hp = max(0, int(health_by_id.get(robot_id, 0)))
                dynamic_hp = int(state.robot_dynamics.get(robot_id, {}).get("current_health", 0))
                hp = dynamic_hp if robot_id == int(state.robot_id) and dynamic_hp > 0 else global_hp or dynamic_hp
                hp_max = int(state.robot_statics.get(robot_id, {}).get("max_health", 0)) or DEFAULT_MAX_HEALTH
                map_robots.append({
                    "id": robot_id,
                    "team": "ally" if robot_id in ally_ids else "enemy",
                    "name": ROBOT_ID_NAMES.get(base, "Robot"),
                    "x": float(pos.get("x", 0.0)),
                    "y": float(pos.get("y", 0.0)),
                    "hp": max(0, hp),
                    "max": max(1, hp_max),
                    "highlight": False,
                })
            state.map_robots = map_robots

        if state.buffs:
            buff_rows: list[dict[str, Any]] = []
            for buff in state.buffs:
                buff_type = int(buff.get("buff_type", 0))
                remaining = max(0, int(buff.get("buff_left_time", 0)))
                buff_rows.append({
                    "name": BUFF_TYPE_NAMES.get(buff_type, f"Buff {buff_type}"),
                    "active": remaining > 0,
                    "remaining": remaining,
                    "buff_type": buff_type,
                    "robot_id": int(buff.get("robot_id", 0)),
                })
            state.buff_rows = buff_rows

    def set_client_id(self, robot_id: int) -> None:
        """机器人档案变更后同步 MQTT client_id 并重建连接。"""
        new_id = str(int(robot_id))
        if new_id == self.mqtt.client_id:
            return
        was_started = self.mqtt._started
        self.mqtt.stop(publish_status=False)
        self.mqtt.client_id = new_id
        if was_started:
            self.mqtt.start()

    def shutdown(self, *, restart: bool = False) -> None:
        # Best-effort neutral KMC before disconnect.  This does not replace the
        # robot-side watchdog or F12 E-stop, but prevents an ordinary app exit
        # or scenario switch from leaving the last movement key latched.
        if self.mqtt.connected and not self.mqtt.mock_output:
            self.mqtt.publish_estop_zero()
        self.safety.trigger_estop()
        self.serial.trigger_estop()
        # 队内链路：固件侧没有断流归零，必须主动发零帧覆盖最后一条指令。
        self.polar.trigger_estop()
        # Scenario changes are reconnects, not process shutdowns.  Do not let a
        # retained offline status race the replacement connection.  A genuine
        # application exit still publishes the retained offline status.
        self.mqtt.stop(publish_status=not restart)
        self.udp.stop()
        self.serial.stop()
        # stop() 内部会先发归零突发帧再关口，避免把最后一条驾驶指令冻在车上。
        self.polar.stop()
        self.native_decoder.stop()
        self._native_recovery.reset()
        # DT7 那个 DJI VCOM 是**独占**口：不关句柄，选手端和下一轮调试都得等本进程
        # 真的退出才拿得到它。放在最后关，顺序不能提前——上面 trigger_estop 触发的
        # 清零钩子要先把松手帧写出去，写完才允许关口。
        dt7_handle = getattr(self, "_dt7_link", None)
        if dt7_handle is not None:
            try:
                dt7_handle.close()
            except Exception:                          # noqa: BLE001
                pass
        self.field_log.emit("backend_shutdown", restart=restart)

    def apply_readiness(
        self,
        state: RobotState | None = None,
        *,
        live_evidence: Any = None,
        registry: Any = None,
    ) -> dict[str, Any]:
        """Run and store the fail-closed match audit for non-UI consumers."""

        from .readiness import audit_backend

        target = state or self.state
        if target is None:
            raise ValueError("RobotState is required for readiness audit")
        report = audit_backend(self, target, live_evidence=live_evidence, registry=registry)
        payload = report.as_dict()
        payload["audited_at"] = time.perf_counter()
        payload["blockers"] = [gate.key for gate in report.blockers]
        target.backend_stats["readiness"] = payload
        return payload

    def latest_video_frame(
        self,
        timeout_s: float = 0.5,
        source_mode: str = "Auto",
    ) -> tuple[Any, float, str]:
        """Return the requested/freshest backend frame on one monotonic clock.

        Explicit ``Native 0x0310`` and UDP modes remain deterministic.  ``Auto``
        compares the latest decoded native and UDP frames, which also gives
        callers a safe fallback during source transitions.
        """

        mode = str(source_mode or "Auto").strip().lower()
        want_native = mode in {"auto", "native", "native 0x0310", "0x0310"}
        want_udp = mode in {
            "auto",
            "shark udp",
            "udp",
            "udp hevc",
            "hevc",
            "h265",
        }
        candidates: list[tuple[Any, float, str]] = []
        if want_native and self.native_decoder.available:
            frame, frame_at = self.native_decoder.latest_with_timestamp(timeout_s)
            if frame is not None:
                candidates.append((frame, frame_at, "native-0x0310"))
        if want_udp:
            frame, frame_at = self.udp.latest_video_frame(timeout_s)
            if frame is not None:
                candidates.append((frame, frame_at, "udp"))
        if not candidates:
            return None, 0.0, "none"
        return max(candidates, key=lambda item: item[1])

    # ------------------------------------------------------------------
    # 下行消息 → RobotState
    # ------------------------------------------------------------------
    def apply_updates(self, state: RobotState) -> int:
        """消费解码队列并写入 RobotState；返回本次应用的消息条数。"""
        messages = self.mqtt.drain_decoded()
        count = 0
        for _topic, name, data, _ts in messages:
            try:
                # Disable synthetic state before applying the first official
                # message. This preserves a first MapClick/Buff update instead
                # of clearing it immediately after it was written.
                self._begin_official_state(state, _ts)
                self._apply_one(state, name, data)
                count += 1
                self._official_state_updates_count += 1
            except Exception as exc:
                # 单条坏消息不能中断本帧，更不能让 Qt timer 永久假死。
                self._apply_error_count += 1
                self._last_apply_error = f"{name}: {type(exc).__name__}: {exc}"
                _LOG.warning(
                    "discarding malformed referee message %s: %s",
                    name,
                    self._last_apply_error,
                )
        if messages and state is not None:
            runtime_stats = self.stats()
            for key, value in state.backend_stats.items():
                if key not in runtime_stats:
                    runtime_stats[key] = value
            state.backend_stats = runtime_stats
            self.sync_frontend_state(state)
        return count

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        """去掉协议层附加的 __present__ 元数据后再存入 state。"""
        return {k: v for k, v in data.items() if k != "__present__"}

    def _apply_one(self, state: RobotState, name: str, data: dict[str, Any]) -> None:
        """把裁判下行的一条模块状态落到 RobotState 上。

        前奏（经济签名快照）与尾声（经济确认）是**全局逻辑**，不属于任何一个
        模块，所以留在这个函数里；每个模块自己的字段映射拆成了
        ``_apply_<模块名>`` 私有方法，由模块级常量 ``_APPLY_ONE_DISPATCH``
        按模块名索引。

        原来的 25 分支 if/elif 链是**互斥**的等值判断（一条消息只命中一个），
        且没有任何分支读另一个分支在本调用里写下的字段（``robot_id`` /
        ``official_robot_bullets`` 这类跨分支读取都发生在**不同消息**之间，
        顺序由解码队列决定，与链上的先后无关）。所以"分支之间有没有顺序依赖"
        的答案是：链内没有，只有"前奏 → 分支 → 尾声"这三段是有序的。

        处理函数返回 bool：True = 继续走尾声，False = 提前结束。
        唯一返回 False 的是 ``_apply_custom_byte_block`` —— 未被选中的原生流
        必须当场停下，连经济确认都不做（原实现里那句裸 ``return`` 就是这个意思，
        拆开后如果写成普通的 ``return``，它只会退出处理函数，尾声会被补跑）。
        """
        economy_before = self._economy_signature(state)
        handler = _APPLY_ONE_DISPATCH.get(name)
        if handler is not None and handler(self, state, data):
            # Economy confirmation is driven by an actual relevant field change,
            # not by the arrival count of unrelated team messages.  This prevents
            # a teammate's purchase from acknowledging our pending request.
            if self._economy_signature(state) != economy_before:
                state.economy_status_revision = int(state.economy_status_revision) + 1
                state.economy_command_status = ""

    def _apply_game_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.current_round = int(data.get("current_round", state.current_round))
        state.total_rounds = int(data.get("total_rounds", state.total_rounds))
        state.red_score = int(data.get("red_score", state.red_score))
        state.blue_score = int(data.get("blue_score", state.blue_score))
        stage = int(data.get("current_stage", 0))
        state.current_stage = _STAGE_STR.get(stage, str(stage))
        self.safety.update_stage(stage)
        if state.current_stage != self._last_logged_stage:
            self._last_logged_stage = state.current_stage
            self.field_log.emit(
                "match_stage",
                raw_stage=stage,
                stage=state.current_stage,
                round=state.current_round,
            )
        state.stage_countdown_sec = max(0, int(data.get("stage_countdown_sec", state.stage_countdown_sec)))
        state.stage_elapsed_sec = max(0, int(data.get("stage_elapsed_sec", state.stage_elapsed_sec)))
        return True

    def _apply_global_unit_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        present = frozenset(data.get("__present__", ()))
        state.base_health = int(data.get("base_health", state.base_health))
        state.base_status = str(data.get("base_status", state.base_status))
        state.base_shield = int(data.get("base_shield", state.base_shield))
        state.outpost_health = int(data.get("outpost_health", state.outpost_health))
        state.outpost_status = str(data.get("outpost_status", state.outpost_status))
        state.enemy_base_health = int(data.get("enemy_base_health", state.enemy_base_health))
        state.enemy_base_status = str(data.get("enemy_base_status", state.enemy_base_status))
        state.enemy_base_shield = int(data.get("enemy_base_shield", state.enemy_base_shield))
        state.enemy_outpost_health = int(data.get("enemy_outpost_health", state.enemy_outpost_health))
        state.enemy_outpost_status = str(data.get("enemy_outpost_status", state.enemy_outpost_status))
        health = [int(v) for v in data.get("robot_health", ()) if isinstance(v, int)]
        if health:
            state.official_robot_health = health
        if 12 in present or "robot_bullets" in data:
            bullets = [int(v) for v in data.get("robot_bullets", ()) if isinstance(v, int)]
            state.official_robot_bullets = bullets
            state.backend_stats["robot_bullets"] = list(bullets)
            self._sync_local_projectile_allowance(state)
        return True

    def _apply_global_logistics_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.remaining_economy = int(data.get("remaining_economy", state.remaining_economy))
        state.total_economy_obtained = int(data.get("total_economy_obtained", state.total_economy_obtained))
        state.tech_level = int(data.get("tech_level", state.tech_level))
        state.encryption_level = int(data.get("encryption_level", state.encryption_level))
        return True

    def _apply_robot_static_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        present = frozenset(data.get("__present__", ()))
        if 4 in present and data["robot_id"]:
            state.robot_id = int(data["robot_id"])
            state.robot_statics[state.robot_id] = self._clean(data)
            self._sync_local_projectile_allowance(state)
        if 8 in present and data["level"]:
            state.level = int(data["level"])
        if 9 in present and data["max_health"]:
            state.max_health = int(data["max_health"])
            state.current_health = min(state.current_health, state.max_health)
        if 10 in present and data["max_heat"]:
            state.max_heat = float(data["max_heat"])
        if 11 in present and data["heat_cooldown_rate"]:
            state.heat_cooldown_rate = float(data["heat_cooldown_rate"])
        if 12 in present and data["max_power"]:
            state.power_limit = int(data["max_power"])
        # 缓冲能量上限属于 RobotStaticStatus（协议字段 13），不在动态报文里。
        # 上一版把它误写进 RobotDynamicStatus 分支，那里根本没这个键，
        # data.get 永远回退到现值 —— 看着接上了，实际一行都不生效。
        # 不写它，effective_capacitor_percent 只能按默认上限 100 算比例。
        if 13 in present and data["max_buffer_energy"]:
            state.max_buffer_energy = int(data["max_buffer_energy"])
        if 6 in present:
            state.performance_sync["shooter"] = int(data["performance_system_shooter"])
        if 7 in present:
            state.performance_sync["chassis"] = int(data["performance_system_chassis"])
        return True

    def _apply_robot_dynamic_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.robot_dynamics[state.robot_id] = self._clean(data)
        state.current_health = int(data.get("current_health", state.current_health))
        state.current_heat = float(data.get("current_heat", state.current_heat))
        state.current_chassis_energy = int(data.get("current_chassis_energy", state.current_chassis_energy))
        state.current_buffer_energy = int(data.get("current_buffer_energy", state.current_buffer_energy))
        state.total_projectiles_fired = int(data.get("total_projectiles_fired", state.total_projectiles_fired))
        state.remaining_ammo = int(data.get("remaining_ammo", state.remaining_ammo))
        state.out_of_combat = bool(data.get("is_out_of_combat", state.out_of_combat))
        state.can_remote_heal = bool(data.get("can_remote_heal", state.can_remote_heal))
        state.can_remote_ammo = bool(data.get("can_remote_ammo", state.can_remote_ammo))
        return True

    def _apply_robot_module_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        clean = self._clean(data)
        state.robot_module_status = {k: int(v) for k, v in clean.items()}
        state.robot_modules[state.robot_id] = clean
        return True

    def _apply_robot_position(self, state: RobotState, data: dict[str, Any]) -> bool:
        self._upsert_positions(state, data)
        return True

    def _apply_buff(self, state: RobotState, data: dict[str, Any]) -> bool:
        self._upsert_buff(state, data)
        return True

    def _apply_penalty_info(self, state: RobotState, data: dict[str, Any]) -> bool:
        self._upsert_penalty(state, data)
        return True

    def _apply_robot_path_plan_info(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.path_plan = self._clean(data)
        return True

    def _apply_map_click_info(self, state: RobotState, data: dict[str, Any]) -> bool:
        clean = self._clean(data)
        marker = {
            "label": chr(int(clean.get("ascii", 0))) if 32 <= int(clean.get("ascii", 0)) <= 126 else "MARK",
            "x": float(clean.get("map_x", 0.0)),
            "y": float(clean.get("map_y", 0.0)),
            "kind": "attack" if int(clean.get("type", 0)) == 1 else "marker",
            "pulse": True,
            **clean,
        }
        state.map_markers = [*state.map_markers[-15:], marker]
        return True

    def _apply_radar_info_to_client(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.radar_detections = [dict(r) for r in data.get("robots", ())]
        state.radar_detections_at = time.perf_counter()
        return True

    def _apply_tech_core_motion_state_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.tech_core = self._clean(data)
        return True

    def _apply_robot_performance_selection_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        clean = self._clean(data)
        state.performance_sync = {k: int(v) for k, v in clean.items()}
        return True

    def _apply_deploy_mode_status_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        next_status = 1 if int(data.get("status", 0)) else 0
        if next_status != int(state.deploy_mode):
            state.deploy_status_revision += 1
        state.deploy_mode = next_status
        state.deploy_status_at = time.perf_counter()
        if bool(next_status) != self._hero_native_override:
            self._hero_native_override = bool(next_status)
            if self._hero_native_override:
                self.set_active_video_source("Native 0x0310", _hero_transition=True)
            else:
                self.set_active_video_source(
                    self._normal_video_source or self.config.connection.video_source,
                    _hero_transition=True,
                )
            self.field_log.emit(
                "hero_video_source",
                deployed=bool(next_status),
                active_source=self._active_video_source,
            )
        return True

    def _apply_rune_status_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.rune_status = self._clean(data)
        state.energy_rune_phase = _RUNE_PHASE.get(int(data.get("rune_status", 1)), "INACTIVE")
        state.energy_rune_progress = int(float(data.get("average_rings", 0)) * 100)
        return True

    def _apply_sentry_status_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.sentry_status = self._clean(data)
        return True

    def _apply_dart_select_target_status_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.dart_status = self._clean(data)
        return True

    def _apply_sentry_ctrl_result(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.sentry_ctrl_result = self._clean(data)
        return True

    def _apply_air_support_status_sync(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.air_support_active = int(data.get("airsupport_status", 0)) == 1
        state.air_support_free_time_sec = int(data.get("left_time", 0))
        state.air_support_cost_coins = int(data.get("cost_coins", 0))
        return True

    def _apply_global_special_mechanism(self, state: RobotState, data: dict[str, Any]) -> bool:
        ids = [int(v) for v in data.get("mechanism_id", ())]
        times = [int(v) for v in data.get("mechanism_time_sec", ())]
        state.special_mechanisms = [
            {"mechanism_id": i, "time_sec": t}
            for i, t in zip(ids, times)
        ]
        return True

    def _apply_event(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.backend_stats["last_event"] = {
            "event_id": int(data.get("event_id", 0)),
            "param": data.get("param", ""),
        }
        return True

    def _apply_custom_byte_block(self, state: RobotState, data: dict[str, Any]) -> bool:
        block = bytes(data.get("data", b""))
        state.backend_stats["last_custom_block"] = {
            "len": len(block),
        }
        telemetry = parse_vehicle_telemetry(block)
        if telemetry is not None:
            previous_sequence = int(state.native_telemetry_frame_seq)
            status_upper = telemetry.status_text.upper()
            # Treat a short backward jump as a producer restart/replay,
            # but do not invalidate calibration at the natural uint32
            # wrap from 0xffffffff to zero.
            sequence_restarted = bool(
                previous_sequence > 0
                and telemetry.frame_seq < previous_sequence
                and (previous_sequence - telemetry.frame_seq) < 0x80000000
            )
            localisation_reset = bool(
                sequence_restarted
                or any(
                    marker in status_upper
                    for marker in (
                        "LOCALIZATION LOST",
                        "LOCALISATION LOST",
                        "RELOCALIZING",
                        "RELOCALISING",
                        "POSE RESET",
                        "MAP RESET",
                    )
                )
            )
            state.native_telemetry_at = time.perf_counter()
            state.native_telemetry_unix_ms = telemetry.unix_ms
            state.native_telemetry_frame_seq = telemetry.frame_seq
            state.native_camera_online = telemetry.camera_online
            state.native_gimbal_online = telemetry.gimbal_online
            state.native_gimbal_mode = telemetry.gimbal_mode_name
            state.native_bullet_count = telemetry.bullet_count
            state.native_bullet_speed = telemetry.bullet_speed
            state.native_yaw_deg = telemetry.yaw
            state.native_yaw_velocity = telemetry.yaw_vel
            state.native_pitch_deg = telemetry.pitch
            state.native_pitch_velocity = telemetry.pitch_vel
            state.native_quaternion = telemetry.quaternion
            state.native_status_text = telemetry.status_text
            if localisation_reset:
                state.hero_lob_relocalization_valid = False
                state.hero_lob_armed = False
                state.hero_lob_status = "BLOCKED: relocalization invalidated by telemetry reset"
            state.backend_stats["native_telemetry"] = {
                "version": telemetry.version,
                "frame_seq": telemetry.frame_seq,
                "camera_online": telemetry.camera_online,
                "gimbal_online": telemetry.gimbal_online,
                "gimbal_mode": telemetry.gimbal_mode_name,
                "bullet_speed": telemetry.bullet_speed,
                "status": telemetry.status_text,
            }
        else:
            video_chunk = parse_video_0310_chunk(block)
            if video_chunk is not None:
                native_selected = self._active_video_source in {
                    "native", "native 0x0310", "0x0310",
                } or int(getattr(state, "deploy_mode", 0)) == 1
                if not native_selected:
                    # An unselected Native stream must not wake ffmpeg or
                    # retain a partial recovery epoch in the background.
                    # Selecting Native later waits for fresh SPS/PPS/IDR.
                    self._native_0310_last_sequence = None
                    self._native_0310_blocked_until_sps = True
                    self._native_recovery.reset()
                    # False = 连尾声都不走，与重构前那条裸 `return` 等价。
                    return False
                # 0x0310 原生 H.264 流：不保留无限增长的历史码流。
                # reset 或向前序号缺口后停止喂流，直到下一个 Annex-B
                # SPS；这与 rm-native-viewer 的正式链路恢复策略一致，
                # 也避免在 UI tick 内同步 stop/restart ffmpeg。重复/迟到
                # 分块直接丢弃，防止同一 payload 破坏 NAL 边界。
                modulus = max(1, int(video_chunk.sequence_modulus))
                sequence = int(video_chunk.sequence) % modulus
                accept_payload = True
                if video_chunk.reset:
                    self._native_0310_bytes_since_reset = 0
                    self._native_0310_last_sequence = None
                    self._native_0310_blocked_until_sps = True
                    self._native_failure_epoch_handled = False
                    self._native_recovery.reset()
                elif self._native_0310_last_sequence is not None:
                    expected = (self._native_0310_last_sequence + 1) % modulus
                    distance = (sequence - expected) % modulus
                    if distance:
                        if distance < modulus // 2:
                            self._native_0310_dropped_chunks += distance
                            self._native_0310_blocked_until_sps = True
                            self._native_failure_epoch_handled = False
                            self._native_recovery.reset()
                        else:
                            self._native_0310_out_of_order_chunks += 1
                            accept_payload = False

                wrote = False
                payload_to_write = b""
                if accept_payload:
                    self._native_0310_bytes_since_reset += len(video_chunk.payload)
                    self._native_0310_last_sequence = sequence
                    if (
                        self._native_0310_decoder_started_once
                        and not self.native_decoder.available
                    ):
                        # A crashed decoder has lost its SPS/PPS parser
                        # state even if transport sequence is continuous.
                        self._native_0310_blocked_until_sps = True
                        if not self._native_failure_epoch_handled:
                            self._native_recovery.reset()
                    payload_to_write = self._native_recovery.feed(video_chunk.payload) or b""
                    self._native_0310_blocked_until_sps = self._native_recovery.blocked
                    if not payload_to_write:
                        self._native_0310_recovery_dropped_chunks += 1

                if payload_to_write:
                    if not self.native_decoder.available:
                        if self._native_0310_decoder_started_once:
                            self._native_0310_decoder_restarts += 1
                        started = self.native_decoder.start()
                        self._native_0310_decoder_started_once = bool(
                            self._native_0310_decoder_started_once or started
                        )
                        if started:
                            self._native_failure_epoch_handled = False
                    if self.native_decoder.available:
                        wrote = self.native_decoder.write(payload_to_write)
                        if not wrote:
                            # Yield briefly to the dedicated stdin writer.
                            # The MQTT callback remains bounded (<10 ms),
                            # while a synthetic burst cannot overflow a
                            # queue that is sized for live 0x0310 cadence.
                            deadline = time.perf_counter() + 0.010
                            while not wrote and time.perf_counter() < deadline:
                                time.sleep(0.0005)
                                wrote = self.native_decoder.write(payload_to_write)
                    if not wrote:
                        # A full/non-running decoder queue is equivalent to
                        # a transport gap.  Resume only from a future SPS.
                        self._native_0310_blocked_until_sps = True
                        self._native_failure_epoch_handled = False
                        self._native_recovery.reset()
                state.backend_stats["native_video_0310"] = {
                    "sequence": sequence,
                    "reset": video_chunk.reset,
                    "payload_len": len(video_chunk.payload),
                    "bytes_since_reset": self._native_0310_bytes_since_reset,
                    "dropped_chunks": self._native_0310_dropped_chunks,
                    "out_of_order_chunks": self._native_0310_out_of_order_chunks,
                    "recovery_dropped_chunks": self._native_0310_recovery_dropped_chunks,
                    "blocked_until_sps": self._native_0310_blocked_until_sps,
                    "decoder_restarts": self._native_0310_decoder_restarts,
                    "payload_accepted": bool(payload_to_write),
                    "payload_input_len": len(video_chunk.payload),
                    "payload_decoder_len": len(payload_to_write),
                    "payload_written": wrote,
                    "payload_queued": wrote,
                    "decoder_input_queue": self.native_decoder.input_queue_depth(),
                    "decoder_input_dropped": self.native_decoder.input_chunks_dropped,
                    "decoder_ready": self.native_decoder.available,
                    "decoded_frames": self.native_decoder.frames_decoded,
                }
                # The decoder thread advances independently after this
                # message snapshot; stats() is the authoritative live
                # source for decoded-frame counters.
        return True

    def _apply_robot_injury_stat(self, state: RobotState, data: dict[str, Any]) -> bool:
        clean = self._clean(data)
        killer_id = int(data.get("killer_id", 0))
        state.injury_stats[killer_id] = clean
        small_total = max(0, int(data.get("small_projectile_damage", 0)))
        large_total = max(0, int(data.get("large_projectile_damage", 0)))
        if state.injury_stats_initialized:
            # Cumulative counters can reset after respawn/reconnect.  A
            # backwards jump establishes a new baseline and must never be
            # interpreted as an enormous fresh hit.
            counters_monotonic = bool(
                small_total >= state.projectile_damage_small_total
                and large_total >= state.projectile_damage_large_total
            )
            if counters_monotonic:
                small_delta = small_total - state.projectile_damage_small_total
                large_delta = large_total - state.projectile_damage_large_total
                damage_delta = small_delta + large_delta
                if damage_delta > 0:
                    state.projectile_hit_revision += 1
                    state.last_projectile_hit_at = time.perf_counter()
                    state.last_projectile_damage_delta = damage_delta
                    state.last_projectile_kind = (
                        "17mm+42mm"
                        if small_delta > 0 and large_delta > 0
                        else ("42mm" if large_delta > 0 else "17mm")
                    )
                    state.last_projectile_killer_id = killer_id
        else:
            # RobotInjuryStat is a cumulative 1 Hz report.  Never trigger
            # an automatic action from the first sample after startup.
            state.injury_stats_initialized = True
        state.projectile_damage_small_total = small_total
        state.projectile_damage_large_total = large_total
        return True

    def _apply_robot_respawn_status(self, state: RobotState, data: dict[str, Any]) -> bool:
        state.respawn_pending = bool(data.get("is_pending_respawn", state.respawn_pending))
        total = max(1, int(data.get("total_respawn_progress", 1)))
        current = int(data.get("current_respawn_progress", 0))
        state.respawn_progress = min(1.0, current / total)
        state.respawn_can_free = bool(data.get("can_free_respawn", state.respawn_can_free))
        state.respawn_gold_cost = int(data.get("gold_cost_for_respawn", state.respawn_gold_cost))
        state.respawn_can_pay = bool(data.get("can_pay_for_respawn", state.respawn_can_pay))
        return True

    @staticmethod
    def _economy_signature(state: RobotState) -> tuple[Any, ...]:
        return (
            int(state.remaining_economy),
            int(state.remaining_ammo),
            int(state.current_health),
            bool(state.respawn_pending),
            bool(state.air_support_active),
        )

    def _upsert_positions(self, state: RobotState, data: dict[str, Any]) -> None:
        robot_id = int(data.get("robot_id", 0))
        positions = list(state.robot_positions)
        clean = self._clean(data)
        for index, item in enumerate(positions):
            if int(item.get("robot_id", -1)) == robot_id:
                positions[index] = clean
                break
        else:
            positions.append(clean)
        state.robot_positions = positions[-16:]

    def _upsert_buff(self, state: RobotState, data: dict[str, Any]) -> None:
        key = (int(data.get("robot_id", 0)), int(data.get("buff_type", 0)))
        buffs = list(state.buffs)
        clean = self._clean(data)
        for index, item in enumerate(buffs):
            if (int(item.get("robot_id", 0)), int(item.get("buff_type", 0))) == key:
                buffs[index] = clean
                break
        else:
            buffs.append(clean)
        state.buffs = buffs[-24:]

    def _upsert_penalty(self, state: RobotState, data: dict[str, Any]) -> None:
        penalty_type = int(data.get("penalty_type", 0))
        penalties = list(state.penalties)
        clean = self._clean(data)
        for index, item in enumerate(penalties):
            if int(item.get("penalty_type", -1)) == penalty_type:
                penalties[index] = clean
                break
        else:
            penalties.append(clean)
        state.penalties = penalties[-8:]
        state.active_penalty = clean

    # ------------------------------------------------------------------
    # 上行指令（对齐 SharkClient MqttCommandPanel 全部命令）
    # ------------------------------------------------------------------
    def _publish(
        self,
        name: str,
        payload: bytes,
        *,
        intent: str = "control",
        qualification_token: str | None = None,
    ) -> tuple[bool, str]:
        return self.mqtt.publish_named(
            name,
            payload,
            qos=1,
            intent=intent,
            qualification_token=qualification_token,
        )

    def publish_keyboard_mouse(self, mouse_x=0, mouse_y=0, mouse_z=0, *,
                               left=False, right=False, keyboard_value=0, mid=False,
                               qualification_token: str | None = None) -> tuple[bool, str]:
        return self._publish(
            "KeyboardMouseControl",
            proto.encode_keyboard_mouse_control(
                mouse_x, mouse_y, mouse_z, left, right, keyboard_value, mid
            ),
            intent="core_kmc",
            qualification_token=qualification_token,
        )

    def _cc_link_for(self, settings: dict) -> "cc_link.CustomControllerLink":
        """按 RobotLink 的设置拿（并按需重建）自定义控制器发送器。

        端口/速率/mock 变化时才重建对象：串口句柄不能每帧开关一次。
        """
        port_s = settings.get("CC Port")
        hz_s = settings.get("CC Hz")
        mock_s = settings.get("CC Mock Writes")
        port = str(getattr(port_s, "value", "Auto") or "Auto")
        hz = float(getattr(hz_s, "value", 30) or 30)
        mock = bool(getattr(mock_s, "value", True))
        link = getattr(self, "_cc_link", None)
        if link is None or (link.port, float(link.max_hz), bool(link.mock_output)) != (port, hz, mock):
            link = cc_link.CustomControllerLink(port=port, max_hz=hz, mock_output=mock)
            self._cc_link = link
        return link

    def publish_km_control(
        self,
        *,
        keyboard_value: int = 0,
        mouse_x: int = 0,
        mouse_y: int = 0,
        mouse_z: int = 0,
        left: bool = False,
        right: bool = False,
        mid: bool = False,
    ) -> tuple[bool, str]:
        """把键鼠打包成 PRM1 kind=3，经 CustomControl -> 图传链路 -> 机器人 0x0311。

        为什么走 CustomControl 而不是 KeyboardMouseControl：官方
        《RM2026 通信协议》**V1.3.0（2026.03.26）**修改日志明写**删除命令码 0x0304**，
        而 0x0311（自定义客户端->机器人，30 字节，75Hz）的 30 字节内容
        完全由队伍自己定，所以不依赖大疆对键鼠的任何封装语义；队内固件
        referee.c:247 已经在收这一条，缺的下游消费者由
        tools/firmware/km_link/apply_km_link.py 补上。

        刻意**不**在这里做按键掩码：能按哪些键由固件的 KM_KEY_ALLOWED_MASK
        决定（默认只放 W/S/A/D/SHIFT，开火相关 Q/E 要显式 --km-fire-keys）。
        上位机放开关火权限的地方只能是固件编译期那一次，不能是这里。
        """
        self._km_seq = (getattr(self, "_km_seq", 0) + 1) & 0xFFFF
        payload = proto.encode_km_control_payload(
            sequence=self._km_seq,
            keyboard_value=keyboard_value,
            mouse_x=mouse_x,
            mouse_y=mouse_y,
            mouse_z=mouse_z,
            left_button=left,
            right_button=right,
            middle_button=mid,
        )
        return self._publish("CustomControl", proto.encode_custom_control(payload),
                             intent="core_km_link")

    def publish_km_release(self) -> tuple[bool, str]:
        """全零一键松开帧：让车上那本图传键位账立刻归零，不等 300ms 超时。

        粘键就是车刹不住，所以释放帧必须能发得出。走 _publish 的普通门控，
        但故意用**普通按键值 0**——它和"客户端没按键"在线上一模一样，
        不需要任何特权。
        """
        return self.publish_km_control(keyboard_value=0, mouse_x=0, mouse_y=0,
                                       mouse_z=0)

    def publish_custom_control(self, data: bytes, *, intent: str = "control") -> tuple[bool, str]:
        return self._publish("CustomControl", proto.encode_custom_control(data), intent=intent)

    def publish_qualification_custom_control(
        self,
        data: bytes,
        *,
        intent: str,
        qualification_token: str,
    ) -> tuple[bool, str]:
        """Qualification-only CustomControl path bound to a live token.

        Normal vision/spin publishers intentionally do not accept tokens, so a
        UI toggle or config value cannot smuggle qualification privileges into
        ordinary operation.
        """

        if intent not in {"prm1_vision", "prm1_spin"}:
            return False, "unsupported qualification CustomControl intent"
        return self._publish(
            "CustomControl",
            proto.encode_custom_control(data),
            intent=intent,
            qualification_token=qualification_token,
        )

    def gimbal_egress_owner(self, state: RobotState | None = None,
                            registry: Any = None) -> str:
        """云台角度到底由哪条出口发 —— 单一裁决点。

        为什么必须有：同一份 `vision_aim_yaw/pitch_deg` 同时被两条出口读：
        官方 PRM1 kind=1（MQTT/图传）和队内 0x5A 的 `0x01 RobotCmd.gimbal.*`。
        两边各自只查自己那套开关，**代码层以前没有任何互锁**：同时打开时
        机器人会同时收到两套云台角指令，互相覆盖、表现为云台抖动或乱转。
        而 0x5A 侧的抢环锁出只能看到另一个 0x5A 主控，**看不见 PRM1 这条**。

        规则（保守）：队内链路已活跃且已获授权控云台时，云台归 0x5A，
        PRM1 不得再发；否则按官方出口。返回 "none" 表示两边都不该发。
        """

        target = state if state is not None else self.state
        if target is None:
            return "none"
        polar_wants = bool(
            getattr(target, "polar_link_active", False)
            and self.polar_arbiter.may_command_gimbal
            and self._polar_gimbal_last is not None
        )
        if polar_wants:
            return "polar"
        module = registry.get("ArmorAutoAim") if registry is not None else None
        if module is None or not module.enabled:
            return "none"
        mock = module.get_setting("Mock Output") if module else None
        if mock is not None and bool(mock.value):
            return "none"
        return "official"

    def publish_vision_control(
        self, *, sequence: int, yaw_deg: float, pitch_deg: float,
        target_id: int = 0, tracking: bool = False,
    ) -> tuple[bool, str]:
        payload = proto.encode_vision_control_payload(
            sequence=sequence,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            target_id=target_id,
            tracking=tracking,
            fire=False,
        )
        return self.publish_custom_control(payload, intent="prm1_vision")

    def publish_spin_control(
        self, *, sequence: int, enabled: bool, direction: str = "Auto",
        speed_dps: float = 420.0, power_guard: bool = True,
    ) -> tuple[bool, str]:
        """Publish the reviewed team spin schema through official CustomControl."""
        payload = proto.encode_spin_control_payload(
            sequence=sequence,
            enabled=enabled,
            direction=direction,
            speed_dps=speed_dps,
            power_guard=power_guard,
        )
        return self.publish_custom_control(payload, intent="prm1_spin")

    def trigger_estop(self) -> tuple[bool, str]:
        """先尽力发送全零 KMC，再锁死所有上行；断网时仍立即本地锁止。"""
        zero_result = self.mqtt.publish_estop_zero()
        self._last_estop_zero_ok, self._last_estop_zero_reason = zero_result
        # 图传键鼠那本账也要同帧清掉。固件侧本来有 300ms 新鲜度超时兜底，
        # 但"等超时"意味着急停之后最多还有 300ms 车仍带着客户端键位——
        # 和 0x5A 那条一样，必须在这一刻就发零帧，而不是留给下一帧。
        # 必须排在 safety.trigger_estop() 之前：门一锁就发不出去了。
        if getattr(self, "_km_seq", 0) > 0:
            self._last_km_release_ok, self._last_km_release_reason = (
                self.publish_km_release())
        self._estop_triggered_at = time.perf_counter()
        self.safety.trigger_estop()
        self.serial.trigger_estop()
        # 队内 0x5A 链路必须同帧锁死：固件侧没有断流归零，不能等下一帧的
        # tick_polar_link 才发现（那一帧之前本地看门狗要 0.2s，车上有速度时太慢）。
        self.polar.trigger_estop()
        self.field_log.emit(
            "estop",
            zero_publish_ok=self._last_estop_zero_ok,
            zero_publish_reason=self._last_estop_zero_reason,
        )
        return zero_result

    def record_firmware_zero_observation(self, observed_at: float | None = None) -> float:
        """Record a physical observer/test fixture's neutral-output timestamp."""

        if self._estop_triggered_at <= 0.0:
            raise ValueError("trigger E-stop before recording firmware zero")
        elapsed_ms = max(
            0.0,
            ((time.perf_counter() if observed_at is None else float(observed_at)) - self._estop_triggered_at)
            * 1000.0,
        )
        if self.state is not None:
            self.state.backend_stats["firmware_zero_observed_ms"] = elapsed_ms
        self.field_log.emit("firmware_zero_observed", elapsed_ms=elapsed_ms)
        return elapsed_ms

    def release_estop(self) -> None:
        self.serial.release_estop()
        # 权威在 safety；这里只是把队内链路的本地锁存与之同步。
        self.polar.release_estop()
        self.safety.release_estop()

    def lock_control(self, reason: str = "operator lock") -> None:
        self.safety.lock_control(reason)
        self.field_log.emit("control_lock", reason=reason)

    def begin_qualification(self, intents: Any, *, ttl_s: float = 30.0):
        token = self.safety.begin_qualification(intents, ttl_s=ttl_s)
        self.field_log.emit(
            "qualification_begin",
            intents=sorted(token.intents),
            expires_at=token.expires_at,
        )
        return token

    def arm_match_control(
        self,
        *,
        robot_id: str | None = None,
        firmware_hash: str,
        match_mode: str | None = None,
        video_source: str | None = None,
    ) -> tuple[bool, str]:
        # Evidence must be minted over the real transport. A mock bridge
        # reports every publish as OK, so a LabDebug/mock session could
        # otherwise produce valid-looking KMC/E-stop evidence without a
        # single byte reaching the referee system.
        if self.mqtt.mock_output or not self.safety.allow_mqtt_publish:
            self.safety.lock_control("arm requires a real MQTT output scenario (mock evidence is not signable)")
            return False, self.safety.last_reason
        if not self.mqtt.connected:
            self.safety.lock_control("arm requires a live MQTT referee connection")
            return False, self.safety.last_reason
        ready, reason = self.qualification.verified_core_ready()
        if not ready:
            self.safety.lock_control(reason)
            return False, reason
        session = self.qualification.session
        if session is None:
            self.safety.lock_control("qualification session disappeared")
            return False, self.safety.last_reason
        identity_ready, identity_reason = self.qualification.identity_matches(
            robot_id=str(
                robot_id
                if robot_id is not None
                else getattr(self.state, "robot_id", session.robot_id)
            ),
            firmware_hash=firmware_hash,
            match_mode=match_mode or getattr(self.state, "match_mode", session.match_mode),
            video_source=video_source or self._canonical_readiness_video_source(),
        )
        if not identity_ready:
            self.safety.lock_control(identity_reason)
            return False, identity_reason
        capabilities = self.qualification.capabilities()
        try:
            self.safety.arm_match(capabilities)
        except ValueError as exc:
            return False, str(exc)
        self.field_log.emit("control_arm", capabilities=sorted(capabilities))
        return True, "MATCH_ARMED"

    def _canonical_readiness_video_source(self) -> str:
        source = str(self._active_video_source).strip().lower()
        if source in {"native", "native 0x0310", "0x0310"}:
            return "native_0310"
        if source in {"shark udp", "udp", "udp hevc", "hevc", "h265"}:
            return "udp"
        return ""

    def publish_map_click_cmd(self, *, is_send_all=0, robot_id=b"", mode=1,
                              enemy_id=0, ascii_code=0, mark_type=1,
                              map_x=0.0, map_y=0.0) -> tuple[bool, str]:
        return self._publish("MapClickCmd", proto.encode_map_click_cmd(
            is_send_all, robot_id, mode, enemy_id, ascii_code, mark_type, map_x, map_y))

    # 旧内部调用兼容，但不再发布已废弃/方向错误的 topic。
    publish_map_click_notify = publish_map_click_cmd

    def publish_assembly_command(self, operation: int, difficulty: int) -> tuple[bool, str]:
        return self._publish("AssemblyCommand", proto.encode_assembly_command(operation, difficulty))

    def publish_robot_performance(self, shooter: int, chassis: int,
                                  sentry_control: int = 0) -> tuple[bool, str]:
        return self._publish("RobotPerformanceSelectionCommand",
                             proto.encode_robot_performance_selection_command(shooter, chassis, sentry_control))

    def publish_hero_deploy(self, mode: int) -> tuple[bool, str]:
        return self._publish(
            "HeroDeployModeEventCommand",
            proto.encode_hero_deploy_mode_event_command(mode),
            intent="hero",
        )

    def publish_rune_activate(self, activate: int) -> tuple[bool, str]:
        return self._publish("RuneActivateCommand", proto.encode_rune_activate_command(activate))

    def publish_dart_command(self, target_id: int, open_gate: bool, launch_confirm: bool) -> tuple[bool, str]:
        return self._publish("DartCommand", proto.encode_dart_command(target_id, open_gate, launch_confirm))

    def publish_sentry_command(self, command_id: int) -> tuple[bool, str]:
        return self._publish("SentryCtrlCommand", proto.encode_sentry_ctrl_command(command_id))

    def publish_common_command(self, cmd_type: int, param: int) -> tuple[bool, str]:
        return self._publish(
            "CommonCommand", proto.encode_common_command(cmd_type, param), intent="economy"
        )

    def publish_air_support(self, command_id: int) -> tuple[bool, str]:
        return self._publish("AirSupportCommand", proto.encode_air_support_command(command_id))

    # ------------------------------------------------------------------
    # 工具：从本地 RobotProfile 设置推导官方性能体系取值并发布
    # ------------------------------------------------------------------
    @staticmethod
    def performance_values(registry: Any) -> dict[str, int]:
        profile = registry.get("RobotProfile")
        if profile is None:
            return {"shooter": 0, "chassis": 0, "sentry_control": 0}
        role = str(getattr(profile.get_setting("Role"), "value", "") or "")
        hero_type = str(getattr(profile.get_setting("Hero Type"), "value", "") or "")
        shooter = str(getattr(profile.get_setting("Infantry Shooter"), "value", "") or "")
        chassis = str(getattr(profile.get_setting("Infantry Chassis"), "value", "") or "")
        sentry = str(getattr(profile.get_setting("Sentinel Control"), "value", "") or "")
        if role == "Hero":
            return {
                "shooter": HERO_MELEE if hero_type == "Melee" else HERO_RANGE,
                "chassis": HERO_MELEE if hero_type == "Melee" else HERO_RANGE,
                "sentry_control": 0,
            }
        if role.startswith("Infantry"):
            return {
                "shooter": SHOOTER_BURST if shooter == "Burst" else SHOOTER_COOLING,
                "chassis": CHASSIS_POWER if chassis == "Power" else CHASSIS_HEALTH,
                "sentry_control": 0,
            }
        if role == "Sentinel":
            return {
                "shooter": 0, "chassis": 0,
                "sentry_control": SENTRY_SEMI_AUTO if sentry == "SemiAuto" else SENTRY_AUTOMATIC,
            }
        return {"shooter": 0, "chassis": 0, "sentry_control": 0}

    def publish_performance_from_registry(self, registry: Any) -> tuple[bool, str]:
        values = self.performance_values(registry)
        return self.publish_robot_performance(
            values["shooter"], values["chassis"], values["sentry_control"])

    # ------------------------------------------------------------------
    def _polar_link_stats(
        self,
        *,
        arbiter_stats: dict[str, Any] | None = None,
        motion_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """0x5A 队内链路的诊断快照 —— **唯一的一份构造逻辑**。

        为什么从两处合并成一处
        ----------------------
        以前这段 20 行的字典字面量在 manager.py 里写了两遍：
        ``tick_polar_link`` 每帧写进 ``state.backend_stats["polar_link"]``，
        ``stats()`` 在被 ``apply_updates`` 调用时整份替换它。两份已经漂移 ——
        ``tick_polar_link`` 那份多一个 ``"motion"`` 键。

        漂移的后果是实打实的：``apply_updates``（:1428-1433）拿 ``self.stats()``
        的结果覆盖 ``state.backend_stats``，而它的合并循环只搬运
        "runtime_stats 里没有的顶层键"；``polar_link`` 本身在 runtime_stats 里，
        于是**每收到一条官方 MQTT 消息，刚写进去的 ``motion`` 就被丢掉**
        （实测 True → False）。而 ``motion`` 正是自转/运动回读闭环的判据
        （AGENT.md 第 4 节不变量 9 说的 0x1F 回读），丢它等于现场无法判断
        "车到底动没动"。

        合并之后不可能再漂移：新增键只需要改这一处。
        ``arbiter_stats`` / ``motion_stats`` 允许外部传入，是为了让
        ``tick_polar_link`` 复用它本拍刚算出来的值，不必重算一遍。
        """
        if arbiter_stats is None:
            arbiter_stats = self.polar_arbiter.stats()
        if motion_stats is None:
            motion_stats = self.polar_motion.stats()
        return {
            **self.polar.stats(),
            "link_alive": bool(self.polar_bridge.active),
            "segment_counts": {
                f"0x{key:02X}": int(value)
                for key, value in sorted(self.polar_bridge.segment_counts.items())
            },
            "dropped_segments": self.polar_bridge.dropped,
            "decode": {
                "frames_ok": self.polar.codec.stats.frames_ok,
                "crc8_errors": self.polar.codec.stats.crc8_errors,
                "crc16_errors": self.polar.codec.stats.crc16_errors,
                "unknown_segments": self.polar.codec.stats.unknown_segments,
                "length_errors": self.polar.codec.stats.length_errors,
                "buffer_overflows": self.polar.codec.stats.buffer_overflows,
            },
            "authority": arbiter_stats,
            "motion": motion_stats,
            "timestamp_roll_detected": self.polar.timestamp.roll_detected,
        }

    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        native_at = self.native_decoder.latest_timestamp()
        native_width, native_height = self.native_decoder.frame_size()
        if self.state is not None:
            native_info = self.state.backend_stats.get("native_video_0310")
            if isinstance(native_info, dict):
                native_info["decoded_frames"] = self.native_decoder.frames_decoded
                native_info["decoder_ready"] = self.native_decoder.available
                native_info["decoded_frame_at"] = native_at
        return {
            "scenario": self.scenario_key,
            "safety": self.safety.stats(),
            "mqtt": self.mqtt.stats(),
            "udp": self.udp.stats(),
            "serial": self.serial.stats(),
            "polar_link": self._polar_link_stats(),
            "native_0310": {
                "decoder_ready": self.native_decoder.available,
                "decoded_connected": bool(
                    native_at > 0.0 and time.perf_counter() - native_at <= 2.0
                ),
                "decoded_frames": self.native_decoder.frames_decoded,
                "continuous_decoded_seconds": self.native_decoder.continuous_decoded_seconds,
                "decoded_frame_at": native_at,
                "output_width": native_width,
                "output_height": native_height,
                "bytes_since_reset": self._native_0310_bytes_since_reset,
                "dropped_chunks": self._native_0310_dropped_chunks,
                "out_of_order_chunks": self._native_0310_out_of_order_chunks,
                "recovery_dropped_chunks": self._native_0310_recovery_dropped_chunks,
                "blocked_until_sps": self._native_0310_blocked_until_sps,
                "decoder_restarts": self._native_0310_decoder_restarts,
                "input_queue": self.native_decoder.input_queue_depth(),
                "input_dropped": self.native_decoder.input_chunks_dropped,
                "input_chunks_written": self.native_decoder.input_chunks_written,
                "input_bytes_written": self.native_decoder.input_bytes_written,
                "last_error": self.native_decoder.last_error,
                "decoder_health": self.native_decoder.health(),
            },
            "qualification": {
                "session_id": self.qualification.session.session_id if self.qualification.session else "",
                "core_ready": self.qualification.core_ready()[0],
                "reason": self.qualification.core_ready()[1],
                "capabilities": sorted(self.qualification.capabilities()),
                "evidence_error": self.qualification.store.last_error,
            },
            "safety_evidence": {
                "estop_triggered_at": self._estop_triggered_at,
                "estop_zero_publish_ok": self._last_estop_zero_ok,
                "estop_zero_publish_reason": self._last_estop_zero_reason,
                "mqtt_disconnect_lock_at": self._mqtt_disconnect_lock_at,
                "mqtt_reconnected_locked_at": self._mqtt_reconnected_locked_at,
                "input_clear_generation": self._input_clear_generation,
            },
            "field_log": {
                "path": str(self.field_log.path),
                "last_error": self.field_log.last_error,
            },
            "message_apply": {
                "errors": self._apply_error_count,
                "last_error": self._last_apply_error,
                "updates": self._official_state_updates_count,
            },
        }


#: ``_apply_one`` 的分发表：裁判下行模块名 -> 处理函数。
#:
#: 提成模块级常量（而不是留在 ``_apply_one`` 里那条 376 行的 if/elif 链）
#: 有三个理由：
#: 1. 25 个分支彼此**互斥**（一条消息只命中一个等值判断），且逐条核对过：
#:    没有任何分支读另一个分支在**同一次调用**里写下的字段 —— ``robot_id``
#:    / ``official_robot_bullets`` 这类跨分支读取都发生在**不同消息**之间，
#:    先后由解码队列决定，与链上顺序无关。所以表项顺序不承载语义
#:    （``tests/apply_one_dispatch_test.py`` 用置乱顺序跑一遍把这件事钉住）。
#: 2. 加一个模块 = 加一个方法 + 一行表项，不必回到 376 行的函数里找位置。
#: 3. 每个分支能单独喂数据单测，不用先读完整条链。
#:
#: 键与裁判下行报文的模块名逐字相同。值是**未绑定方法**，由 ``_apply_one``
#: 传 ``self`` 调用；返回值 True = 继续走经济确认尾声（见 ``_apply_one`` 文档）。
_APPLY_ONE_DISPATCH: dict[str, Callable[["BackendManager", RobotState, dict[str, Any]], bool]] = {
    "GameStatus": BackendManager._apply_game_status,
    "GlobalUnitStatus": BackendManager._apply_global_unit_status,
    "GlobalLogisticsStatus": BackendManager._apply_global_logistics_status,
    "RobotStaticStatus": BackendManager._apply_robot_static_status,
    "RobotDynamicStatus": BackendManager._apply_robot_dynamic_status,
    "RobotModuleStatus": BackendManager._apply_robot_module_status,
    "RobotPosition": BackendManager._apply_robot_position,
    "Buff": BackendManager._apply_buff,
    "PenaltyInfo": BackendManager._apply_penalty_info,
    "RobotPathPlanInfo": BackendManager._apply_robot_path_plan_info,
    "MapClickInfo": BackendManager._apply_map_click_info,
    "RadarInfoToClient": BackendManager._apply_radar_info_to_client,
    "TechCoreMotionStateSync": BackendManager._apply_tech_core_motion_state_sync,
    "RobotPerformanceSelectionSync": BackendManager._apply_robot_performance_selection_sync,
    "DeployModeStatusSync": BackendManager._apply_deploy_mode_status_sync,
    "RuneStatusSync": BackendManager._apply_rune_status_sync,
    "SentryStatusSync": BackendManager._apply_sentry_status_sync,
    "DartSelectTargetStatusSync": BackendManager._apply_dart_select_target_status_sync,
    "SentryCtrlResult": BackendManager._apply_sentry_ctrl_result,
    "AirSupportStatusSync": BackendManager._apply_air_support_status_sync,
    "GlobalSpecialMechanism": BackendManager._apply_global_special_mechanism,
    "Event": BackendManager._apply_event,
    "CustomByteBlock": BackendManager._apply_custom_byte_block,
    "RobotInjuryStat": BackendManager._apply_robot_injury_stat,
    "RobotRespawnStatus": BackendManager._apply_robot_respawn_status,
}


def spin_block_reason(*, allow_spin: bool, link_active: bool, estop: bool,
                      module_on: bool, reported: Any = None) -> str:
    """给出「自转为什么没发出去」的人话原因 + 三条可执行办法。

    客户端的自转走上行 0x04 的**设定**语义（不是 SHIFT 边沿翻转）：
    用 SHIFT 只能开、关不掉，再开一次反而关掉。所以在拿不到 0x1F 回读时
    客户端去替驾驶员翻 SHIFT 是**故意不做**的，不是漏实现。
    """

    if estop or not allow_spin or not module_on:
        return ""                      # 没要求过，就不该弹提示
    if link_active:
        return ""                      # 0x5A 在线，0x04 真发出去了，没资格弹
    if reported is None:
        detail = ("车上从没回读过 0x1F（固件未打 host_bridge 桥），"
                  "客户端连它在不在转都无法确认")
    else:
        detail = f"最后一次 0x1F 回读是 {reported}，此刻 0x5A 断了，客户端改不动它"
    return ("自转要走 0x5A 的 0x04 设定指令，现在 0x5A 链路没在线，所以没发出去。"
            "三条办法：① 插 C 板 USB；② 把 Link Mode 改成 tcp 走车端桥（D 路）；"
            f"③ 让驾驶员自己按遥控器上的 SHIFT。客户端不替驾驶员翻 SHIFT：{detail}")
