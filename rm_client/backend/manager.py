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
from copy import deepcopy
import json
import time
import threading
from queue import Empty, Full, Queue
from dataclasses import replace
from typing import Any, Callable

from ..model import RobotState
from ..video_sources import video_source_notice
from ..lifecycle import LifecycleCompletion, LifecycleSnapshot, TeardownTask
from .lifecycle import ScenarioRequest, ShutdownCompletion, emit_lifecycle_event
from .lifecycle_controller import BackendLifecycleController
from .teardown_plan import TeardownResources
from .video_source import (VideoSourceAdmission, VideoSourceAdmissionError,
                           VideoSourceRequest, VideoSourceSnapshot, VideoSourceFailure)
from ..native_viewer_telemetry import (
    VehicleTelemetry, Video0310Chunk, parse_vehicle_telemetry, parse_video_0310_chunk,
)
from .native_stream_policy import assess_native_video_sequence, localization_invalidated
from ..scenarios import SCENARIOS
from . import cc_link
from . import dt7_link
from .dt7_worker import AsyncDt7KeyMouseLink
from .port_admission import PortReservation, first_port_conflict
from .polar_health import capture_polar_link_health
from .device_egress_settings import (
    CustomControllerSettings, DeviceEgressSettings, EgressPermissions,
)
from . import team_control as proto
from .referee.registry import DEFAULT_PROFILE, get_profile
from .referee.profile_change import RefereeProfileChange
from .referee.rm2026.state_adapter import Rm2026StateAdapter
from .mqtt_backend import RmMqttBackend
from .mqtt_endpoint import admit_mqtt_endpoint
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
    deg_to_rad,
)
from .polar_transport import PolarDriveIntent, PolarLinkTransport
from .polar_control_policy import (
    ControlGates, RobotRoleSelection, SpinCommandState, may_plan_gimbal,
    gimbal_egress_owner as select_gimbal_egress,
)
from .radio_km_feedback import (
    RadioKmReadback, decode_radio_km_policy, evaluate_radio_km_feedback,
)
from .robot_link_settings import (
    ABSENT, BAUD_DEFAULT, PORT_DEFAULT, RobotLinkSettings,
    PolarDriveSettings, PolarGimbalSettings, PolarSpinSettings,
)
from .safety import OfficialSafety
from .serial_backend import RmSerialBackend
from .controller_angles import (AngleRelayConfig, ControllerAngleRelay, LEGACY_ANGLE_ROUTES,
                                explicit_port, port_key)
from .video import AnnexBRecoveryGate, ContinuousMjpegDecoder, RmVideoBackend
from .field_log import RotatingJsonlLogger
from .qualification import QualificationEvidenceStore, QualificationManager
from ..runtime_paths import RuntimePaths
from ..rules_compatibility import normalize_rule_season, retire_official_observations

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

_LOG = logging.getLogger(__name__)


