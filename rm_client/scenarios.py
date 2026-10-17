from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from .referee_profiles import get_profile_definition

_REFEREE = get_profile_definition()


SCENARIO_MODES = ["OfficialEvent", "CampusSimulation", "LabDebug"]


@dataclass
class ConnectionProfile:
    mqtt_host: str
    mqtt_port: int
    udp_host: str
    udp_port: int
    serial_port: str
    serial_baud: int
    video_source: str
    # 队内 StandardRobot++ 0x5A 链路（机器人自身 USB CDC / 串口）。
    # 与上面那个 referee 串口是两条不同物理链路，必须分开配置。
    polar_port: str = "Stub"
    polar_baud: int = 115200
    polar_send_hz: float = 50.0


@dataclass
class SafetyProfile:
    mock_writes: bool
    allow_real_serial: bool
    allow_mqtt_publish: bool
    enforce_official_limits: bool
    # 队内链路驾驶开关。默认关闭：只有台架/校内场景显式打开，
    # 且仍受 LOCKED 状态机与本地看门狗约束。
    allow_polar_drive: bool = False
    # 台架实验（LabDebug）直连 DT7 驾驶开关：默认关闭。
    # 打开后该场景允许把键鼠帧真实写进 DT7 VCOM（不走比赛资格链），
    # 但仍受急停 / 模块开关 / Allow Drive / 30Hz 限频约束。
    # OfficialEvent 与 CampusSimulation 保持 False = 原有资格链完全不变。
    allow_dt7_drive: bool = False


@dataclass
class ScenarioConfig:
    key: str
    title: str
    subtitle: str
    description: str
    connection: ConnectionProfile
    safety: SafetyProfile


SCENARIOS: dict[str, ScenarioConfig] = {
    "OfficialEvent": ScenarioConfig(
        key="OfficialEvent",
        title="大疆官方",
        subtitle="192.168.12.1 MQTT / UDP 3334",
        description="对齐 RM2026 自定义客户端默认传输参数的官方赛事配置。",
        connection=ConnectionProfile(
            mqtt_host=_REFEREE.mqtt_host,
            mqtt_port=_REFEREE.mqtt_port,
            udp_host="0.0.0.0",
            udp_port=_REFEREE.udp_port,
            serial_port="Stub",
            serial_baud=115200,
            video_source="UDP HEVC",
        ),
        safety=SafetyProfile(
            mock_writes=True,
            allow_real_serial=False,
            allow_mqtt_publish=True,
            enforce_official_limits=True,
        ),
    ),
    "CampusSimulation": ScenarioConfig(
        key="CampusSimulation",
        title="校内模拟",
        subtitle="Shark 本地 MQTT / HEVC UDP",
        description="用于校内训练、SharkDataServer 和本地图传模拟的配置。",
        connection=ConnectionProfile(
            mqtt_host="127.0.0.1",
            mqtt_port=_REFEREE.mqtt_port,
            udp_host="127.0.0.1",
            udp_port=_REFEREE.udp_port,
            serial_port="Stub",
            serial_baud=115200,
            video_source="Shark UDP",
        ),
        safety=SafetyProfile(
            mock_writes=True,
            allow_real_serial=False,
            allow_mqtt_publish=True,
            enforce_official_limits=True,
        ),
    ),
    "LabDebug": ScenarioConfig(
        key="LabDebug",
        title="实验室调试",
        subtitle="摄像头 / 合成图传 / 串口 stub",
        description="用于摄像头预览、协议测试和 mock 输出的本地调试配置。",
        connection=ConnectionProfile(
            mqtt_host="127.0.0.1",
            mqtt_port=_REFEREE.mqtt_port,
            udp_host="127.0.0.1",
            udp_port=_REFEREE.udp_port,
            serial_port="Stub",
            serial_baud=115200,
            video_source="Webcam",
        ),
        safety=SafetyProfile(
            mock_writes=True,
            allow_real_serial=False,
            allow_mqtt_publish=False,
            enforce_official_limits=False,
            allow_dt7_drive=True,      # [台架] LabDebug 显式允许直连 DT7 驾驶
        ),
    ),
}


