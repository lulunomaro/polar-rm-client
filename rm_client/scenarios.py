from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCENARIO_MODES = ["LabDebug", "CampusSimulation", "OfficialEvent"]


@dataclass
class ConnectionProfile:
    mqtt_host: str
    mqtt_port: int
    udp_host: str
    udp_port: int
    serial_port: str
    serial_baud: int
    video_source: str


@dataclass
class SafetyProfile:
    mock_writes: bool
    allow_real_serial: bool
    allow_mqtt_publish: bool
    enforce_official_limits: bool


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
            mqtt_host="192.168.12.1",
            mqtt_port=3333,
            udp_host="0.0.0.0",
            udp_port=3334,
            serial_port="Stub",
            serial_baud=115200,
            video_source="UDP HEVC",
        ),
        safety=SafetyProfile(
            mock_writes=True,
            allow_real_serial=False,
            allow_mqtt_publish=False,
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
            mqtt_port=3333,
            udp_host="127.0.0.1",
            udp_port=3334,
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
        subtitle="Gestalt SHM 图传 / 自瞄 SHM+UDP 上屏",
        description="Gestalt 实验室：client 图传读 VisionBridge 共享内存；同济外挂同读 SHM 检测经 UDP 上屏；ArmorAutoAim 控制画框/跟枪；默认 mock 控制。",
        connection=ConnectionProfile(
            mqtt_host="127.0.0.1",
            mqtt_port=3333,
            udp_host="127.0.0.1",
            udp_port=3334,
            serial_port="Stub",
            serial_baud=115200,
            video_source="Gestalt SHM",
        ),
        safety=SafetyProfile(
            mock_writes=True,
            allow_real_serial=False,
            allow_mqtt_publish=False,
            enforce_official_limits=False,
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

    def apply_to_runtime(self, mqtt: Any, udp: Any, control: Any) -> None:
        config = self.current
        mqtt.host = config.connection.mqtt_host
        mqtt.port = config.connection.mqtt_port
        if hasattr(mqtt, "mock_output"):
            mqtt.mock_output = not config.safety.allow_mqtt_publish
        udp.host = config.connection.udp_host
        udp.port = config.connection.udp_port
        control.mock_output = config.safety.mock_writes

    def _set_setting(self, registry: Any, module_name: str, setting_name: str, value: object) -> None:
        module = registry.get(module_name)
        setting = module.get_setting(setting_name) if module else None
        if setting is not None:
            setting.value = value