class BackendManager:
    """本地三种模式的 SharkClient 对齐后端。"""

    def __init__(
        self,
        workspace: Any = None,
        state: RobotState | None = None,
        client_id: str = "3",
        scenario_key: str = "LabDebug",
        referee_profile: str = DEFAULT_PROFILE,
    ) -> None:
        self._lifecycle = BackendLifecycleController(
            invalidate_runtime=self._invalidate_shutdown_runtime,
            stopped_runtime=self._finish_stopped_runtime,
            activate_scenario=self._activate_scenario,
        )
        self._serial_start_thread: threading.Thread | None = None
        self.paths = RuntimePaths.discover()
        self.workspace = workspace if workspace is not None else self.paths.workspace_root
        self.state = state
        self.rule_season = 2026
        self.scenario_key = scenario_key
        self.safety = OfficialSafety(scenario_key)
        self.referee = get_profile(referee_profile)
        self.referee_adapter = self.referee.create_state_adapter(self)
        if state is not None:
            state.referee_profile = self.referee.profile_id
        self.mqtt = RmMqttBackend(
            host="127.0.0.1", port=3333, client_id=client_id,
            mock_output=True, safety=self.safety, referee_profile=referee_profile,
        )
        self.udp = RmVideoBackend(host="127.0.0.1", port=3334)
        self.udp.legacy_control_enabled = True
        self.serial = RmSerialBackend(port="Stub", baud=115200, mock_writes=True, safety=self.safety)
        self.controller_angles = ControllerAngleRelay()
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
        self._native_0310_last_accepted_at = 0.0
        self._native_0310_dropped_chunks = 0
        self._native_0310_out_of_order_chunks = 0
        self._native_0310_recovery_dropped_chunks = 0
        self._native_0310_blocked_until_sps = True
        self._native_0310_decoder_restarts = 0
        self._native_0310_decoder_started_once = False
        self._native_next_restart_at = 0.0
        self._native_restart_backoff_s = 0.5
        self._native_failure_epoch_handled = False
        self._native_restart_generation = 0
        self._native_restart_thread: threading.Thread | None = None
        self._native_restart_results: Queue[tuple[int, bool, dict[str, Any]]] = Queue(maxsize=1)
        self._apply_error_count = 0
        self._last_apply_error = ""
        self._official_state_updates_count = 0
        self._mqtt_disconnect_lock_applied = False
        self._mqtt_disconnect_lock_at = 0.0
        self._mqtt_reconnected_locked_at = 0.0
        self._active_video_source = ""
        self._video_source_request: VideoSourceRequest | None = None
        self._video_source_generation = 0
        self._video_source_failures = ()
        self._referee_change_requested = ""
        self._referee_change_error = ""
        self._normal_video_source = ""
        self._estop_triggered_at = 0.0
        self._last_estop_zero_ok = False
        self._last_estop_zero_reason = "not run"
        self._input_clear_generation = 0
        self._input_clear_error = ""
        self._input_owner_thread = threading.get_ident()
        self._input_clear_requests: Queue[str] = Queue(maxsize=1)
        self.input_clear_hook: Callable[[str], None] | None = None
        # Optional presentation-only observer, called after authoritative apply.
        # It never owns/drains a queue and errors cannot affect control state.
        self.referee_view_observer: Callable[[str, dict[str, Any], float], None] | None = None
        self._referee_view_error = ""
        self._last_logged_stage = ""
        self.field_log = RotatingJsonlLogger(self.paths.logs_root)
        self._last_spin_note = ''
        self.safety.input_clear_hook = self._request_input_clear
        self.mqtt.event_hook = self._log_backend_event
        self.udp.event_hook = self._log_backend_event
        build_commit = self._build_commit()
        self.qualification = QualificationManager(
            QualificationEvidenceStore(
                self.paths.evidence_root, build_commit=build_commit,
                referee_profile=self.referee.profile_id,
                protocol_version=self.referee.protocol_version,
            )
        )
        self.mqtt.bind_economy_context(self.state, self.qualification)
        self.config = SCENARIOS.get(scenario_key, SCENARIOS["LabDebug"])
        self._apply_config(self.config)
        self.set_rule_season(getattr(state, "rules_season", 2026))

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
        self.udp.configure_legacy_receiver(int(config.connection.legacy_video_channel))
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
        # Configuration must be side-effect free with respect to starting
        # transports; apply_scenario starts them only after final endpoints.
        self.set_active_video_source(config.connection.video_source, start=False)

    @staticmethod
    def _config_from_registry(config: Any, registry: Any) -> Any:
        if registry is None:
            return config

        def value(module_name: str, setting_name: str, default: Any) -> Any:
            module = registry.get(module_name)
            setting = module.get_setting(setting_name) if module is not None else None
            return setting.value if setting is not None else default

        connection = config.connection
        changes = {
            "mqtt_host": str(value("MQTTLink", "Host", connection.mqtt_host)).strip(),
            "mqtt_port": int(value("MQTTLink", "Port", connection.mqtt_port)),
            "udp_host": str(value("UDPVideo", "Host", connection.udp_host)).strip(),
            "udp_port": int(value("UDPVideo", "Port", connection.udp_port)),
            "serial_port": str(value("SerialBridge", "Port", connection.serial_port)),
            "serial_baud": int(value("SerialBridge", "Baud", connection.serial_baud)),
            "video_source": str(value("CameraFeed", "Source", connection.video_source)),
            "legacy_video_channel": int(value("CameraFeed", "Legacy Channel", connection.legacy_video_channel)),
        }
        if changes["legacy_video_channel"] not in range(1, 7):
            raise ValueError("Legacy receiver channel must be 1..6")
        for name in ("mqtt_host", "udp_host"):
            if not changes[name] or any(ch.isspace() for ch in changes[name]):
                raise ValueError(f"Invalid configured {name}")
        for name in ("mqtt_port", "udp_port"):
            if not 1 <= changes[name] <= 65535:
                raise ValueError(f"Invalid configured {name}")
        if not 1200 <= changes["serial_baud"] <= 3_000_000:
            raise ValueError("Invalid configured serial baud")
        port = str(value("RobotLink", "Port", PORT_DEFAULT)).strip()
        baud = int(value("RobotLink", "Baud", BAUD_DEFAULT))
        if port and port != PORT_DEFAULT:
            changes["polar_port"] = port
        if baud != BAUD_DEFAULT:
            if not 1200 <= baud <= 3_000_000:
                raise ValueError("Invalid configured robot baud")
            changes["polar_baud"] = baud
        return replace(config, connection=replace(connection, **changes))

    def sync_mqtt_link(self, registry: Any) -> str:
        """让 MQTTLink 的 Host/Port **真的决定**连到哪个 broker。

        以前这两个设置项只有人写、没有人读：``scenarios.py`` 把场景值抄到界面上，
        操作员改了以后后端一行都不动，屏幕上留下的地址是假的。
        非法输入退回当前有效值，并且把有效值写回设置项 —— 宁可覆盖你的手误，
        也不让界面显示一个根本没在用的地址。
        """
        if self._backend_transition_blocks_work():
            return "Backend lifecycle transition pending or failed"
        module = registry.get("MQTTLink") if registry is not None else None
        if module is None:
            return ""
        profile_setting = module.get_setting("Referee Profile")
        if profile_setting is not None and str(profile_setting.value) != self.referee.profile_id:
            try:
                self.set_referee_profile(str(profile_setting.value))
            except ValueError as exc:
                self.mqtt.last_error = str(exc)
                return str(exc)
        host_setting = module.get_setting("Host")
        port_setting = module.get_setting("Port")
        if host_setting is None or port_setting is None:
            return ""
        raw_host, raw_port = host_setting.value, port_setting.value
        endpoint = admit_mqtt_endpoint(raw_host, raw_port,
            current_host=self.mqtt.host, current_port=self.mqtt.port)
        changed = self.mqtt.set_address(endpoint.host, endpoint.port)
        # set_address may deliver an input-clear observer that edits settings
        # or reenters sync. Repair only the captured request, without parsing
        # raw values again or erasing a newer request/diagnostic from that hook.
        host_unchanged = host_setting.value is raw_host
        port_unchanged = port_setting.value is raw_port
        if host_unchanged:
            host_setting.value = self.mqtt.host
        if port_unchanged:
            port_setting.value = self.mqtt.port
        if host_unchanged and port_unchanged:
            self.mqtt.last_error = endpoint.note
        if changed:
            print(f"[mqtt] broker 改为 {self.mqtt.host}:{self.mqtt.port}")
        # 停用 MQTT 模块要真正停止其重连；否则每次连接失败都会撤销独立串口角度授权。
        if not module.enabled:
            if self.mqtt._started:
                self.mqtt.stop(publish_status=False, wait=False)
        else:
            auto_connect = module.get_setting("Auto Connect")
            if auto_connect is not None and auto_connect.value and not self.mqtt._started:
                self.mqtt.start()
        return endpoint.note

    @property
    def referee_profile_change(self) -> RefereeProfileChange:
        return RefereeProfileChange(
            self._referee_change_requested or self.referee.profile_id,
            self.referee.profile_id, self.mqtt.referee.profile_id,
            self.state.referee_profile if self.state is not None else None,
            bool(self._referee_change_requested), self._referee_change_error,
        )

    def set_rule_season(self, season: int) -> None:
        """Invalidate annual official authority, not independent device schemas.

        Called before runtime startup as well as from the owner-thread UI.
        Never infer the rule season from a profile's version number.
        """
        season = normalize_rule_season(season)
        state = self.state
        changed = (getattr(self, "rule_season", 2026) != season
                   or getattr(state, "rules_season", 2026) != season
                   or self.mqtt.rule_season != season)
        self.rule_season = season
        if state is not None:
            state.rules_season = season
        if changed:
            # Retire the in-memory context without deleting historical signed
            # evidence or modifying its year/profile identity.
            self.qualification.session = None
        self.serial.bind_rule_state(state)
        self.serial.set_rule_season(season, _invalidate=changed)
        self.mqtt.set_rule_season(season, _invalidate=changed)
        if changed:
            # Revoke wire authority before any fallible state/UI data cleanup.
            retire_official_observations(state)

    def set_referee_profile(self, profile_id: str, *, start_video: bool = True) -> None:
        """Accept a locked target; an explicit repeat retries incomplete application."""
        if (not self._lifecycle.applying
                and (self._input_clear_incomplete() or self._lifecycle.blocks_work)):
            return
        try:
            profile = get_profile(profile_id)
        except ValueError:
            self.mqtt.publish_estop_zero()
            self.mqtt.stop(publish_status=False, wait=False)
            self.safety.lock_control("unsupported referee profile")
            self.safety.clear_stage_evidence()
            raise
        if profile.profile_id == self.referee.profile_id and not self._referee_change_requested:
            return
        retry = bool(self._referee_change_error)
        self._referee_change_requested = profile.profile_id
        self._referee_change_error = ""
        try:
            self.mqtt.publish_estop_zero()
            self.safety.lock_control("referee profile changed")
            self._native_restart_generation += 1
            self._native_0310_blocked_until_sps = True
            # Retire the old decoder before publishing a new profile identity.
            # Failed admission leaves the previous profile label consistent.
            self.native_decoder.request_stop()
            self._native_recovery.reset()
            self._native_0310_last_sequence = None
            self._normal_video_source = ""
            if self.state is not None:
                self._reset_state_for_scenario(self.state)
            self.mqtt.set_referee_profile(profile.profile_id)
            adapter = profile.create_state_adapter(self)
            self.referee = profile
            self.referee_adapter = adapter
            self.qualification.store.referee_profile = profile.profile_id
            self.qualification.store.protocol_version = profile.protocol_version
            if self.state is not None:
                self.state.referee_profile = profile.profile_id
            if self._input_clear_incomplete():
                raise RuntimeError("Profile input clear incomplete: " +
                                   (self._input_clear_error or "pending observer"))
            self.set_active_video_source(self.config.connection.video_source,
                                         start=start_video, _retry=retry)
            self.field_log.emit("referee_profile", profile=profile.profile_id,
                                version=profile.protocol_version)
        except Exception as exc:
            self._referee_change_error = f"{type(exc).__name__}: {exc}"
            self.safety.lock_control("referee profile change incomplete")
            raise
        self._referee_change_requested = ""

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

    def _scenario_request(self, key: str, *, start: bool, registry: Any,
                          robot_id: int | None, synchronous_start: bool) -> ScenarioRequest:
        """Read and validate GUI-owned settings before starting a transition."""
        key = key if key in SCENARIOS else "LabDebug"
        config = deepcopy(self._config_from_registry(SCENARIOS[key], registry))
        profile_id = self.referee.profile_id
        mode, host, port = self.polar.transport, self.polar.host, self.polar.tcp_port
        mqtt_autostart = True
        if registry is not None:
            mqtt_module = registry.get("MQTTLink")
            auto_connect = mqtt_module.get_setting("Auto Connect") if mqtt_module else None
            mqtt_autostart = bool(mqtt_module and mqtt_module.enabled and
                                  (auto_connect is None or auto_connect.value))
            item = mqtt_module.get_setting("Referee Profile") if mqtt_module else None
            if item is not None:
                profile_id = str(item.value)
            robot_module = registry.get("RobotLink")
            if robot_module is not None:
                def setting(name: str, default: Any) -> Any:
                    item = robot_module.get_setting(name)
                    return item.value if item is not None else default
                mode = str(setting("Link Mode", "serial")).strip().lower()
                host = str(setting("Bridge Host", "")).strip()
                port = int(setting("Bridge Port", 50570))
        profile = get_profile(profile_id)
        identity = profile.validate_client_id(str(self.mqtt.client_id if robot_id is None else robot_id))
        if mode not in {"serial", "tcp"}:
            raise ValueError("Invalid configured robot transport")
        if not 1 <= port <= 65535:
            raise ValueError("Invalid configured robot bridge port")
        preserve_estop = bool(self.safety.estop_active)
        if self._lifecycle.pending_scenario is not None:
            preserve_estop = (self._lifecycle.pending_scenario.preserve_estop
                              or self.safety.estop_at != self._lifecycle.transition_estop_at)
        return ScenarioRequest(key, config, bool(start), identity, profile.profile_id,
                               mode, host, port, preserve_estop, synchronous_start, mqtt_autostart)

    def request_scenario(self, key: str, *, start: bool = True, registry: Any = None,
                         robot_id: int | None = None) -> int:
        """UI entry point: neutralize now; close asynchronously; apply on poll."""
        self._lifecycle.snapshot()  # Enforce owner affinity before touching settings/hooks.
        if self._lifecycle.final:
            raise RuntimeError("Backend is closing; scenario replacement is forbidden")
        try:
            request = self._scenario_request(key, start=start, registry=registry,
                                             robot_id=robot_id, synchronous_start=False)
        except (ValueError, TypeError):
            # Bad settings cannot preserve a previously armed live epoch.
            self.request_shutdown(restart=True)
            raise
        return self._request_scenario(request)

    def _request_scenario(self, request: ScenarioRequest) -> int:
        return self._lifecycle.request_scenario(request, self._capture_teardown_resources())

    def apply_scenario(self, key: str, *, start: bool = True, registry: Any = None,
                       robot_id: int | None = None) -> None:
        """Explicit synchronous compatibility path for CLI and non-UI tests."""
        self._lifecycle.snapshot()
        if self._lifecycle.final:
            raise RuntimeError("Backend is closing; scenario replacement is forbidden")
        try:
            request = self._scenario_request(key, start=start, registry=registry,
                                             robot_id=robot_id, synchronous_start=True)
        except (ValueError, TypeError):
            self.shutdown(restart=True)
            raise
        self._request_scenario(request)
        completion = self._wait_lifecycle()
        if completion is not None and not completion.succeeded:
            raise RuntimeError("Scenario transition failed: " + "; ".join(
                failure.error for failure in completion.failures))

    def _activate_scenario(self, request: ScenarioRequest) -> None:
        """Owner-thread commit after old resource ownership is confirmed gone."""
        # An explicit scenario request has already strictly closed all old
        # video owners. Its activation begins a fresh source-admission epoch.
        self._video_source_request = None
        self._video_source_failures = ()
        self._active_video_source = ""
        self.scenario_key = request.key
        self._normal_video_source = ""
        if self.state is not None:
            self._reset_state_for_scenario(self.state)
        self._apply_config(request.config)
        self.set_referee_profile(request.referee_profile, start_video=False)
        self.set_client_id(request.robot_id)
        self.polar.transport = request.polar_mode
        self.polar.host = request.polar_host
        self.polar.tcp_port = request.polar_tcp_port
        # A transition-created latch may become a fresh LOCKED epoch. An
        # operator E-stop before or during teardown must never be released.
        if not request.preserve_estop and self.safety.estop_at == self._lifecycle.transition_estop_at:
            self.safety.release_estop()
        if self._input_clear_incomplete():
            raise RuntimeError("Scenario input clear incomplete: " + (self._input_clear_error or "pending observer"))
        # 场景启动的临时急停已结束；编码器授权独立等待数据和图传就绪。
        angle_link = getattr(self, "controller_angles", None)
        if angle_link is not None and not self.safety.estop_active:
            angle_link.prepare_startup_arm()
        if not request.start:
            return
        if request.mqtt_autostart:
            self.mqtt.start()
        if self._active_video_source in {"shark udp", "udp", "udp hevc", "hevc", "h265", "legacy vt02 udp", "legacy official bridge"}:
            self.udp.start()
        if request.synchronous_start:
            self.serial.start()
        else:
            # Serial open can stall inside an OS driver. This one captured
            # owner is joined before a later close/replacement may proceed.
            starter = threading.Thread(target=self.serial.start,
                name="backend-start-serial", daemon=True)
            self._serial_start_thread = starter
            try:
                starter.start()
            except Exception:
                # ident survives thread exit: start-then-raise still owns any
                # late-opened handle, even if is_alive() is already false.
                if starter.ident is None:
                    self._serial_start_thread = None
                raise
        self.polar.request_start()

    def _retry_polar_connect(self) -> None:
        """操作员经常是先启动客户端、后插机器人，或者中途重启机器人。

        `polar.start()` 原本只在 `apply_scenario` 里调一次，所以那种情况下
        链路会永远停在未连接，界面上只会看到 LNK 不亮。这里在“场景要求队内
        链路但未连上”时按固定间隔重试；Auto 也会重新探测端口枚举，因此
        插上新 COM 号能自动接上。
        """

        # 重试的判据是“用户给这条链路配了真端口”，而不是场景名：
        # 否则在大疆官方场景下选了 COM3 也永远不会重连。
        tcp_mode = str(self.polar.transport).lower() in {"tcp", "wifi", "bridge"}
        if (tcp_mode and not str(self.polar.host).strip()) or (
                not tcp_mode and str(self.polar.port or "").strip().lower() in {"", "stub", "none"}):
            return
        if self.polar.connected or self.polar._running:
            return
        now = time.perf_counter()
        if now - self._polar_last_connect_attempt < POLAR_RETRY_S:
            return
        self._polar_last_connect_attempt = now
        self._invalidate_polar_epoch("robot transport reconnect; fresh telemetry required")
        was_connected = self.polar.connected
        self.polar.request_start()
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

    def _invalidate_polar_epoch(self, reason: str) -> None:
        self.safety.lock_control(reason)
        self.polar_bridge.reset()
        if self.state is not None:
            self.state.clear_polar_reported_identity()
        self.polar_arbiter.invalidate_pending_divergence()
        self.polar_motion.reset()
        self.polar.request_estop()
        self._polar_gimbal_last = None
        self.polar_chassis_state_seen = False

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

        decision = SpinCommandState(self._spin_ever_on, self._spin_off_since).advance(
            desired=desired, reported=reported, now=time.perf_counter(),
            off_assert_s=self.SPIN_OFF_ASSERT_S,
        )
        self._spin_ever_on = decision.ever_on
        self._spin_off_since = decision.off_since
        self._spin_assert = decision.command
        self.polar.set_spin_command(decision.command)
        return decision.command

    def sync_controller_angles(self, registry: Any) -> dict[str, Any]:
        """由管理器统一检查端口、配置和授权；前端只消费值快照。"""
        return self._sync_controller_angles(registry)

    def arm_controller_angles(self, registry: Any) -> tuple[bool, str]:
        """在用户完成确认后重新检查当前配置与授权，再尝试开启转发。"""
        self.sync_controller_angles(registry)
        return self.controller_angles.arm()

    def disarm_controller_angles(self) -> None:
        self.controller_angles.disarm()

    def controller_angle_snapshot(self) -> dict[str, Any]:
        """汇总角度与接收端诊断，不向 UI 暴露设备所有者。"""
        return {"angles": self.controller_angles.stats(), "receiver": self.video_receiver_status()}

    def _sync_controller_angles(self, registry: Any) -> dict[str, Any]:
        """Configure the independent RX relay; never open a device on the UI thread.

        Same human driving gates as the existing serial CC route, plus the
        relay's explicit arm. MQTT qualification does not authorize or block
        this separate physical-controller serial link.
        """
        if self._backend_transition_blocks_work():
            self.controller_angles.disarm("Backend transition pending or failed", preserve_startup=True)
            status = self.controller_angles.stats()
            if self.state is not None:
                self.state.backend_stats["controller_angles"] = status
            return status
        module = registry.get("ControllerAngles") if registry is not None else None
        robot = registry.get("RobotLink") if registry is not None else None

        def value(owner, name, fallback):
            setting = owner.get_setting(name) if owner else None
            return setting.value if setting is not None else fallback

        config = AngleRelayConfig(
            enabled=bool(module is not None and module.enabled),
            rx_port=str(value(module, "Angle RX Port", "Stub")).strip(),
            tx_port=str(value(module, "Angle TX Port", "Stub")).strip(),
            forward=bool(value(module, "Forward Angles", False)),
            mock_output=bool(value(module, "Angle Mock Writes", True)),
            output_route=str(value(module, "Angle Output", "Official Client Serial")),
            auto_arm=bool(value(module, "Angle Auto Arm", False)),
        )
        receiver = getattr(getattr(self, "udp", None), "legacy_control", None)
        if config.output_route in LEGACY_ANGLE_ROUTES:
            self.controller_angles.set_legacy_receiver(receiver)
        authorized = bool(robot is not None and robot.enabled
                          and value(robot, "Allow Drive", False)
                          and not self.safety.estop_active)
        conflict = ""
        # Do not enumerate/resolve Auto here: that could stall the UI and could
        # change its answer before an independent owner opens a port. Require
        # explicit disjoint names while this receiver owns physical hardware.
        owners = []
        receiver_port = getattr(receiver, "owned_port", "") if receiver is not None else ""
        if receiver_port:
            owners.append(("Legacy receiver", receiver_port))
        if robot is not None:
            if value(robot, "Link Mode", "serial") == "serial":
                owners.append(("RobotLink", str(value(robot, "Port", "Stub"))))
            if value(robot, "Custom Controller Egress", False) and not value(robot, "CC Mock Writes", True):
                owners.append(("CC keymouse", str(value(robot, "CC Port", "Auto"))))
            if value(robot, "Direct DT7 Egress", False) and value(robot, "DT7 Real TX", False):
                owners.append(("DT7", str(value(robot, "DT7 Port", "Auto"))))
        for label, handle in (("CC prior writer", getattr(self, "_cc_link", None)),
                              ("DT7 prior writer", getattr(self, "_dt7_link", None))):
            if (handle is not None and not getattr(handle, "mock_output", True)
                    and getattr(handle, "port_handoff_ready", False) is not True):
                # A generic closed flag is insufficient: the writer must
                # prove neutral, completed teardown and no retained handle.
                owners.append((label, str(getattr(handle, "resolved_port", "")
                                          or getattr(handle, "port", "Auto"))))
        serial = getattr(self, "serial", None)
        if serial is not None and getattr(serial, "running", False):
            owners.append(("referee serial", str(serial.port)))
        used = {port_key(config.rx_port)}
        if config.forward and not config.mock_output and config.output_route == "Official Client Serial":
            used.add(port_key(config.tx_port))
        if config.enabled:
            # Keep alias observations lazy: the first conflict stops evaluation,
            # and disabled reception must not inspect owner aliases at all.
            reservations = (
                PortReservation(label, unresolved_auto=True)
                if port.strip().lower() == "auto" else
                PortReservation(label, port_key(port) if explicit_port(port) else None)
                for label, port in owners
            )
            conflict = first_port_conflict(used, reservations)
        if conflict:
            config = replace(config, enabled=False)
            authorized = False
        self.controller_angles.configure(config, authorized)
        if conflict:
            self.controller_angles.disarm(conflict)
        elif self.safety.estop_active:
            self.controller_angles.disarm("E-stop active")
        else:
            armed, reason = self.controller_angles.arm_on_startup()
            log = getattr(self, "field_log", None)
            if armed and log is not None:
                log.emit("controller_angle_auto_arm", reason=reason,
                         status=self.controller_angles.stats())
        status = self.controller_angles.stats()
        if self.state is not None:
            self.state.backend_stats["controller_angles"] = status
        return status

    def tick_polar_link(self, state=None, registry=None, control=None) -> dict[str, Any]:
        """Never wait for the transport's driver-held TX lock on a UI tick."""
        if control is not None:
            self._dt7_control_ref = control
        self._drain_input_clear()
        if self._backend_transition_blocks_work():
            return {"active": False, "polar_status": self.lifecycle_snapshot.phase.value}
        self._poll_device_writers(control)
        # The independent angle watchdog/ownership must run even if POLAR is
        # busy or closing, before another serial egress can claim a port.
        if hasattr(self, "controller_angles"):
            self._sync_controller_angles(registry)
        self._configure_device_egress(registry, control)
        if getattr(self.polar, "_stopping", False):
            return {"active": self.polar_bridge.active, "polar_status": "closing"}
        lock = self.polar._intent_lock
        if not lock.acquire(blocking=False):
            self._polar_ui_busy_skips = getattr(self, "_polar_ui_busy_skips", 0) + 1
            return {"active": self.polar_bridge.active, "polar_status": "writer busy"}
        try:
            return self._tick_polar_link_ready(state, registry, control)
        finally:
            lock.release()

    def _prepare_polar_reconfigure(self, reason):
        worker = self.polar._thread
        if (not self.polar._stopping and not self.polar._connecting
                and not self.polar._running and self.polar._serial is None
                and (worker is None or not worker.is_alive())):
            if not getattr(self, "_polar_reconfigure_pending", False):
                self._invalidate_polar_epoch(reason)
            self._polar_reconfigure_pending = False
            return True
        if not getattr(self, "_polar_reconfigure_pending", False):
            self._polar_reconfigure_pending = True
            self._invalidate_polar_epoch(reason)
            self.polar.request_stop()
        worker = self.polar._thread
        if (self.polar._stopping or self.polar._connecting or self.polar._running
                or (worker is not None and worker.is_alive())):
            return False
        self._polar_reconfigure_pending = False
        return True

    def _poll_device_writers(self, control):
        dt7_writer = getattr(self, "_dt7_link", None)
        if getattr(dt7_writer, "asynchronous", False):
            dt7_writer.poll()
            if control is not None:
                control.dt7_sent = dt7_writer.sent
                control.dt7_release_pending = dt7_writer.release_pending
                if dt7_writer.last_error:
                    control.dt7_last_error = dt7_writer.last_error
        cc_writer = getattr(self, "_cc_link", None)
        if control is not None and getattr(cc_writer, "asynchronous", False):
            if cc_writer.faulted and getattr(self, "_cc_failure_seen", None) is not cc_writer:
                self._cc_failure_seen = cc_writer
                self.safety.lock_control("CC disconnected/write failed; explicit re-arm required")
            control.cc_sent = cc_writer.sent
            control.cc_release_pending = cc_writer.release_pending
            if cc_writer.last_error:
                control.cc_last_error = cc_writer.last_error

    def _configure_device_egress(self, registry, control):
        settings = DeviceEgressSettings.capture(registry)
        permissions = EgressPermissions(
            settings.module_enabled, bool(settings.allow_drive_value), bool(self.safety.estop_active),
        )
        # Independent writers must process every falling edge even while the
        # other writer is still retiring an old target.
        self._configure_cc_egress(control, settings.cc, permissions)
        dt7_settings = settings.dt7
        dt7_wanted = dt7_settings.requested
        if control is not None:
            control.dt7_selected = dt7_wanted
        self._dt7_selected = bool(dt7_wanted and permissions.module_enabled)
        if control is not None and dt7_wanted:
            prof = dt7_link.load_profile()
            endpoint = dt7_settings.endpoint()
            port = endpoint.port
            # "DT7 Real TX" 是显式硬件闸：不勾就只产帧不碰串口。原来这里连
            # mock_output 都不传，等于 C 路在客户端里根本发不出一个字节。
            real_tx = endpoint.real_tx
            link = getattr(self, "_dt7_link", None)
            if getattr(link, "startup_failed", False) and not link.closed:
                control.dt7_link = link
                control.dt7_enabled = False
                control.dt7_last_error = link.last_error or "DT7 writer startup cleanup pending"
                self._dt7_was_enabled = False
                return {"active": self.polar_bridge.active, "dt7_error": control.dt7_last_error}
            angle_link = getattr(self, "controller_angles", None)
            owned_ports = angle_link.owned_ports if angle_link is not None else ()
            if real_tx and owned_ports and (port.strip().lower() == "auto"
                    or any(port_key(port) == port_key(p) for p in owned_ports)):
                # A stop request does not release the angle reader's serial
                # ownership. Revoke before neutralizing the retained DT7
                # writer; neither a new writer nor its authority may bypass it.
                control.dt7_enabled = False
                if link is not None:
                    control.dt7_link = link
                if (getattr(self, "_dt7_was_enabled", False)
                        or getattr(control, "dt7_release_pending", False)
                        or not getattr(link, "wire_neutral", True)):
                    control.release_direct_dt7()
                control.dt7_last_error = "角度链路仍占用串口；等待关闭后再启用直连 DT7 出口"
                self._dt7_was_enabled = False
                return {"active": self.polar_bridge.active,
                        "dt7_error": control.dt7_last_error}
            if (link is None or getattr(link, "closed", False)
                    or link.port != port or link.profile != prof
                    or link.mock_output == real_tx):
                if link is not None:
                    change_key = (port, repr(prof), real_tx)
                    if getattr(self, "_dt7_change_key", None) != change_key:
                        self._dt7_change_key = change_key
                        self.safety.lock_control("DT7 transport/profile/mode changed; requalification/re-arm required")
                    # Never discard a non-neutral writer just because the UI
                    # changed port, profile or mock mode.
                    if not link.wire_neutral:
                        ok, why = link.release()
                        if not ok:
                            control.dt7_link = link
                            control.dt7_enabled = False
                            control.dt7_release_pending = True
                            return {"active": self.polar_bridge.active, "dt7_error": str(why)}
                    try:
                        link.close()
                    except Exception:
                        pass
                    if getattr(link, "asynchronous", False) and not link.closed:
                        control.dt7_enabled = False
                        return {"active": self.polar_bridge.active, "dt7_error": "DT7 previous writer closing"}
                writer_type = AsyncDt7KeyMouseLink if real_tx else dt7_link.Dt7KeyMouseLink
                ownership = ({"retain_owner": lambda owner: setattr(self, "_dt7_link", owner)}
                             if real_tx else {})
                link = writer_type(port=port, profile=prof,
                                   mock_output=not real_tx, safety=self.safety, **ownership)
                if getattr(self, "_dt7_link", None) is not link:
                    self._dt7_link = link
                self._dt7_change_key = None
            control.dt7_link = link
            link.safety = self.safety
            link.authorization_guard = self._dt7_evidence_gate
            policy_ok, policy_reason = (True, "mock") if link.mock_output else self.safety.can_transmit_dt7(
                qualification_token=getattr(control, "dt7_qualification_token", None))
            dt7_live = bool(permissions.may_drive and policy_ok)
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
            if dt7_settings.spin_allowed:
                mask |= int(DT7_SPIN_KEY_MASK)
            if dt7_settings.fire_allowed:
                mask |= int(DT7_FIRE_KEY_MASK)
            control.dt7_allow_fire = dt7_settings.fire_allowed
            control.dt7_key_mask = mask
            control.dt7_enabled = dt7_live
            if not dt7_live:
                control.dt7_last_error = (str(policy_reason) if not policy_ok else
                    "直连 DT7 出口未生效（急停/未授权/模块未启用）")
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

    def _tick_polar_link_ready(
        self,
        state: RobotState | None = None,
        registry: Any = None,
        control: Any = None,
    ) -> dict[str, Any]:
        """Apply one POLAR tick after simulation; publish only gated intentions."""
        target = state if state is not None else self.state
        if target is None:
            return {"active": False}
        if control is not None:
            self._dt7_control_ref = control
        self._apply_polar_telemetry(target)
        settings = RobotLinkSettings.capture(registry)
        if not self._apply_polar_configuration(settings):
            return {"active": self.polar_bridge.active, "polar_status": "closing"}
        # Retry only after all endpoint settings, never against the old target.
        self._retry_polar_connect()
        target.polar_link_active = self.polar_bridge.active
        self.polar_arbiter.request_takeover(
            client_gimbal_enabled=settings.gimbal.requested,
            vision_nuc_occupied=settings.gimbal.vision_nuc_occupied,
        )
        estopped = self._sync_polar_estop()
        plan = self._plan_polar_gimbal(target, estopped=estopped)
        command = getattr(control, "last_command", None) if control is not None else None
        role = RobotRoleSelection.resolve(
            int(getattr(target, "robot_id", 0) or 0),
            int(getattr(target, "polar_reported_robot_id", 0) or 0),
        )
        gates = ControlGates(estopped, settings.drive.module_enabled,
                             settings.drive.allow_drive, bool(self.polar_bridge.active))
        drive_armed = gates.chassis_authorized(
            requested=settings.drive.chassis_requested, role_block=role.drive_block,
        )
        if control is not None:
            self._apply_radio_km_feedback(control, self.polar_bridge, gates,
                                          requested=settings.drive.radio_requested)
        velocity = self._scale_polar_drive(command, settings.drive, role, armed=drive_armed)
        motion_state, motion_stats = self._update_polar_motion(target, velocity)
        self._publish_polar_authority(target, estopped=estopped,
                                      drive_armed=drive_armed, motion_stats=motion_stats)
        self._apply_polar_spin(target, settings.spin, gates)
        self._apply_polar_gimbal_host_request(control, settings.gimbal, gates)
        self._submit_polar_intent(velocity, plan, command, drive_armed=drive_armed)
        return {
            "active": self.polar_bridge.active,
            "drive_armed": drive_armed,
            "estopped": estopped,
            "motion_state": motion_state,
            "authority": self.polar_arbiter.state if not estopped else "ESTOP",
            "gimbal_target_deg": plan,
            "error": self.polar.last_error or self.polar_bridge.last_error,
            **role.diagnostics(),
            "gimbal_egress": self.gimbal_egress_owner(target, registry),
        }

    def _apply_polar_telemetry(self, target: RobotState) -> None:
        """Apply authoritative robot data after the frontend simulation step."""
        bridge = self.polar_bridge
        for segment in self.polar.drain_segments():
            bridge.apply(segment)
            if segment.segment_id == SEG_ROBOT_MOTION:
                self.polar_motion.note_motion_segment()
            elif segment.segment_id == SEG_ROBOT_STATUS:
                # Position is motion evidence when firmware has no 0x1E segment.
                self.polar_motion.note_position(
                    bridge.overlay.get("polar_self_x", 0.0),
                    bridge.overlay.get("polar_self_y", 0.0),
                )
            elif segment.segment_id == SEG_CHASSIS_STATE:
                self.polar_chassis_state_seen = True
        target.polar_link_active = bridge.active
        target.polar_link_age_s = min(bridge.link_age_s, 999.0)
        target.polar_segments_received = sum(bridge.segment_counts.values())
        target.polar_dropped_segments = bridge.dropped
        target.polar_debug_packages = dict(bridge.debug_packages)
        target.polar_module_errors = dict(bridge.module_errors)
        target.polar_overlay = dict(bridge.overlay)
        target.apply_polar_overlay()

    def _sync_polar_estop(self) -> bool:
        estopped = bool(getattr(self.safety, "estop_active", False))
        if estopped:
            self.polar.request_estop()
            self._polar_gimbal_last = None
        else:
            self.polar.release_estop()
        return estopped

    def _plan_polar_gimbal(self, target: RobotState, *, estopped: bool) -> tuple[float, float] | None:
        plan = None
        if may_plan_gimbal(
            estopped=estopped,
            arbiter_authorized=self.polar_arbiter.may_command_gimbal,
            link_active=self.polar_bridge.active,
            calibrated=bool(getattr(target, "vision_calibrated", False)),
            target_locked=bool(getattr(target, "target_locked", False)),
            selected_robot_id=getattr(target, "robot_id", 0),
        ):
            plan = self.polar_arbiter.plan_gimbal(
                float(getattr(target, "vision_aim_yaw_deg", 0.0) or 0.0),
                float(getattr(target, "vision_aim_pitch_deg", 0.0) or 0.0),
                self.polar_bridge.overlay.get("gimbal_yaw_deg"),
                self.polar_bridge.overlay.get("gimbal_pitch_deg"),
            )
        self._polar_gimbal_last = plan
        return plan

    @staticmethod
    def _scale_polar_drive(command, settings: PolarDriveSettings,
                           role: RobotRoleSelection, *, armed: bool) -> tuple[float, float, float]:
        if not armed or command is None:
            return 0.0, 0.0, 0.0
        speed_scale, yaw_scale = settings.scales()
        return scale_drive(
            command.vx, command.vy, command.wz,
            max_linear_mps=role.limits.max_linear_mps * speed_scale,
            max_yaw_rps=role.limits.max_yaw_rps * yaw_scale,
        )

    def _update_polar_motion(self, target: RobotState, velocity: tuple[float, float, float]):
        bridge = self.polar_bridge
        motion_state = self.polar_motion.update(
            link_active=bridge.active,
            commanded_vx=velocity[0], commanded_vy=velocity[1], commanded_wz=velocity[2],
            measured_vx=bridge.overlay.get("measured_vx_mps"),
            measured_vy=bridge.overlay.get("measured_vy_mps"),
            measured_wz=bridge.overlay.get("measured_wz_rps"),
        )
        stats = self.polar_motion.stats()
        target.polar_motion_state = motion_state
        target.polar_motion_detail = str(stats["detail"] or "")
        return motion_state, stats

    def _publish_polar_authority(self, target: RobotState, *, estopped: bool,
                                 drive_armed: bool, motion_stats: dict[str, Any]) -> None:
        """Refresh authority/diagnostics even without any referee traffic."""
        bridge = self.polar_bridge
        authority = self.polar_arbiter.stats()
        target.polar_drive_armed = drive_armed
        target.polar_gimbal_client = self.polar_arbiter.may_command_gimbal
        target.polar_authority_state = "ESTOP" if estopped else str(authority["state"])
        target.polar_authority_reason = (
            "急停激活，队内链路控制已锁零" if estopped else str(authority["reason"] or "")
        )
        target.backend_stats["polar_link"] = {
            **capture_polar_link_health(lambda: self.polar, lambda: bridge),
            "authority": authority,
            "motion": motion_stats,
            "timestamp_roll_detected": self.polar.timestamp.roll_detected,
        }

    def _apply_polar_spin(self, target: RobotState, settings: PolarSpinSettings,
                          gates: ControlGates) -> None:
        """Assert 0x04 spin independently of chassis drive; never inject SHIFT."""
        bridge = self.polar_bridge
        gates = replace(gates, link_active=bool(bridge.active))
        desired = gates.spin_authorized(allowed=settings.allowed, module_enabled=settings.module_enabled)
        reported = bridge.overlay.get("polar_spin_active")
        if reported is None:
            reported = bridge.overlay.get("spin_active")
        self._update_spin_command(desired=desired, reported=reported)
        target.polar_spin_note = spin_block_reason(
            allow_spin=settings.allowed, link_active=bool(bridge.active),
            estop=gates.estopped, module_on=settings.module_enabled, reported=reported,
        )
        note = str(target.polar_spin_note or "")
        if note != self._last_spin_note:
            self._last_spin_note = note
            if note:
                _LOG.warning("spin command not sent: %s", note)
                self.field_log.emit("spin_command_blocked", reason=note,
                                    link_active=bool(bridge.active), reported=reported)

    def _apply_polar_gimbal_host_request(self, control, settings: PolarGimbalSettings,
                                         gates: ControlGates) -> None:
        gates = replace(gates, link_active=bool(self.polar_bridge.active))
        requested = gates.gimbal_host_requested(requested=settings.requested)
        self.polar.set_gimbal_host_request(requested)
        # Publish after calculating this tick's request, never the previous bit.
        if control is not None:
            control.dt7_mouse_note = (
                "云台直控请求占着（0x04 param0 bit0）："
                "固件中间档走 AUTO_AIM，DT7 鼠标不控云台"
                if (getattr(control, "dt7_enabled", False) and requested) else "")

    def _submit_polar_intent(self, velocity: tuple[float, float, float],
                             plan: tuple[float, float] | None, command, *, drive_armed: bool) -> None:
        key_value = int(getattr(command, "keyboard_value", 0) or 0) if drive_armed else 0
        self.polar.submit(PolarDriveIntent(
            vx=velocity[0], vy=velocity[1], wz=velocity[2],
            gimbal_pitch_rad=deg_to_rad(plan[1]) if plan else 0.0,
            gimbal_yaw_rad=deg_to_rad(plan[0]) if plan else 0.0,
            friction_on=False, tracking=bool(plan), key_value=key_value,
            # Spin uses 0x04; the transport retains the structural W/S/A/D mask.
            allow_spin_key=False,
        ))

    def _apply_polar_configuration(self, settings: RobotLinkSettings) -> bool:
        """Apply configuration in order, retaining the old writer until retired.

        Default Auto/115200 do not override a scenario endpoint. A changing
        endpoint, baud, bridge or output mode must finish its existing safe
        reconfiguration handshake before subsequent settings are applied.
        """
        request = settings.transport
        port = request.explicit_port()
        if port is not None and port != str(self.polar.port or ""):
            if not self._prepare_polar_reconfigure("robot port changed; fresh telemetry required"):
                return False
            self.polar.port = port
            self.polar.last_error = self.polar.last_error_code = ""
        baud = request.explicit_baud()
        if baud is not None and baud != int(self.polar.baud):
            if not self._prepare_polar_reconfigure("robot baud changed; fresh telemetry required"):
                return False
            self.polar.baud = baud
        want_mock = (self.polar.mock_writes if request.mock_writes is ABSENT
                     else bool(request.mock_writes))
        if settings.gimbal.slew is not ABSENT and self.polar_arbiter.slew != float(settings.gimbal.slew):
            self.polar_arbiter.slew = max(1.0, min(45.0, float(settings.gimbal.slew)))
        endpoint = request.bridge_endpoint()
        if endpoint is not None and (self.polar.transport, self.polar.host, self.polar.tcp_port) != endpoint:
            if not self._prepare_polar_reconfigure("robot bridge endpoint changed; fresh telemetry required"):
                return False
            self.polar.transport, self.polar.host, self.polar.tcp_port = endpoint
            if self.polar.transport == "tcp" and not self.polar.host:
                self.polar.last_error = "TCP 模式要先填桥接主机地址"
                self.polar.last_error_code = "NO_BRIDGE_HOST"
        if want_mock != self.polar.mock_writes:
            if (self.polar._serial is not None or self.polar._running
                    or self.polar._connecting or self.polar._stopping):
                if not self._prepare_polar_reconfigure("robot output mode changed; fresh telemetry required"):
                    return False
            self.polar.mock_writes = want_mock
        if request.send_hz is not ABSENT:
            try:
                self.polar.set_send_hz(float(request.send_hz))
            except (TypeError, ValueError):
                pass  # Malformed persisted frequency retains the current value.
        channel = request.normalized_drive_channel()
        if channel is not None:
            self.polar.drive_channel = channel
        return True

    def _apply_radio_km_feedback(self, control, bridge, gates: ControlGates, *, requested: bool) -> None:
        """Adapt a detached readback decision to the existing control/HUD fields."""
        debug = bridge.debug_packages or {}
        authorized = gates.radio_authorized(requested=requested)
        # Revoke authority before decoding diagnostic data: bad telemetry must
        # never preserve the previous tick's permission to publish key/mouse.
        control.radio_km_enabled = authorized
        policy = decode_radio_km_policy(debug.get("km_pol"))
        control.radio_km_policy = policy.name
        control.radio_km_policy_hint = policy.hint
        feedback = evaluate_radio_km_feedback(
            RadioKmReadback(debug.get("km_rx"), debug.get("km_key"), debug.get("km_pol"),
                            bridge.debug_key_at, bridge.debug_key_sequence),
            requested=requested, authorized=authorized,
            previous_received=getattr(self, "_km_rx_prev", None), now=time.perf_counter(),
            policy=policy,
        )
        control.radio_km_robot_key = feedback.robot_key
        control.radio_km_robot_key_at = feedback.robot_key_at
        control.radio_km_robot_key_sequence = feedback.robot_key_sequence
        control.radio_km_robot_rx = feedback.robot_rx
        control.radio_km_robot_rx_growing = feedback.robot_rx_growing
        control.radio_km_link_state = feedback.link_state
        control.radio_km_link_note = feedback.link_note
        control.radio_km_gate_reason = gates.radio_block_reason(requested=requested)
        self._km_rx_prev = feedback.next_received

    def _reset_state_for_scenario(self, state: RobotState) -> None:
        """Discard data from the previous transport epoch before reconnecting."""

        self._clear_mock_frontend_state(state)
        state.clear_native_telemetry()
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
        state.clear_polar_reported_identity()
        self.polar_arbiter.invalidate_pending_divergence()
        self.polar_arbiter.clear_lockout()
        self.polar_motion.reset()
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
        state.clear_air_support_observations()
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
        state.projectile_damage_small_initialized = False
        state.projectile_damage_large_initialized = False
        state.projectile_damage_small_total = 0
        state.projectile_damage_large_total = 0
        state.last_projectile_hit_at = 0.0
        state.last_projectile_damage_delta = 0
        state.last_projectile_kind = ""
        state.last_projectile_killer_id = 0
        state.deploy_mode = 0
        state.deploy_status_revision += 1
        state.deploy_status_at = 0.0
        state.total_damage_ally = None
        state.total_damage_enemy = None
        state.clear_match_settlement()
        state.current_stage = "NOT_STARTED"
        for key in ("respawn_status", "student_legacy_roster", "student_legacy_fields"):
            state.backend_stats.pop(key, None)

    @staticmethod
    def _sync_local_projectile_allowance(state: RobotState) -> None:
        return Rm2026StateAdapter._sync_local_projectile_allowance(state)

    def tick(self) -> None:
        self.poll_lifecycle()
        if self._backend_transition_blocks_work():
            return
        self.mqtt.poll_connection()
        self.serial.tick()
        if self.mqtt._started and not self.mqtt.connected and self.mqtt.disconnected_at > 0.0:
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

    def _poll_native_decoder_restart(self) -> None:
        """Apply completion only on the manager/UI thread, never the worker."""

        try:
            generation, restarted, health = self._native_restart_results.get_nowait()
        except Empty:
            return
        self._native_restart_thread = None
        if generation != self._native_restart_generation:
            return
        self._native_failure_epoch_handled = bool(restarted)
        self._native_recovery.reset()
        self._native_0310_blocked_until_sps = True
        if restarted:
            self._native_0310_decoder_restarts += 1
            self._native_0310_decoder_started_once = True
            self._native_restart_backoff_s = 0.5
            self._native_next_restart_at = 0.0
        else:
            self._native_next_restart_at = time.perf_counter() + self._native_restart_backoff_s
            self._native_restart_backoff_s = min(5.0, self._native_restart_backoff_s * 2.0)
        self.field_log.emit(
            "decoder_restart", source="native_0310", restarted=restarted, health=health,
        )

    def _native_video_selected(self, state: RobotState | None) -> bool:
        """Recovery and chunk admission share video-source activation policy.

        The caller supplies its authoritative state; chunk application can use
        a different state object from the manager's current UI state.
        """
        return self._active_video_source in {
            "native", "native 0x0310", "0x0310",
        }

    def _recover_video_decoders(self) -> None:
        """Schedule at most one recovery; the UI tick never tears down pipes."""

        self._poll_native_decoder_restart()
        if self._video_source_failures:
            return
        request = getattr(self, "_video_source_request", None)
        if (request is not None and request.start and self._active_video_source in {
                "udp hevc", "legacy vt02 udp", "legacy official bridge"}):
            self.udp.poll_connection()
        if self.udp.decode_h265 and self.udp.running and self.udp.h265_decoder is not None:
            health = self.udp.h265_decoder.health()
            if health.get("first_frame_timeout") or health.get("stale"):
                self.udp._request_h265_restart()

        native_selected = self._native_video_selected(self.state)
        if not native_selected or self._native_restart_thread is not None:
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
        if time.perf_counter() < self._native_next_restart_at:
            return
        decoder = self.native_decoder
        decoder_generation = decoder.lifecycle_generation
        generation = self._native_restart_generation
        results = self._native_restart_results
        self._native_recovery.reset()
        self._native_0310_blocked_until_sps = True

        def recover() -> None:
            try:
                restarted = decoder.restart(clear_latest=True, expected_generation=decoder_generation)
            except Exception as exc:
                restarted = False
                health["restart_error"] = f"{type(exc).__name__}: {exc}"
            # The only manager-owned object touched here is a bounded mailbox.
            # Source switches and shutdown invalidate completion by generation.
            try:
                results.put_nowait((generation, restarted, health))
            except Full:
                pass

        worker = threading.Thread(target=recover, name="native-decoder-restart", daemon=True)
        self._native_restart_thread = worker
        try:
            worker.start()
        except Exception as exc:
            # ident survives completion. A start wrapper can raise after the
            # real thread ran, so only a never-admitted thread loses ownership.
            if worker.ident is None:
                self._native_restart_thread = None
            decoder.last_error = f"Native recovery admission failed: {type(exc).__name__}: {exc}"
            self._native_next_restart_at = time.perf_counter() + self._native_restart_backoff_s
            self._native_restart_backoff_s = min(5.0, self._native_restart_backoff_s * 2.0)
            raise

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
        if self.field_log.snapshot()["closing"]:
            emit_lifecycle_event(self.field_log, event, **payload)
            return
        if event == "referee_rx" and isinstance(payload.get("payload"), bytes):
            self.field_log.referee_payload(str(payload.get("topic", "")), payload["payload"])
            return
        self.field_log.emit(event, **payload)

    def _dt7_evidence_gate(self, profile: dict) -> tuple[bool, str]:
        # 台架实验（LabDebug）：场景显式打开台架驾驶时，跳过比赛资格证据校验。
        # 传输本身仍受急停 / 模块开关 / Allow Drive / 30Hz 限频约束；
        # 其他场景（OfficialEvent / CampusSimulation）的资格链完全不变。
        if self.safety.allow_dt7_drive:
            return True, "lab drive (LabDebug): qualification evidence waived"
        if "dt7_control" not in self.qualification.capabilities():
            return False, "DT7 evidence missing/expired or human sign-off invalid"
        session = self.qualification.session
        identity_ok, reason = self.qualification.identity_matches(
            robot_id=str(getattr(self.state, "robot_id", session.robot_id)),
            firmware_hash=session.firmware_hash,
            match_mode=getattr(self.state, "match_mode", session.match_mode),
            video_source=self._canonical_readiness_video_source())
        if not identity_ok:
            return False, reason
        digest = dt7_link.profile_digest(profile)
        for key in ("dt7_link_test", "dt7_robot_safety_test"):
            result = session.tests.get(key) if session is not None else None
            if not result or not isinstance(result.measured, dict) or result.measured.get("profile_sha256") != digest:
                return False, "DT7 profile differs from qualified evidence"
        return True, "DT7 evidence matched"

    def _request_input_clear(self, reason: str) -> None:
        """Admit neutral immediately; deliver presentation work on the owner.

        Safety authority has already been revoked by the caller. A worker
        may enqueue neutral for the asynchronous device writers, but it must
        never mutate the UI control snapshot or invoke its observer.
        """
        self._admit_input_clear(reason)
        if threading.get_ident() != self._input_owner_thread:
            self._request_device_neutral(reason, worker=True)
            return
        self._drain_input_clear()

    def _admit_input_clear(self, reason: str) -> None:
        try:
            self._input_clear_requests.put_nowait(str(reason))
        except Full:
            pass  # Clearing is idempotent; retain one pending request.

    def _drain_input_clear(self) -> None:
        if threading.get_ident() != self._input_owner_thread:
            raise RuntimeError("Input observers must be drained on their owner thread")
        try:
            reason = self._input_clear_requests.get_nowait()
        except Empty:
            return
        try:
            self._apply_input_clear(reason)
        except Exception as exc:
            # Removing an item is not successful delivery. Keep a failed clear
            # pending across polls, coalesced callbacks and repeated re-arms.
            self._admit_input_clear(reason)
            self._input_clear_error = f"{type(exc).__name__}: {exc}"
            if not self.safety.estop_active:
                self.safety.lock_control("input clear failed; explicit re-arm required", notify=False)
            emit_lifecycle_event(self.field_log, "input_clear_error", reason=reason,
                generation=self._input_clear_generation, error=self._input_clear_error)
            raise
        self._input_clear_error = ""

    def _input_clear_incomplete(self) -> bool:
        return bool(self._input_clear_error) or not self._input_clear_requests.empty()

    def _request_device_neutral(self, reason: str, *, worker: bool = False) -> tuple[bool | None, bool | None]:
        """Clear keyboard outputs; independent encoder forwarding survives focus loss."""
        angle_link = getattr(self, "controller_angles", None)
        if angle_link is not None and reason not in {"window focus lost", "menu dialog opened"}:
            if reason == "video settings applied":
                angle_link.disarm(reason, preserve_startup=True)
            else:
                angle_link.disarm(reason)
        dt7_ok = cc_ok = None
        link = getattr(self, "_dt7_link", None)
        if link is not None and not link.wire_neutral:
            try:
                if getattr(link, "asynchronous", False):
                    dt7_ok, _ = link.request_release()
                elif not worker:
                    dt7_ok, _ = link.release()
            except Exception:
                dt7_ok = False
        cc_writer = getattr(self, "_cc_link", None)
        if cc_writer is not None and not getattr(cc_writer, "wire_neutral", True):
            try:
                if not worker or getattr(cc_writer, "asynchronous", False):
                    cc_ok, _ = cc_writer.release_all()
            except Exception:
                cc_ok = False
        return dt7_ok, cc_ok

    def _apply_input_clear(self, reason: str) -> None:
        """Clear owner-thread control caches before delivering the UI hook."""
        self._input_clear_generation += 1
        # Even a broken control/UI adapter cannot skip physical neutral.
        dt7_ok, cc_ok = self._request_device_neutral(reason)
        control = getattr(self, "_dt7_control_ref", None)
        failures: list[Exception] = []

        def attempt(action: Callable[[], Any]) -> None:
            try:
                action()
            except Exception as error:
                failures.append(error)

        if control is not None:
            for name, value in (("dt7_qualification_token", None), ("dt7_forced_keys", None),
                                ("dt7_send_paused", False), ("dt7_enabled", False), ("cc_enabled", False)):
                attempt(lambda: setattr(control, name, value))
            attempt(lambda: control.reset_inputs())
            # These independent caches must still be cleared if reset_inputs
            # fails. Otherwise a later recovery can replay the old held W.
            attempt(lambda: setattr(control, "last_command", None))
            attempt(lambda: setattr(control, "last_payload", b""))
            attempt(lambda: control.clear_pending())
            if dt7_ok is not None:
                attempt(lambda: setattr(control, "dt7_release_pending", not dt7_ok))
            if cc_ok is not None:
                attempt(lambda: setattr(control, "cc_release_pending", not cc_ok))
        if failures:
            # Keep the original failure and pending retry. The UI hook remains
            # success-only: it must not observe an incompletely cleared owner.
            raise failures[0]
        hook = self.input_clear_hook
        if hook is not None:
            hook(str(reason))
        emit_lifecycle_event(self.field_log,
            "input_clear",
            reason=reason,
            generation=self._input_clear_generation,
            callback_installed=hook is not None,
        )

    @property
    def video_source_snapshot(self) -> VideoSourceSnapshot:
        pending, inflight, failures = [], [], []
        for name, status in (("UDP/HEVC", self.udp.stop_status),
                             ("Native 0x0310", self.native_decoder.stop_status)):
            if status.pending:
                pending.append(name)
            if status.inflight:
                inflight.append(name)
            failures.extend(VideoSourceFailure(name, error) for error in status.errors)
        recovery = self._native_restart_thread
        if recovery is not None and recovery.is_alive():
            if "Native 0x0310" not in pending:
                pending.append("Native 0x0310")
            if "Native 0x0310" not in inflight:
                inflight.append("Native 0x0310")
        request = self._video_source_request
        return VideoSourceSnapshot(request.source if request is not None else "",
                                   self._active_video_source, tuple(pending),
                                   self._video_source_failures, self._video_source_generation,
                                   tuple(inflight), tuple(failures))

    def retry_active_video_source(self, *, expected_generation: int | None = None) -> None:
        """Explicit owner-thread retry; reject stale clicks and active closers."""
        snapshot = self.video_source_snapshot
        if expected_generation is not None and expected_generation != snapshot.generation:
            raise RuntimeError("Video-source request changed; retry is stale")
        if self._backend_transition_blocks_work():
            raise RuntimeError("Backend transition blocks video-source retry")
        if snapshot.inflight_resources:
            raise RuntimeError("Video-source cleanup is still in flight")
        request = self._video_source_request
        if request is None or not snapshot.retryable:
            raise RuntimeError("No failed or pending video-source request to retry")
        self.set_active_video_source(request.source, start=request.start,
                                     _retry=True)

    def configure_video_source(self, source_mode: str, *, legacy_channel: int) -> None:
        """统一接入来源与接收信道配置；无效信道不能触发来源切换。"""
        self.udp.configure_legacy_receiver(legacy_channel)
        self.set_active_video_source(source_mode)

    def video_receiver_status(self) -> dict[str, Any]:
        """返回接收端诊断快照，前端无需访问 UDP 内部所有者。"""
        return self.udp.receiver_status()

    def set_active_video_source(self, source_mode: str, *, start: bool = True, _retry: bool = False) -> None:
        if (hasattr(self, "_lifecycle") and not self._lifecycle.applying
                and self._backend_transition_blocks_work()):
            return
        normalized = str(source_mode or "").strip().lower()
        normalized = {
            "shark udp": "udp hevc", "udp": "udp hevc", "hevc": "udp hevc",
            "h265": "udp hevc", "native": "native 0x0310", "0x0310": "native 0x0310",
        }.get(normalized, normalized)
        self._normal_video_source = normalized
        if self.state is not None and int(self.state.deploy_mode) == 1:
            # The operator's choice is retained for exit, never used as a
            # fallback while the referee owns deployed Native-only video.
            normalized = "native 0x0310"
        request = VideoSourceRequest(normalized, start)
        previous = self._video_source_request
        if (not _retry and previous is not None and previous.source == normalized
                and self._video_source_failures):
            # A periodic UI refresh must expose the retained error, not spawn
            # an unbounded series of retries or claim the failed target ready.
            raise VideoSourceAdmissionError(self._video_source_failures)
        udp_selected = normalized in {"udp hevc", "legacy vt02 udp", "legacy official bridge"}
        ingress_mode = {"legacy vt02 udp": "legacy-vt02-h264", "legacy official bridge": "legacy-official-bridge"}.get(normalized, "shark-framed")
        ingress_change = udp_selected and self.udp.ingress_mode != ingress_mode
        if (not _retry and normalized == self._active_video_source
                and (not udp_selected or self.udp.running)):
            # 配置阶段的 start=False 可能已由模块启动了 socket。
            # 前端正式选中后仍需记录 start=True，否则视频维护不会运行。
            if previous != request:
                self._video_source_generation += 1
                self._video_source_request = request
            return
        if not _retry and previous == request and (
                self.udp.stop_pending or self.native_decoder.stop_pending):
            # A regular refresh may observe cleanup, but never grants another
            # native-close attempt after the previous worker has failed.
            return
        if _retry or previous != request:
            self._video_source_generation += 1
        self._video_source_request = request
        self._active_video_source = ""
        admission = VideoSourceAdmission()
        if not udp_selected or ingress_change or (_retry and self.udp.stop_pending):
            # This operation disables HEVC and attempts both native owners,
            # including a retained stopped session after a failed admission.
            admission.attempt("UDP/HEVC", self.udp.request_stop)
        if normalized != "native 0x0310" or (_retry and self.native_decoder.stop_pending):
            self._native_restart_generation += 1
            self._native_0310_blocked_until_sps = True
            admission.attempt("Native recovery reset", self._native_recovery.reset)
            admission.attempt("Native stop", self.native_decoder.request_stop)
        if not admission.failures and udp_selected and ingress_change:
            if not self.udp.cleanup_complete:
                # Target is recorded, but not active until the captured socket
                # and decoder have both retired. A later refresh observes that
                # completion; it never spawns a competing UDP owner.
                return
            admission.attempt("UDP ingress selection", lambda: self.udp.configure_ingress(ingress_mode))
        if not admission.failures and udp_selected:
            admission.attempt("HEVC enable", lambda: self.udp.request_decode_enabled(True))
            if not admission.failures and start and not self.udp.running:
                admission.attempt("UDP start", self.udp.start)
        if admission.failures and udp_selected:
            admission.attempt("Rejected UDP/HEVC target", self.udp.request_stop)
        if admission.failures and normalized == "native 0x0310":
            self._native_restart_generation += 1
            self._native_0310_blocked_until_sps = True
            admission.attempt("Rejected Native recovery reset", self._native_recovery.reset)
            admission.attempt("Rejected Native stop", self.native_decoder.request_stop)
        self._video_source_failures = tuple(admission.failures)
        admission.raise_if_failed()
        self._active_video_source = normalized

    def sync_frontend_state(self, state: RobotState) -> None:
        from rm_client.rules_compatibility import backend_rule_compatibility
        if not backend_rule_compatibility(self, state)[0]:
            return
        if self._backend_transition_blocks_work():
            return
        return self.referee_adapter.sync_frontend_state(state)

    def set_client_id(self, robot_id: int | str) -> None:
        """Change identity without joining Paho's old loop on the UI thread."""
        self._lifecycle.snapshot()
        if self._lifecycle.final:
            raise RuntimeError("Backend is closing; identity replacement is forbidden")
        profile = (get_profile(self._lifecycle.pending_scenario.referee_profile)
                   if self._lifecycle.pending_scenario is not None else self.referee)
        try:
            new_id = profile.validate_client_id(str(robot_id))
        except (ValueError, TypeError):
            self.safety.lock_control("invalid referee robot identity")
            self.safety.clear_stage_evidence()
            self.mqtt.stop(publish_status=False, wait=False)
            raise
        self._lifecycle.update_pending_identity(new_id)
        if new_id == self.mqtt.client_id:
            return
        self.safety.lock_control("referee robot identity changed")
        self.safety.clear_stage_evidence()
        self.mqtt.publish_estop_zero()
        was_started = self.mqtt._started
        self.mqtt.stop(publish_status=False, wait=False)
        self.mqtt.client_id = new_id
        if was_started and not self.lifecycle_pending:
            self.mqtt.start()

    @property
    def lifecycle_pending(self) -> bool:
        return self._lifecycle.pending

    @property
    def lifecycle_closed(self) -> bool:
        return self._lifecycle.closed

    @property
    def lifecycle_snapshot(self) -> LifecycleSnapshot:
        return self._lifecycle.snapshot()

    def _backend_transition_blocks_work(self) -> bool:
        return (bool(self._referee_change_error) or self._input_clear_incomplete()
                or self._lifecycle.blocks_work)

    def _capture_teardown_resources(self) -> TeardownResources:
        self._lifecycle.snapshot()  # Assert owner before reading runtime owners.
        return TeardownResources(
            safety=self.safety, mqtt=self.mqtt, udp=self.udp, serial=self.serial,
            polar=self.polar, native_decoder=self.native_decoder, logger=self.field_log,
            serial_starter=self._serial_start_thread,
            native_recovery=self._native_restart_thread,
            controller_angles=getattr(self, "controller_angles", None),
            dt7=getattr(self, "_dt7_link", None), cc=getattr(self, "_cc_link", None),
        )

    def _invalidate_shutdown_runtime(self) -> None:
        self._native_restart_generation += 1
        self._native_recovery.reset()
        self._native_0310_blocked_until_sps = True
        self.polar_bridge.reset()
        if self.state is not None:
            self.state.clear_polar_reported_identity()
        self.polar_arbiter.invalidate_pending_divergence()
        self.polar_motion.reset()
        self._polar_gimbal_last = None
        self.polar_chassis_state_seen = False

    def _finish_stopped_runtime(self) -> None:
        self._serial_start_thread = None
        self._native_recovery.reset()

    def request_shutdown(self, *, restart: bool = False,
                         teardown_tasks: tuple[TeardownTask, ...] = ()) -> int:
        """UI close request, with optional captured non-Qt presentation workers."""
        return self._lifecycle.request_shutdown(self._capture_teardown_resources(),
            restart=restart, teardown_tasks=teardown_tasks)

    @property
    def initial_shutdown_completion(self) -> ShutdownCompletion | None:
        return self._lifecycle.initial_shutdown_completion

    @property
    def shutdown_completion(self) -> ShutdownCompletion | None:
        return self._lifecycle.shutdown_completion

    def retry_shutdown(self) -> int:
        """Retry ended failures explicitly, without reopening final authority."""
        return self._lifecycle.retry_shutdown()

    def _drain_lifecycle_input_clear(self) -> None:
        """A broken UI observer cannot strand already-admitted resource cleanup."""
        if threading.get_ident() != self._input_owner_thread:
            raise RuntimeError("Input observers must be drained on their owner thread")
        try:
            self._drain_input_clear()
        except Exception as exc:
            if not self._lifecycle.record_input_failure(exc):
                raise

    def poll_lifecycle(self) -> LifecycleCompletion | None:
        """Commit resource ownership and input observers on the manager/UI thread."""
        self._drain_lifecycle_input_clear()
        return self._lifecycle.poll()

    def _wait_lifecycle(self) -> LifecycleCompletion | None:
        self._drain_lifecycle_input_clear()
        return self._lifecycle.wait()

    def shutdown(self, *, restart: bool = False) -> None:
        """Explicit ordered synchronous shutdown for CLI/tests, never UI ticks."""
        self._lifecycle.request_shutdown(self._capture_teardown_resources(),
            restart=restart, device_wait_timeout=2.5)
        self._wait_lifecycle()

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

        if self._video_source_failures:
            return None, 0.0, ""
        mode = str(source_mode or "Auto").strip().lower()
        if getattr(getattr(self, "state", None), "deploy_mode", 0) == 1:
            mode = "native 0x0310"
        if video_source_notice(mode):
            return None, 0.0, "not-implemented"
        want_native = mode in {"auto", "native", "native 0x0310", "0x0310"}
        want_udp = mode in {
            "auto",
            "shark udp",
            "udp",
            "udp hevc",
            "hevc",
            "h265",
            "legacy vt02 udp",
            "legacy official bridge",
        }
        candidates: list[tuple[Any, float, str]] = []
        if want_native and self.native_decoder.available:
            frame, frame_at = self.native_decoder.latest_with_timestamp(timeout_s)
            if frame is not None:
                candidates.append((frame, frame_at, "native-0x0310"))
        if want_udp:
            expected_ingress = {"legacy vt02 udp": "legacy-vt02-h264", "legacy official bridge": "legacy-official-bridge"}.get(mode, "shark-framed")
            if mode != "auto" and self.udp.ingress_mode != expected_ingress:
                return max(candidates, key=lambda item: item[1]) if candidates else (None, 0.0, "none")
            frame, frame_at = self.udp.latest_video_frame(timeout_s)
            if frame is not None:
                candidates.append((frame, frame_at, {"legacy-vt02-h264": "legacy-vt02-udp",
                    "legacy-official-bridge": "legacy-official-bridge"}.get(self.udp.ingress_mode, "udp")))
        if not candidates:
            return None, 0.0, "none"
        return max(candidates, key=lambda item: item[1])

    def _apply_custom_byte_block(self, state: RobotState, data: dict[str, Any]) -> None:
        block = bytes(data.get("data", b""))
        state.backend_stats["last_custom_block"] = {"len": len(block)}
        telemetry = parse_vehicle_telemetry(block)
        if telemetry is not None:
            self._apply_native_telemetry(state, telemetry)
            return
        video_chunk = parse_video_0310_chunk(block)
        if video_chunk is not None:
            self._apply_native_video_chunk(state, video_chunk)

    def _apply_native_telemetry(self, state: RobotState, telemetry: VehicleTelemetry) -> None:
        """Publish decoded telemetry, revoking localization trust before diagnostics."""
        localisation_reset = localization_invalidated(
            int(state.native_telemetry_frame_seq), telemetry.frame_seq, telemetry.status_text,
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

    def _apply_native_video_chunk(self, state: RobotState, video_chunk: Video0310Chunk) -> None:
        """Apply continuity decisions while checking live decoder availability."""
        if self._video_source_failures:
            return
        native_selected = self._native_video_selected(state)
        if not native_selected:
            # An unselected Native stream must not wake ffmpeg or
            # retain a partial recovery epoch in the background.
            # Selecting Native later waits for fresh SPS/PPS/IDR.
            self._native_0310_last_sequence = None
            self._native_0310_last_accepted_at = 0.0
            self._native_0310_blocked_until_sps = True
            self._native_recovery.reset()
            return
        # 0x0310 原生 H.264 流：不保留无限增长的历史码流。
        # reset 或向前序号缺口后停止喂流，直到下一个 Annex-B
        # SPS；这与 rm-native-viewer 的正式链路恢复策略一致，
        # 也避免在 UI tick 内同步 stop/restart ffmpeg。重复/迟到
        # 分块直接丢弃，防止同一 payload 破坏 NAL 边界。
        now = time.monotonic()
        last_accepted_at = getattr(self, "_native_0310_last_accepted_at", 0.0)
        idle_reset = bool(last_accepted_at > 0.0 and now - last_accepted_at > 2.0)
        decision = assess_native_video_sequence(
            sequence=video_chunk.sequence,
            modulus=video_chunk.sequence_modulus,
            reset=video_chunk.reset or idle_reset,
            previous_sequence=self._native_0310_last_sequence,
        )
        sequence = decision.sequence
        if decision.reset:
            self._native_0310_bytes_since_reset = 0
            self._native_0310_last_sequence = None
            self._native_0310_blocked_until_sps = True
            self._native_failure_epoch_handled = False
            self._native_recovery.reset()
        elif decision.dropped_chunks:
            self._native_0310_dropped_chunks += decision.dropped_chunks
            self._native_0310_blocked_until_sps = True
            self._native_failure_epoch_handled = False
            self._native_recovery.reset()
        elif not decision.accept_payload:
            self._native_0310_out_of_order_chunks += 1

        wrote = False
        payload_to_write = b""
        if decision.accept_payload:
            self._native_0310_last_accepted_at = now
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

        if payload_to_write and self._native_restart_thread is not None:
            # Recovery completion is consumed by tick(). Do not let a
            # transport payload race its process creation or gate reset.
            payload_to_write = b""
            self._native_recovery.reset()
            self._native_0310_blocked_until_sps = True
            self._native_0310_recovery_dropped_chunks += 1
        if payload_to_write:
            if not self.native_decoder.available:
                # start() never reaps a crashed process: that work is
                # reserved for the asynchronous health recovery path.
                started = self.native_decoder.start()
                if started and self._native_0310_decoder_started_once:
                    self._native_0310_decoder_restarts += 1
                self._native_0310_decoder_started_once = bool(
                    self._native_0310_decoder_started_once or started
                )
                if started:
                    self._native_failure_epoch_handled = False
            if self.native_decoder.available:
                wrote = self.native_decoder.write(payload_to_write)
                if not wrote:
                    # Yield briefly to the dedicated stdin writer.
                    # The owner-thread drain allows a 10 ms retry
                    # window, without waiting for process teardown.
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

    # ------------------------------------------------------------------
    # 下行消息 → RobotState
    # ------------------------------------------------------------------
    def _notify_referee_view(self, name: str, data: dict[str, Any], timestamp: float) -> None:
        try:
            observer = getattr(self, "referee_view_observer", None)
            if observer is None:
                return
            observer(name, deepcopy(data), float(timestamp))
            self._referee_view_error = ""
        except Exception as exc:
            try:
                error = f"{type(exc).__name__}: {exc}"
            except Exception:
                error = type(exc).__name__
            if error != getattr(self, "_referee_view_error", ""):
                self._referee_view_error = error
                try:
                    _LOG.warning("read-only referee view observer failed: %s", error)
                except Exception:
                    pass  # Even a broken display-error logger cannot affect apply.

    def apply_updates(self, state: RobotState) -> int:
        """消费解码队列并写入 RobotState；返回本次应用的消息条数。"""
        from rm_client.rules_compatibility import backend_rule_compatibility
        if self._backend_transition_blocks_work():
            return 0
        generation = self.mqtt.receive_generation
        messages = self.mqtt.drain_decoded()
        count = 0
        for _topic, name, data, _ts in messages:
            if (self.mqtt.receive_generation != generation
                    or not backend_rule_compatibility(self, state)[0]):
                break
            try:
                # Disable synthetic state before applying the first official
                # message. This preserves a first MapClick/Buff update instead
                # of clearing it immediately after it was written.
                self._begin_official_state(state, _ts)
                self._apply_one(state, name, data)
                if (self.mqtt.receive_generation != generation
                        or not backend_rule_compatibility(self, state)[0]):
                    break
                self._notify_referee_view(name, data, _ts)
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
    def _clean(data):
        return Rm2026StateAdapter._clean(data)

    def _apply_one(self, state: RobotState, name: str, data: dict[str, Any]) -> None:
        from rm_client.rules_compatibility import backend_rule_compatibility
        if not backend_rule_compatibility(self, state)[0]:
            return
        if name == "RobotStaticStatus" and int(data.get("robot_id", 0)) not in {0, int(self.mqtt.client_id)}:
            self.safety.lock_control("referee robot ID differs from MQTT ClientId")
            self.field_log.emit("referee_identity_mismatch", client_id=self.mqtt.client_id, robot_id=data.get("robot_id"))
        previous_deploy = state.deploy_mode
        result = self.referee_adapter._apply_one(state, name, data)
        if name == "DeployModeStatusSync" and state.deploy_mode != previous_deploy:
            self.set_active_video_source(self._normal_video_source or "Auto")
        return result

    @staticmethod
    def _economy_signature(state: RobotState) -> tuple[Any, ...]:
        return Rm2026StateAdapter._economy_signature(state)

    def _upsert_positions(self, state: RobotState, data: dict[str, Any]) -> None:
        return self.referee_adapter._upsert_positions(state, data)

    def _upsert_buff(self, state: RobotState, data: dict[str, Any]) -> None:
        return self.referee_adapter._upsert_buff(state, data)

    def _upsert_penalty(self, state: RobotState, data: dict[str, Any]) -> None:
        return self.referee_adapter._upsert_penalty(state, data)

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
        expected_economy_snapshot: Any = None,
    ) -> tuple[bool, str]:
        return self.mqtt.publish_named(
            name,
            payload,
            qos=1,
            intent=intent,
            qualification_token=qualification_token,
            expected_economy_snapshot=expected_economy_snapshot,
        )

    def publish_keyboard_mouse(self, mouse_x=0, mouse_y=0, mouse_z=0, *,
                               left=False, right=False, keyboard_value=0, mid=False,
                               qualification_token: str | None = None) -> tuple[bool, str]:
        return self._publish(
            "KeyboardMouseControl",
            self.referee.codec.encode_keyboard_mouse_control(
                mouse_x, mouse_y, mouse_z, left, right, keyboard_value, mid
            ),
            intent="core_kmc",
            qualification_token=qualification_token,
        )

    def _configure_cc_egress(self, control: Any, settings: CustomControllerSettings,
                             permissions: EgressPermissions) -> None:
        """Apply every CC falling edge before retiring the previous writer."""
        cc_wanted = settings.requested
        angle_link = getattr(self, "controller_angles", None)
        owned_ports = angle_link.owned_ports if angle_link is not None else ()
        cc_mock = settings.mock_output
        cc_port = settings.conflict_port
        if control is not None and cc_wanted and not cc_mock and owned_ports:
            if cc_port.strip().lower() == "auto" or any(port_key(cc_port) == port_key(p) for p in owned_ports):
                # A worker's stop request is not proof that its serial handle
                # has closed. Wait without starting a competing port writer.
                control.cc_enabled = False
                previous = getattr(self, "_cc_link", None)
                if previous is not None:
                    control.cc_link = previous
                if (getattr(self, "_cc_was_enabled", False)
                        or getattr(control, "cc_release_pending", False)
                        or not getattr(previous, "wire_neutral", True)):
                    # The replacement port is blocked, but the old writer
                    # still owns its last keys. Revoke and neutralize it now;
                    # repeated polls do not replenish its bounded retry budget.
                    control.release_custom_controller()
                control.cc_last_error = "角度链路仍占用串口；等待关闭后再启用键鼠出口"
                self._cc_was_enabled = False
                return
        if control is not None and cc_wanted:
            link_obj = self._cc_link_for(settings)
            control.cc_link = link_obj
            control.cc_enabled = bool(permissions.may_drive
                                      and not getattr(self, "_cc_reconfigure_blocked", False))
            control.cc_key_mask = 0x0F | (0x10 if settings.spin_allowed else 0)
            if not control.cc_enabled and (getattr(self, "_cc_was_enabled", False)
                                          or getattr(control, "cc_release_pending", False)):
                # 急停必须把选手端那侧的键鼠也归零。放在写原因**之前**：
                # release 成功时会清 cc_last_error，先写原因就被它抹掉了。
                control.release_custom_controller()
            if not control.cc_enabled:
                release_failed = (getattr(control, "cc_release_pending", False)
                                  or getattr(self, "_cc_reconfigure_blocked", False))
                control.cc_last_error = (str(getattr(link_obj, "last_error", "")
                                            or control.cc_last_error or "CC 中立帧尚未成功发送")
                    if release_failed else
                    "自定义控制器出口未生效："
                    + ("急停激活" if permissions.estopped else
                       ("模块 RobotLink 未启用" if not permissions.module_enabled
                        else "未授予 Allow Drive")))
        elif control is not None:
            control.cc_enabled = False
            if (getattr(self, "_cc_was_enabled", False)
                    or getattr(control, "cc_release_pending", False)):
                control.release_custom_controller()
        self._cc_was_enabled = bool(control is not None and control.cc_enabled)

    def _cc_link_for(self, settings: CustomControllerSettings) -> "cc_link.CustomControllerLink":
        """按 RobotLink 的设置拿（并按需重建）自定义控制器发送器。

        端口/速率/mock 变化时才重建对象：串口句柄不能每帧开关一次。
        """
        endpoint = settings.endpoint()
        port, hz, mock = endpoint.identity
        link = getattr(self, "_cc_link", None)
        self._cc_reconfigure_blocked = False
        if getattr(link, "startup_failed", False) and not link.closed:
            self._cc_reconfigure_blocked = True
            return link
        if (link is None or getattr(link, "closed", False)
                or (link.port, float(link.max_hz), bool(link.mock_output)) != (port, hz, mock)):
            if link is not None and not getattr(link, "wire_neutral", True):
                try:
                    ok, why = link.release_all()
                except Exception as exc:
                    ok, why = False, str(exc)
                if not ok:
                    # Retain the old target for neutral retries. Never discard
                    # it or send movement to a new port after uncertain release.
                    self._cc_reconfigure_blocked = True
                    change_key = (port, hz, mock)
                    if getattr(self, "_cc_change_key", None) != change_key:
                        self._cc_change_key = change_key
                        self.safety.lock_control("CC previous writer did not release: " + str(why))
                    return link
            if link is not None and hasattr(link, "close"):
                link.close()
                if not getattr(link, "closed", True):
                    self._cc_reconfigure_blocked = True
                    return link
            writer_type = cc_link.CustomControllerLink if mock else cc_link.AsyncCustomControllerLink
            ownership = ({"retain_owner": lambda owner: setattr(self, "_cc_link", owner)}
                         if not mock else {})
            link = writer_type(port=port, max_hz=hz, mock_output=mock, **ownership)
            if getattr(self, "_cc_link", None) is not link:
                self._cc_link = link
            self._cc_change_key = None
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
        return self._publish("CustomControl", self.referee.codec.encode_custom_control(payload),
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
        return self._publish("CustomControl", self.referee.codec.encode_custom_control(data), intent=intent)

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
            self.referee.codec.encode_custom_control(data),
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
        module = registry.get("ArmorAutoAim") if registry is not None else None
        mock = module.get_setting("Mock Output") if module is not None else None
        return select_gimbal_egress(
            polar_active=bool(getattr(target, "polar_link_active", False)),
            polar_authorized=self.polar_arbiter.may_command_gimbal,
            polar_has_target=self._polar_gimbal_last is not None,
            official_enabled=bool(module is not None and module.enabled),
            official_mock=bool(mock is not None and mock.value),
        )

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
        angle_link = getattr(self, "controller_angles", None)
        if angle_link is not None:
            angle_link.disarm("E-stop active")
        try:
            zero_result = self.mqtt.publish_estop_zero()
        except Exception as exc:
            zero_result = (False, f"zero publish failed: {exc}")
        self._last_estop_zero_ok, self._last_estop_zero_reason = zero_result
        # 图传键鼠那本账也要同帧清掉。固件侧本来有 300ms 新鲜度超时兜底，
        # 但"等超时"意味着急停之后最多还有 300ms 车仍带着客户端键位——
        # 和 0x5A 那条一样，必须在这一刻就发零帧，而不是留给下一帧。
        # 必须排在 safety.trigger_estop() 之前：门一锁就发不出去了。
        if getattr(self, "_km_seq", 0) > 0:
            try:
                self._last_km_release_ok, self._last_km_release_reason = self.publish_km_release()
            except Exception as exc:
                self._last_km_release_ok = False
                self._last_km_release_reason = str(exc)
        self._estop_triggered_at = time.perf_counter()
        self.safety.trigger_estop()
        self.serial.request_estop()
        # 队内 0x5A 链路必须同帧锁死：固件侧没有断流归零，不能等下一帧的
        # tick_polar_link 才发现（那一帧之前本地看门狗要 0.2s，车上有速度时太慢）。
        self.polar.request_estop()
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
        self._drain_input_clear()
        if self._backend_transition_blocks_work():
            return
        # Releasing E-stop returns authority only to LOCKED, never ARMED.
        self.safety.release_estop()
        # A driver-held lock must not stall the UI. The serial latch stays
        # revoked until this or a later tick can safely restore its generation.
        self.serial.tick()

    def lock_control(self, reason: str = "operator lock") -> None:
        self.safety.lock_control(reason)
        self.field_log.emit("control_lock", reason=reason)

    def begin_qualification(self, intents: Any, *, ttl_s: float = 30.0):
        from rm_client.rules_compatibility import backend_rule_compatibility
        compatible, reason = backend_rule_compatibility(self, getattr(self, "state", None))
        if not compatible:
            raise ValueError(reason)
        self._drain_input_clear()
        if self._backend_transition_blocks_work():
            raise RuntimeError("Backend lifecycle transition must finish before qualification")
        token = self.safety.begin_qualification(intents, ttl_s=ttl_s)
        compatible, reason = backend_rule_compatibility(self, getattr(self, "state", None))
        if not compatible:
            if not self.safety.estop_active:
                self.safety.lock_control(reason)
            raise ValueError(reason)
        if self._input_clear_incomplete():
            raise RuntimeError("Input clear must complete before qualification")
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
        from rm_client.rules_compatibility import backend_rule_compatibility
        compatible, reason = backend_rule_compatibility(self, getattr(self, "state", None))
        if not compatible:
            return False, reason
        try:
            self._drain_input_clear()
        except Exception:
            return False, "Input clear must complete before arming"
        if self._backend_transition_blocks_work():
            return False, "Backend lifecycle transition must finish before arming"
        if self.safety.estop_active:
            return False, "release E-stop explicitly before arming"
        # Evidence must be minted over the real transport. A mock bridge
        # reports every publish as OK, so a LabDebug/mock session could
        # otherwise produce valid-looking KMC/E-stop evidence without a
        # single byte reaching the referee system.
        if self.mqtt.mock_output or not self.safety.allow_mqtt_publish:
            self.safety.lock_control("arm requires a real MQTT output scenario (mock evidence is not signable)")
            return False, self.safety.last_reason
        if not self.mqtt.connected or not getattr(self.mqtt, "subscriptions_ready", False):
            self.safety.lock_control("arm requires a live MQTT connection with current SUBACK")
            return False, self.safety.last_reason
        runtime_robot = str(getattr(self.state, "robot_id", self.mqtt.client_id))
        if runtime_robot != self.mqtt.client_id or (robot_id is not None and str(robot_id) != self.mqtt.client_id):
            self.safety.lock_control("arm robot identity differs from MQTT ClientId")
            return False, self.safety.last_reason
        arm_owner = (self.mqtt.client, self.mqtt.client_id, self.mqtt.receive_generation,
                     self.referee.profile_id)
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
        dt7_writer = getattr(self, "_dt7_link", None)
        if getattr(self, "_dt7_selected", False) and dt7_writer is not None and not dt7_writer.mock_output:
            if "dt7_control" not in capabilities:
                self.safety.lock_control("DT7 link and robot safety evidence missing/expired")
                return False, self.safety.last_reason
            dt7_ready, dt7_reason = self._dt7_evidence_gate(dt7_writer.profile)
            if not dt7_ready:
                self.safety.lock_control(dt7_reason)
                return False, self.safety.last_reason
        if (not getattr(self.mqtt, "subscriptions_ready", False)
                or arm_owner != (self.mqtt.client, self.mqtt.client_id,
                                 self.mqtt.receive_generation, self.referee.profile_id)
                or not backend_rule_compatibility(self, getattr(self, "state", None))[0]):
            self.safety.lock_control("MQTT session changed during qualification verification")
            return False, self.safety.last_reason
        try:
            self.safety.arm_match(capabilities)
        except ValueError as exc:
            return False, str(exc)
        if self._input_clear_incomplete():
            return False, "Input clear must complete before arming"
        cc_writer = getattr(self, "_cc_link", None)
        if getattr(cc_writer, "asynchronous", False) and cc_writer.faulted:
            if not cc_writer.rearm(self.safety.last_transition_at):
                self.safety.lock_control("CC neutral remains unconfirmed; retry neutral before re-arm")
                return False, self.safety.last_reason
            self._cc_failure_seen = None
        if (not getattr(self.mqtt, "subscriptions_ready", False)
                or arm_owner != (self.mqtt.client, self.mqtt.client_id,
                                 self.mqtt.receive_generation, self.referee.profile_id)
                or self.safety.control_state.value != "MATCH_ARMED"
                or not backend_rule_compatibility(self, getattr(self, "state", None))[0]):
            self.safety.lock_control("control authority changed during arm notification")
            return False, self.safety.last_reason
        self.field_log.emit("control_arm", capabilities=sorted(capabilities))
        return True, "MATCH_ARMED"

    def _canonical_readiness_video_source(self) -> str:
        source = str(self._active_video_source).strip().lower()
        if source == "legacy official bridge":
            return "legacy_official_bridge"
        if source == "legacy vt02 udp":
            return "legacy_vt02_udp"  # Not the official-2026 qualification source.
        if source in {"native", "native 0x0310", "0x0310"}:
            return "native_0310"
        if source in {"shark udp", "udp", "udp hevc", "hevc", "h265"}:
            return "udp"
        return ""

    def _v2_action_admission(self, name: str, **values: Any) -> tuple[bool, str]:
        """Reject wrong role/value types before codecs can coerce them.

        Final wire identity, receipt, owner, stage and authority remain MQTT's
        responsibility. This early gate never grants permission by itself.
        """
        if self.referee.profile_id != "rm2026-v2.0.0":
            return True, "legacy profile"
        from .referee.rm2026_v2.uplink_validation import ROLE_IDS
        robot = getattr(self.state, "robot_id", None)
        if type(robot) is not int or str(robot) != self.mqtt.client_id:
            return False, "V2 requester identity differs from configured ClientId"
        if name in ROLE_IDS and robot not in ROLE_IDS[name]:
            return False, f"{name}: requester role is not supported"
        for field, value in values.items():
            if field in {"open_gate", "launch_confirm"}:
                if type(value) is not bool:
                    return False, f"{name}.{field} must be bool"
            elif type(value) is not int:
                return False, f"{name}.{field} must be integer"
        if name == "AssemblyCommand" and (values['operation'] not in {0,1,2} or values['difficulty'] not in {1,2,3,4}):
            return False, "V2 Assembly requires operation0/1/2 and difficulty1..4"
        if name == "RuneActivateCommand" and values['activate'] != 1:
            return False, "V2 Rune only defines activate1"
        if name == "DartCommand" and values['target_id'] not in range(1,6):
            return False, "V2 Dart target must be1..5"
        if name == "RobotPerformanceSelectionCommand":
            own = (values['shooter'], values['chassis'])
            control = values['sentry_control']
            if control not in {0,1}:
                return False, "V2 Sentry control must be0/1"
            role = robot % 100
            if role == 6:
                if any(own):
                    return False, "V2 aerial selection is same-team Sentry mode only"
            else:
                allowed = {0,3,4} if role == 1 else {0,1,2}
                if control != 0 or any(item not in allowed for item in own):
                    return False, "V2 performance system does not match requester role"
        if name in {"CommonCommand", "SentryCtrlCommand", "AirSupportCommand"}:
            from .referee.rm2026_v2.economy_rules import is_documented_command
            command = values['cmd_type'] if name == "CommonCommand" else values['command_id']
            if not is_documented_command(name, command):
                return False, f"{name}: unsupported V2 command"
        return True, "ok"

    def publish_map_click_cmd(self, *, is_send_all=0, robot_id=b"", mode=1,
                              enemy_id=0, ascii_code=0, mark_type=1,
                              map_x=0.0, map_y=0.0, intent="control",
                              qualification_token=None, expected_economy_snapshot=None) -> tuple[bool, str]:
        return self._publish("MapClickCmd", self.referee.codec.encode_map_click_cmd(
            is_send_all, robot_id, mode, enemy_id, ascii_code, mark_type, map_x, map_y),
            intent=intent, qualification_token=qualification_token,
            expected_economy_snapshot=expected_economy_snapshot)

    publish_map_click_notify = publish_map_click_cmd

    def publish_assembly_command(self, operation: int, difficulty: int) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("AssemblyCommand", operation=operation, difficulty=difficulty)
        if not allowed:
            return False, reason
        return self._publish("AssemblyCommand", self.referee.codec.encode_assembly_command(operation, difficulty))

    def publish_robot_performance(self, shooter: int, chassis: int,
                                  sentry_control: int = 0, *, qualification_token=None) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("RobotPerformanceSelectionCommand", shooter=shooter, chassis=chassis, sentry_control=sentry_control)
        if not allowed:
            return False, reason
        return self._publish("RobotPerformanceSelectionCommand",
            self.referee.codec.encode_robot_performance_selection_command(shooter, chassis, sentry_control),
            intent="core_kmc", qualification_token=qualification_token)

    def publish_rune_activate(self, activate: int) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("RuneActivateCommand", activate=activate)
        if not allowed:
            return False, reason
        return self._publish("RuneActivateCommand", self.referee.codec.encode_rune_activate_command(activate))

    def publish_dart_command(self, target_id: int, open_gate: bool, launch_confirm: bool) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("DartCommand", target_id=target_id, open_gate=open_gate, launch_confirm=launch_confirm)
        if not allowed:
            return False, reason
        return self._publish("DartCommand", self.referee.codec.encode_dart_command(target_id, open_gate, launch_confirm))

    def publish_sentry_command(self, command_id: int, *, qualification_token=None, expected_economy_snapshot=None) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("SentryCtrlCommand", command_id=command_id)
        if not allowed:
            return False, reason
        return self._publish("SentryCtrlCommand", self.referee.codec.encode_sentry_ctrl_command(command_id),
            intent="economy", qualification_token=qualification_token, expected_economy_snapshot=expected_economy_snapshot)

    def publish_common_command(self, cmd_type: int, param: int, *, qualification_token=None, expected_economy_snapshot=None) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("CommonCommand", cmd_type=cmd_type, param=param)
        if not allowed:
            return False, reason
        return self._publish("CommonCommand", self.referee.codec.encode_common_command(cmd_type, param),
            intent="economy", qualification_token=qualification_token, expected_economy_snapshot=expected_economy_snapshot)

    def publish_air_support(self, command_id: int, *, qualification_token=None, expected_economy_snapshot=None) -> tuple[bool, str]:
        allowed, reason = self._v2_action_admission("AirSupportCommand", command_id=command_id)
        if not allowed:
            return False, reason
        return self._publish("AirSupportCommand", self.referee.codec.encode_air_support_command(command_id),
            intent="economy", qualification_token=qualification_token, expected_economy_snapshot=expected_economy_snapshot)

    # ------------------------------------------------------------------
    # 工具：从本地 RobotProfile 设置推导官方性能体系取值并发布
    # ------------------------------------------------------------------
    @staticmethod
    def performance_values(registry: Any, *, referee_profile: str = DEFAULT_PROFILE) -> dict[str, int]:
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
        if ((referee_profile == "rm2026-v2.0.0" and role == "Aerial")
                or (referee_profile != "rm2026-v2.0.0" and role == "Sentinel")):
            return {
                "shooter": 0, "chassis": 0,
                "sentry_control": SENTRY_SEMI_AUTO if sentry == "SemiAuto" else SENTRY_AUTOMATIC,
            }
        return {"shooter": 0, "chassis": 0, "sentry_control": 0}

    def publish_performance_from_registry(self, registry: Any, *, qualification_token=None) -> tuple[bool, str]:
        values = self.performance_values(registry, referee_profile=self.referee.profile_id)
        return self.publish_robot_performance(
            values["shooter"], values["chassis"], values["sentry_control"],
            qualification_token=qualification_token)

    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        lifecycle = self.lifecycle_snapshot
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
            "video_source": {
                "selected": self._normal_video_source,
                "active": self._active_video_source,
                "state": ("not_implemented" if video_source_notice(self._normal_video_source)
                          else "selected"),
                "message": video_source_notice(self._normal_video_source),
            },
            "lifecycle": {
                "phase": lifecycle.phase.value,
                "generation": lifecycle.generation,
                "pending_resources": list(lifecycle.pending_resources),
                "failures": [{"resource": item.resource, "error": item.error}
                             for item in lifecycle.failures],
                "elapsed_s": lifecycle.elapsed_s,
                "final": lifecycle.final,
            },
            "referee_profile": self.referee.profile_id,
            "referee_protocol_version": self.referee.protocol_version,
            "safety": self.safety.stats(),
            "mqtt": self.mqtt.stats(),
            "udp": self.udp.stats(),
            "serial": self.serial.stats(),
            "controller_angles": self.controller_angles.stats(),
            "polar_link": {
                **capture_polar_link_health(lambda: self.polar, lambda: self.polar_bridge),
                "authority": self.polar_arbiter.stats(),
                "timestamp_roll_detected": self.polar.timestamp.roll_detected,
            },
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
                **dict(self.field_log.snapshot()),
            },
            "message_apply": {
                "errors": self._apply_error_count,
                "last_error": self._last_apply_error,
                "updates": self._official_state_updates_count,
            },
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