class ScenarioManager:
    def __init__(self, default: str = "LabDebug") -> None:
        self.current = SCENARIOS[default]

    def set_scenario(self, key: str) -> ScenarioConfig:
        if key not in SCENARIOS:
            key = "LabDebug"
        self.current = SCENARIOS[key]
        return self.current

    def apply_to_registry(self, registry: Any) -> None:
        config = self.current
        vision_sensitivity = {
            "LabDebug": "Lab Responsive",
            "CampusSimulation": "Balanced",
            "OfficialEvent": "Match Reliable",
        }[config.key]
        self._set_setting(registry, "Scenario", "Mode", config.key)
        self._set_setting(registry, "Scenario", "Mock Writes", config.safety.mock_writes)
        self._set_setting(registry, "Scenario", "Official Limits", config.safety.enforce_official_limits)
        self._set_setting(registry, "MQTTLink", "Host", config.connection.mqtt_host)
        self._set_setting(registry, "MQTTLink", "Port", config.connection.mqtt_port)
        self._set_setting(registry, "UDPVideo", "Host", config.connection.udp_host)
        self._set_setting(registry, "UDPVideo", "Port", config.connection.udp_port)
        self._set_setting(registry, "CameraFeed", "Source", config.connection.video_source)
        self._set_setting(registry, "SerialBridge", "Port", config.connection.serial_port)
        self._set_setting(registry, "SerialBridge", "Baud", config.connection.serial_baud)
        self._set_setting(registry, "SerialBridge", "Mock Writes", config.safety.mock_writes)
        # 队内 0x5A 链路的设置**不在此处触碰**：它是与裁判系统并行的独立通道，
        # 端口/波特率/频率/驾驶授权全部归 RobotLink 模块自己管，在所有场景下
        # 一致且持久。曾经由场景写入会覆盖用户选择，把“选了大疆官方就开不了车”
        # 固化成架构约束，已整块移除。
        self._set_setting(registry, "TongjiVisionBridge", "Vision Kill Sensitivity", vision_sensitivity)
        # Economy is a referee MQTT command path, not a serial path.  Official
        # mode is live by default; local scenarios remain deterministic mocks.
        economy_mock = config.key != "OfficialEvent"
        self._set_setting(registry, "EconomyPanel", "Mock Requests", economy_mock)
        self._set_setting(registry, "ProjectileShop", "Mock Request", economy_mock)

    def apply_to_runtime(self, mqtt: Any, udp: Any, control: Any) -> None:
        config = self.current
        # 用 set_address 而不是赋值：切场景时 MQTT 往往**已经连着**上一个 broker，
        # 光改字段不会重连，于是"切到正式赛"之后键鼠还在往 127.0.0.1 发。
        # 老测试替身可能没有这个方法，所以保留赋值作为退路。
        setter = getattr(mqtt, "set_address", None)
        if callable(setter):
            setter(config.connection.mqtt_host, config.connection.mqtt_port)
        else:
            mqtt.host = config.connection.mqtt_host
            mqtt.port = config.connection.mqtt_port
        if hasattr(mqtt, "mock_output"):
            mqtt.mock_output = not config.safety.allow_mqtt_publish
        udp.host = config.connection.udp_host
        udp.port = config.connection.udp_port
        # 键鼠走 MQTT/图传，不跟随串口 Mock Writes。官方与校内 MQTT
        # 场景可真实发布；实验室保持 mock。
        control.mock_output = not config.safety.allow_mqtt_publish

    def _set_setting(self, registry: Any, module_name: str, setting_name: str, value: object) -> None:
        module = registry.get(module_name)
        setting = module.get_setting(setting_name) if module else None
        if setting is not None:
            setting.value = value
